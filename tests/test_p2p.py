"""Tests for qbit_network.network.p2p — P2P node, peer management, auth logic."""
import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from qbit_network.network.p2p import (
    P2PNode, Peer,
    MSG_HELLO, MSG_HELLO_AUTH, MSG_AUTH_RESPONSE, MSG_AUTH_CONFIRM,
    MSG_NEW_BLOCK, MSG_NEW_TX, MSG_GET_BLOCKS, MSG_BLOCKS,
    MSG_STATUS, MSG_GET_PEERS, MSG_PEERS,
    _is_safe_peer, _build_auth_message, _AUTH_DOMAIN, _CHALLENGE_LEN,
    _AUTH_RATE_MAX, _AUTH_RATE_WINDOW, _RATE_EXEMPT,
)
from qbit_network.core.wallet import Wallet
from qbit_network.config import MAX_PEERS


# =========================================================================
# _is_safe_peer (SSRF / peer filtering)
# =========================================================================

class TestIsSafePeer:

    def test_valid_public_ip_allowed(self):
        assert _is_safe_peer("8.8.8.8", 9000, "0.0.0.0", 9001)

    def test_loopback_v4_blocked(self):
        assert not _is_safe_peer("127.0.0.1", 9000, "0.0.0.0", 9001)

    def test_loopback_v6_blocked(self):
        assert not _is_safe_peer("::1", 9000, "0.0.0.0", 9001)

    def test_private_192_blocked(self):
        assert not _is_safe_peer("192.168.10.5", 9000, "0.0.0.0", 9001)

    def test_private_10_blocked(self):
        assert not _is_safe_peer("10.0.0.1", 9000, "0.0.0.0", 9001)

    def test_private_172_blocked(self):
        assert not _is_safe_peer("172.16.0.1", 9000, "0.0.0.0", 9001)

    def test_link_local_blocked(self):
        assert not _is_safe_peer("169.254.1.1", 9000, "0.0.0.0", 9001)

    def test_negative_port_blocked(self):
        assert not _is_safe_peer("8.8.8.8", -1, "0.0.0.0", 9001)

    def test_zero_port_blocked(self):
        assert not _is_safe_peer("8.8.8.8", 0, "0.0.0.0", 9001)

    def test_port_too_high_blocked(self):
        assert not _is_safe_peer("8.8.8.8", 65536, "0.0.0.0", 9001)

    def test_port_65535_allowed(self):
        assert _is_safe_peer("8.8.8.8", 65535, "0.0.0.0", 9001)

    def test_blocked_port_22(self):
        assert not _is_safe_peer("8.8.8.8", 22, "0.0.0.0", 9001)

    def test_blocked_port_443(self):
        assert not _is_safe_peer("8.8.8.8", 443, "0.0.0.0", 9001)

    def test_blocked_port_3306(self):
        assert not _is_safe_peer("8.8.8.8", 3306, "0.0.0.0", 9001)

    def test_blocked_port_6379(self):
        assert not _is_safe_peer("8.8.8.8", 6379, "0.0.0.0", 9001)

    def test_self_same_ip_and_port_blocked(self):
        assert not _is_safe_peer("8.8.8.8", 9000, "8.8.8.8", 9000)

    def test_same_ip_different_port_allowed(self):
        # Same IP but different port is allowed (not a self-connection)
        assert _is_safe_peer("8.8.8.8", 9001, "8.8.8.8", 9000)

    def test_localhost_string_blocked(self):
        assert not _is_safe_peer("localhost", 9000, "0.0.0.0", 9001)

    def test_metadata_hostname_blocked(self):
        assert not _is_safe_peer("metadata.google.internal", 9000, "0.0.0.0", 9001)

    def test_instance_data_hostname_blocked(self):
        assert not _is_safe_peer("instance-data", 9000, "0.0.0.0", 9001)


# =========================================================================
# _build_auth_message
# =========================================================================

class TestBuildAuthMessage:

    def test_contains_domain_prefix(self):
        challenge = b"\xab" * 32
        msg = _build_auth_message(challenge, "qv1test")
        assert msg.startswith(_AUTH_DOMAIN)

    def test_contains_challenge(self):
        challenge = b"\xcd" * 32
        msg = _build_auth_message(challenge, "qv1test")
        assert challenge in msg

    def test_contains_address(self):
        addr = "qv1" + "a" * 64
        msg = _build_auth_message(b"\x00" * 32, addr)
        assert addr.encode() in msg

    def test_different_challenges_produce_different_messages(self):
        c1 = b"\x01" * 32
        c2 = b"\x02" * 32
        m1 = _build_auth_message(c1, "qv1addr")
        m2 = _build_auth_message(c2, "qv1addr")
        assert m1 != m2

    def test_different_addresses_produce_different_messages(self):
        c = b"\x01" * 32
        m1 = _build_auth_message(c, "qv1" + "a" * 64)
        m2 = _build_auth_message(c, "qv1" + "b" * 64)
        assert m1 != m2


# =========================================================================
# Peer class
# =========================================================================

class TestPeer:

    def test_addr_property(self):
        p = Peer("1.2.3.4", 9000)
        assert p.addr == "1.2.3.4:9000"

    def test_initial_state(self):
        p = Peer("1.2.3.4", 9000)
        assert p.connected is False
        assert p.authenticated is False
        assert p.challenge == b''
        assert p.remote_pubkey == b''
        assert p.height == -1
        assert p.is_validator is False

    def test_peer_can_be_marked_authenticated(self):
        p = Peer("1.2.3.4", 9000)
        p.authenticated = True
        assert p.authenticated is True

    @pytest.mark.asyncio
    async def test_send_no_writer_is_noop(self):
        p = Peer("1.2.3.4", 9000)
        # No writer — should not raise
        await p.send("test", {})

    @pytest.mark.asyncio
    async def test_close_no_writer_is_noop(self):
        p = Peer("1.2.3.4", 9000)
        await p.close()
        assert p.connected is False

    @pytest.mark.asyncio
    async def test_send_sets_connected_false_on_error(self):
        p = Peer("1.2.3.4", 9000)
        writer = MagicMock()
        writer.is_closing.return_value = False
        writer.write.side_effect = OSError("broken pipe")
        p.writer = writer
        p.connected = True
        await p.send("test", {})
        assert p.connected is False

    @pytest.mark.asyncio
    async def test_close_calls_writer_close(self):
        p = Peer("1.2.3.4", 9000)
        writer = MagicMock()
        writer.is_closing.return_value = False
        p.writer = writer
        p.connected = True
        await p.close()
        writer.close.assert_called_once()
        assert p.connected is False


# =========================================================================
# P2PNode basic state
# =========================================================================

class TestP2PNodeBasic:

    def test_initial_no_peers(self):
        node = P2PNode()
        assert node.peer_count() == 0

    def test_initial_best_height_minus_one(self):
        node = P2PNode()
        assert node.best_peer_height() == -1

    def test_on_registers_handler(self):
        node = P2PNode()
        handler = AsyncMock()
        node.on("test_msg", handler)
        assert node._handlers.get("test_msg") is handler

    def test_has_signing_keys_false_when_empty(self):
        node = P2PNode()
        assert node._has_signing_keys() is False

    def test_has_signing_keys_true_with_keys(self):
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        assert node._has_signing_keys() is True

    def test_peer_count_only_counts_connected(self):
        node = P2PNode()
        p1 = Peer("1.1.1.1", 9000)
        p1.connected = True
        p2 = Peer("2.2.2.2", 9001)
        p2.connected = False
        node.peers["1.1.1.1:9000"] = p1
        node.peers["2.2.2.2:9001"] = p2
        assert node.peer_count() == 1

    def test_best_peer_height_from_connected_peers(self):
        node = P2PNode()
        p1 = Peer("1.1.1.1", 9000)
        p1.connected = True
        p1.height = 5
        p2 = Peer("2.2.2.2", 9001)
        p2.connected = True
        p2.height = 10
        node.peers["1.1.1.1:9000"] = p1
        node.peers["2.2.2.2:9001"] = p2
        assert node.best_peer_height() == 10

    def test_best_peer_height_excludes_disconnected(self):
        node = P2PNode()
        p1 = Peer("1.1.1.1", 9000)
        p1.connected = False
        p1.height = 100
        node.peers["1.1.1.1:9000"] = p1
        assert node.best_peer_height() == -1

    def test_pending_requests_dict_exists(self):
        node = P2PNode()
        assert isinstance(node._pending_requests, dict)


# =========================================================================
# _resolve_peer_pubkey
# =========================================================================

class TestResolvePeerPubkey:

    def test_valid_pubkey_resolves(self):
        w = Wallet.generate()
        node = P2PNode()
        pk = node._resolve_peer_pubkey(w.signing_pk.hex())
        assert pk == w.signing_pk

    def test_non_string_returns_none(self):
        node = P2PNode()
        assert node._resolve_peer_pubkey(12345) is None

    def test_invalid_hex_returns_none(self):
        node = P2PNode()
        assert node._resolve_peer_pubkey("zzzz") is None

    def test_wrong_length_returns_none(self):
        node = P2PNode()
        # Too short
        assert node._resolve_peer_pubkey("aabb" * 10) is None

    def test_correct_length_resolves(self):
        node = P2PNode()
        pk_bytes = b"\x01" * 1952
        pk_hex = pk_bytes.hex()
        result = node._resolve_peer_pubkey(pk_hex)
        assert result == pk_bytes

    def test_empty_string_returns_none(self):
        node = P2PNode()
        assert node._resolve_peer_pubkey("") is None


# =========================================================================
# _check_auth_gate
# =========================================================================

class TestCheckAuthGate:

    def test_exempt_messages_always_allowed(self):
        node = P2PNode()
        for msg in (MSG_HELLO, MSG_HELLO_AUTH, MSG_AUTH_RESPONSE, MSG_AUTH_CONFIRM):
            peer = Peer("1.1.1.1", 9000)
            peer.protocol_version = 2
            peer.authenticated = False
            assert node._check_auth_gate(msg, peer) is True

    def test_v1_peer_allowed_without_auth(self):
        node = P2PNode()
        peer = Peer("1.1.1.1", 9000)
        peer.protocol_version = 1
        peer.authenticated = False
        assert node._check_auth_gate(MSG_NEW_BLOCK, peer) is True

    def test_v2_unauthenticated_blocked_for_block(self):
        node = P2PNode()
        peer = Peer("1.1.1.1", 9000)
        peer.protocol_version = 2
        peer.authenticated = False
        assert node._check_auth_gate(MSG_NEW_BLOCK, peer) is False

    def test_v2_unauthenticated_blocked_for_tx(self):
        node = P2PNode()
        peer = Peer("1.1.1.1", 9000)
        peer.protocol_version = 2
        peer.authenticated = False
        assert node._check_auth_gate(MSG_NEW_TX, peer) is False

    def test_v2_authenticated_allowed_for_block(self):
        node = P2PNode()
        peer = Peer("1.1.1.1", 9000)
        peer.protocol_version = 2
        peer.authenticated = True
        assert node._check_auth_gate(MSG_NEW_BLOCK, peer) is True

    def test_v2_authenticated_allowed_for_tx(self):
        node = P2PNode()
        peer = Peer("1.1.1.1", 9000)
        peer.protocol_version = 2
        peer.authenticated = True
        assert node._check_auth_gate(MSG_NEW_TX, peer) is True

    def test_unknown_message_allowed_without_auth(self):
        node = P2PNode()
        peer = Peer("1.1.1.1", 9000)
        peer.protocol_version = 2
        peer.authenticated = False
        # Unknown messages not in _AUTH_REQUIRED_MSGS
        assert node._check_auth_gate("unknown_msg", peer) is True

    def test_get_blocks_blocked_for_unauthenticated_v2(self):
        node = P2PNode()
        peer = Peer("1.1.1.1", 9000)
        peer.protocol_version = 2
        peer.authenticated = False
        assert node._check_auth_gate(MSG_GET_BLOCKS, peer) is False

    def test_blocks_blocked_for_unauthenticated_v2(self):
        node = P2PNode()
        peer = Peer("1.1.1.1", 9000)
        peer.protocol_version = 2
        peer.authenticated = False
        assert node._check_auth_gate(MSG_BLOCKS, peer) is False


# =========================================================================
# _check_auth_rate
# =========================================================================

class TestCheckAuthRate:

    def test_first_attempt_allowed(self):
        node = P2PNode()
        assert node._check_auth_rate("8.8.8.8") is True

    def test_attempts_below_max_allowed(self):
        node = P2PNode()
        for _ in range(_AUTH_RATE_MAX - 1):
            assert node._check_auth_rate("1.2.3.4") is True

    def test_max_attempts_exceeded_blocked(self):
        node = P2PNode()
        for _ in range(_AUTH_RATE_MAX):
            node._check_auth_rate("5.5.5.5")
        # Next attempt should be blocked
        assert node._check_auth_rate("5.5.5.5") is False

    def test_localhost_always_allowed(self):
        node = P2PNode()
        for _ in range(_AUTH_RATE_MAX + 5):
            assert node._check_auth_rate("127.0.0.1") is True

    def test_ipv6_localhost_always_allowed(self):
        node = P2PNode()
        for _ in range(_AUTH_RATE_MAX + 5):
            assert node._check_auth_rate("::1") is True

    def test_different_ips_are_independent(self):
        node = P2PNode()
        for _ in range(_AUTH_RATE_MAX):
            node._check_auth_rate("1.1.1.1")
        # 1.1.1.1 is now blocked, but 2.2.2.2 should be fine
        assert node._check_auth_rate("2.2.2.2") is True

    def test_old_attempts_expire(self):
        node = P2PNode()
        # Pre-fill with old attempts
        import time
        old_time = time.monotonic() - _AUTH_RATE_WINDOW - 1
        node._auth_attempts["9.9.9.9"] = [old_time] * _AUTH_RATE_MAX
        # Old attempts should have expired, so this attempt should be allowed
        assert node._check_auth_rate("9.9.9.9") is True


# =========================================================================
# _check_rate_limit (wrapping RateLimiter)
# =========================================================================

class TestCheckRateLimit:

    def test_exempt_messages_bypass_rate_limit(self):
        node = P2PNode()
        peer = Peer("9.9.9.9", 9000)
        peer.host = "9.9.9.9"
        for msg_type in _RATE_EXEMPT:
            # Even with empty bucket this should pass
            result = node._check_rate_limit(peer, msg_type)
            assert result is True

    def test_localhost_bypasses_rate_limit(self):
        node = P2PNode()
        peer = Peer("127.0.0.1", 9000)
        peer.host = "127.0.0.1"
        for _ in range(100):
            assert node._check_rate_limit(peer, MSG_NEW_BLOCK) is True

    def test_rate_limit_triggers_for_remote_host(self):
        from qbit_network.network.rate_limiter import RateLimiter
        node = P2PNode()
        node._rate_limiter = RateLimiter(rate=0.0, burst=1.0)
        peer = Peer("8.8.8.8", 9000)
        peer.host = "8.8.8.8"
        # Consume the one token
        node._check_rate_limit(peer, MSG_NEW_BLOCK)
        # Now it should be rate limited
        assert node._check_rate_limit(peer, MSG_NEW_BLOCK) is False


# =========================================================================
# _check_validator_status
# =========================================================================

class TestCheckValidatorStatus:

    def test_peer_marked_as_validator_when_in_registry(self):
        w = Wallet.generate()
        node = P2PNode()
        node._validators[w.address] = w.signing_pk

        peer = Peer("1.2.3.4", 9000)
        peer.remote_pubkey = w.signing_pk
        node._check_validator_status(peer)
        assert peer.is_validator is True

    def test_peer_not_validator_when_not_in_registry(self):
        w = Wallet.generate()
        node = P2PNode()
        # Don't add to validators registry

        peer = Peer("1.2.3.4", 9000)
        peer.remote_pubkey = w.signing_pk
        node._check_validator_status(peer)
        assert peer.is_validator is False

    def test_no_remote_pubkey_is_noop(self):
        node = P2PNode()
        peer = Peer("1.2.3.4", 9000)
        peer.remote_pubkey = b''
        # Should not raise
        node._check_validator_status(peer)
        assert peer.is_validator is False


# =========================================================================
# _update_peer_key
# =========================================================================

class TestUpdatePeerKey:

    def test_valid_port_updates_key(self):
        node = P2PNode()
        peer = Peer("1.2.3.4", 0)
        peer.connected = True
        node.peers["_inbound_1.2.3.4_12345"] = peer
        new_key = node._update_peer_key(peer, "_inbound_1.2.3.4_12345",
                                         "_inbound_1.2.3.4_12345", 9000)
        assert peer.port == 9000
        assert "1.2.3.4:9000" in node.peers

    def test_invalid_port_returns_original_key(self):
        node = P2PNode()
        peer = Peer("1.2.3.4", 0)
        node.peers["_inbound_"] = peer
        key = node._update_peer_key(peer, "_inbound_", "_inbound_", 0)
        assert key == "_inbound_"
        assert peer.port == 0

    def test_duplicate_key_uses_temp_key(self):
        node = P2PNode()
        # Create a peer that already occupies the target key
        existing = Peer("1.2.3.4", 9000)
        existing.connected = True
        node.peers["1.2.3.4:9000"] = existing

        new_peer = Peer("1.2.3.4", 0)
        node.peers["_inbound_temp"] = new_peer
        key = node._update_peer_key(new_peer, "_inbound_temp", "_inbound_temp", 9000)
        # Should fall back to temp_key since real key is taken
        assert key == "_inbound_temp"


# =========================================================================
# _parse
# =========================================================================

class TestParse:

    def test_valid_json_parsed(self):
        line = b'{"type": "hello", "data": {"node_id": "abc"}}\n'
        result = P2PNode._parse(line)
        assert result is not None
        msg_type, data = result
        assert msg_type == "hello"
        assert data["node_id"] == "abc"

    def test_invalid_json_returns_none(self):
        result = P2PNode._parse(b"not json\n")
        assert result is None

    def test_empty_line_returns_none(self):
        result = P2PNode._parse(b"\n")
        assert result is None

    def test_missing_type_field_returns_empty_string(self):
        line = b'{"data": {"key": "val"}}\n'
        result = P2PNode._parse(line)
        assert result is not None
        msg_type, data = result
        assert msg_type == ""

    def test_missing_data_field_returns_empty_dict(self):
        line = b'{"type": "hello"}\n'
        result = P2PNode._parse(line)
        assert result is not None
        msg_type, data = result
        assert data == {}

    def test_non_utf8_returns_none(self):
        result = P2PNode._parse(b"\xff\xfe invalid utf8\n")
        assert result is None


# =========================================================================
# Auth handshake: _handle_hello_auth_inbound
# =========================================================================

class TestHelloAuthInbound:

    @pytest.mark.asyncio
    async def test_no_signing_keys_returns_false(self):
        node = P2PNode()  # no keys
        peer = Peer("1.2.3.4", 9000)
        result = await node._handle_hello_auth_inbound(peer, {})
        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_protocol_version_returns_false(self):
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        data = {
            "protocol_version": 1,  # must be >= 2
            "chain_id": "qbit-mainnet-1",
            "challenge": "aa" * 32,
            "timestamp": int(time.time()),
            "signing_pk": w.signing_pk.hex(),
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_wrong_chain_id_returns_false(self):
        from qbit_network.config import CHAIN_ID
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        data = {
            "protocol_version": 2,
            "chain_id": "wrong-chain-id",
            "challenge": "aa" * 32,
            "timestamp": int(time.time()),
            "signing_pk": w.signing_pk.hex(),
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_timestamp_returns_false(self):
        from qbit_network.config import CHAIN_ID, MAX_BLOCK_DRIFT
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        data = {
            "protocol_version": 2,
            "chain_id": CHAIN_ID,
            "challenge": "aa" * 32,
            "timestamp": int(time.time()) - MAX_BLOCK_DRIFT - 100,
            "signing_pk": w.signing_pk.hex(),
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_wrong_challenge_length_returns_false(self):
        from qbit_network.config import CHAIN_ID
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        data = {
            "protocol_version": 2,
            "chain_id": CHAIN_ID,
            "challenge": "aa" * 10,  # wrong length (20 bytes, not 32)
            "timestamp": int(time.time()),
            "signing_pk": w.signing_pk.hex(),
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_signing_pk_returns_false(self):
        from qbit_network.config import CHAIN_ID
        import os
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        peer.writer = AsyncMock()
        peer.writer.is_closing.return_value = False
        data = {
            "protocol_version": 2,
            "chain_id": CHAIN_ID,
            "challenge": os.urandom(32).hex(),
            "timestamp": int(time.time()),
            "signing_pk": "tooshort",
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False


# =========================================================================
# Auth handshake: _handle_auth_response
# =========================================================================

class TestHandleAuthResponse:

    @pytest.mark.asyncio
    async def test_no_pending_challenge_returns_false(self):
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        peer.challenge = b''  # no pending challenge
        result = await node._handle_auth_response(peer, {})
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_deadline_returns_false(self):
        import os
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        peer.challenge = os.urandom(32)
        peer.auth_deadline = time.monotonic() - 1  # expired
        result = await node._handle_auth_response(peer, {})
        assert result is False
        assert peer.challenge == b''

    @pytest.mark.asyncio
    async def test_invalid_signing_pk_returns_false(self):
        import os
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        peer.challenge = os.urandom(32)
        peer.auth_deadline = time.monotonic() + 10
        result = await node._handle_auth_response(peer, {"signing_pk": "bad"})
        assert result is False
        assert peer.challenge == b''

    @pytest.mark.asyncio
    async def test_bad_signature_returns_false(self):
        import os
        w = Wallet.generate()
        w2 = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        peer.challenge = os.urandom(32)
        peer.auth_deadline = time.monotonic() + 10
        # Provide valid-format pk but wrong signature
        data = {
            "signing_pk": w2.signing_pk.hex(),
            "challenge_sig": ("00" * 3309),  # garbage signature
            "counter_challenge": os.urandom(32).hex(),
        }
        result = await node._handle_auth_response(peer, data)
        assert result is False


# =========================================================================
# Auth handshake: _handle_auth_confirm
# =========================================================================

class TestHandleAuthConfirm:

    @pytest.mark.asyncio
    async def test_no_pending_challenge_returns_false(self):
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        peer.challenge = b''
        result = await node._handle_auth_confirm(peer, {})
        assert result is False

    @pytest.mark.asyncio
    async def test_no_remote_pubkey_returns_false(self):
        import os
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        peer.challenge = os.urandom(32)
        peer.remote_pubkey = b''  # not set
        peer.auth_deadline = time.monotonic() + 10
        result = await node._handle_auth_confirm(peer, {})
        assert result is False
        assert peer.challenge == b''

    @pytest.mark.asyncio
    async def test_expired_deadline_returns_false(self):
        import os
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        peer.challenge = os.urandom(32)
        peer.remote_pubkey = w.signing_pk
        peer.auth_deadline = time.monotonic() - 1  # expired
        result = await node._handle_auth_confirm(peer, {})
        assert result is False

    @pytest.mark.asyncio
    async def test_bad_signature_returns_false(self):
        import os
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        peer.challenge = os.urandom(32)
        peer.remote_pubkey = w.signing_pk
        peer.auth_deadline = time.monotonic() + 10
        data = {"challenge_sig": "00" * 3309}  # garbage
        result = await node._handle_auth_confirm(peer, data)
        assert result is False


# =========================================================================
# Full mutual auth handshake (unit-level, no real TCP)
# =========================================================================

class TestMutualAuthHandshake:

    @pytest.mark.asyncio
    async def test_full_mutual_auth_roundtrip(self):
        """Simulate a full mutual auth handshake between two nodes.

        Since Peer uses __slots__, we can't monkey-patch send().
        Instead we give peers a real writer that captures writes via
        asyncio StreamWriter mock.
        """
        import os
        from qbit_network.config import CHAIN_ID
        from qbit_network.crypto.mldsa import MLDSA

        wa = Wallet.generate()
        wb = Wallet.generate()

        node_a = P2PNode(signing_sk=wa.signing_sk, signing_pk=wa.signing_pk,
                         validator_address=wa.address)
        node_b = P2PNode(signing_sk=wb.signing_sk, signing_pk=wb.signing_pk,
                         validator_address=wb.address)

        # Capture what each peer "sends" via the writer
        a_writes = []
        b_writes = []

        def make_writer(capture_list):
            w = MagicMock()
            w.is_closing.return_value = False
            async def _drain():
                pass
            w.drain = _drain
            def _write(data):
                capture_list.append(data)
            w.write = _write
            return w

        peer_b_at_a = Peer("2.2.2.2", 9001)
        peer_b_at_a.writer = make_writer(a_writes)

        peer_a_at_b = Peer("1.1.1.1", 9000)
        peer_a_at_b.writer = make_writer(b_writes)

        # Step 1: A initiates — send hello_auth to B
        challenge_a = os.urandom(_CHALLENGE_LEN)
        peer_b_at_a.challenge = challenge_a
        peer_b_at_a.auth_deadline = time.monotonic() + 10
        peer_b_at_a.protocol_version = 2

        # Step 2: B handles hello_auth from A
        hello_auth_data = {
            "protocol_version": 2,
            "node_id": "node_a",
            "port": 9000,
            "chain_id": CHAIN_ID,
            "challenge": challenge_a.hex(),
            "timestamp": int(time.time()),
            "signing_pk": wa.signing_pk.hex(),
        }
        result = await node_b._handle_hello_auth_inbound(peer_a_at_b, hello_auth_data)
        assert result is True, "hello_auth_inbound should succeed"
        # B wrote an auth_response — parse it from b_writes
        assert len(b_writes) >= 1
        import json as _json
        auth_response_msg = _json.loads(b_writes[0].decode().strip())
        assert auth_response_msg["type"] == MSG_AUTH_RESPONSE
        auth_response_data = auth_response_msg["data"]

        # Step 3: A handles auth_response from B
        result = await node_a._handle_auth_response(peer_b_at_a, auth_response_data)
        assert result is True, "auth_response should succeed"
        assert peer_b_at_a.authenticated is True
        # A wrote an auth_confirm
        assert len(a_writes) >= 1
        auth_confirm_msg = _json.loads(a_writes[0].decode().strip())
        assert auth_confirm_msg["type"] == MSG_AUTH_CONFIRM
        auth_confirm_data = auth_confirm_msg["data"]

        # Step 4: B handles auth_confirm from A
        result = await node_b._handle_auth_confirm(peer_a_at_b, auth_confirm_data)
        assert result is True, "auth_confirm should succeed"
        assert peer_a_at_b.authenticated is True


# =========================================================================
# P2PNode.connect — safety checks (no actual TCP)
# =========================================================================

class TestP2PNodeConnect:

    @pytest.mark.asyncio
    async def test_connect_unsafe_peer_skipped(self):
        node = P2PNode()
        # 127.0.0.1 is unsafe
        await node.connect("127.0.0.1", 9000)
        assert "127.0.0.1:9000" not in node.peers

    @pytest.mark.asyncio
    async def test_connect_already_connected_skipped(self):
        node = P2PNode()
        peer = Peer("8.8.8.8", 9000)
        peer.connected = True
        node.peers["8.8.8.8:9000"] = peer
        # Connecting again should not duplicate
        with patch("asyncio.open_connection", side_effect=OSError("refused")):
            await node.connect("8.8.8.8", 9000)
        assert len(node.peers) == 1

    @pytest.mark.asyncio
    async def test_connect_max_peers_respected(self):
        node = P2PNode()
        for i in range(MAX_PEERS):
            p = Peer(f"10.0.0.{i}", 9000)
            p.connected = True
            node.peers[f"10.0.0.{i}:9000"] = p
        # At max peers, connect to a new host should be skipped
        await node.connect("8.8.8.8", 9001)
        assert "8.8.8.8:9001" not in node.peers

    @pytest.mark.asyncio
    async def test_connect_network_error_does_not_crash(self):
        node = P2PNode()
        with patch("asyncio.open_connection", side_effect=OSError("refused")):
            await node.connect("8.8.8.8", 9001)  # should not raise


# =========================================================================
# P2PNode.broadcast
# =========================================================================

class TestP2PNodeBroadcast:

    def _make_capturing_peer(self, host, port, connected=True):
        """Create a Peer with a capturing writer (Peer uses __slots__)."""
        writes = []
        writer = MagicMock()
        writer.is_closing.return_value = False
        writer.write = lambda data: writes.append(data)
        async def _drain():
            pass
        writer.drain = _drain
        p = Peer(host, port, writer=writer)
        p.connected = connected
        return p, writes

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_connected(self):
        node = P2PNode()
        p1, w1 = self._make_capturing_peer("1.1.1.1", 9000)
        p2, w2 = self._make_capturing_peer("2.2.2.2", 9000)
        node.peers["1.1.1.1:9000"] = p1
        node.peers["2.2.2.2:9000"] = p2

        await node.broadcast(MSG_NEW_TX, {"key": "val"})
        assert len(w1) == 1
        assert len(w2) == 1

    @pytest.mark.asyncio
    async def test_broadcast_excludes_peer(self):
        node = P2PNode()
        p1, w1 = self._make_capturing_peer("1.1.1.1", 9000)
        p2, w2 = self._make_capturing_peer("2.2.2.2", 9000)
        node.peers["1.1.1.1:9000"] = p1
        node.peers["2.2.2.2:9000"] = p2

        await node.broadcast(MSG_NEW_TX, {}, exclude="1.1.1.1:9000")
        assert len(w1) == 0
        assert len(w2) == 1

    @pytest.mark.asyncio
    async def test_broadcast_skips_disconnected(self):
        node = P2PNode()
        p1, w1 = self._make_capturing_peer("1.1.1.1", 9000, connected=False)
        node.peers["1.1.1.1:9000"] = p1

        await node.broadcast(MSG_NEW_TX, {})
        assert len(w1) == 0

    @pytest.mark.asyncio
    async def test_broadcast_empty_peers_is_noop(self):
        node = P2PNode()
        # Should not raise
        await node.broadcast(MSG_NEW_TX, {})


# =========================================================================
# P2PNode.stop
# =========================================================================

class TestP2PNodeStop:

    @pytest.mark.asyncio
    async def test_stop_clears_peers(self):
        node = P2PNode()
        p = Peer("1.1.1.1", 9000)
        p.connected = True
        writer = MagicMock()
        writer.is_closing.return_value = False
        p.writer = writer
        node.peers["1.1.1.1:9000"] = p
        await node.stop()
        assert len(node.peers) == 0

    @pytest.mark.asyncio
    async def test_stop_cancels_cleanup_task(self):
        node = P2PNode()
        # Create a dummy cleanup task
        async def noop():
            await asyncio.sleep(100)
        node._cleanup_task = asyncio.create_task(noop())
        await node.stop()
        # Give the event loop a chance to process the cancellation
        await asyncio.sleep(0)
        # Task is either cancelled or done (cancel request was sent)
        assert node._cleanup_task.cancelled() or node._cleanup_task.done()

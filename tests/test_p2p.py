"""Tests for qbit_network.network.p2p — P2P node, peer management, auth logic."""
import asyncio
import os
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from qbit_network.network.p2p import (
    P2PNode, Peer,
    MSG_HELLO, MSG_HELLO_AUTH, MSG_AUTH_RESPONSE, MSG_AUTH_CONFIRM,
    MSG_SESSION_KEY, MSG_ENCRYPTED,
    MSG_NEW_BLOCK, MSG_NEW_TX, MSG_GET_BLOCKS, MSG_BLOCKS,
    MSG_STATUS, MSG_GET_PEERS, MSG_PEERS,
    _is_safe_peer, _build_auth_message, _derive_session_key,
    _AUTH_DOMAIN, _CHALLENGE_LEN,
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

        # Step 2: B handles hello_auth from A (with proof)
        proof_msg = _build_auth_message(challenge_a, wa.address)
        proof_sig = MLDSA.sign(wa.signing_sk, proof_msg)
        hello_auth_data = {
            "protocol_version": 2,
            "node_id": "node_a",
            "port": 9000,
            "chain_id": CHAIN_ID,
            "challenge": challenge_a.hex(),
            "timestamp": int(time.time()),
            "signing_pk": wa.signing_pk.hex(),
            "proof": proof_sig.hex(),
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


# =========================================================================
# Part 1: Auth Verify-Before-Sign — proof field tests
# =========================================================================

class TestAuthProofField:
    """Tests for the proof field in hello_auth (verify-before-sign fix)."""

    def _make_node(self, wallet):
        return P2PNode(signing_sk=wallet.signing_sk, signing_pk=wallet.signing_pk,
                       validator_address=wallet.address)

    def _make_peer_with_writer(self, host="1.2.3.4", port=9000):
        writes = []
        writer = MagicMock()
        writer.is_closing.return_value = False
        writer.write = lambda data: writes.append(data)
        async def _drain():
            pass
        writer.drain = _drain
        peer = Peer(host, port, writer=writer)
        return peer, writes

    def _valid_hello_auth_data(self, initiator_wallet, challenge):
        from qbit_network.config import CHAIN_ID
        from qbit_network.crypto.mldsa import MLDSA
        proof_msg = _build_auth_message(challenge, initiator_wallet.address)
        proof_sig = MLDSA.sign(initiator_wallet.signing_sk, proof_msg)
        return {
            "protocol_version": 2,
            "node_id": "test_node",
            "port": 9000,
            "chain_id": CHAIN_ID,
            "challenge": challenge.hex(),
            "timestamp": int(time.time()),
            "signing_pk": initiator_wallet.signing_pk.hex(),
            "proof": proof_sig.hex(),
        }

    @pytest.mark.asyncio
    async def test_missing_proof_field_rejected(self):
        """hello_auth without proof field must be rejected."""
        import os
        from qbit_network.config import CHAIN_ID
        wa = Wallet.generate()
        wb = Wallet.generate()
        node = self._make_node(wb)
        peer, _ = self._make_peer_with_writer()
        challenge = os.urandom(32)
        data = {
            "protocol_version": 2,
            "node_id": "a",
            "port": 9000,
            "chain_id": CHAIN_ID,
            "challenge": challenge.hex(),
            "timestamp": int(time.time()),
            "signing_pk": wa.signing_pk.hex(),
            # no proof field
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_proof_field_rejected(self):
        """hello_auth with empty proof string must be rejected."""
        import os
        from qbit_network.config import CHAIN_ID
        wa = Wallet.generate()
        wb = Wallet.generate()
        node = self._make_node(wb)
        peer, _ = self._make_peer_with_writer()
        challenge = os.urandom(32)
        data = {
            "protocol_version": 2,
            "node_id": "a",
            "port": 9000,
            "chain_id": CHAIN_ID,
            "challenge": challenge.hex(),
            "timestamp": int(time.time()),
            "signing_pk": wa.signing_pk.hex(),
            "proof": "",
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_proof_hex_rejected(self):
        """hello_auth with non-hex proof must be rejected."""
        import os
        from qbit_network.config import CHAIN_ID
        wa = Wallet.generate()
        wb = Wallet.generate()
        node = self._make_node(wb)
        peer, _ = self._make_peer_with_writer()
        challenge = os.urandom(32)
        data = {
            "protocol_version": 2,
            "node_id": "a",
            "port": 9000,
            "chain_id": CHAIN_ID,
            "challenge": challenge.hex(),
            "timestamp": int(time.time()),
            "signing_pk": wa.signing_pk.hex(),
            "proof": "not_valid_hex!!!",
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_wrong_key_proof_rejected(self):
        """Proof signed by a different key than signing_pk must be rejected."""
        import os
        from qbit_network.config import CHAIN_ID
        from qbit_network.crypto.mldsa import MLDSA
        wa = Wallet.generate()
        wrong_wallet = Wallet.generate()
        wb = Wallet.generate()
        node = self._make_node(wb)
        peer, _ = self._make_peer_with_writer()
        challenge = os.urandom(32)
        # Sign proof with wrong_wallet but claim wa's pubkey
        proof_msg = _build_auth_message(challenge, wa.address)
        bad_proof = MLDSA.sign(wrong_wallet.signing_sk, proof_msg)
        data = {
            "protocol_version": 2,
            "node_id": "a",
            "port": 9000,
            "chain_id": CHAIN_ID,
            "challenge": challenge.hex(),
            "timestamp": int(time.time()),
            "signing_pk": wa.signing_pk.hex(),
            "proof": bad_proof.hex(),
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_proof_for_wrong_challenge_rejected(self):
        """Proof signed over a different challenge must be rejected."""
        import os
        from qbit_network.config import CHAIN_ID
        from qbit_network.crypto.mldsa import MLDSA
        wa = Wallet.generate()
        wb = Wallet.generate()
        node = self._make_node(wb)
        peer, _ = self._make_peer_with_writer()
        challenge = os.urandom(32)
        other_challenge = os.urandom(32)
        # Sign proof over different challenge
        proof_msg = _build_auth_message(other_challenge, wa.address)
        bad_proof = MLDSA.sign(wa.signing_sk, proof_msg)
        data = {
            "protocol_version": 2,
            "node_id": "a",
            "port": 9000,
            "chain_id": CHAIN_ID,
            "challenge": challenge.hex(),
            "timestamp": int(time.time()),
            "signing_pk": wa.signing_pk.hex(),
            "proof": bad_proof.hex(),
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_valid_proof_accepted(self):
        """hello_auth with valid proof field must be accepted."""
        import os
        wa = Wallet.generate()
        wb = Wallet.generate()
        node = self._make_node(wb)
        peer, writes = self._make_peer_with_writer()
        challenge = os.urandom(32)
        data = self._valid_hello_auth_data(wa, challenge)
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is True
        # Should have written auth_response
        assert len(writes) >= 1

    @pytest.mark.asyncio
    async def test_proof_non_string_type_rejected(self):
        """proof field that is not a string must be rejected."""
        import os
        from qbit_network.config import CHAIN_ID
        wa = Wallet.generate()
        wb = Wallet.generate()
        node = self._make_node(wb)
        peer, _ = self._make_peer_with_writer()
        challenge = os.urandom(32)
        data = {
            "protocol_version": 2,
            "node_id": "a",
            "port": 9000,
            "chain_id": CHAIN_ID,
            "challenge": challenge.hex(),
            "timestamp": int(time.time()),
            "signing_pk": wa.signing_pk.hex(),
            "proof": 12345,  # not a string
        }
        result = await node._handle_hello_auth_inbound(peer, data)
        assert result is False


# =========================================================================
# Part 2: Encrypted channel tests
# =========================================================================

class TestEncryptedChannel:
    """Tests for P2P encrypted channel using ML-KEM + AES-256-GCM."""

    def _make_capturing_peer(self, host="1.2.3.4", port=9000, connected=True):
        writes = []
        writer = MagicMock()
        writer.is_closing.return_value = False
        writer.write = lambda data: writes.append(data)
        async def _drain():
            pass
        writer.drain = _drain
        peer = Peer(host, port, writer=writer)
        peer.connected = connected
        return peer, writes

    def test_peer_initial_encryption_state(self):
        """New peers start with no encryption."""
        p = Peer("1.2.3.4", 9000)
        assert p.session_key is None
        assert p.encrypted is False
        assert p.encryption_pk is None

    def test_derive_session_key_deterministic(self):
        """Same shared secret produces same session key."""
        ss = os.urandom(32)
        k1 = _derive_session_key(ss)
        k2 = _derive_session_key(ss)
        assert k1 == k2
        assert len(k1) == 32

    def test_derive_session_key_different_inputs(self):
        """Different shared secrets produce different keys."""
        k1 = _derive_session_key(b"\x01" * 32)
        k2 = _derive_session_key(b"\x02" * 32)
        assert k1 != k2

    @pytest.mark.asyncio
    async def test_send_encrypted_with_session_key(self):
        """send_encrypted wraps message in AES-GCM when encrypted."""
        from qbit_network.crypto.aes import aes_decrypt
        peer, writes = self._make_capturing_peer()
        peer.session_key = os.urandom(32)
        peer.encrypted = True

        await peer.send_encrypted("new_block", {"index": 1})
        assert len(writes) == 1
        import json as _json
        outer = _json.loads(writes[0].decode().strip())
        assert outer["type"] == MSG_ENCRYPTED
        # Decrypt the inner message
        ct = bytes.fromhex(outer["data"]["data"])
        plaintext = aes_decrypt(peer.session_key, ct)
        inner = _json.loads(plaintext.decode())
        assert inner["type"] == "new_block"
        assert inner["data"]["index"] == 1

    @pytest.mark.asyncio
    async def test_send_encrypted_fallback_without_key(self):
        """send_encrypted sends plaintext when no session key."""
        peer, writes = self._make_capturing_peer()
        peer.session_key = None
        peer.encrypted = False

        await peer.send_encrypted("new_block", {"index": 1})
        assert len(writes) == 1
        import json as _json
        msg = _json.loads(writes[0].decode().strip())
        assert msg["type"] == "new_block"

    def test_decrypt_message_valid(self):
        """_decrypt_message correctly decrypts valid encrypted message."""
        from qbit_network.crypto.aes import aes_encrypt
        import json as _json
        node = P2PNode()
        peer = Peer("1.2.3.4", 9000)
        key = os.urandom(32)
        peer.session_key = key

        inner_msg = _json.dumps({"type": "new_tx", "data": {"tx_id": "abc"}}).encode()
        ct = aes_encrypt(key, inner_msg)
        result = node._decrypt_message(peer, {"data": ct.hex()})
        assert result is not None
        mt, data = result
        assert mt == "new_tx"
        assert data["tx_id"] == "abc"

    def test_decrypt_message_no_session_key(self):
        """_decrypt_message returns None when no session key."""
        node = P2PNode()
        peer = Peer("1.2.3.4", 9000)
        peer.session_key = None
        result = node._decrypt_message(peer, {"data": "aabb"})
        assert result is None

    def test_decrypt_message_invalid_ciphertext(self):
        """_decrypt_message returns None on decryption failure."""
        node = P2PNode()
        peer = Peer("1.2.3.4", 9000)
        peer.session_key = os.urandom(32)
        result = node._decrypt_message(peer, {"data": "00" * 50})
        assert result is None

    def test_decrypt_message_invalid_hex(self):
        """_decrypt_message returns None on invalid hex."""
        node = P2PNode()
        peer = Peer("1.2.3.4", 9000)
        peer.session_key = os.urandom(32)
        result = node._decrypt_message(peer, {"data": "not_hex!!!"})
        assert result is None

    def test_decrypt_message_non_string_data(self):
        """_decrypt_message handles non-string data field."""
        node = P2PNode()
        peer = Peer("1.2.3.4", 9000)
        peer.session_key = os.urandom(32)
        result = node._decrypt_message(peer, {"data": 12345})
        assert result is None

    @pytest.mark.asyncio
    async def test_handle_session_key_valid(self):
        """Responder successfully handles session_key message."""
        from qbit_network.crypto.mlkem import MLKEM
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address,
                       encryption_sk=w.encryption_sk, encryption_pk=w.encryption_pk)
        peer = Peer("1.2.3.4", 9000)
        peer.authenticated = True

        # Encapsulate a shared secret using responder's enc pk
        ct, shared_secret = MLKEM.encapsulate(w.encryption_pk)
        expected_key = _derive_session_key(shared_secret)

        result = await node._handle_session_key(peer, {
            "ciphertext": ct.hex(),
            "encryption_pk": w.encryption_pk.hex(),
        })
        assert result is True
        assert peer.encrypted is True
        assert peer.session_key == expected_key

    @pytest.mark.asyncio
    async def test_handle_session_key_no_encryption_keys(self):
        """session_key rejected when node has no encryption keys."""
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        # no encryption keys
        peer = Peer("1.2.3.4", 9000)
        peer.authenticated = True
        result = await node._handle_session_key(peer, {"ciphertext": "aa" * 1088})
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_session_key_unauthenticated_peer(self):
        """session_key rejected from unauthenticated peer."""
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address,
                       encryption_sk=w.encryption_sk, encryption_pk=w.encryption_pk)
        peer = Peer("1.2.3.4", 9000)
        peer.authenticated = False
        result = await node._handle_session_key(peer, {"ciphertext": "aa" * 1088})
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_session_key_invalid_ct_length(self):
        """session_key rejected with wrong ciphertext length."""
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address,
                       encryption_sk=w.encryption_sk, encryption_pk=w.encryption_pk)
        peer = Peer("1.2.3.4", 9000)
        peer.authenticated = True
        result = await node._handle_session_key(peer, {"ciphertext": "aa" * 10})
        assert result is False

    @pytest.mark.asyncio
    async def test_encrypted_message_roundtrip(self):
        """Full roundtrip: encrypt at sender, decrypt at receiver."""
        from qbit_network.crypto.aes import aes_encrypt
        import json as _json
        node = P2PNode()
        key = os.urandom(32)

        # Sender side
        peer_send, writes = self._make_capturing_peer()
        peer_send.session_key = key
        peer_send.encrypted = True
        await peer_send.send_encrypted("new_block", {"index": 42})

        # Receiver side
        peer_recv = Peer("2.2.2.2", 9001)
        peer_recv.session_key = key
        outer = _json.loads(writes[0].decode().strip())
        result = node._decrypt_message(peer_recv, outer["data"])
        assert result is not None
        mt, data = result
        assert mt == "new_block"
        assert data["index"] == 42

    @pytest.mark.asyncio
    async def test_initiate_encrypted_channel_both_have_keys(self):
        """Initiator sends session_key when both sides have encryption keys."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address,
                       encryption_sk=w.encryption_sk, encryption_pk=w.encryption_pk)
        peer, writes = self._make_capturing_peer()
        peer.encryption_pk = w2.encryption_pk
        peer.authenticated = True

        await node._initiate_encrypted_channel(peer)
        assert peer.encrypted is True
        assert peer.session_key is not None
        assert len(peer.session_key) == 32
        # Should have sent session_key message
        import json as _json
        msg = _json.loads(writes[0].decode().strip())
        assert msg["type"] == MSG_SESSION_KEY

    @pytest.mark.asyncio
    async def test_initiate_encrypted_channel_no_peer_enc_pk(self):
        """No session_key sent when peer has no encryption_pk (v1 fallback)."""
        w = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address,
                       encryption_sk=w.encryption_sk, encryption_pk=w.encryption_pk)
        peer, writes = self._make_capturing_peer()
        peer.encryption_pk = None
        peer.authenticated = True

        await node._initiate_encrypted_channel(peer)
        assert peer.encrypted is False
        assert len(writes) == 0

    @pytest.mark.asyncio
    async def test_initiate_encrypted_channel_no_own_enc_keys(self):
        """No session_key sent when we have no encryption keys."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        node = P2PNode(signing_sk=w.signing_sk, signing_pk=w.signing_pk,
                       validator_address=w.address)
        # no encryption_sk/pk
        peer, writes = self._make_capturing_peer()
        peer.encryption_pk = w2.encryption_pk
        peer.authenticated = True

        await node._initiate_encrypted_channel(peer)
        assert peer.encrypted is False
        assert len(writes) == 0

    def test_resolve_encryption_pk_valid(self):
        """Valid ML-KEM-768 public key resolves correctly."""
        w = Wallet.generate()
        node = P2PNode()
        result = node._resolve_encryption_pk(w.encryption_pk.hex())
        assert result == w.encryption_pk

    def test_resolve_encryption_pk_wrong_length(self):
        node = P2PNode()
        assert node._resolve_encryption_pk("aa" * 10) is None

    def test_resolve_encryption_pk_empty(self):
        node = P2PNode()
        assert node._resolve_encryption_pk("") is None

    def test_resolve_encryption_pk_non_string(self):
        node = P2PNode()
        assert node._resolve_encryption_pk(12345) is None

    def test_resolve_encryption_pk_invalid_hex(self):
        node = P2PNode()
        assert node._resolve_encryption_pk("zzzz") is None

    def test_has_encryption_keys(self):
        w = Wallet.generate()
        node = P2PNode(encryption_sk=w.encryption_sk, encryption_pk=w.encryption_pk)
        assert node._has_encryption_keys() is True

    def test_has_no_encryption_keys(self):
        node = P2PNode()
        assert node._has_encryption_keys() is False

    @pytest.mark.asyncio
    async def test_broadcast_uses_encrypted_send(self):
        """broadcast() uses send_encrypted which wraps when encrypted."""
        from qbit_network.crypto.aes import aes_decrypt
        import json as _json
        node = P2PNode()
        peer, writes = self._make_capturing_peer()
        key = os.urandom(32)
        peer.session_key = key
        peer.encrypted = True
        node.peers["1.2.3.4:9000"] = peer

        await node.broadcast(MSG_NEW_TX, {"tx_id": "test123"})
        assert len(writes) == 1
        outer = _json.loads(writes[0].decode().strip())
        assert outer["type"] == MSG_ENCRYPTED
        ct = bytes.fromhex(outer["data"]["data"])
        plaintext = aes_decrypt(key, ct)
        inner = _json.loads(plaintext.decode())
        assert inner["type"] == MSG_NEW_TX
        assert inner["data"]["tx_id"] == "test123"


# =========================================================================
# Part 3: Connection dedup tests
# =========================================================================

class TestConnectionDedup:
    """Tests for connection deduplication logic."""

    def test_peer_initial_dedup_fields(self):
        """New peers have correct initial dedup field values."""
        p = Peer("1.2.3.4", 9000)
        assert p.is_initiator is False
        assert p.remote_address == ""

    def test_no_dedup_when_no_remote_address(self):
        """Dedup is skipped when remote_address is not set."""
        w = Wallet.generate()
        node = P2PNode(validator_address=w.address)
        peer = Peer("1.2.3.4", 9000)
        peer.remote_address = ""
        result = node._dedup_connection(peer)
        assert result is False

    def test_no_dedup_when_no_validator_address(self):
        """Dedup is skipped when our validator_address is not set."""
        node = P2PNode()
        peer = Peer("1.2.3.4", 9000)
        peer.remote_address = "qv1someaddress"
        result = node._dedup_connection(peer)
        assert result is False

    def test_no_dedup_when_no_duplicate(self):
        """Dedup returns False when there is no duplicate connection."""
        wa = Wallet.generate()
        wb = Wallet.generate()
        node = P2PNode(validator_address=wa.address)

        peer = Peer("1.2.3.4", 9000)
        peer.remote_address = wb.address
        peer.authenticated = True
        peer.is_initiator = True
        node.peers["1.2.3.4:9000"] = peer

        result = node._dedup_connection(peer)
        assert result is False

    def test_dedup_we_are_smaller_keep_our_outbound(self):
        """When we have smaller address, keep the connection we initiated."""
        # Create wallets and ensure ordering
        wa = Wallet.generate()
        wb = Wallet.generate()
        # Ensure wa.address < wb.address for deterministic test
        if wa.address > wb.address:
            wa, wb = wb, wa

        node = P2PNode(validator_address=wa.address)

        # Existing connection (they initiated — inbound)
        existing = Peer("1.2.3.4", 9000)
        existing.remote_address = wb.address
        existing.authenticated = True
        existing.is_initiator = False
        existing.connected = True
        writer = MagicMock()
        writer.is_closing.return_value = False
        existing.writer = writer
        node.peers["1.2.3.4:9000"] = existing

        # New connection (we initiated — outbound)
        new_peer = Peer("1.2.3.4", 9001)
        new_peer.remote_address = wb.address
        new_peer.authenticated = True
        new_peer.is_initiator = True
        node.peers["1.2.3.4:9001"] = new_peer

        result = node._dedup_connection(new_peer)
        # We are smaller, we keep our outbound (new_peer), close existing
        assert result is False
        assert "1.2.3.4:9000" not in node.peers

    def test_dedup_we_are_smaller_close_their_outbound(self):
        """When we have smaller address, close the connection they initiated."""
        wa = Wallet.generate()
        wb = Wallet.generate()
        if wa.address > wb.address:
            wa, wb = wb, wa

        node = P2PNode(validator_address=wa.address)

        # Existing connection (we initiated — outbound)
        existing = Peer("1.2.3.4", 9000)
        existing.remote_address = wb.address
        existing.authenticated = True
        existing.is_initiator = True
        existing.connected = True
        writer = MagicMock()
        writer.is_closing.return_value = False
        existing.writer = writer
        node.peers["1.2.3.4:9000"] = existing

        # New connection (they initiated — inbound)
        new_peer = Peer("1.2.3.4", 9001)
        new_peer.remote_address = wb.address
        new_peer.authenticated = True
        new_peer.is_initiator = False
        node.peers["1.2.3.4:9001"] = new_peer

        result = node._dedup_connection(new_peer)
        # We are smaller, close the inbound (new_peer), keep our outbound
        assert result is True

    def test_dedup_they_are_smaller_close_our_outbound(self):
        """When they have smaller address, close the connection we initiated."""
        wa = Wallet.generate()
        wb = Wallet.generate()
        if wa.address > wb.address:
            wa, wb = wb, wa
        # wb has larger address, use it as our address
        node = P2PNode(validator_address=wb.address)

        # Existing connection (they initiated — inbound)
        existing = Peer("1.2.3.4", 9000)
        existing.remote_address = wa.address
        existing.authenticated = True
        existing.is_initiator = False
        existing.connected = True
        writer = MagicMock()
        writer.is_closing.return_value = False
        existing.writer = writer
        node.peers["1.2.3.4:9000"] = existing

        # New connection (we initiated — outbound)
        new_peer = Peer("1.2.3.4", 9001)
        new_peer.remote_address = wa.address
        new_peer.authenticated = True
        new_peer.is_initiator = True
        node.peers["1.2.3.4:9001"] = new_peer

        result = node._dedup_connection(new_peer)
        # They are smaller, close our outbound (new_peer), keep their inbound
        assert result is True

    def test_dedup_they_are_smaller_keep_their_outbound(self):
        """When they have smaller address, keep the connection they initiated."""
        wa = Wallet.generate()
        wb = Wallet.generate()
        if wa.address > wb.address:
            wa, wb = wb, wa
        node = P2PNode(validator_address=wb.address)

        # Existing connection (we initiated — outbound)
        existing = Peer("1.2.3.4", 9000)
        existing.remote_address = wa.address
        existing.authenticated = True
        existing.is_initiator = True
        existing.connected = True
        writer = MagicMock()
        writer.is_closing.return_value = False
        existing.writer = writer
        node.peers["1.2.3.4:9000"] = existing

        # New connection (they initiated — inbound)
        new_peer = Peer("1.2.3.4", 9001)
        new_peer.remote_address = wa.address
        new_peer.authenticated = True
        new_peer.is_initiator = False
        node.peers["1.2.3.4:9001"] = new_peer

        result = node._dedup_connection(new_peer)
        # They are smaller, keep their outbound (new_peer), close our outbound
        assert result is False
        assert "1.2.3.4:9000" not in node.peers

    def test_dedup_skips_unauthenticated_peers(self):
        """Dedup does not consider unauthenticated peers as duplicates."""
        wa = Wallet.generate()
        wb = Wallet.generate()
        node = P2PNode(validator_address=wa.address)

        # Existing connection (not authenticated)
        existing = Peer("1.2.3.4", 9000)
        existing.remote_address = wb.address
        existing.authenticated = False  # not authenticated
        existing.is_initiator = True
        existing.connected = True
        node.peers["1.2.3.4:9000"] = existing

        # New connection
        new_peer = Peer("1.2.3.4", 9001)
        new_peer.remote_address = wb.address
        new_peer.authenticated = True
        new_peer.is_initiator = True
        node.peers["1.2.3.4:9001"] = new_peer

        result = node._dedup_connection(new_peer)
        # Unauthenticated peer is not a duplicate
        assert result is False

    def test_dedup_different_remote_addresses(self):
        """Peers with different remote_addresses are not duplicates."""
        wa = Wallet.generate()
        wb = Wallet.generate()
        wc = Wallet.generate()
        node = P2PNode(validator_address=wa.address)

        p1 = Peer("1.2.3.4", 9000)
        p1.remote_address = wb.address
        p1.authenticated = True
        p1.is_initiator = True
        node.peers["1.2.3.4:9000"] = p1

        p2 = Peer("1.2.3.4", 9001)
        p2.remote_address = wc.address  # different address
        p2.authenticated = True
        p2.is_initiator = True
        node.peers["1.2.3.4:9001"] = p2

        result = node._dedup_connection(p2)
        assert result is False


# =========================================================================
# Full mutual auth handshake with encryption (unit-level, no real TCP)
# =========================================================================

class TestMutualAuthWithEncryption:

    @pytest.mark.asyncio
    async def test_full_handshake_with_encrypted_channel(self):
        """Full mutual auth + encrypted channel establishment."""
        import os
        import json as _json
        from qbit_network.config import CHAIN_ID
        from qbit_network.crypto.mldsa import MLDSA
        from qbit_network.crypto.mlkem import MLKEM

        wa = Wallet.generate()
        wb = Wallet.generate()

        node_a = P2PNode(signing_sk=wa.signing_sk, signing_pk=wa.signing_pk,
                         validator_address=wa.address,
                         encryption_sk=wa.encryption_sk, encryption_pk=wa.encryption_pk)
        node_b = P2PNode(signing_sk=wb.signing_sk, signing_pk=wb.signing_pk,
                         validator_address=wb.address,
                         encryption_sk=wb.encryption_sk, encryption_pk=wb.encryption_pk)

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
        peer_b_at_a.is_initiator = True

        peer_a_at_b = Peer("1.1.1.1", 9000)
        peer_a_at_b.writer = make_writer(b_writes)
        peer_a_at_b.is_initiator = False

        # Step 1: A initiates (with proof + encryption_pk)
        challenge_a = os.urandom(_CHALLENGE_LEN)
        peer_b_at_a.challenge = challenge_a
        peer_b_at_a.auth_deadline = time.monotonic() + 10
        peer_b_at_a.protocol_version = 2

        proof_msg = _build_auth_message(challenge_a, wa.address)
        proof_sig = MLDSA.sign(wa.signing_sk, proof_msg)

        hello_auth_data = {
            "protocol_version": 2,
            "node_id": "node_a",
            "port": 9000,
            "chain_id": CHAIN_ID,
            "challenge": challenge_a.hex(),
            "timestamp": int(time.time()),
            "signing_pk": wa.signing_pk.hex(),
            "proof": proof_sig.hex(),
            "encryption_pk": wa.encryption_pk.hex(),
        }

        # Step 2: B handles hello_auth
        result = await node_b._handle_hello_auth_inbound(peer_a_at_b, hello_auth_data)
        assert result is True

        auth_response_msg = _json.loads(b_writes[0].decode().strip())
        assert auth_response_msg["type"] == "auth_response"
        auth_response_data = auth_response_msg["data"]
        assert "encryption_pk" in auth_response_data

        # Step 3: A handles auth_response
        result = await node_a._handle_auth_response(peer_b_at_a, auth_response_data)
        assert result is True
        assert peer_b_at_a.authenticated is True

        # A should have sent auth_confirm + session_key
        assert len(a_writes) >= 2
        auth_confirm_msg = _json.loads(a_writes[0].decode().strip())
        assert auth_confirm_msg["type"] == "auth_confirm"

        session_key_msg = _json.loads(a_writes[1].decode().strip())
        assert session_key_msg["type"] == "session_key"
        assert peer_b_at_a.encrypted is True

        # Step 4: B handles auth_confirm
        auth_confirm_data = auth_confirm_msg["data"]
        result = await node_b._handle_auth_confirm(peer_a_at_b, auth_confirm_data)
        assert result is True
        assert peer_a_at_b.authenticated is True

        # Step 5: B handles session_key
        session_key_data = session_key_msg["data"]
        result = await node_b._handle_session_key(peer_a_at_b, session_key_data)
        assert result is True
        assert peer_a_at_b.encrypted is True

        # Both sides should have the same session key
        assert peer_b_at_a.session_key == peer_a_at_b.session_key
        assert len(peer_b_at_a.session_key) == 32

"""Tests for HELLO_AUTH P2P authentication (Sprint 3)."""
import os
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from qbit_network.network.p2p import (
    P2PNode,
    Peer,
    _build_auth_message,
    _is_safe_peer,
    MSG_HELLO_AUTH,
    MSG_AUTH_RESPONSE,
    MSG_AUTH_CONFIRM,
    _AUTH_DOMAIN,
    _CHALLENGE_LEN,
    _AUTH_REQUIRED_MSGS,
    _AUTH_RATE_MAX,
    _AUTH_RATE_WINDOW,
)
from qbit_network.crypto.mldsa import MLDSA
from qbit_network.core.wallet import Wallet
from qbit_network.config import CHAIN_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_p2p_node(wallet=None):
    """Return a minimal P2PNode with signing keys."""
    if wallet is None:
        wallet = Wallet.generate()
    node = P2PNode(
        host="0.0.0.0",
        port=9000,
        node_id="test-node",
        validator_address=wallet.address,
        signing_sk=wallet.signing_sk,
        signing_pk=wallet.signing_pk,
    )
    return node, wallet


def _make_peer(host="1.2.3.4", port=9001):
    reader = MagicMock()
    writer = MagicMock()
    writer.is_closing.return_value = False
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    peer = Peer(host, port, reader, writer)
    peer.connected = True
    return peer


# ===========================================================================
# _build_auth_message
# ===========================================================================

class TestBuildAuthMessage:
    """Unit tests for _build_auth_message helper."""

    def test_contains_domain(self):
        challenge = os.urandom(32)
        address = "qv1testaddress"
        msg = _build_auth_message(challenge, address)
        assert msg.startswith(_AUTH_DOMAIN)

    def test_contains_challenge(self):
        challenge = os.urandom(32)
        address = "qv1testaddress"
        msg = _build_auth_message(challenge, address)
        assert challenge in msg

    def test_contains_address(self):
        challenge = os.urandom(32)
        address = "qv1testaddress"
        msg = _build_auth_message(challenge, address)
        assert address.encode() in msg

    def test_different_challenges_produce_different_messages(self):
        c1 = os.urandom(32)
        c2 = os.urandom(32)
        addr = "qv1same"
        assert _build_auth_message(c1, addr) != _build_auth_message(c2, addr)

    def test_different_addresses_produce_different_messages(self):
        challenge = os.urandom(32)
        assert (
            _build_auth_message(challenge, "qv1alice")
            != _build_auth_message(challenge, "qv1bob")
        )

    def test_message_is_bytes(self):
        msg = _build_auth_message(os.urandom(32), "qv1test")
        assert isinstance(msg, bytes)


# ===========================================================================
# Challenge-response: signature verification
# ===========================================================================

class TestChallengeResponseSignature:
    """Verify the signature round-trip used in HELLO_AUTH."""

    def test_valid_signature_verifies(self):
        wallet = Wallet.generate()
        challenge = os.urandom(_CHALLENGE_LEN)
        msg = _build_auth_message(challenge, wallet.address)
        sig = MLDSA.sign(wallet.signing_sk, msg)
        assert MLDSA.verify(wallet.signing_pk, msg, sig) is True

    def test_wrong_key_does_not_verify(self):
        wallet_a = Wallet.generate()
        wallet_b = Wallet.generate()
        challenge = os.urandom(_CHALLENGE_LEN)
        msg = _build_auth_message(challenge, wallet_a.address)
        sig = MLDSA.sign(wallet_a.signing_sk, msg)
        # Verify with wrong key
        assert MLDSA.verify(wallet_b.signing_pk, msg, sig) is False

    def test_tampered_challenge_fails_verify(self):
        wallet = Wallet.generate()
        challenge = os.urandom(_CHALLENGE_LEN)
        msg = _build_auth_message(challenge, wallet.address)
        sig = MLDSA.sign(wallet.signing_sk, msg)
        # Tamper the challenge in the message by rebuilding with different challenge
        tampered_msg = _build_auth_message(os.urandom(_CHALLENGE_LEN), wallet.address)
        assert MLDSA.verify(wallet.signing_pk, tampered_msg, sig) is False

    def test_replay_with_same_challenge_second_use_detectable(self):
        """Same challenge used twice can be detected if server tracks seen challenges."""
        wallet = Wallet.generate()
        challenge = os.urandom(_CHALLENGE_LEN)
        msg = _build_auth_message(challenge, wallet.address)
        sig = MLDSA.sign(wallet.signing_sk, msg)

        # First use: valid
        assert MLDSA.verify(wallet.signing_pk, msg, sig) is True

        # Same challenge, same sig -- still cryptographically valid but
        # the server SHOULD check challenge uniqueness separately.
        # Here we test that the signature alone cannot tell apart replay:
        assert MLDSA.verify(wallet.signing_pk, msg, sig) is True
        # The server-side protection against replay is the single-use peer.challenge
        # field being cleared after first use (see p2p.py _handle_auth_response).


# ===========================================================================
# Invalid pubkey size rejection
# ===========================================================================

class TestInvalidPubkeyRejection:
    """Test _resolve_peer_pubkey rejects wrong-size pubkeys."""

    def test_empty_pubkey_rejected(self):
        server, _ = _make_p2p_node()
        result = server._resolve_peer_pubkey("")
        assert result is None

    def test_short_pubkey_rejected(self):
        server, _ = _make_p2p_node()
        result = server._resolve_peer_pubkey("ab" * 100)  # 100 bytes, need 1952
        assert result is None

    def test_non_hex_pubkey_rejected(self):
        server, _ = _make_p2p_node()
        result = server._resolve_peer_pubkey("not-valid-hex!")
        assert result is None

    def test_valid_pubkey_accepted(self):
        wallet = Wallet.generate()
        server, _ = _make_p2p_node()
        result = server._resolve_peer_pubkey(wallet.signing_pk.hex())
        assert result == wallet.signing_pk

    def test_pubkey_too_long_rejected(self):
        server, _ = _make_p2p_node()
        # 1953 bytes = 3906 hex chars (one byte too many)
        result = server._resolve_peer_pubkey("ab" * 1953)
        assert result is None


# ===========================================================================
# Auth-required messages gating
# ===========================================================================

class TestAuthGating:
    """Unauthenticated peers blocked from sending blocks/txs."""

    def test_new_block_requires_auth(self):
        assert "new_block" in _AUTH_REQUIRED_MSGS

    def test_new_tx_requires_auth(self):
        assert "new_tx" in _AUTH_REQUIRED_MSGS

    def test_get_blocks_requires_auth(self):
        assert "get_blocks" in _AUTH_REQUIRED_MSGS

    def test_blocks_requires_auth(self):
        assert "blocks" in _AUTH_REQUIRED_MSGS

    def test_hello_auth_not_in_required(self):
        """hello_auth itself must NOT be in the auth-required set (chicken-and-egg)."""
        assert MSG_HELLO_AUTH not in _AUTH_REQUIRED_MSGS

    def test_auth_response_not_in_required(self):
        assert MSG_AUTH_RESPONSE not in _AUTH_REQUIRED_MSGS

    def test_auth_confirm_not_in_required(self):
        assert MSG_AUTH_CONFIRM not in _AUTH_REQUIRED_MSGS


# ===========================================================================
# Auth rate limiting constants
# ===========================================================================

class TestAuthRateLimitConstants:
    """Verify rate limiting constants are set to safe defaults."""

    def test_max_attempts_is_reasonable(self):
        """At most _AUTH_RATE_MAX attempts per window."""
        assert _AUTH_RATE_MAX == 5

    def test_window_is_60_seconds(self):
        assert _AUTH_RATE_WINDOW == 60

    def test_challenge_length_is_32_bytes(self):
        assert _CHALLENGE_LEN == 32


# ===========================================================================
# Peer state: authenticated flag
# ===========================================================================

class TestPeerAuthState:
    """Unit tests for Peer authentication state transitions."""

    def test_new_peer_is_unauthenticated(self):
        peer = _make_peer()
        assert peer.authenticated is False

    def test_new_peer_challenge_empty(self):
        peer = _make_peer()
        assert peer.challenge == b''

    def test_new_peer_remote_pubkey_empty(self):
        peer = _make_peer()
        assert peer.remote_pubkey == b''

    def test_peer_can_be_marked_authenticated(self):
        peer = _make_peer()
        peer.authenticated = True
        assert peer.authenticated is True

    def test_peer_address_property(self):
        peer = _make_peer("10.0.0.1", 9002)
        assert peer.addr == "10.0.0.1:9002"


# ===========================================================================
# hello_auth field validation (unit-level, no network)
# ===========================================================================

class TestHelloAuthFieldValidation:
    """Validate fields the _handle_hello_auth_inbound method checks."""

    @pytest.mark.asyncio
    async def test_invalid_protocol_version_rejected(self):
        server, _ = _make_p2p_node()
        peer = _make_peer()
        data = {
            "protocol_version": 1,  # must be >= 2
            "timestamp": time.time(),
            "chain_id": CHAIN_ID,
            "challenge": os.urandom(32).hex(),
            "signing_pk": Wallet.generate().signing_pk.hex(),
        }
        result = await server._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_wrong_chain_id_rejected(self):
        server, _ = _make_p2p_node()
        peer = _make_peer()
        data = {
            "protocol_version": 2,
            "timestamp": time.time(),
            "chain_id": "wrong-chain-id",
            "challenge": os.urandom(32).hex(),
            "signing_pk": Wallet.generate().signing_pk.hex(),
        }
        result = await server._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_wrong_challenge_length_rejected(self):
        server, _ = _make_p2p_node()
        peer = _make_peer()
        data = {
            "protocol_version": 2,
            "timestamp": time.time(),
            "chain_id": CHAIN_ID,
            "challenge": "ab" * 16,  # 16 bytes, need 32
            "signing_pk": Wallet.generate().signing_pk.hex(),
        }
        result = await server._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_pubkey_rejected(self):
        server, _ = _make_p2p_node()
        peer = _make_peer()
        data = {
            "protocol_version": 2,
            "timestamp": time.time(),
            "chain_id": CHAIN_ID,
            "challenge": os.urandom(32).hex(),
            "signing_pk": "badpubkey",
        }
        result = await server._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_timestamp_rejected(self):
        server, _ = _make_p2p_node()
        peer = _make_peer()
        data = {
            "protocol_version": 2,
            "timestamp": time.time() - 9999,  # way in the past
            "chain_id": CHAIN_ID,
            "challenge": os.urandom(32).hex(),
            "signing_pk": Wallet.generate().signing_pk.hex(),
        }
        result = await server._handle_hello_auth_inbound(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_valid_hello_auth_accepted(self):
        """A well-formed hello_auth from a valid peer is accepted."""
        server, _ = _make_p2p_node()
        remote = Wallet.generate()
        peer = _make_peer()
        data = {
            "protocol_version": 2,
            "timestamp": time.time(),
            "chain_id": CHAIN_ID,
            "challenge": os.urandom(32).hex(),
            "signing_pk": remote.signing_pk.hex(),
            "node_id": "test-node",
        }
        result = await server._handle_hello_auth_inbound(peer, data)
        assert result is True
        # Counter-challenge stored on peer
        assert len(peer.challenge) == _CHALLENGE_LEN
        assert peer.remote_pubkey == remote.signing_pk


# ===========================================================================
# auth_response: replay detection via challenge clearing
# ===========================================================================

class TestAuthResponseReplayDetection:
    """Auth response handling: single-use challenge cleared after first use."""

    @pytest.mark.asyncio
    async def test_no_pending_challenge_rejected(self):
        server, _ = _make_p2p_node()
        peer = _make_peer()
        peer.challenge = b''  # no pending challenge
        data = {
            "challenge_sig": "ab" * 3309,
            "signing_pk": Wallet.generate().signing_pk.hex(),
            "counter_challenge": os.urandom(32).hex(),
            "protocol_version": 2,
            "node_id": "x",
            "port": 9000,
        }
        result = await server._handle_auth_response(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_auth_deadline_rejected(self):
        server, _ = _make_p2p_node()
        peer = _make_peer()
        peer.challenge = os.urandom(32)
        peer.auth_deadline = time.monotonic() - 1.0  # already expired
        data = {
            "challenge_sig": "ab" * 3309,
            "signing_pk": Wallet.generate().signing_pk.hex(),
            "counter_challenge": os.urandom(32).hex(),
            "protocol_version": 2,
            "node_id": "x",
            "port": 9000,
        }
        result = await server._handle_auth_response(peer, data)
        assert result is False
        # Challenge cleared on timeout
        assert peer.challenge == b''

    @pytest.mark.asyncio
    async def test_bad_signature_rejected(self):
        server, _ = _make_p2p_node()
        remote = Wallet.generate()
        peer = _make_peer()
        peer.challenge = os.urandom(32)
        peer.auth_deadline = time.monotonic() + 30.0
        # Use wrong signature bytes
        data = {
            "challenge_sig": "ff" * 3309,  # wrong signature
            "signing_pk": remote.signing_pk.hex(),
            "counter_challenge": os.urandom(32).hex(),
            "protocol_version": 2,
            "node_id": "x",
            "port": 9000,
        }
        result = await server._handle_auth_response(peer, data)
        assert result is False

    @pytest.mark.asyncio
    async def test_challenge_cleared_after_bad_response(self):
        """Challenge is consumed even on failure (prevents retry with same challenge)."""
        server, _ = _make_p2p_node()
        remote = Wallet.generate()
        peer = _make_peer()
        peer.challenge = os.urandom(32)
        peer.auth_deadline = time.monotonic() + 30.0
        data = {
            "challenge_sig": "ff" * 3309,
            "signing_pk": remote.signing_pk.hex(),
            "counter_challenge": os.urandom(32).hex(),
            "protocol_version": 2,
            "node_id": "x",
            "port": 9000,
        }
        await server._handle_auth_response(peer, data)
        # Must be cleared regardless of outcome
        assert peer.challenge == b''

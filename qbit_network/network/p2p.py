"""P2P networking layer using asyncio TCP. Supports JSON (v3) and msgpack (v4+) wire formats."""
import asyncio
import collections
import hashlib
import ipaddress
import json
import logging
import os
import time
from ..config import (MAX_PEERS, PROTOCOL_VERSION, CHAIN_ID, MAX_BLOCK_DRIFT,
                      P2P_RATE_LIMIT, P2P_RATE_BURST, P2P_RATE_VIOLATIONS_MAX)
from ..crypto.mldsa import MLDSA
from ..crypto.mlkem import MLKEM, MLKEM_PK_SIZE, MLKEM_CT_SIZE
from ..crypto.aes import aes_encrypt, aes_decrypt
from .rate_limiter import RateLimiter
from .reputation import PeerReputation
from .codec import MessageCodec

logger = logging.getLogger("qbit_network.p2p")

MSG_HELLO = "hello"
MSG_HELLO_AUTH = "hello_auth"
MSG_AUTH_RESPONSE = "auth_response"
MSG_AUTH_CONFIRM = "auth_confirm"
MSG_SESSION_KEY = "session_key"
MSG_ENCRYPTED = "encrypted"
MSG_NEW_BLOCK = "new_block"
MSG_NEW_TX = "new_tx"
MSG_GET_BLOCKS = "get_blocks"
MSG_BLOCKS = "blocks"
MSG_GET_PEERS = "get_peers"
MSG_PEERS = "peers"
MSG_STATUS = "status"

# Sync protocol messages (v0.10.0)
MSG_GET_CHAIN_INFO = "get_chain_info"
MSG_CHAIN_INFO = "chain_info"
MSG_GET_HEADERS = "get_headers"
MSG_HEADERS = "headers"
MSG_GET_BLOCK_BODIES = "get_block_bodies"
MSG_BLOCK_BODIES = "block_bodies"

_AUTH_DOMAIN = b"QBIT_AUTH_v2:"
_AUTH_TIMEOUT = 10.0   # seconds to complete authentication handshake
_CHALLENGE_LEN = 32    # bytes

_READER_LIMIT = 10 * 1024 * 1024  # 10 MB

_BLOCKED_PORTS = {22, 23, 25, 53, 80, 443, 445, 3306, 5432, 6379, 8080, 8443}

# Auth handshake rate limiting per IP
_AUTH_RATE_MAX = 5       # max auth attempts per IP
_AUTH_RATE_WINDOW = 60   # seconds
_AUTH_ATTEMPTS_CAP = 10_000  # max tracked IPs (R30-005 LRU eviction)

# Message types exempt from rate limiting (handshake/auth messages)
_RATE_EXEMPT = {MSG_HELLO, MSG_HELLO_AUTH, MSG_AUTH_RESPONSE, MSG_AUTH_CONFIRM,
                MSG_SESSION_KEY, MSG_ENCRYPTED}

# Message types that require authentication (for protocol_version >= 2)
_AUTH_REQUIRED_MSGS = frozenset({
    MSG_NEW_BLOCK, MSG_NEW_TX, MSG_GET_BLOCKS, MSG_BLOCKS,
    MSG_GET_CHAIN_INFO, MSG_CHAIN_INFO,
    MSG_GET_HEADERS, MSG_HEADERS,
    MSG_GET_BLOCK_BODIES, MSG_BLOCK_BODIES,
})


def _is_safe_peer(host: str, port: int, own_host: str, own_port: int) -> bool:
    if port <= 0 or port > 65535:
        return False
    if port in _BLOCKED_PORTS:
        return False
    if host in ("127.0.0.1", "localhost", "::1", own_host) and port == own_port:
        return False
    if not _is_safe_inbound_ip(host):
        return False
    return True


def _is_safe_inbound_ip(host: str) -> bool:
    """Check if an IP address is safe for connection (inbound or outbound).

    Validates IP-level safety independent of port, used for inbound
    connections where the remote listening port is not yet known.
    """
    from ..config import ALLOW_PRIVATE_PEERS
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_link_local or addr.is_reserved:
            return False
        if not ALLOW_PRIVATE_PEERS and (addr.is_private or addr.is_loopback):
            return False
    except ValueError:
        lower = host.lower()
        if lower in ("localhost", "metadata.google.internal", "instance-data"):
            return False
    return True


def _build_auth_message(challenge: bytes, address: str) -> bytes:
    """Build the message to be signed: domain || challenge || address.

    Domain separation via _AUTH_DOMAIN prevents cross-protocol signature reuse.
    Including the signer's address binds the signature to a specific identity.
    """
    return _AUTH_DOMAIN + challenge + address.encode("utf-8")


def _derive_session_key(shared_secret: bytes) -> bytes:
    """Derive a 32-byte AES-256 key from ML-KEM shared secret via SHA3-256."""
    return hashlib.sha3_256(shared_secret).digest()


class Peer:
    __slots__ = ('host', 'port', 'reader', 'writer', 'node_id',
                 'connected', 'last_seen', 'height',
                 'authenticated', 'protocol_version',
                 'challenge', 'remote_pubkey', 'auth_deadline',
                 'is_validator',
                 'session_key', 'encrypted', 'encryption_pk',
                 'wire_format', '_pending_wire_format', '_codec',
                 'is_initiator', 'remote_address')

    def __init__(self, host: str, port: int, reader=None, writer=None):
        self.host = host
        self.port = port
        self.reader = reader
        self.writer = writer
        self.node_id = ""
        self.connected = False
        self.last_seen = time.time()
        self.height = -1
        self.authenticated = False
        self.protocol_version = 1
        self.challenge: bytes = b''       # pending challenge we sent
        self.remote_pubkey: bytes = b''   # authenticated peer's ML-DSA pubkey
        self.auth_deadline: float = 0.0   # deadline for completing auth
        self.is_validator: bool = False   # True if peer is a registered validator
        # Encrypted channel fields
        self.session_key: bytes | None = None   # 32-byte AES-256 key
        self.encrypted: bool = False            # whether transport is encrypted
        self.encryption_pk: bytes | None = None # peer's ML-KEM public key
        # Wire format negotiation (v4+ binary protocol)
        self.wire_format: str = "json"         # "json" or "msgpack" after negotiation
        self._pending_wire_format: str = "json"  # remote peer's preference (stored during hello_auth)
        self._codec = None                        # cached MessageCodec for msgpack mode
        # Connection dedup fields
        self.is_initiator: bool = False         # True if we initiated the connection
        self.remote_address: str = ""           # peer's validator address (derived from pubkey)

    @property
    def addr(self) -> str:
        return f"{self.host}:{self.port}"

    async def send(self, msg_type: str, data: dict):
        if not self.writer or self.writer.is_closing():
            return
        try:
            if self.wire_format == "msgpack":
                if not hasattr(self, '_codec') or self._codec is None:
                    self._codec = MessageCodec("msgpack")
                raw = self._codec.encode(msg_type, data)
                self.writer.write(raw)
            else:
                line = json.dumps({"type": msg_type, "data": data}) + "\n"
                self.writer.write(line.encode())
            await self.writer.drain()
        except Exception:
            self.connected = False

    async def send_encrypted(self, msg_type: str, data: dict):
        """Send a message, encrypting it if the channel is established."""
        if self.encrypted and self.session_key:
            # Inner message is always JSON-encoded before encryption
            inner = json.dumps({"type": msg_type, "data": data}).encode()
            ct = aes_encrypt(self.session_key, inner)
            await self.send(MSG_ENCRYPTED, {"data": ct.hex()})
        else:
            await self.send(msg_type, data)

    async def close(self):
        if self.writer and not self.writer.is_closing():
            self.writer.close()
        self.connected = False


class P2PNode:
    """Async TCP P2P node."""

    def __init__(self, host: str = "0.0.0.0", port: int = 9000, node_id: str = "",
                 signing_sk: bytes = b'', signing_pk: bytes = b'',
                 validator_address: str = "",
                 encryption_sk: bytes = b'', encryption_pk: bytes = b''):
        self.host = host
        self.port = port
        self.node_id = node_id
        self.signing_sk = signing_sk  # ML-DSA-65 sk for auth handshake
        self.signing_pk = signing_pk  # ML-DSA-65 pk for auth handshake
        self.validator_address = validator_address
        self.encryption_sk = encryption_sk  # ML-KEM-768 sk for encrypted channel
        self.encryption_pk = encryption_pk  # ML-KEM-768 pk for encrypted channel
        self.peers: dict[str, Peer] = {}
        self._handlers: dict[str, object] = {}
        self._server = None
        self._pending_requests: dict[str, float] = {}
        self._validators: dict[str, bytes] = {}  # address -> pk for auth verification
        self._rate_limiter = RateLimiter(P2P_RATE_LIMIT, P2P_RATE_BURST)
        self._auth_attempts: collections.OrderedDict[str, list[float]] = collections.OrderedDict()  # IP -> timestamps
        self.reputation = PeerReputation()
        self._cleanup_task = None

    def _has_signing_keys(self) -> bool:
        """Return True if this node has ML-DSA keys for authentication."""
        return bool(self.signing_sk and self.signing_pk and self.validator_address)

    def _has_encryption_keys(self) -> bool:
        """Return True if this node has ML-KEM keys for encrypted channels."""
        return bool(self.encryption_sk and self.encryption_pk)

    def on(self, msg_type: str, handler):
        self._handlers[msg_type] = handler

    async def start(self):
        self._server = await asyncio.start_server(
            self._on_connect, self.host, self.port, limit=_READER_LIMIT)
        self._cleanup_task = asyncio.create_task(self._rate_cleanup_loop())
        logger.info(f"P2P listening on {self.host}:{self.port}")

    async def stop(self):
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        for peer in list(self.peers.values()):
            await peer.close()
        self.peers.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        # Zero secret key material on shutdown (R15-002)
        if hasattr(self, 'signing_sk') and hasattr(self.signing_sk, 'zero'):
            self.signing_sk.zero()
        if hasattr(self, 'encryption_sk') and hasattr(self.encryption_sk, 'zero'):
            self.encryption_sk.zero()

    # ================================================================
    # Outbound connections
    # ================================================================

    async def connect(self, host: str, port: int):
        if len(self.peers) >= MAX_PEERS:
            return
        addr = f"{host}:{port}"
        if addr in self.peers:
            return
        if not _is_safe_peer(host, port, self.host, self.port):
            logger.warning(f"Blocked unsafe peer: {addr}")
            return
        try:
            reader, writer = await asyncio.open_connection(
                host, port, limit=_READER_LIMIT)
            peer = Peer(host, port, reader, writer)
            peer.connected = True
            peer.is_initiator = True
            self.peers[addr] = peer

            if self._has_signing_keys():
                # v2 authenticated handshake: send hello_auth with challenge
                challenge = os.urandom(_CHALLENGE_LEN)
                peer.challenge = challenge
                peer.auth_deadline = time.monotonic() + _AUTH_TIMEOUT
                peer.protocol_version = PROTOCOL_VERSION

                # Part 1: sign proof = Sign(sk, domain || challenge || our_address)
                proof_msg = _build_auth_message(challenge, self.validator_address)
                try:
                    proof_sig = MLDSA.sign(self.signing_sk, proof_msg)
                except RuntimeError:
                    logger.error(f"Failed to sign auth proof for {addr}")
                    peer.connected = False
                    self.peers.pop(addr, None)
                    writer.close()
                    return

                hello_data = {
                    "protocol_version": PROTOCOL_VERSION,
                    "wire_format": MessageCodec.preferred_wire_format(),
                    "node_id": self.node_id,
                    "port": self.port,
                    "chain_id": CHAIN_ID,
                    "challenge": challenge.hex(),
                    "timestamp": int(time.time()),
                    "signing_pk": self.signing_pk.hex(),
                    "proof": proof_sig.hex(),
                }
                # Include encryption_pk if we have one
                if self._has_encryption_keys():
                    hello_data["encryption_pk"] = self.encryption_pk.hex()

                await peer.send(MSG_HELLO_AUTH, hello_data)
                logger.debug(f"Sent hello_auth to {addr}")
            else:
                # v1 fallback: plain hello, no authentication
                await peer.send(MSG_HELLO, {"node_id": self.node_id, "port": self.port})

            asyncio.create_task(self._read_loop(peer))
            logger.info(f"Connected to {addr}")
        except Exception as e:
            logger.debug(f"Failed to connect to {addr}: {e}")

    async def broadcast(self, msg_type: str, data: dict, exclude: str = ""):
        tasks = []
        for addr, peer in list(self.peers.items()):
            if addr != exclude and peer.connected:
                tasks.append(peer.send_encrypted(msg_type, data))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def peer_count(self) -> int:
        return sum(1 for p in self.peers.values() if p.connected)

    def best_peer_height(self) -> int:
        heights = [p.height for p in self.peers.values() if p.connected and p.height >= 0]
        return max(heights) if heights else -1

    # ================================================================
    # Authentication helpers
    # ================================================================

    def _resolve_peer_pubkey(self, pk_hex: str) -> bytes | None:
        """Validate and resolve a peer's signing pubkey from hex.

        Returns the pubkey bytes if valid, None otherwise.
        ML-DSA-65 public keys are 1952 bytes.
        """
        if not isinstance(pk_hex, str):
            return None
        try:
            pk_bytes = bytes.fromhex(pk_hex)
        except (ValueError, TypeError):
            return None
        if len(pk_bytes) != 1952:
            return None
        return pk_bytes

    def _resolve_encryption_pk(self, pk_hex: str) -> bytes | None:
        """Validate and resolve a peer's ML-KEM public key from hex.

        Returns the pubkey bytes if valid, None otherwise.
        ML-KEM-768 public keys are 1184 bytes.
        """
        if not isinstance(pk_hex, str) or not pk_hex:
            return None
        try:
            pk_bytes = bytes.fromhex(pk_hex)
        except (ValueError, TypeError):
            return None
        if len(pk_bytes) != MLKEM_PK_SIZE:
            return None
        return pk_bytes

    async def _handle_hello_auth_inbound(self, peer: Peer, data: dict) -> bool:
        """Handle an inbound hello_auth message (we are the responder).

        The initiator sent us their challenge, pubkey, and proof. We:
        1. Validate all fields (version, timestamp, chain_id, challenge, pubkey)
        2. Verify the initiator's proof signature BEFORE signing anything
        3. Sign their challenge with our key
        4. Generate our own counter-challenge
        5. Send auth_response back

        Returns True if we sent a valid auth_response, False on error.
        """
        if not self._has_signing_keys():
            logger.debug(f"Received hello_auth from {peer.addr} but we have no signing keys")
            return False

        # --- Field validation ---

        pv = data.get("protocol_version", 1)
        if not isinstance(pv, int) or pv < 2:
            logger.debug(f"Peer {peer.addr} hello_auth: invalid protocol_version")
            return False

        ts = data.get("timestamp", 0)
        if not isinstance(ts, (int, float)):
            return False
        if abs(time.time() - ts) > MAX_BLOCK_DRIFT:
            logger.debug(f"Peer {peer.addr} hello_auth: timestamp out of range")
            return False

        chain_id = data.get("chain_id", "")
        if chain_id != CHAIN_ID:
            logger.debug(f"Peer {peer.addr} hello_auth: wrong chain_id")
            return False

        challenge_hex = data.get("challenge", "")
        if not isinstance(challenge_hex, str):
            return False
        try:
            peer_challenge = bytes.fromhex(challenge_hex)
        except (ValueError, TypeError):
            return False
        if len(peer_challenge) != _CHALLENGE_LEN:
            return False

        peer_pk = self._resolve_peer_pubkey(data.get("signing_pk", ""))
        if peer_pk is None:
            logger.debug(f"Peer {peer.addr} hello_auth: invalid signing_pk")
            return False

        # --- Part 1: Verify-Before-Sign ---
        # Verify the initiator's proof BEFORE we sign anything.
        # The proof is: Sign(initiator_sk, domain || challenge || initiator_address)
        proof_hex = data.get("proof", "")
        if not isinstance(proof_hex, str) or not proof_hex:
            logger.debug(f"Peer {peer.addr} hello_auth: missing proof field")
            return False
        try:
            proof_bytes = bytes.fromhex(proof_hex)
        except (ValueError, TypeError):
            logger.debug(f"Peer {peer.addr} hello_auth: invalid proof hex")
            return False

        from ..core.wallet import Wallet
        peer_address = Wallet.derive_address(peer_pk)
        proof_msg = _build_auth_message(peer_challenge, peer_address)
        if not MLDSA.verify(peer_pk, proof_msg, proof_bytes):
            logger.warning(f"Peer {peer.addr} hello_auth: invalid proof signature")
            return False

        # Store peer's encryption_pk if provided (for encrypted channel)
        peer_enc_pk = self._resolve_encryption_pk(data.get("encryption_pk", ""))
        if peer_enc_pk is not None:
            peer.encryption_pk = peer_enc_pk

        # --- Accept handshake ---

        node_id = data.get("node_id", "")
        if isinstance(node_id, str):
            peer.node_id = node_id
        peer.protocol_version = min(pv, PROTOCOL_VERSION)
        # Store initiator's wire_format preference for later negotiation
        peer._pending_wire_format = data.get("wire_format", "json")

        # Sign their challenge: Sign(sk, domain || peer_challenge || our_address)
        auth_msg = _build_auth_message(peer_challenge, self.validator_address)
        try:
            challenge_sig = MLDSA.sign(self.signing_sk, auth_msg)
        except RuntimeError:
            logger.error(f"Failed to sign auth challenge for {peer.addr}")
            return False

        # Generate counter-challenge for the initiator to sign back
        counter_challenge = os.urandom(_CHALLENGE_LEN)
        peer.challenge = counter_challenge
        peer.auth_deadline = time.monotonic() + _AUTH_TIMEOUT
        peer.remote_pubkey = peer_pk
        peer.remote_address = peer_address

        response_data = {
            "protocol_version": PROTOCOL_VERSION,
            "wire_format": MessageCodec.preferred_wire_format(),
            "node_id": self.node_id,
            "port": self.port,
            "challenge_sig": challenge_sig.hex(),
            "counter_challenge": counter_challenge.hex(),
            "signing_pk": self.signing_pk.hex(),
        }
        # Include encryption_pk if we have one
        if self._has_encryption_keys():
            response_data["encryption_pk"] = self.encryption_pk.hex()

        await peer.send(MSG_AUTH_RESPONSE, response_data)
        logger.debug(f"Sent auth_response to {peer.addr}")
        return True

    async def _handle_auth_response(self, peer: Peer, data: dict) -> bool:
        """Handle auth_response (we are the initiator, peer is responder).

        The responder signed our challenge and sent a counter-challenge.
        We verify their signature, sign their counter-challenge, and send auth_confirm.
        """
        if not peer.challenge:
            logger.debug(f"auth_response from {peer.addr}: no pending challenge")
            return False

        if time.monotonic() > peer.auth_deadline:
            logger.warning(f"Auth timeout for {peer.addr}")
            peer.challenge = b''
            return False

        # Validate responder pubkey
        responder_pk = self._resolve_peer_pubkey(data.get("signing_pk", ""))
        if responder_pk is None:
            logger.debug(f"auth_response from {peer.addr}: invalid signing_pk")
            peer.challenge = b''
            return False

        # Validate signature
        sig_hex = data.get("challenge_sig", "")
        if not isinstance(sig_hex, str):
            peer.challenge = b''
            return False
        try:
            challenge_sig = bytes.fromhex(sig_hex)
        except (ValueError, TypeError):
            peer.challenge = b''
            return False

        # Derive responder's address to build expected signed message
        from ..core.wallet import Wallet
        responder_address = Wallet.derive_address(responder_pk)
        expected_msg = _build_auth_message(peer.challenge, responder_address)

        # Single-use: consume challenge before verification
        peer.challenge = b''

        if not MLDSA.verify(responder_pk, expected_msg, challenge_sig):
            logger.warning(f"Auth failed: bad challenge signature from {peer.addr}")
            return False

        # Validate counter-challenge
        counter_hex = data.get("counter_challenge", "")
        if not isinstance(counter_hex, str):
            return False
        try:
            counter_challenge = bytes.fromhex(counter_hex)
        except (ValueError, TypeError):
            return False
        if len(counter_challenge) != _CHALLENGE_LEN:
            return False

        # Sign their counter-challenge
        auth_msg = _build_auth_message(counter_challenge, self.validator_address)
        try:
            counter_sig = MLDSA.sign(self.signing_sk, auth_msg)
        except RuntimeError:
            logger.error(f"Failed to sign counter-challenge for {peer.addr}")
            return False

        await peer.send(MSG_AUTH_CONFIRM, {
            "challenge_sig": counter_sig.hex(),
        })

        # Mutual authentication complete (initiator side)
        peer.authenticated = True
        peer.remote_pubkey = responder_pk
        peer.remote_address = responder_address
        peer.auth_deadline = 0.0
        peer.protocol_version = min(
            data.get("protocol_version", 1), PROTOCOL_VERSION)

        # Store remote wire_format preference (applied after channel setup)
        remote_wire = data.get("wire_format", "json")

        node_id = data.get("node_id", "")
        if isinstance(node_id, str):
            peer.node_id = node_id
        new_port = data.get("port", 0)
        if isinstance(new_port, int) and 0 < new_port <= 65535:
            peer.port = new_port

        # Store peer's encryption_pk if provided
        peer_enc_pk = self._resolve_encryption_pk(data.get("encryption_pk", ""))
        if peer_enc_pk is not None:
            peer.encryption_pk = peer_enc_pk

        logger.info(f"Peer {peer.addr} authenticated (we initiated)")
        self._check_validator_status(peer)

        # Connection dedup check
        if await self._dedup_connection(peer):
            return False  # this connection was closed as duplicate

        # Initiator initiates encrypted channel if both sides have encryption keys
        # (session_key sent in JSON before wire format switch)
        await self._initiate_encrypted_channel(peer)

        # Negotiate wire format AFTER channel setup (v4+ binary protocol)
        peer.wire_format = MessageCodec.negotiate(
            peer.protocol_version, peer.protocol_version,
            MessageCodec.preferred_wire_format(), remote_wire)
        if peer.wire_format == "msgpack":
            logger.info(f"Peer {peer.addr}: negotiated msgpack wire format")

        return True

    async def _handle_auth_confirm(self, peer: Peer, data: dict) -> bool:
        """Handle auth_confirm (we are the responder, peer is initiator).

        The initiator signed our counter-challenge. Verify and mark authenticated.
        """
        if not peer.challenge:
            logger.debug(f"auth_confirm from {peer.addr}: no pending challenge")
            return False

        if not peer.remote_pubkey:
            logger.debug(f"auth_confirm from {peer.addr}: no remote_pubkey stored")
            peer.challenge = b''
            return False

        if time.monotonic() > peer.auth_deadline:
            logger.warning(f"Auth timeout for {peer.addr}")
            peer.challenge = b''
            return False

        # Validate signature
        sig_hex = data.get("challenge_sig", "")
        if not isinstance(sig_hex, str):
            peer.challenge = b''
            return False
        try:
            challenge_sig = bytes.fromhex(sig_hex)
        except (ValueError, TypeError):
            peer.challenge = b''
            return False

        # Derive initiator's address from their stored pubkey
        from ..core.wallet import Wallet
        initiator_address = Wallet.derive_address(peer.remote_pubkey)
        expected_msg = _build_auth_message(peer.challenge, initiator_address)

        # Single-use: consume counter-challenge before verification
        peer.challenge = b''

        if not MLDSA.verify(peer.remote_pubkey, expected_msg, challenge_sig):
            logger.warning(f"Auth failed: bad auth_confirm signature from {peer.addr}")
            peer.remote_pubkey = b''
            return False

        # Mutual authentication complete (responder side)
        peer.authenticated = True
        peer.auth_deadline = 0.0
        peer.remote_address = initiator_address

        # Wire format was stored during hello_auth processing
        # (peer._pending_wire_format set in _handle_hello_auth_inbound)
        remote_wire = peer._pending_wire_format
        peer.wire_format = MessageCodec.negotiate(
            peer.protocol_version, peer.protocol_version,
            MessageCodec.preferred_wire_format(), remote_wire)
        if peer.wire_format == "msgpack":
            logger.info(f"Peer {peer.addr}: negotiated msgpack wire format")

        logger.info(f"Peer {peer.addr} authenticated (they initiated)")
        self._check_validator_status(peer)

        # Connection dedup check
        if await self._dedup_connection(peer):
            return False  # this connection was closed as duplicate

        return True

    def _check_auth_gate(self, msg_type: str, peer: Peer) -> bool:
        """Check if a message is allowed from this peer.

        For protocol_version >= 2 peers, block/tx messages require authentication.
        v1 peers are exempt for backwards compatibility.
        Unauthenticated v2 peers are NEVER allowed to send block/tx messages,
        even during the auth handshake grace period.
        """
        if msg_type not in _AUTH_REQUIRED_MSGS:
            return True
        if peer.protocol_version < 2:
            return True
        if peer.authenticated:
            return True
        logger.debug(f"Rejected {msg_type} from unauthenticated v2 peer {peer.addr}")
        return False

    def _check_validator_status(self, peer: Peer):
        """After successful auth, check if the peer is a registered validator.

        Derives the peer's address from their pubkey and checks against the
        validator registry and consensus validators. Sets peer.is_validator.
        """
        if not peer.remote_pubkey:
            return
        from ..core.wallet import Wallet
        peer_address = Wallet.derive_address(peer.remote_pubkey)
        if peer_address in self._validators:
            peer.is_validator = True
            logger.info(f"Authenticated peer {peer.addr} is a registered validator: "
                        f"{peer_address[:16]}...")
        else:
            peer.is_validator = False
            logger.warning(f"Authenticated peer {peer.addr} ({peer_address[:16]}...) "
                           f"is NOT a registered validator")

    # ================================================================
    # Connection deduplication
    # ================================================================

    async def _dedup_connection(self, peer: Peer) -> bool:
        """Check for duplicate connections to the same remote address.

        After successful authentication, if another connection to the same
        remote_address already exists, use a deterministic tie-breaker:
        the node with the lexicographically smaller address keeps the
        connection it initiated.

        Returns True if THIS connection should be closed (duplicate).
        """
        if not peer.remote_address or not self.validator_address:
            return False

        # Find any other peer with the same remote_address
        for other_key, other_peer in list(self.peers.items()):
            if other_peer is peer:
                continue
            if not other_peer.authenticated:
                continue
            if other_peer.remote_address != peer.remote_address:
                continue

            # Duplicate found. Deterministic tie-breaker:
            # The node with the smaller address keeps the connection IT initiated.
            we_are_smaller = self.validator_address < peer.remote_address

            if we_are_smaller:
                # We keep the connection we initiated; close the one we didn't
                if peer.is_initiator:
                    # Keep this one (we initiated), close the other
                    logger.info(f"Dedup: closing duplicate connection {other_key} "
                                f"to {peer.remote_address[:16]}... "
                                f"(keeping our outbound)")
                    other_peer.connected = False
                    self.peers.pop(other_key, None)
                    await other_peer.close()
                    return False
                else:
                    # Close this one (they initiated), keep the other
                    logger.info(f"Dedup: closing this inbound connection to "
                                f"{peer.remote_address[:16]}... "
                                f"(keeping our outbound)")
                    return True
            else:
                # They have the smaller address; they keep their initiated conn.
                # We close the connection we initiated.
                if peer.is_initiator:
                    # Close this one (we initiated), keep their inbound
                    logger.info(f"Dedup: closing our outbound connection to "
                                f"{peer.remote_address[:16]}... "
                                f"(peer has smaller address)")
                    return True
                else:
                    # Keep this one (they initiated), close the other
                    logger.info(f"Dedup: closing duplicate connection {other_key} "
                                f"to {peer.remote_address[:16]}... "
                                f"(peer has smaller address, keeping their outbound)")
                    other_peer.connected = False
                    self.peers.pop(other_key, None)
                    await other_peer.close()
                    return False

        return False

    # ================================================================
    # Encrypted channel
    # ================================================================

    async def _initiate_encrypted_channel(self, peer: Peer):
        """Initiator sends session_key message after auth completes.

        Uses ML-KEM to encapsulate a shared secret with the responder's
        encryption_pk, then derives an AES-256 session key.
        """
        if not self._has_encryption_keys():
            return
        if peer.encryption_pk is None:
            return

        try:
            ct, shared_secret = MLKEM.encapsulate(peer.encryption_pk)
        except (ValueError, RuntimeError) as e:
            logger.warning(f"Failed to encapsulate session key for {peer.addr}: {e}")
            return

        session_key = _derive_session_key(shared_secret)
        peer.session_key = session_key

        await peer.send(MSG_SESSION_KEY, {
            "ciphertext": ct.hex(),
            "encryption_pk": self.encryption_pk.hex(),
        })
        # Mark encrypted only after sending (responder needs to process first)
        peer.encrypted = True
        logger.info(f"Encrypted channel established with {peer.addr} (initiator)")

    async def _handle_session_key(self, peer: Peer, data: dict) -> bool:
        """Handle session_key message (we are the responder).

        Decapsulate the shared secret and derive the AES-256 session key.
        """
        if not self._has_encryption_keys():
            logger.debug(f"Received session_key from {peer.addr} but no encryption keys")
            return False

        if not peer.authenticated:
            logger.debug(f"Received session_key from unauthenticated peer {peer.addr}")
            return False

        ct_hex = data.get("ciphertext", "")
        if not isinstance(ct_hex, str):
            return False
        try:
            ct = bytes.fromhex(ct_hex)
        except (ValueError, TypeError):
            return False
        if len(ct) != MLKEM_CT_SIZE:
            logger.debug(f"session_key from {peer.addr}: invalid ciphertext length")
            return False

        # Store initiator's encryption_pk if provided
        enc_pk = self._resolve_encryption_pk(data.get("encryption_pk", ""))
        if enc_pk is not None:
            peer.encryption_pk = enc_pk

        try:
            shared_secret = MLKEM.decapsulate(self.encryption_sk, ct)
        except (ValueError, RuntimeError) as e:
            logger.warning(f"Failed to decapsulate session key from {peer.addr}: {e}")
            return False

        session_key = _derive_session_key(shared_secret)
        peer.session_key = session_key
        peer.encrypted = True
        logger.info(f"Encrypted channel established with {peer.addr} (responder)")
        return True

    def _decrypt_message(self, peer: Peer, data: dict):
        """Decrypt an encrypted message and return (msg_type, inner_data) or None."""
        if not peer.session_key:
            logger.debug(f"Encrypted msg from {peer.addr} but no session key")
            return None

        ct_hex = data.get("data", "")
        if not isinstance(ct_hex, str):
            return None
        try:
            ct = bytes.fromhex(ct_hex)
        except (ValueError, TypeError):
            return None

        try:
            plaintext = aes_decrypt(peer.session_key, ct)
        except Exception:
            logger.warning(f"Failed to decrypt message from {peer.addr}")
            return None

        try:
            inner = json.loads(plaintext.decode())
            return inner.get("type", ""), inner.get("data", {})
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    # ================================================================
    # Rate limiting
    # ================================================================

    def _check_auth_rate(self, host: str) -> bool:
        """Check per-IP auth handshake rate limit.

        Returns True if the auth attempt is allowed, False to reject.
        Max _AUTH_RATE_MAX attempts per _AUTH_RATE_WINDOW seconds per IP.
        """
        if self._is_localhost(host):
            return True
        now = time.monotonic()
        attempts = self._auth_attempts.get(host, [])
        # Prune old attempts outside the window
        attempts = [t for t in attempts if now - t < _AUTH_RATE_WINDOW]
        if len(attempts) >= _AUTH_RATE_MAX:
            logger.warning(f"Auth rate limit exceeded for {host}")
            return False
        attempts.append(now)
        self._auth_attempts[host] = attempts
        # Move to end for LRU ordering
        self._auth_attempts.move_to_end(host)
        # Evict oldest entries if cap exceeded (R30-005)
        while len(self._auth_attempts) > _AUTH_ATTEMPTS_CAP:
            self._auth_attempts.popitem(last=False)
        return True

    @staticmethod
    def _is_localhost(host: str) -> bool:
        return host in ("127.0.0.1", "::1", "localhost")

    def _check_rate_limit(self, peer: Peer, msg_type: str) -> bool:
        """Check per-peer rate limit. Returns True if allowed, False to drop/disconnect."""
        if msg_type in _RATE_EXEMPT:
            return True
        if self._is_localhost(peer.host):
            return True
        return self._rate_limiter.check(peer.host)

    def _should_disconnect_rate(self, peer: Peer) -> bool:
        """Return True if peer should be disconnected for rate violations."""
        if self._rate_limiter.violations(peer.host) >= P2P_RATE_VIOLATIONS_MAX:
            self.reputation.record(peer.addr, "rate_limited")
            return True
        return False

    def _check_reputation(self, peer: Peer) -> bool:
        """Return True if peer is allowed (not banned). False = should disconnect."""
        return not self.reputation.is_banned(peer.addr)

    async def _rate_cleanup_loop(self):
        """Periodically remove stale rate-limiter entries."""
        try:
            while True:
                await asyncio.sleep(60)
                removed = self._rate_limiter.cleanup()
                if removed:
                    logger.debug(f"Rate limiter cleanup: removed {removed} stale entries")
                # Cleanup stale _auth_attempts entries (R14-004)
                cutoff = time.monotonic() - _AUTH_RATE_WINDOW
                stale = [k for k, ts_list in self._auth_attempts.items()
                         if not ts_list or ts_list[-1] < cutoff]
                for k in stale:
                    del self._auth_attempts[k]
                if stale:
                    logger.debug(f"Auth attempts cleanup: removed {len(stale)} stale entries")
                # Decay peer reputation scores
                self.reputation.decay()
        except asyncio.CancelledError:
            pass

    # ================================================================
    # Inbound connections
    # ================================================================

    def _update_peer_key(self, peer: Peer, peer_key: str, temp_key: str,
                         new_port: int) -> str:
        """Update peer port and re-key in self.peers. Returns new peer_key."""
        if not (isinstance(new_port, int) and 0 < new_port <= 65535):
            return peer_key
        self.peers.pop(peer_key, None)
        peer.port = new_port
        real_key = peer.addr
        if real_key not in self.peers:
            self.peers[real_key] = peer
            return real_key
        else:
            self.peers[temp_key] = peer
            return temp_key

    async def _on_connect(self, reader, writer):
        if len(self.peers) >= MAX_PEERS:
            writer.close()
            return
        info = writer.get_extra_info('peername')
        host = info[0] if info else "unknown"

        # Reject connections from banned peers (check by host IP)
        if self.reputation.is_banned(host):
            logger.debug(f"Rejected inbound connection from banned peer {host}")
            writer.close()
            return

        # Reject inbound connections from private/reserved IPs (R29-002)
        if not _is_safe_inbound_ip(host):
            logger.debug(f"Rejected inbound connection from unsafe IP {host}")
            writer.close()
            return

        temp_key = f"_inbound_{host}_{id(writer)}"
        peer = Peer(host, 0, reader, writer)
        peer.connected = True
        peer.is_initiator = False
        self.peers[temp_key] = peer
        peer_key = temp_key  # tracks current key in self.peers
        try:
            # Require HELLO or HELLO_AUTH within 10 seconds
            try:
                first_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.debug(f"Inbound {host}: no HELLO within timeout")
                return
            if not first_line:
                return
            msg = self._parse(first_line)
            if msg:
                mt, data = msg

                if mt == MSG_HELLO_AUTH:
                    # Auth rate limiting per IP
                    if not self._check_auth_rate(host):
                        logger.warning(f"Inbound {host}: auth rate limit exceeded, disconnecting")
                        return

                    # v2 authenticated handshake (initiator -> us)
                    new_port = data.get("port", 0)
                    peer_key = self._update_peer_key(peer, peer_key, temp_key, new_port)

                    auth_ok = await self._handle_hello_auth_inbound(peer, data)
                    if not auth_ok:
                        # Peer sent hello_auth but failed validation — disconnect
                        # instead of silently downgrading to v1 (prevents auth bypass)
                        self.reputation.record(peer.addr, "auth_failed")
                        logger.warning(f"Inbound {peer.addr}: hello_auth failed, disconnecting")
                        return

                elif mt == MSG_HELLO:
                    # v1 plain handshake (backwards compatible)
                    peer.node_id = data.get("node_id", "")
                    peer.protocol_version = 1
                    new_port = data.get("port", 0)
                    peer_key = self._update_peer_key(peer, peer_key, temp_key, new_port)
                    await peer.send(MSG_HELLO, {
                        "node_id": self.node_id, "port": self.port})

                await self._dispatch(mt, peer, data)
                peer.last_seen = time.time()

            # Main message loop (supports JSON readline or msgpack length-prefixed)
            while True:
                try:
                    msg = await self._read_message(reader, peer)
                except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.TimeoutError):
                    break
                if not msg:
                    break
                mt, data = msg

                # Rate limiting
                if not self._check_rate_limit(peer, mt):
                    if self._should_disconnect_rate(peer):
                        logger.warning(f"Disconnecting rate-limited peer {peer.addr}")
                        break
                    continue

                # Handle encrypted messages: decrypt and re-dispatch
                if mt == MSG_ENCRYPTED:
                    inner = self._decrypt_message(peer, data)
                    if inner is None:
                        continue
                    mt, data = inner

                if mt == MSG_STATUS:
                    h = data.get("height", -1)
                    if isinstance(h, int) and -1 <= h <= 10_000_000:
                        peer.height = h

                # Handle session_key inline (encrypted channel setup)
                if mt == MSG_SESSION_KEY:
                    await self._handle_session_key(peer, data)
                    peer.last_seen = time.time()
                    continue

                # Handle auth_confirm inline (responder completing handshake)
                if mt == MSG_AUTH_CONFIRM:
                    auth_ok = await self._handle_auth_confirm(peer, data)
                    if not auth_ok:
                        self.reputation.record(peer.addr, "auth_failed")
                        logger.warning(f"Auth confirm failed from {peer.addr}, disconnecting")
                        return
                    peer.last_seen = time.time()
                    continue

                # Reputation ban check (mid-session)
                if not self._check_reputation(peer):
                    logger.warning(f"Disconnecting banned peer {peer.addr}")
                    break

                # Gate block/tx messages on authentication for v2 peers
                if not self._check_auth_gate(mt, peer):
                    continue

                await self._dispatch(mt, peer, data)
                peer.last_seen = time.time()
        except asyncio.LimitOverrunError:
            logger.warning(f"Oversized message from {peer.addr}")
            self.reputation.record(peer.addr, "protocol_error")
        except Exception:
            pass
        finally:
            peer.connected = False
            self.peers.pop(peer_key, None)

    # ================================================================
    # Outbound read loop
    # ================================================================

    async def _read_loop(self, peer: Peer):
        """Read loop for outbound connections."""
        try:
            while True:
                try:
                    msg = await self._read_message(peer.reader, peer)
                except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.TimeoutError):
                    break
                if not msg:
                    break
                mt, data = msg

                # Rate limiting
                if not self._check_rate_limit(peer, mt):
                    if self._should_disconnect_rate(peer):
                        logger.warning(f"Disconnecting rate-limited peer {peer.addr}")
                        break
                    continue

                # Handle encrypted messages: decrypt and re-dispatch
                if mt == MSG_ENCRYPTED:
                    inner = self._decrypt_message(peer, data)
                    if inner is None:
                        continue
                    mt, data = inner

                if mt == MSG_STATUS:
                    h = data.get("height", -1)
                    if isinstance(h, int) and -1 <= h <= 10_000_000:
                        peer.height = h

                # Handle session_key inline (encrypted channel setup)
                if mt == MSG_SESSION_KEY:
                    await self._handle_session_key(peer, data)
                    peer.last_seen = time.time()
                    continue

                # Handle auth_response inline (we sent hello_auth, peer responds)
                if mt == MSG_AUTH_RESPONSE:
                    auth_ok = await self._handle_auth_response(peer, data)
                    if not auth_ok:
                        self.reputation.record(peer.addr, "auth_failed")
                        logger.warning(f"Auth response failed from {peer.addr}, disconnecting")
                        break
                    peer.last_seen = time.time()
                    continue

                # Reputation ban check (mid-session)
                if not self._check_reputation(peer):
                    logger.warning(f"Disconnecting banned peer {peer.addr}")
                    break

                # Gate block/tx messages on authentication for v2 peers
                if not self._check_auth_gate(mt, peer):
                    continue

                await self._dispatch(mt, peer, data)
                peer.last_seen = time.time()
        except asyncio.LimitOverrunError:
            logger.warning(f"Oversized message from {peer.addr}")
            self.reputation.record(peer.addr, "protocol_error")
        except Exception:
            pass
        finally:
            peer.connected = False
            self.peers.pop(peer.addr, None)

    # ================================================================
    # Dispatch and parse
    # ================================================================

    async def _dispatch(self, msg_type: str, peer: Peer, data: dict):
        handler = self._handlers.get(msg_type)
        if handler:
            await handler(peer, data)

    @staticmethod
    def _parse(line: bytes):
        try:
            obj = json.loads(line.decode().strip())
            return obj.get("type", ""), obj.get("data", {})
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    @staticmethod
    async def _read_message(reader, peer) -> tuple[str, dict] | None:
        """Read a message from a peer, handling both JSON and msgpack formats.

        Pre-auth: always JSON (readline).
        Post-auth: uses peer.wire_format (json=readline, msgpack=length-prefixed).
        """
        if getattr(peer, 'wire_format', 'json') == "msgpack":
            # Length-prefixed msgpack: read 4-byte header, then payload
            header = await reader.readexactly(4)
            length = int.from_bytes(header, 'big')
            if length == 0 or length > MessageCodec.MAX_FRAME_SIZE:
                return None
            payload = await reader.readexactly(length)
            codec = MessageCodec("msgpack")
            return codec.decode(payload)
        else:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not line:
                return None
            try:
                obj = json.loads(line.decode().strip())
                return obj.get("type", ""), obj.get("data", {})
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None

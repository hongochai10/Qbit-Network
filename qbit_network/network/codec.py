"""Message codec for P2P wire protocol — JSON and msgpack backends.

Supports two wire formats:
- "json": newline-delimited JSON (v3 legacy, always available)
- "msgpack": length-prefixed msgpack with binary fields (v4+, optional)

Binary fields (signature, pubkeys, challenges) are transmitted as raw bytes
in msgpack mode instead of hex strings, reducing bandwidth ~40-60%.
"""
import json
import struct
import logging

logger = logging.getLogger("qbit_network.codec")

# Fields that should be transmitted as raw bytes in msgpack mode
_BINARY_FIELDS = frozenset({
    "signature", "sender_pubkey", "signing_pk", "encryption_pk",
    "challenge", "challenge_sig", "counter_challenge",
    "ciphertext", "validator_pubkey",
})

# Maximum frame size (10 MB, same as JSON reader limit)
MAX_FRAME_SIZE = 10 * 1024 * 1024

# Try to import msgpack; if unavailable, only JSON mode works
try:
    import msgpack
    HAS_MSGPACK = True
except ImportError:
    msgpack = None
    HAS_MSGPACK = False


def _hex_to_bytes_recursive(obj, depth: int = 0):
    """Convert hex string fields in _BINARY_FIELDS to raw bytes for msgpack encoding."""
    if depth > 10:
        return obj
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k in _BINARY_FIELDS and isinstance(v, str):
                try:
                    result[k] = bytes.fromhex(v)
                except ValueError:
                    result[k] = v  # keep as string if not valid hex
            else:
                result[k] = _hex_to_bytes_recursive(v, depth + 1)
        return result
    if isinstance(obj, list):
        return [_hex_to_bytes_recursive(item, depth + 1) for item in obj]
    return obj


def _bytes_to_hex_recursive(obj, depth: int = 0):
    """Convert raw bytes fields back to hex strings after msgpack decoding."""
    if depth > 10:
        return obj
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k in _BINARY_FIELDS and isinstance(v, (bytes, bytearray)):
                result[k] = v.hex()
            else:
                result[k] = _bytes_to_hex_recursive(v, depth + 1)
        return result
    if isinstance(obj, list):
        return [_bytes_to_hex_recursive(item, depth + 1) for item in obj]
    if isinstance(obj, bytes):
        # Generic bytes that aren't in a known field — try to decode as UTF-8
        try:
            return obj.decode('utf-8')
        except UnicodeDecodeError:
            return obj.hex()
    return obj


class MessageCodec:
    """Encodes/decodes P2P messages in JSON or msgpack format."""

    MAX_FRAME_SIZE = MAX_FRAME_SIZE

    def __init__(self, wire_format: str = "json"):
        if wire_format not in ("json", "msgpack"):
            raise ValueError(f"unknown wire_format: {wire_format}")
        if wire_format == "msgpack" and not HAS_MSGPACK:
            logger.warning("msgpack not available, falling back to json")
            wire_format = "json"
        self.wire_format = wire_format

    def encode(self, msg_type: str, data: dict) -> bytes:
        """Encode a message to wire bytes.

        JSON: returns JSON + newline (UTF-8)
        msgpack: returns 4-byte big-endian length prefix + msgpack payload
        """
        msg = {"type": msg_type, "data": data}

        if self.wire_format == "msgpack":
            # Convert hex string fields to raw bytes for efficiency
            msg = _hex_to_bytes_recursive(msg)
            payload = msgpack.packb(msg, use_bin_type=True)
            if len(payload) > MAX_FRAME_SIZE:
                raise ValueError(f"frame too large: {len(payload)} > {MAX_FRAME_SIZE}")
            return struct.pack(">I", len(payload)) + payload
        else:
            line = json.dumps(msg, separators=(',', ':')) + "\n"
            return line.encode('utf-8')

    def decode(self, raw: bytes) -> tuple[str, dict] | None:
        """Decode a message from wire bytes.

        For JSON mode: raw is a single JSON line (without length prefix).
        For msgpack mode: raw is the msgpack payload (without length prefix).

        Returns (msg_type, data) or None on error.
        """
        try:
            if self.wire_format == "msgpack":
                obj = msgpack.unpackb(raw, raw=False)
                obj = _bytes_to_hex_recursive(obj)
            else:
                obj = json.loads(raw.decode('utf-8').strip())
            return obj.get("type", ""), obj.get("data", {})
        except Exception as e:
            logger.debug(f"decode error ({self.wire_format}): {e}")
            return None

    @staticmethod
    def negotiate(local_version: int, remote_version: int,
                  local_wire: str, remote_wire: str) -> str:
        """Negotiate wire format between two peers.

        Both must be protocol v4+ and support msgpack to use binary mode.
        """
        if (local_version >= 4 and remote_version >= 4
                and local_wire == "msgpack" and remote_wire == "msgpack"
                and HAS_MSGPACK):
            return "msgpack"
        return "json"

    @staticmethod
    def preferred_wire_format() -> str:
        """Return the preferred wire format for this node."""
        return "msgpack" if HAS_MSGPACK else "json"

"""Tests for qbit_network.network.codec — binary P2P wire protocol codec.

Covers MessageCodec JSON/msgpack modes, hex-bytes recursive converters,
protocol negotiation, Peer wire_format integration, and cross-codec roundtrips.
"""
import json
import struct
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from qbit_network.network.codec import (
    MessageCodec,
    _hex_to_bytes_recursive,
    _bytes_to_hex_recursive,
    _BINARY_FIELDS,
    HAS_MSGPACK,
    MAX_FRAME_SIZE,
)
from qbit_network.network.p2p import Peer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A realistic hex signature (64 bytes = 128 hex chars)
_FAKE_SIG_HEX = "ab" * 64
_FAKE_SIG_BYTES = bytes.fromhex(_FAKE_SIG_HEX)

# A realistic hex pubkey (32 bytes = 64 hex chars)
_FAKE_PK_HEX = "cd" * 32
_FAKE_PK_BYTES = bytes.fromhex(_FAKE_PK_HEX)


def _make_tx_data() -> dict:
    """Return a realistic transaction-like data dict with binary fields."""
    return {
        "type": "NOTARIZE",
        "sender": "qbit_abc123",
        "sender_pubkey": _FAKE_PK_HEX,
        "signature": _FAKE_SIG_HEX,
        "nonce": 7,
        "document_hash": "deadbeef" * 8,
        "timestamp": 1700000000,
    }


def _make_block_data() -> dict:
    """Return a realistic block-like data dict with nested transactions."""
    return {
        "height": 42,
        "prev_hash": "00" * 32,
        "validator_pubkey": _FAKE_PK_HEX,
        "signature": _FAKE_SIG_HEX,
        "transactions": [
            {
                "type": "NOTARIZE",
                "sender_pubkey": _FAKE_PK_HEX,
                "signature": _FAKE_SIG_HEX,
                "nonce": 1,
            },
            {
                "type": "STORE",
                "sender_pubkey": _FAKE_PK_HEX,
                "signature": _FAKE_SIG_HEX,
                "nonce": 2,
                "ciphertext": "ff" * 16,
            },
        ],
    }


# =========================================================================
# MessageCodec — JSON mode
# =========================================================================

class TestCodecJSON:

    def test_roundtrip_simple(self):
        """Encode then decode a simple message in JSON mode."""
        codec = MessageCodec("json")
        raw = codec.encode("ping", {"ts": 123})
        result = codec.decode(raw)
        assert result is not None
        msg_type, data = result
        assert msg_type == "ping"
        assert data == {"ts": 123}

    def test_roundtrip_nested_block(self):
        """Encode/decode a block message with nested transactions."""
        codec = MessageCodec("json")
        block = _make_block_data()
        raw = codec.encode("new_block", block)
        msg_type, data = codec.decode(raw)
        assert msg_type == "new_block"
        assert data["height"] == 42
        assert len(data["transactions"]) == 2
        assert data["transactions"][0]["signature"] == _FAKE_SIG_HEX

    def test_encode_produces_utf8_json_newline(self):
        """JSON encode produces UTF-8 bytes ending with newline."""
        codec = MessageCodec("json")
        raw = codec.encode("test", {"key": "value"})
        assert isinstance(raw, bytes)
        assert raw.endswith(b"\n")
        text = raw.decode("utf-8")
        parsed = json.loads(text.strip())
        assert parsed["type"] == "test"
        assert parsed["data"]["key"] == "value"

    def test_encode_compact_json(self):
        """JSON encode uses compact separators (no spaces)."""
        codec = MessageCodec("json")
        raw = codec.encode("x", {"a": 1, "b": 2})
        text = raw.decode("utf-8").strip()
        assert " " not in text  # compact separators

    def test_decode_malformed_json_returns_none(self):
        """Malformed JSON input returns None instead of raising."""
        codec = MessageCodec("json")
        result = codec.decode(b"not json at all{{{")
        assert result is None

    def test_decode_invalid_utf8_returns_none(self):
        """Non-UTF-8 bytes return None in JSON mode."""
        codec = MessageCodec("json")
        result = codec.decode(b"\xff\xfe")
        assert result is None

    def test_empty_data_dict(self):
        """Empty data dict roundtrips correctly."""
        codec = MessageCodec("json")
        raw = codec.encode("heartbeat", {})
        msg_type, data = codec.decode(raw)
        assert msg_type == "heartbeat"
        assert data == {}

    def test_missing_type_field(self):
        """Message without 'type' field returns empty string for type."""
        codec = MessageCodec("json")
        raw = json.dumps({"data": {"x": 1}}).encode("utf-8")
        msg_type, data = codec.decode(raw)
        assert msg_type == ""
        assert data == {"x": 1}

    def test_missing_data_field(self):
        """Message without 'data' field returns empty dict for data."""
        codec = MessageCodec("json")
        raw = json.dumps({"type": "foo"}).encode("utf-8")
        msg_type, data = codec.decode(raw)
        assert msg_type == "foo"
        assert data == {}


# =========================================================================
# MessageCodec — msgpack mode
# =========================================================================

@pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
class TestCodecMsgpack:

    def test_roundtrip_simple(self):
        """Encode then decode a simple message in msgpack mode."""
        codec = MessageCodec("msgpack")
        raw = codec.encode("ping", {"ts": 456})
        # Strip the 4-byte length prefix for decode
        payload = raw[4:]
        result = codec.decode(payload)
        assert result is not None
        msg_type, data = result
        assert msg_type == "ping"
        assert data == {"ts": 456}

    def test_length_prefix_format(self):
        """Encode produces 4-byte big-endian length prefix matching payload."""
        codec = MessageCodec("msgpack")
        raw = codec.encode("test", {"a": 1})
        prefix = raw[:4]
        payload = raw[4:]
        length = struct.unpack(">I", prefix)[0]
        assert length == len(payload)

    def test_binary_fields_converted_to_bytes(self):
        """Known binary fields are converted from hex strings to raw bytes."""
        import msgpack
        codec = MessageCodec("msgpack")
        tx = _make_tx_data()
        raw = codec.encode("new_tx", tx)
        payload = raw[4:]
        obj = msgpack.unpackb(payload, raw=False)
        # signature and sender_pubkey should be raw bytes in the payload
        tx_data = obj["data"]
        assert isinstance(tx_data["signature"], bytes)
        assert tx_data["signature"] == _FAKE_SIG_BYTES
        assert isinstance(tx_data["sender_pubkey"], bytes)
        assert tx_data["sender_pubkey"] == _FAKE_PK_BYTES

    def test_binary_fields_roundtrip_to_hex(self):
        """Binary fields are converted back to hex strings on decode."""
        codec = MessageCodec("msgpack")
        tx = _make_tx_data()
        raw = codec.encode("new_tx", tx)
        payload = raw[4:]
        msg_type, data = codec.decode(payload)
        assert data["signature"] == _FAKE_SIG_HEX
        assert data["sender_pubkey"] == _FAKE_PK_HEX

    def test_nested_binary_fields_in_transactions(self):
        """Binary fields inside nested transactions survive roundtrip."""
        codec = MessageCodec("msgpack")
        block = _make_block_data()
        raw = codec.encode("new_block", block)
        payload = raw[4:]
        msg_type, data = codec.decode(payload)
        assert msg_type == "new_block"
        for tx in data["transactions"]:
            assert tx["signature"] == _FAKE_SIG_HEX
            assert tx["sender_pubkey"] == _FAKE_PK_HEX
        assert data["transactions"][1]["ciphertext"] == "ff" * 16

    def test_non_binary_strings_preserved(self):
        """String fields not in _BINARY_FIELDS remain as strings."""
        codec = MessageCodec("msgpack")
        raw = codec.encode("test", {"sender": "qbit_abc", "label": "hello"})
        payload = raw[4:]
        msg_type, data = codec.decode(payload)
        assert data["sender"] == "qbit_abc"
        assert data["label"] == "hello"

    def test_integer_fields_preserved(self):
        """Integer fields survive msgpack roundtrip unchanged."""
        codec = MessageCodec("msgpack")
        raw = codec.encode("test", {"nonce": 42, "height": 999})
        payload = raw[4:]
        _, data = codec.decode(payload)
        assert data["nonce"] == 42
        assert data["height"] == 999

    def test_oversized_frame_raises(self):
        """Encoding a frame larger than MAX_FRAME_SIZE raises ValueError."""
        codec = MessageCodec("msgpack")
        # Create data that will exceed the 10 MB limit
        huge = {"payload": "x" * (MAX_FRAME_SIZE + 1)}
        with pytest.raises(ValueError, match="frame too large"):
            codec.encode("huge", huge)

    def test_decode_malformed_msgpack_returns_none(self):
        """Malformed msgpack payload returns None."""
        codec = MessageCodec("msgpack")
        result = codec.decode(b"\x00\x01\x02\x03garbage")
        assert result is None

    def test_empty_data_msgpack(self):
        """Empty data dict roundtrips in msgpack mode."""
        codec = MessageCodec("msgpack")
        raw = codec.encode("heartbeat", {})
        payload = raw[4:]
        msg_type, data = codec.decode(payload)
        assert msg_type == "heartbeat"
        assert data == {}


# =========================================================================
# MessageCodec — constructor validation
# =========================================================================

class TestCodecConstructor:

    def test_unknown_wire_format_raises(self):
        """Unknown wire format raises ValueError."""
        with pytest.raises(ValueError, match="unknown wire_format"):
            MessageCodec("protobuf")

    def test_json_always_available(self):
        """JSON wire format is always accepted."""
        codec = MessageCodec("json")
        assert codec.wire_format == "json"

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_msgpack_accepted_when_available(self):
        """msgpack wire format accepted when the library is installed."""
        codec = MessageCodec("msgpack")
        assert codec.wire_format == "msgpack"

    def test_msgpack_fallback_when_unavailable(self):
        """msgpack falls back to json when library is not installed."""
        with patch("qbit_network.network.codec.HAS_MSGPACK", False):
            codec = MessageCodec("msgpack")
            assert codec.wire_format == "json"

    def test_max_frame_size_class_attr(self):
        """MAX_FRAME_SIZE is accessible as a class attribute."""
        assert MessageCodec.MAX_FRAME_SIZE == 10 * 1024 * 1024


# =========================================================================
# _hex_to_bytes_recursive
# =========================================================================

class TestHexToBytesRecursive:

    def test_converts_known_fields(self):
        """Fields in _BINARY_FIELDS are converted from hex to bytes."""
        d = {"signature": "aabb", "sender_pubkey": "ccdd"}
        result = _hex_to_bytes_recursive(d)
        assert result["signature"] == bytes.fromhex("aabb")
        assert result["sender_pubkey"] == bytes.fromhex("ccdd")

    def test_leaves_unknown_fields_as_strings(self):
        """Fields not in _BINARY_FIELDS remain as strings."""
        d = {"sender": "aabb", "label": "hello"}
        result = _hex_to_bytes_recursive(d)
        assert result["sender"] == "aabb"
        assert result["label"] == "hello"

    def test_nested_dicts(self):
        """Recursively converts binary fields in nested dicts."""
        d = {"data": {"signature": "aabb"}}
        result = _hex_to_bytes_recursive(d)
        assert result["data"]["signature"] == bytes.fromhex("aabb")

    def test_nested_lists(self):
        """Recursively converts binary fields inside lists."""
        d = {"transactions": [{"signature": "aabb"}, {"signature": "ccdd"}]}
        result = _hex_to_bytes_recursive(d)
        assert result["transactions"][0]["signature"] == bytes.fromhex("aabb")
        assert result["transactions"][1]["signature"] == bytes.fromhex("ccdd")

    def test_depth_limit_prevents_infinite_recursion(self):
        """Recursion stops at depth > 10, returning the object as-is."""
        # Build a nested dict 12 levels deep
        inner = {"signature": "aabb"}
        obj = inner
        for _ in range(12):
            obj = {"nested": obj}
        result = _hex_to_bytes_recursive(obj)
        # Walk 10 levels deep — the inner signature should NOT be converted
        node = result
        for _ in range(12):
            node = node["nested"]
        assert node["signature"] == "aabb"  # still a string, not bytes

    def test_invalid_hex_stays_as_string(self):
        """Non-hex string in a binary field remains as a string."""
        d = {"signature": "not_hex_at_all"}
        result = _hex_to_bytes_recursive(d)
        assert result["signature"] == "not_hex_at_all"

    def test_odd_length_hex_stays_as_string(self):
        """Odd-length hex string (invalid) remains as a string."""
        d = {"signature": "abc"}
        result = _hex_to_bytes_recursive(d)
        assert result["signature"] == "abc"

    def test_non_string_binary_field_unchanged(self):
        """Non-string value in a binary field is left unchanged."""
        d = {"signature": 12345}
        result = _hex_to_bytes_recursive(d)
        assert result["signature"] == 12345

    def test_all_binary_fields_recognized(self):
        """Every field in _BINARY_FIELDS is converted when present."""
        d = {field: "aa" for field in _BINARY_FIELDS}
        result = _hex_to_bytes_recursive(d)
        for field in _BINARY_FIELDS:
            assert result[field] == bytes.fromhex("aa")

    def test_empty_dict(self):
        """Empty dict returns empty dict."""
        assert _hex_to_bytes_recursive({}) == {}

    def test_scalar_passthrough(self):
        """Non-dict, non-list values pass through unchanged."""
        assert _hex_to_bytes_recursive(42) == 42
        assert _hex_to_bytes_recursive("hello") == "hello"
        assert _hex_to_bytes_recursive(None) is None


# =========================================================================
# _bytes_to_hex_recursive
# =========================================================================

class TestBytesToHexRecursive:

    def test_converts_known_binary_fields(self):
        """bytes/bytearray in _BINARY_FIELDS are converted to hex strings."""
        d = {"signature": b"\xaa\xbb", "sender_pubkey": bytearray(b"\xcc\xdd")}
        result = _bytes_to_hex_recursive(d)
        assert result["signature"] == "aabb"
        assert result["sender_pubkey"] == "ccdd"

    def test_generic_bytes_decoded_as_utf8(self):
        """bytes not in a known field are decoded as UTF-8."""
        d = {"sender": b"hello"}
        result = _bytes_to_hex_recursive(d)
        assert result["sender"] == "hello"

    def test_non_decodable_bytes_become_hex(self):
        """bytes not in a known field that aren't valid UTF-8 become hex."""
        d = {"data": b"\xff\xfe"}
        result = _bytes_to_hex_recursive(d)
        assert result["data"] == "fffe"

    def test_nested_dicts(self):
        """Recursively converts binary fields in nested dicts."""
        d = {"inner": {"signature": b"\xaa"}}
        result = _bytes_to_hex_recursive(d)
        assert result["inner"]["signature"] == "aa"

    def test_nested_lists(self):
        """Recursively converts binary fields in lists."""
        d = {"txs": [{"signature": b"\xaa"}, {"signature": b"\xbb"}]}
        result = _bytes_to_hex_recursive(d)
        assert result["txs"][0]["signature"] == "aa"
        assert result["txs"][1]["signature"] == "bb"

    def test_depth_limit(self):
        """Recursion stops at depth > 10."""
        inner = {"signature": b"\xaa"}
        obj = inner
        for _ in range(12):
            obj = {"nested": obj}
        result = _bytes_to_hex_recursive(obj)
        node = result
        for _ in range(12):
            node = node["nested"]
        # Still bytes, not converted
        assert node["signature"] == b"\xaa"

    def test_bare_bytes_in_list_decoded(self):
        """Bare bytes values in a list are decoded as UTF-8 or hex."""
        result = _bytes_to_hex_recursive([b"hello", b"\xff"])
        assert result[0] == "hello"
        assert result[1] == "ff"

    def test_string_values_unchanged(self):
        """String values pass through unchanged."""
        d = {"signature": "already_hex", "label": "test"}
        result = _bytes_to_hex_recursive(d)
        assert result["signature"] == "already_hex"
        assert result["label"] == "test"

    def test_empty_bytes(self):
        """Empty bytes in a binary field become empty string."""
        d = {"signature": b""}
        result = _bytes_to_hex_recursive(d)
        assert result["signature"] == ""


# =========================================================================
# Protocol negotiation
# =========================================================================

class TestNegotiation:

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_both_v4_both_msgpack(self):
        """Both v4+ and both msgpack results in msgpack."""
        result = MessageCodec.negotiate(4, 4, "msgpack", "msgpack")
        assert result == "msgpack"

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_v5_and_v4_both_msgpack(self):
        """Higher version peers still negotiate msgpack."""
        result = MessageCodec.negotiate(5, 4, "msgpack", "msgpack")
        assert result == "msgpack"

    def test_v4_and_v3_falls_back_to_json(self):
        """v4 + v3 falls back to json (backward compat)."""
        result = MessageCodec.negotiate(4, 3, "msgpack", "msgpack")
        assert result == "json"

    def test_v3_and_v3_is_json(self):
        """v3 + v3 always results in json."""
        result = MessageCodec.negotiate(3, 3, "json", "json")
        assert result == "json"

    def test_v4_both_but_one_json(self):
        """v4 + v4 but one side prefers json results in json."""
        result = MessageCodec.negotiate(4, 4, "msgpack", "json")
        assert result == "json"

    def test_v4_both_but_other_json(self):
        """v4 + v4 but the other side prefers json results in json."""
        result = MessageCodec.negotiate(4, 4, "json", "msgpack")
        assert result == "json"

    def test_both_json_v4(self):
        """v4 + v4 both json stays json."""
        result = MessageCodec.negotiate(4, 4, "json", "json")
        assert result == "json"

    def test_msgpack_unavailable_falls_back(self):
        """Even if both claim msgpack, unavailable library means json."""
        with patch("qbit_network.network.codec.HAS_MSGPACK", False):
            result = MessageCodec.negotiate(4, 4, "msgpack", "msgpack")
            assert result == "json"

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_preferred_wire_format_msgpack(self):
        """preferred_wire_format returns 'msgpack' when available."""
        assert MessageCodec.preferred_wire_format() == "msgpack"

    def test_preferred_wire_format_json_fallback(self):
        """preferred_wire_format returns 'json' when msgpack unavailable."""
        with patch("qbit_network.network.codec.HAS_MSGPACK", False):
            assert MessageCodec.preferred_wire_format() == "json"


# =========================================================================
# Peer wire_format
# =========================================================================

class TestPeerWireFormat:

    def test_new_peer_defaults_to_json(self):
        """A new Peer defaults to wire_format='json'."""
        peer = Peer("127.0.0.1", 9000)
        assert peer.wire_format == "json"

    def test_new_peer_has_pending_wire_format(self):
        """A new Peer has _pending_wire_format='json'."""
        peer = Peer("127.0.0.1", 9000)
        assert peer._pending_wire_format == "json"

    @pytest.mark.asyncio
    async def test_send_json_mode(self):
        """Peer.send() with wire_format='json' writes JSON + newline."""
        writer = MagicMock()
        writer.is_closing = MagicMock(return_value=False)
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        peer = Peer("127.0.0.1", 9000, writer=writer)
        peer.wire_format = "json"
        await peer.send("ping", {"ts": 1})

        writer.write.assert_called_once()
        written = writer.write.call_args[0][0]
        assert isinstance(written, bytes)
        text = written.decode("utf-8")
        assert text.endswith("\n")
        parsed = json.loads(text.strip())
        assert parsed["type"] == "ping"
        assert parsed["data"]["ts"] == 1

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    @pytest.mark.asyncio
    async def test_send_msgpack_mode(self):
        """Peer.send() with wire_format='msgpack' writes length-prefixed msgpack."""
        writer = MagicMock()
        writer.is_closing = MagicMock(return_value=False)
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        peer = Peer("127.0.0.1", 9000, writer=writer)
        peer.wire_format = "msgpack"
        await peer.send("ping", {"ts": 1})

        writer.write.assert_called_once()
        written = writer.write.call_args[0][0]
        assert isinstance(written, bytes)
        # First 4 bytes are big-endian length prefix
        length = struct.unpack(">I", written[:4])[0]
        assert length == len(written) - 4

    @pytest.mark.asyncio
    async def test_send_with_closed_writer_does_nothing(self):
        """Peer.send() does nothing if writer is closing."""
        writer = MagicMock()
        writer.is_closing = MagicMock(return_value=True)
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        peer = Peer("127.0.0.1", 9000, writer=writer)
        await peer.send("ping", {})
        writer.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_with_no_writer_does_nothing(self):
        """Peer.send() does nothing if writer is None."""
        peer = Peer("127.0.0.1", 9000)
        # Should not raise
        await peer.send("ping", {})


# =========================================================================
# Cross-codec integration
# =========================================================================

class TestCrossCodecIntegration:

    def test_json_cross_codec_roundtrip(self):
        """JSON message encoded by one codec is decoded by another."""
        encoder = MessageCodec("json")
        decoder = MessageCodec("json")
        raw = encoder.encode("test", {"key": "value", "num": 42})
        msg_type, data = decoder.decode(raw)
        assert msg_type == "test"
        assert data == {"key": "value", "num": 42}

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_msgpack_cross_codec_roundtrip(self):
        """Msgpack message encoded by one codec is decoded by another."""
        encoder = MessageCodec("msgpack")
        decoder = MessageCodec("msgpack")
        raw = encoder.encode("test", {"key": "value", "num": 42})
        payload = raw[4:]
        msg_type, data = decoder.decode(payload)
        assert msg_type == "test"
        assert data == {"key": "value", "num": 42}

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_all_binary_fields_roundtrip_msgpack(self):
        """Message with all binary fields survives msgpack roundtrip."""
        data = {field: "aabbccdd" for field in _BINARY_FIELDS}
        data["normal_string"] = "keep_me"
        data["number"] = 99

        encoder = MessageCodec("msgpack")
        decoder = MessageCodec("msgpack")
        raw = encoder.encode("full_binary", data)
        payload = raw[4:]
        msg_type, decoded = decoder.decode(payload)

        assert msg_type == "full_binary"
        for field in _BINARY_FIELDS:
            assert decoded[field] == "aabbccdd"
        assert decoded["normal_string"] == "keep_me"
        assert decoded["number"] == 99

    def test_all_binary_fields_roundtrip_json(self):
        """Message with all binary fields survives JSON roundtrip."""
        data = {field: "aabbccdd" for field in _BINARY_FIELDS}
        data["normal_string"] = "keep_me"

        encoder = MessageCodec("json")
        decoder = MessageCodec("json")
        raw = encoder.encode("full_binary", data)
        msg_type, decoded = decoder.decode(raw)

        assert msg_type == "full_binary"
        for field in _BINARY_FIELDS:
            assert decoded[field] == "aabbccdd"
        assert decoded["normal_string"] == "keep_me"

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_msgpack_smaller_than_json_for_binary(self):
        """Msgpack is smaller than JSON for messages with large signatures."""
        data = _make_block_data()

        json_codec = MessageCodec("json")
        mp_codec = MessageCodec("msgpack")

        json_raw = json_codec.encode("new_block", data)
        mp_raw = mp_codec.encode("new_block", data)

        # msgpack with binary fields should be notably smaller
        assert len(mp_raw) < len(json_raw), (
            f"msgpack ({len(mp_raw)} bytes) should be smaller than "
            f"JSON ({len(json_raw)} bytes)"
        )

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_nested_block_full_roundtrip_msgpack(self):
        """Full block with nested txs roundtrips through msgpack."""
        block = _make_block_data()
        encoder = MessageCodec("msgpack")
        decoder = MessageCodec("msgpack")

        raw = encoder.encode("new_block", block)
        payload = raw[4:]
        msg_type, data = decoder.decode(payload)

        assert msg_type == "new_block"
        assert data["height"] == 42
        assert data["prev_hash"] == "00" * 32
        assert data["validator_pubkey"] == _FAKE_PK_HEX
        assert data["signature"] == _FAKE_SIG_HEX
        assert len(data["transactions"]) == 2
        assert data["transactions"][0]["nonce"] == 1
        assert data["transactions"][1]["nonce"] == 2

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_msgpack_bool_and_none_roundtrip(self):
        """Booleans and None survive msgpack roundtrip."""
        codec = MessageCodec("msgpack")
        raw = codec.encode("flags", {"active": True, "deleted": False, "extra": None})
        payload = raw[4:]
        _, data = codec.decode(payload)
        assert data["active"] is True
        assert data["deleted"] is False
        assert data["extra"] is None

    def test_json_bool_and_none_roundtrip(self):
        """Booleans and None survive JSON roundtrip."""
        codec = MessageCodec("json")
        raw = codec.encode("flags", {"active": True, "deleted": False, "extra": None})
        _, data = codec.decode(raw)
        assert data["active"] is True
        assert data["deleted"] is False
        assert data["extra"] is None

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_msgpack_list_data(self):
        """Lists as values survive msgpack roundtrip."""
        codec = MessageCodec("msgpack")
        raw = codec.encode("peers", {"addrs": ["1.2.3.4:9000", "5.6.7.8:9001"]})
        payload = raw[4:]
        _, data = codec.decode(payload)
        assert data["addrs"] == ["1.2.3.4:9000", "5.6.7.8:9001"]

    @pytest.mark.skipif(not HAS_MSGPACK, reason="msgpack not installed")
    def test_msgpack_encode_then_read_message_style(self):
        """Simulate _read_message flow: read 4-byte header, then payload."""
        codec = MessageCodec("msgpack")
        raw = codec.encode("sync", {"height": 100})

        # Simulate what _read_message does
        header = raw[:4]
        length = int.from_bytes(header, "big")
        assert length <= MAX_FRAME_SIZE
        payload = raw[4:4 + length]
        assert len(payload) == length

        msg_type, data = codec.decode(payload)
        assert msg_type == "sync"
        assert data["height"] == 100

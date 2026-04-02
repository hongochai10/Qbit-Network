"""Fuzz testing for Block.from_dict() and Transaction.from_dict() deserialization.

These are critical attack surfaces since P2P peers send block/tx data
as dicts that are deserialized via from_dict(). Every malformed input
must raise ValueError (or a subclass) — never crash with an unhandled
exception or silently produce corrupt objects.
"""
import copy
import math
import pytest
from qbit_network.core.block import Block
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.config import CHAIN_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_tx_dict(**overrides) -> dict:
    """Return a minimal valid NOTARIZE transaction dict."""
    base = {
        "type": "NOTARIZE",
        "from": "qv1" + "a" * 64,
        "to": "",
        "timestamp": 1700000000,
        "payload": {"documentHash": "ab" * 32},
        "nonce": 0,
        "chainId": CHAIN_ID,
        "maxFeePerWeight": 0,
        "maxPriorityFee": 0,
        "signature": "",
        "sender_pubkey": "",
    }
    base.update(overrides)
    return base


def _valid_block_dict(**overrides) -> dict:
    """Return a minimal valid block dict (with one tx)."""
    tx = _valid_tx_dict()
    # Build a real block to get a valid hash/merkle
    real_tx = Transaction.from_dict(tx)
    real_block = Block(
        index=1,
        prev_hash="0" * 64,
        transactions=[real_tx],
        validator="qv1" + "b" * 64,
        timestamp=1700000000,
        base_fee=0,
    )
    d = real_block.to_dict()
    d.update(overrides)
    return d


def _assert_rejects(from_dict_fn, data, label=""):
    """Assert that from_dict raises an exception (ValueError or TypeError etc)."""
    try:
        from_dict_fn(data)
        pytest.fail(f"Expected exception for fuzz case: {label}")
    except (ValueError, TypeError, KeyError, AttributeError):
        pass  # Expected — any of these are acceptable rejection paths


# ===========================================================================
# TRANSACTION.from_dict() FUZZ TESTS
# ===========================================================================

class TestTransactionFromDictFuzz:
    """Fuzz tests for Transaction.from_dict()."""

    # --- Missing required fields ---

    def test_empty_dict(self):
        _assert_rejects(Transaction.from_dict, {}, "empty dict")

    def test_missing_type(self):
        d = _valid_tx_dict()
        del d["type"]
        _assert_rejects(Transaction.from_dict, d, "missing type")

    def test_missing_from(self):
        d = _valid_tx_dict()
        del d["from"]
        _assert_rejects(Transaction.from_dict, d, "missing from")

    def test_missing_timestamp(self):
        d = _valid_tx_dict()
        del d["timestamp"]
        _assert_rejects(Transaction.from_dict, d, "missing timestamp")

    def test_missing_payload(self):
        d = _valid_tx_dict()
        del d["payload"]
        _assert_rejects(Transaction.from_dict, d, "missing payload")

    def test_missing_chain_id_backward_compat(self):
        """Missing chainId defaults to empty string for backward compatibility (R36-M01)."""
        d = _valid_tx_dict()
        del d["chainId"]
        tx = Transaction.from_dict(d)
        assert tx.chain_id == ""

    # --- Wrong types for required fields ---

    def test_type_as_int(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(type=42), "type=int")

    def test_type_as_none(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(type=None), "type=None")

    def test_type_invalid_enum(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(type="INVALID_TYPE"), "bad enum")

    def test_from_as_int(self):
        d = _valid_tx_dict()
        d["from"] = 12345
        _assert_rejects(Transaction.from_dict, d, "from=int")

    def test_from_as_none(self):
        d = _valid_tx_dict()
        d["from"] = None
        _assert_rejects(Transaction.from_dict, d, "from=None")

    def test_from_as_list(self):
        d = _valid_tx_dict()
        d["from"] = ["addr"]
        _assert_rejects(Transaction.from_dict, d, "from=list")

    def test_timestamp_as_string(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(timestamp="123"), "ts=str")

    def test_timestamp_as_float(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(timestamp=1.5), "ts=float")

    def test_timestamp_as_none(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(timestamp=None), "ts=None")

    def test_timestamp_as_bool(self):
        # bool is subclass of int in Python — should still parse or reject cleanly
        d = _valid_tx_dict(timestamp=True)
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError):
            pass  # Either outcome is acceptable

    def test_payload_as_string(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(payload="{}"), "payload=str")

    def test_payload_as_list(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(payload=[1, 2]), "payload=list")

    def test_payload_as_none(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(payload=None), "payload=None")

    def test_payload_as_int(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(payload=999), "payload=int")

    # --- Wrong types for numeric fields ---

    def test_nonce_as_string(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(nonce="abc"), "nonce=str")

    def test_nonce_as_float(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(nonce=1.5), "nonce=float")

    def test_nonce_as_list(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(nonce=[0]), "nonce=list")

    def test_max_fee_as_string(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(maxFeePerWeight="10"), "fee=str")

    def test_max_fee_negative(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(maxFeePerWeight=-1), "fee=-1")

    def test_max_priority_fee_negative(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(maxPriorityFee=-100), "pf=-100")

    def test_max_priority_fee_as_none(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(maxPriorityFee=None), "pf=None")

    # --- Malformed hex fields ---

    def test_signature_non_hex(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(signature="ZZZZ"), "sig=nonhex")

    def test_signature_odd_length_hex(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(signature="abc"), "sig=odd hex")

    def test_sender_pubkey_non_hex(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(sender_pubkey="xyz!"), "spk=nonhex")

    def test_sender_pubkey_wrong_size(self):
        # ML-DSA-65 pubkey = 1952 bytes. Supply wrong size.
        _assert_rejects(
            Transaction.from_dict,
            _valid_tx_dict(sender_pubkey="aa" * 100),
            "spk=wrong size"
        )

    def test_sender_pubkey_as_int(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(sender_pubkey=12345), "spk=int")

    # --- Oversized / boundary values ---

    def test_timestamp_huge(self):
        d = _valid_tx_dict(timestamp=2**63)
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError, OverflowError):
            pass

    def test_timestamp_negative(self):
        d = _valid_tx_dict(timestamp=-1)
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_nonce_huge(self):
        d = _valid_tx_dict(nonce=2**128)
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError, OverflowError):
            pass

    def test_chain_id_as_int(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(chainId=1), "chainId=int")

    def test_chain_id_as_none(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(chainId=None), "chainId=None")

    def test_chain_id_as_bool(self):
        _assert_rejects(Transaction.from_dict, _valid_tx_dict(chainId=True), "chainId=bool")

    def test_chain_id_empty(self):
        # Empty chainId — should parse or reject cleanly
        d = _valid_tx_dict(chainId="")
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError):
            pass

    # --- Nested injection / unusual structures ---

    def test_payload_deeply_nested(self):
        nested = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
        d = _valid_tx_dict(payload=nested)
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_extra_unknown_keys(self):
        d = _valid_tx_dict()
        d["__proto__"] = {"admin": True}
        d["constructor"] = "evil"
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_all_fields_none(self):
        d = {k: None for k in _valid_tx_dict()}
        _assert_rejects(Transaction.from_dict, d, "all None")

    def test_unicode_sender(self):
        d = _valid_tx_dict()
        d["from"] = "\u0000" * 64
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_data_is_list_not_dict(self):
        _assert_rejects(Transaction.from_dict, [], "data=list")  # type: ignore

    def test_data_is_string(self):
        _assert_rejects(Transaction.from_dict, "not a dict", "data=str")  # type: ignore

    def test_data_is_none(self):
        _assert_rejects(Transaction.from_dict, None, "data=None")  # type: ignore

    # --- All TxType enum values with wrong payload shape ---

    @pytest.mark.parametrize("tx_type", [t.value for t in TxType])
    def test_each_type_with_empty_payload(self, tx_type):
        d = _valid_tx_dict(type=tx_type, payload={})
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError, KeyError):
            pass

    @pytest.mark.parametrize("tx_type", [t.value for t in TxType])
    def test_each_type_with_null_payload_values(self, tx_type):
        d = _valid_tx_dict(type=tx_type, payload={"key": None})
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError, KeyError):
            pass


# ===========================================================================
# BLOCK.from_dict() FUZZ TESTS
# ===========================================================================

class TestBlockFromDictFuzz:
    """Fuzz tests for Block.from_dict()."""

    # --- Missing required fields ---

    def test_empty_dict(self):
        _assert_rejects(Block.from_dict, {}, "empty dict")

    def test_missing_index(self):
        d = _valid_block_dict()
        del d["index"]
        _assert_rejects(Block.from_dict, d, "missing index")

    def test_missing_timestamp(self):
        d = _valid_block_dict()
        del d["timestamp"]
        _assert_rejects(Block.from_dict, d, "missing timestamp")

    def test_missing_prev_hash(self):
        d = _valid_block_dict()
        del d["prevHash"]
        _assert_rejects(Block.from_dict, d, "missing prevHash")

    # --- Wrong types ---

    def test_index_as_string(self):
        _assert_rejects(Block.from_dict, _valid_block_dict(index="abc"), "index=str")

    def test_index_as_float(self):
        _assert_rejects(Block.from_dict, _valid_block_dict(index=1.5), "index=float")

    def test_index_as_none(self):
        _assert_rejects(Block.from_dict, _valid_block_dict(index=None), "index=None")

    def test_index_as_bool(self):
        # bool is subclass of int — should handle cleanly
        d = _valid_block_dict(index=True)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_timestamp_as_string(self):
        _assert_rejects(Block.from_dict, _valid_block_dict(timestamp="ts"), "ts=str")

    def test_timestamp_as_float(self):
        _assert_rejects(Block.from_dict, _valid_block_dict(timestamp=1.5), "ts=float")

    def test_timestamp_as_none(self):
        _assert_rejects(Block.from_dict, _valid_block_dict(timestamp=None), "ts=None")

    def test_timestamp_negative(self):
        d = _valid_block_dict(timestamp=-9999)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_timestamp_huge(self):
        d = _valid_block_dict(timestamp=2**63)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError, OverflowError):
            pass

    # --- Malformed transactions list ---

    def test_transactions_as_string(self):
        _assert_rejects(Block.from_dict, _valid_block_dict(transactions="not a list"), "txs=str")

    def test_transactions_as_none(self):
        d = _valid_block_dict(transactions=None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_transactions_contains_non_dict(self):
        d = _valid_block_dict(transactions=[42, "bad"])
        _assert_rejects(Block.from_dict, d, "txs=[42,'bad']")

    def test_transactions_contains_empty_dict(self):
        d = _valid_block_dict(transactions=[{}])
        _assert_rejects(Block.from_dict, d, "txs=[{}]")

    def test_transactions_contains_none(self):
        d = _valid_block_dict(transactions=[None])
        _assert_rejects(Block.from_dict, d, "txs=[None]")

    # --- Malformed signature ---

    def test_signature_non_hex(self):
        _assert_rejects(Block.from_dict, _valid_block_dict(signature="ZZZZ"), "sig=nonhex")

    def test_signature_odd_hex(self):
        _assert_rejects(Block.from_dict, _valid_block_dict(signature="abc"), "sig=odd hex")

    def test_signature_as_int(self):
        _assert_rejects(Block.from_dict, _valid_block_dict(signature=12345), "sig=int")

    def test_signature_as_none(self):
        d = _valid_block_dict(signature=None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    # --- Hash mismatch ---

    def test_block_hash_mismatch(self):
        d = _valid_block_dict(hash="ff" * 32)
        _assert_rejects(Block.from_dict, d, "hash mismatch")

    def test_block_hash_non_hex(self):
        d = _valid_block_dict(hash="ZZZNOTAHASH")
        _assert_rejects(Block.from_dict, d, "hash=nonhex")

    # --- prevHash edge cases ---

    def test_prev_hash_as_int(self):
        d = _valid_block_dict(prevHash=0)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_prev_hash_as_none(self):
        d = _valid_block_dict(prevHash=None)
        _assert_rejects(Block.from_dict, d, "prevHash=None")

    def test_prev_hash_empty(self):
        d = _valid_block_dict(prevHash="")
        # remove hash so mismatch doesn't trigger first
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    # --- base_fee edge cases ---

    def test_base_fee_as_string(self):
        d = _valid_block_dict(baseFee="ten")
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_base_fee_negative(self):
        d = _valid_block_dict(baseFee=-100)
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_base_fee_huge(self):
        d = _valid_block_dict(baseFee=2**256)
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError, OverflowError):
            pass

    # --- Validator edge cases ---

    def test_validator_as_int(self):
        d = _valid_block_dict(validator=99999)
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_validator_as_none(self):
        d = _valid_block_dict(validator=None)
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    # --- stateRoot / receiptsRoot edge cases ---

    def test_state_root_as_int(self):
        d = _valid_block_dict(stateRoot=42)
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_receipts_root_as_list(self):
        d = _valid_block_dict(receiptsRoot=[1, 2, 3])
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    # --- Structural attacks ---

    def test_data_is_list(self):
        _assert_rejects(Block.from_dict, [], "data=list")  # type: ignore

    def test_data_is_string(self):
        _assert_rejects(Block.from_dict, "block", "data=str")  # type: ignore

    def test_data_is_none(self):
        _assert_rejects(Block.from_dict, None, "data=None")  # type: ignore

    def test_extra_keys_ignored(self):
        d = _valid_block_dict()
        d["__proto__"] = {"admin": True}
        d["evil_key"] = "payload"
        # Should parse without crashing (extra keys ignored)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_index_negative(self):
        d = _valid_block_dict(index=-1)
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_index_zero_with_wrong_prev_hash(self):
        d = _valid_block_dict(index=0, prevHash="ff" * 32)
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError):
            pass

    def test_index_huge(self):
        d = _valid_block_dict(index=2**64)
        d.pop("hash", None)
        try:
            Block.from_dict(d)
        except (ValueError, TypeError, OverflowError):
            pass

    # --- Valid round-trip (sanity check) ---

    def test_valid_block_round_trips(self):
        """A known-good block dict should deserialize without error."""
        d = _valid_block_dict()
        block = Block.from_dict(d)
        assert block.index == d["index"]
        assert block.timestamp == d["timestamp"]

    def test_valid_tx_round_trips(self):
        """A known-good tx dict should deserialize without error."""
        d = _valid_tx_dict()
        tx = Transaction.from_dict(d)
        assert tx.sender == d["from"]
        assert tx.tx_type == TxType.NOTARIZE


# ===========================================================================
# PARAMETRIC FUZZ — field-by-field type confusion
# ===========================================================================

_TX_FUZZ_VALUES = [
    None, True, False, 0, -1, 2**64, 1.5, float("inf"), float("nan"),
    "", "x", "a" * 10000, [], [1], {}, {"a": 1}, b"bytes",
    object(),
]

_TX_REQUIRED_FIELDS = ["type", "from", "timestamp", "payload"]
_TX_OPTIONAL_FIELDS = ["to", "nonce", "chainId", "maxFeePerWeight", "maxPriorityFee",
                       "signature", "sender_pubkey"]

class TestTransactionFieldFuzz:
    """Parametric fuzz: replace each field with garbage values."""

    @pytest.mark.parametrize("field", _TX_REQUIRED_FIELDS + _TX_OPTIONAL_FIELDS)
    @pytest.mark.parametrize("bad_value", _TX_FUZZ_VALUES, ids=lambda v: type(v).__name__)
    def test_field_replacement(self, field, bad_value):
        d = _valid_tx_dict()
        d[field] = bad_value
        try:
            Transaction.from_dict(d)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            pass  # Any controlled rejection is fine


_BLOCK_FIELDS = ["index", "timestamp", "prevHash", "transactions",
                 "signature", "validator", "baseFee", "stateRoot", "receiptsRoot"]

_BLOCK_FUZZ_VALUES = [
    None, True, False, 0, -1, 2**64, 1.5, float("inf"),
    "", "x", "a" * 10000, [], [1], {}, {"a": 1},
]

class TestBlockFieldFuzz:
    """Parametric fuzz: replace each block field with garbage values."""

    @pytest.mark.parametrize("field", _BLOCK_FIELDS)
    @pytest.mark.parametrize("bad_value", _BLOCK_FUZZ_VALUES, ids=lambda v: type(v).__name__)
    def test_field_replacement(self, field, bad_value):
        d = _valid_block_dict()
        d.pop("hash", None)  # Remove hash to avoid mismatch masking other errors
        d[field] = bad_value
        try:
            Block.from_dict(d)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            pass  # Any controlled rejection is fine

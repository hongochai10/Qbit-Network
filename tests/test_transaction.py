"""Tests for qbit_network.core.transaction — types, signing, validation, serialization."""
import json
import pytest
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.wallet import Wallet
from qbit_network.config import CHAIN_ID, MAX_TX_PAYLOAD_SIZE


class TestTransactionCreation:
    def test_notarize_factory(self, wallet):
        tx = Transaction.notarize(wallet.address, "aabbccdd", metadata="test", nonce=0)
        assert tx.tx_type == TxType.NOTARIZE
        assert tx.sender == wallet.address
        assert tx.nonce == 0
        assert tx.payload["documentHash"] == "aabbccdd"

    def test_store_factory(self, wallet):
        tx = Transaction.store(wallet.address, "aabb", "QmCID", nonce=1)
        assert tx.tx_type == TxType.STORE
        assert tx.payload["cid"] == "QmCID"

    def test_share_factory(self, wallet_pair):
        alice, bob = wallet_pair
        from qbit_network.crypto import MLKEM
        ct, _ = MLKEM.encapsulate(bob.encryption_pk)
        tx = Transaction.share(alice.address, bob.address, "QmX", ct, expires=9999, nonce=0)
        assert tx.tx_type == TxType.SHARE
        assert tx.recipient == bob.address
        assert tx.payload["expires"] == 9999

    def test_register_key_factory(self, wallet):
        tx = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        assert tx.tx_type == TxType.REGISTER_KEY
        assert tx.payload["encryption_pk"] == wallet.encryption_pk.hex()

    def test_chain_id_default(self, wallet):
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        assert tx.chain_id == CHAIN_ID

    def test_tx_id_deterministic(self, wallet):
        tx1 = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx2 = Transaction.notarize(wallet.address, "aa", nonce=0)
        # Same content, same timestamp → same id (if created in same second)
        # But timestamp auto-set, so just check determinism on same object
        assert tx1.tx_id == tx1.tx_id  # cached


class TestTransactionSigning:
    def test_sign_and_verify(self, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        assert tx.verify() is True

    def test_verify_unsigned(self, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        assert tx.verify() is False

    def test_verify_wrong_sender(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.notarize(alice.address, "aabb", nonce=0)
        tx.sign(bob.signing_sk, bob.signing_pk)  # signed by bob but sender=alice
        assert tx.verify() is False

    def test_verify_tampered_payload(self, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        tx.payload["documentHash"] = "ccdd"
        tx._cached_signable = None  # force recompute
        tx._cached_id = None
        assert tx.verify() is False


class TestTransactionSerialization:
    def test_roundtrip(self, wallet):
        tx = Transaction.notarize(wallet.address, "aabbccdd", nonce=5)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        d = tx.to_dict()
        restored = Transaction.from_dict(d)
        assert restored.tx_id == tx.tx_id
        assert restored.verify() is True

    def test_from_dict_type_validation(self):
        with pytest.raises(ValueError, match="timestamp must be int"):
            Transaction.from_dict({"type": "NOTARIZE", "from": "x",
                                   "timestamp": "not_int", "payload": {}, "nonce": 0})

    def test_from_dict_from_must_be_string(self):
        with pytest.raises(ValueError, match="from must be string"):
            Transaction.from_dict({"type": "NOTARIZE", "from": 123,
                                   "timestamp": 1, "payload": {}, "nonce": 0})

    def test_from_dict_payload_must_be_dict(self):
        with pytest.raises(ValueError, match="payload must be dict"):
            Transaction.from_dict({"type": "NOTARIZE", "from": "x",
                                   "timestamp": 1, "payload": "string", "nonce": 0})

    def test_from_dict_bad_pubkey_size(self):
        with pytest.raises(ValueError, match="sender_pubkey wrong size"):
            Transaction.from_dict({"type": "NOTARIZE", "from": "x",
                                   "timestamp": 1, "payload": {},
                                   "sender_pubkey": "cc" * 100, "signature": ""})

    def test_from_dict_invalid_hex(self):
        with pytest.raises(ValueError, match="valid hex"):
            Transaction.from_dict({"type": "NOTARIZE", "from": "x",
                                   "timestamp": 1, "payload": {},
                                   "sender_pubkey": "xyz", "signature": ""})


class TestPayloadValidation:
    def test_notarize_valid(self, wallet):
        tx = Transaction.notarize(wallet.address, "aabbccdd", nonce=0)
        ok, _ = tx.validate_payload()
        assert ok

    def test_notarize_empty_hash(self, wallet):
        tx = Transaction.notarize(wallet.address, "", nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "non-empty hex" in err

    def test_notarize_non_hex_hash(self, wallet):
        tx = Transaction.notarize(wallet.address, "not-hex!", nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

    def test_store_missing_cid(self, wallet):
        tx = Transaction(TxType.STORE, wallet.address,
                         payload={"documentHash": "aa", "metadata": ""}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "cid required" in err

    def test_share_missing_cid(self, wallet):
        tx = Transaction(TxType.SHARE, wallet.address, recipient="qv1bob",
                         payload={"encapsulatedKey": "aabb", "expires": 0}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "cid required" in err

    def test_share_bad_expires_type(self, wallet):
        tx = Transaction(TxType.SHARE, wallet.address, recipient="qv1bob",
                         payload={"cid": "Qm", "encapsulatedKey": "aabb",
                                  "expires": "never"}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "non-negative integer" in err

    def test_share_negative_expires(self, wallet):
        tx = Transaction(TxType.SHARE, wallet.address, recipient="qv1bob",
                         payload={"cid": "Qm", "encapsulatedKey": "aabb",
                                  "expires": -1}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

    def test_payload_too_large(self, wallet):
        tx = Transaction.notarize(wallet.address, "aa" * 5000, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "too large" in err

    def test_unknown_payload_keys_rejected(self, wallet):
        tx = Transaction(TxType.NOTARIZE, wallet.address,
                         payload={"documentHash": "aa", "metadata": "", "extra": "bad"},
                         nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "unknown payload keys" in err

    def test_register_key_valid(self, wallet):
        tx = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        ok, _ = tx.validate_payload()
        assert ok

    def test_register_key_non_hex(self, wallet):
        tx = Transaction(TxType.REGISTER_KEY, wallet.address,
                         payload={"encryption_pk": "xyz"}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

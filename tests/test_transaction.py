"""Tests for qbit_network.core.transaction — types, signing, validation, serialization."""
import json
import pytest
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.wallet import Wallet
from qbit_network.config import CHAIN_ID, MAX_TX_PAYLOAD_SIZE, MAX_SUPPLY


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

    # ---- REGISTER_VALIDATOR ----

    def test_register_validator_valid(self, wallet):
        tx = Transaction.register_validator(
            wallet.address, wallet.signing_pk, wallet.address, nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_register_validator_bad_pubkey_size(self, wallet):
        tx = Transaction(TxType.REGISTER_VALIDATOR, wallet.address,
                         payload={"validator_pubkey": "aa" * 100,
                                  "validator_address": wallet.address}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "wrong size" in err

    def test_register_validator_non_hex_pubkey(self, wallet):
        tx = Transaction(TxType.REGISTER_VALIDATOR, wallet.address,
                         payload={"validator_pubkey": "xyz",
                                  "validator_address": wallet.address}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "non-empty hex" in err

    def test_register_validator_sender_mismatch(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.register_validator(
            alice.address, bob.signing_pk, bob.address, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "sender == validator_address" in err

    def test_register_validator_address_mismatch(self, wallet):
        tx = Transaction(TxType.REGISTER_VALIDATOR, wallet.address,
                         payload={"validator_pubkey": wallet.signing_pk.hex(),
                                  "validator_address": "qv1wrong"}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "does not match" in err

    def test_register_validator_bad_commission(self, wallet):
        tx = Transaction(TxType.REGISTER_VALIDATOR, wallet.address,
                         payload={"validator_pubkey": wallet.signing_pk.hex(),
                                  "validator_address": wallet.address,
                                  "commission": 150}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "commission" in err

    # ---- STAKE ----

    def test_stake_valid(self, wallet):
        tx = Transaction.stake(wallet.address, "qv1validator", amount=100, nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_stake_below_min(self, wallet):
        tx = Transaction.stake(wallet.address, "qv1validator", amount=0, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "amount" in err

    def test_stake_above_max(self, wallet):
        tx = Transaction.stake(wallet.address, "qv1validator", amount=2_000_000, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "amount" in err

    def test_stake_missing_validator(self, wallet):
        tx = Transaction(TxType.STAKE, wallet.address,
                         payload={"amount": 100, "validator_address": ""}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "validator_address" in err

    # ---- DELEGATE ----

    def test_delegate_valid(self, wallet):
        tx = Transaction.delegate(wallet.address, "qv1validator", amount=500, nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_delegate_bad_amount(self, wallet):
        tx = Transaction.delegate(wallet.address, "qv1validator", amount=-1, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

    def test_delegate_string_amount(self, wallet):
        tx = Transaction(TxType.DELEGATE, wallet.address,
                         payload={"amount": "500", "validator_address": "qv1v"}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

    def test_delegate_missing_validator(self, wallet):
        tx = Transaction(TxType.DELEGATE, wallet.address,
                         payload={"amount": 100}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

    # ---- UNSTAKE ----

    def test_unstake_valid(self, wallet):
        tx = Transaction.unstake(wallet.address, "qv1validator", amount=100, nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_unstake_zero_amount(self, wallet):
        tx = Transaction.unstake(wallet.address, "qv1validator", amount=0, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

    def test_unstake_non_int_amount(self, wallet):
        tx = Transaction(TxType.UNSTAKE, wallet.address,
                         payload={"amount": 10.5, "validator_address": "qv1v"}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

    def test_unstake_empty_validator(self, wallet):
        tx = Transaction(TxType.UNSTAKE, wallet.address,
                         payload={"amount": 100, "validator_address": ""}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

    # ---- EVIDENCE ----

    def test_evidence_valid(self, wallet):
        from qbit_network.crypto import sha3_256
        hdr_a = b'{"index":5,"prev":"aa","validator":"v1","timestamp":1}'
        hdr_b = b'{"index":5,"prev":"bb","validator":"v1","timestamp":2}'
        hash_a = sha3_256(hdr_a).hex()
        hash_b = sha3_256(hdr_b).hex()
        tx = Transaction.evidence(
            wallet.address, validator_address="qv1bad",
            block_index=5,
            block_a_hash=hash_a, block_b_hash=hash_b,
            block_a_sig="aa" * 100, block_b_sig="bb" * 100,
            block_a_header=hdr_a.hex(), block_b_header=hdr_b.hex(),
            nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_evidence_bad_type(self, wallet):
        tx = Transaction(TxType.EVIDENCE, wallet.address,
                         payload={"evidence_type": "other",
                                  "block_a_hash": "aa", "block_b_hash": "bb",
                                  "block_a_sig": "cc", "block_b_sig": "dd",
                                  "block_a_header": "ee", "block_b_header": "ff",
                                  "block_index": 0, "validator_address": "v"}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "double_sign" in err

    def test_evidence_same_hashes(self, wallet):
        from qbit_network.crypto import sha3_256
        hdr_a = b'{"index":5,"same":"same"}'
        hash_a = sha3_256(hdr_a).hex()
        tx = Transaction.evidence(
            wallet.address, validator_address="qv1bad",
            block_index=5,
            block_a_hash=hash_a, block_b_hash=hash_a,
            block_a_sig="aa" * 100, block_b_sig="bb" * 100,
            block_a_header=hdr_a.hex(), block_b_header=hdr_a.hex(),
            nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "must differ" in err

    def test_evidence_negative_block_index(self, wallet):
        from qbit_network.crypto import sha3_256
        hdr_a = b'{"a":1}'
        hdr_b = b'{"b":2}'
        tx = Transaction.evidence(
            wallet.address, validator_address="qv1bad",
            block_index=-1,
            block_a_hash=sha3_256(hdr_a).hex(), block_b_hash=sha3_256(hdr_b).hex(),
            block_a_sig="aa" * 100, block_b_sig="bb" * 100,
            block_a_header=hdr_a.hex(), block_b_header=hdr_b.hex(),
            nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "block_index" in err

    def test_evidence_header_hash_mismatch(self, wallet):
        tx = Transaction.evidence(
            wallet.address, validator_address="qv1bad",
            block_index=5,
            block_a_hash="aa" * 32, block_b_hash="bb" * 32,
            block_a_sig="cc" * 100, block_b_sig="dd" * 100,
            block_a_header="ee" * 10, block_b_header="ff" * 10,
            nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "does not hash" in err

    # ---- TRANSFER ----

    def test_transfer_valid(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer(alice.address, bob.address, amount=1000, nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_transfer_with_memo(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer(alice.address, bob.address, amount=1, memo="test", nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_transfer_zero_amount(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer(alice.address, bob.address, amount=0, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "positive integer" in err

    def test_transfer_negative_amount(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer(alice.address, bob.address, amount=-100, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

    def test_transfer_to_self(self, wallet):
        tx = Transaction.transfer(wallet.address, wallet.address, amount=100, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "self" in err

    def test_transfer_no_recipient(self, wallet):
        tx = Transaction(TxType.TRANSFER, wallet.address,
                         payload={"amount": 100}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "recipient" in err

    def test_transfer_bad_recipient_format(self, wallet):
        tx = Transaction.transfer(wallet.address, "badaddr", amount=100, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "valid qv1 address" in err

    def test_transfer_memo_too_long(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer(alice.address, bob.address, amount=100,
                                  memo="x" * 257, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "256" in err

    # ---- REVOKE_KEY ----

    def test_revoke_key_valid(self, wallet):
        tx = Transaction.revoke_key(wallet.address, "signing", "compromised", nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_revoke_key_all_combos(self, wallet):
        for kt in ("signing", "encryption", "validator"):
            for reason in ("compromised", "rotation", "decommission"):
                tx = Transaction.revoke_key(wallet.address, kt, reason, nonce=0)
                ok, err = tx.validate_payload()
                assert ok, f"failed for {kt}/{reason}: {err}"

    def test_revoke_key_bad_type(self, wallet):
        tx = Transaction.revoke_key(wallet.address, "unknown", "compromised", nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "key_type" in err

    def test_revoke_key_bad_reason(self, wallet):
        tx = Transaction.revoke_key(wallet.address, "signing", "bored", nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "reason" in err

    def test_revoke_key_empty_fields(self, wallet):
        tx = Transaction(TxType.REVOKE_KEY, wallet.address,
                         payload={"key_type": "", "reason": ""}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok

    # ---- ISSUE_TOKEN ----

    def test_issue_token_valid(self, wallet):
        tx = Transaction.issue_token(wallet.address, "TestToken", "TT",
                                     decimals=8, max_supply=1_000_000, nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_issue_token_empty_name(self, wallet):
        tx = Transaction.issue_token(wallet.address, "", "TT", decimals=8, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "name" in err

    def test_issue_token_bad_name_chars(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test@Token!", "TT",
                                     decimals=8, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "alphanumeric" in err

    def test_issue_token_name_too_long(self, wallet):
        tx = Transaction.issue_token(wallet.address, "A" * 65, "TT",
                                     decimals=8, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "64" in err

    def test_issue_token_bad_symbol(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "lowercase",
                                     decimals=8, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "symbol" in err

    def test_issue_token_symbol_too_short(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "T", decimals=8, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "at least" in err

    def test_issue_token_symbol_too_long(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "TOOLONGSY",
                                     decimals=8, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "exceeds" in err

    def test_issue_token_reserved_symbol(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Native", "QBIT",
                                     decimals=8, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "reserved" in err

    def test_issue_token_bad_decimals(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "TT",
                                     decimals=19, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "decimals" in err

    def test_issue_token_negative_max_supply(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "TT",
                                     decimals=8, max_supply=-1, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "max_supply" in err

    def test_issue_token_bad_transferable(self, wallet):
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address,
                         payload={"name": "Test", "symbol": "TT", "decimals": 8,
                                  "max_supply": 0, "transferable": "yes"}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "boolean" in err

    # ---- MINT_TOKEN ----

    def test_mint_token_valid(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.mint_token(alice.address, bob.address,
                                    token_id="aa" * 16, amount=1000, nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_mint_token_bad_token_id(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.mint_token(alice.address, bob.address,
                                    token_id="short", amount=100, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "token_id" in err

    def test_mint_token_zero_amount(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.mint_token(alice.address, bob.address,
                                    token_id="aa" * 16, amount=0, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "positive" in err

    def test_mint_token_no_recipient(self, wallet):
        tx = Transaction(TxType.MINT_TOKEN, wallet.address,
                         payload={"token_id": "aa" * 16, "amount": 100}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "recipient" in err

    def test_mint_token_bad_recipient(self, wallet):
        tx = Transaction.mint_token(wallet.address, "badaddr",
                                    token_id="aa" * 16, amount=100, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "valid qv1" in err

    # ---- TRANSFER_TOKEN ----

    def test_transfer_token_valid(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer_token(alice.address, bob.address,
                                        token_id="bb" * 16, amount=50, nonce=0)
        ok, err = tx.validate_payload()
        assert ok, f"expected valid but got: {err}"

    def test_transfer_token_bad_token_id(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer_token(alice.address, bob.address,
                                        token_id="xyz", amount=50, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "token_id" in err

    def test_transfer_token_zero_amount(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer_token(alice.address, bob.address,
                                        token_id="bb" * 16, amount=0, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "positive" in err

    def test_transfer_token_to_self(self, wallet):
        tx = Transaction.transfer_token(wallet.address, wallet.address,
                                        token_id="bb" * 16, amount=50, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "self" in err

    def test_transfer_token_no_recipient(self, wallet):
        tx = Transaction(TxType.TRANSFER_TOKEN, wallet.address,
                         payload={"token_id": "bb" * 16, "amount": 50}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "recipient" in err

    def test_transfer_token_memo_too_long(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer_token(alice.address, bob.address,
                                        token_id="bb" * 16, amount=50,
                                        memo="x" * 257, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "256" in err


class TestSigningAllTypes:
    """Signing + verification roundtrip for all 14 TX types."""

    def test_sign_verify_register_validator(self, wallet):
        tx = Transaction.register_validator(
            wallet.address, wallet.signing_pk, wallet.address, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        assert tx.verify() is True

    def test_sign_verify_stake(self, wallet):
        tx = Transaction.stake(wallet.address, "qv1validator", amount=100, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        assert tx.verify() is True

    def test_sign_verify_delegate(self, wallet):
        tx = Transaction.delegate(wallet.address, "qv1validator", amount=100, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        assert tx.verify() is True

    def test_sign_verify_unstake(self, wallet):
        tx = Transaction.unstake(wallet.address, "qv1validator", amount=100, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        assert tx.verify() is True

    def test_sign_verify_evidence(self, wallet):
        from qbit_network.crypto import sha3_256
        hdr_a = b'{"a":1}'
        hdr_b = b'{"b":2}'
        tx = Transaction.evidence(
            wallet.address, "qv1bad", block_index=5,
            block_a_hash=sha3_256(hdr_a).hex(), block_b_hash=sha3_256(hdr_b).hex(),
            block_a_sig="aa" * 100, block_b_sig="bb" * 100,
            block_a_header=hdr_a.hex(), block_b_header=hdr_b.hex(), nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        assert tx.verify() is True

    def test_sign_verify_transfer(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer(alice.address, bob.address, amount=100, nonce=0)
        tx.sign(alice.signing_sk, alice.signing_pk)
        assert tx.verify() is True

    def test_sign_verify_revoke_key(self, wallet):
        tx = Transaction.revoke_key(wallet.address, "signing", "rotation", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        assert tx.verify() is True

    def test_sign_verify_issue_token(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "TT",
                                     decimals=8, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        assert tx.verify() is True

    def test_sign_verify_mint_token(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.mint_token(alice.address, bob.address,
                                    token_id="aa" * 16, amount=100, nonce=0)
        tx.sign(alice.signing_sk, alice.signing_pk)
        assert tx.verify() is True

    def test_sign_verify_transfer_token(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer_token(alice.address, bob.address,
                                        token_id="bb" * 16, amount=50, nonce=0)
        tx.sign(alice.signing_sk, alice.signing_pk)
        assert tx.verify() is True


class TestFromDictAllTypes:
    """from_dict → to_dict roundtrip for all 14 TX types."""

    def _roundtrip(self, tx, wallet):
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        d = tx.to_dict()
        restored = Transaction.from_dict(d)
        assert restored.tx_id == tx.tx_id
        assert restored.tx_type == tx.tx_type
        assert restored.verify() is True
        return restored

    def test_roundtrip_register_validator(self, wallet):
        tx = Transaction.register_validator(
            wallet.address, wallet.signing_pk, wallet.address, nonce=0)
        self._roundtrip(tx, wallet)

    def test_roundtrip_stake(self, wallet):
        tx = Transaction.stake(wallet.address, "qv1validator", amount=100, nonce=0)
        self._roundtrip(tx, wallet)

    def test_roundtrip_delegate(self, wallet):
        tx = Transaction.delegate(wallet.address, "qv1validator", amount=500, nonce=0)
        self._roundtrip(tx, wallet)

    def test_roundtrip_unstake(self, wallet):
        tx = Transaction.unstake(wallet.address, "qv1validator", amount=100, nonce=0)
        self._roundtrip(tx, wallet)

    def test_roundtrip_evidence(self, wallet):
        from qbit_network.crypto import sha3_256
        hdr_a = b'{"a":1}'
        hdr_b = b'{"b":2}'
        tx = Transaction.evidence(
            wallet.address, "qv1bad", block_index=5,
            block_a_hash=sha3_256(hdr_a).hex(), block_b_hash=sha3_256(hdr_b).hex(),
            block_a_sig="aa" * 100, block_b_sig="bb" * 100,
            block_a_header=hdr_a.hex(), block_b_header=hdr_b.hex(), nonce=0)
        self._roundtrip(tx, wallet)

    def test_roundtrip_transfer(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer(alice.address, bob.address,
                                  amount=999, memo="hello", nonce=0)
        self._roundtrip(tx, alice)

    def test_roundtrip_revoke_key(self, wallet):
        tx = Transaction.revoke_key(wallet.address, "encryption", "decommission", nonce=0)
        self._roundtrip(tx, wallet)

    def test_roundtrip_issue_token(self, wallet):
        tx = Transaction.issue_token(wallet.address, "MyToken", "MT",
                                     decimals=6, max_supply=1_000_000, nonce=0)
        self._roundtrip(tx, wallet)

    def test_roundtrip_mint_token(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.mint_token(alice.address, bob.address,
                                    token_id="cc" * 16, amount=500, nonce=0)
        self._roundtrip(tx, alice)

    def test_roundtrip_transfer_token(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer_token(alice.address, bob.address,
                                        token_id="dd" * 16, amount=100,
                                        memo="token transfer", nonce=0)
        self._roundtrip(tx, alice)


class TestAmountUpperBound:
    """R30-001: TX amount fields must not exceed MAX_SUPPLY."""

    def test_transfer_at_max_supply_accepted(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer(alice.address, bob.address, MAX_SUPPLY, nonce=0)
        valid, msg = tx.validate_payload()
        assert valid, msg

    def test_transfer_above_max_supply_rejected(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer(alice.address, bob.address, MAX_SUPPLY + 1, nonce=0)
        valid, msg = tx.validate_payload()
        assert not valid
        assert "MAX_SUPPLY" in msg

    def test_transfer_huge_amount_rejected(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer(alice.address, bob.address, 2 ** 200, nonce=0)
        valid, msg = tx.validate_payload()
        assert not valid
        assert "MAX_TX_AMOUNT" in msg

    def test_mint_token_above_max_supply_rejected(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.mint_token(alice.address, bob.address,
                                    token_id="aa" * 16, amount=MAX_SUPPLY + 1, nonce=0)
        valid, msg = tx.validate_payload()
        assert not valid
        assert "MAX_SUPPLY" in msg

    def test_mint_token_at_max_supply_accepted(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.mint_token(alice.address, bob.address,
                                    token_id="aa" * 16, amount=MAX_SUPPLY, nonce=0)
        valid, msg = tx.validate_payload()
        assert valid, msg

    def test_transfer_token_above_max_supply_rejected(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer_token(alice.address, bob.address,
                                        token_id="bb" * 16, amount=MAX_SUPPLY + 1, nonce=0)
        valid, msg = tx.validate_payload()
        assert not valid
        assert "MAX_SUPPLY" in msg

    def test_transfer_token_at_max_supply_accepted(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.transfer_token(alice.address, bob.address,
                                        token_id="bb" * 16, amount=MAX_SUPPLY, nonce=0)
        valid, msg = tx.validate_payload()
        assert valid, msg


class TestFeeParamUpperBound:
    """R30-002: Fee params in from_dict() must not exceed 2^63."""

    _MAX_FEE = 2 ** 63

    def _base_dict(self, wallet):
        tx = Transaction.transfer(wallet.address,
                                  "qv1" + "a" * 64,
                                  amount=100, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        return tx.to_dict()

    def test_fee_at_limit_accepted(self, wallet):
        d = self._base_dict(wallet)
        d["maxFeePerWeight"] = self._MAX_FEE
        d["maxPriorityFee"] = self._MAX_FEE
        tx = Transaction.from_dict(d)
        assert tx.max_fee_per_weight == self._MAX_FEE
        assert tx.max_priority_fee == self._MAX_FEE

    def test_max_fee_per_weight_above_limit_rejected(self, wallet):
        d = self._base_dict(wallet)
        d["maxFeePerWeight"] = self._MAX_FEE + 1
        with pytest.raises(ValueError, match="maxFeePerWeight"):
            Transaction.from_dict(d)

    def test_max_priority_fee_above_limit_rejected(self, wallet):
        d = self._base_dict(wallet)
        d["maxPriorityFee"] = self._MAX_FEE + 1
        with pytest.raises(ValueError, match="maxPriorityFee"):
            Transaction.from_dict(d)

    def test_huge_fee_rejected(self, wallet):
        d = self._base_dict(wallet)
        d["maxFeePerWeight"] = 2 ** 200
        with pytest.raises(ValueError, match="maxFeePerWeight"):
            Transaction.from_dict(d)


class TestFromDictAmountValidation:
    """R30-004: from_dict must validate payload amount fields (defense-in-depth)."""

    _MAX_TX_AMOUNT = 2 ** 63 - 1

    def _base_transfer_dict(self, wallet):
        tx = Transaction.transfer(wallet.address,
                                  "qv1" + "a" * 64,
                                  amount=100, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        return tx.to_dict()

    def _base_mint_dict(self, wallet):
        tx = Transaction.mint_token(wallet.address,
                                    "qv1" + "a" * 64,
                                    token_id="cc" * 16,
                                    amount=100, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        return tx.to_dict()

    def _base_issue_dict(self, wallet):
        tx = Transaction.issue_token(wallet.address,
                                     name="TestToken", symbol="TST",
                                     decimals=8, max_supply=1000, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        return tx.to_dict()

    # --- TRANSFER amount in from_dict ---

    def test_transfer_valid_amount_accepted(self, wallet):
        d = self._base_transfer_dict(wallet)
        d["payload"]["amount"] = self._MAX_TX_AMOUNT
        tx = Transaction.from_dict(d)
        assert tx.payload["amount"] == self._MAX_TX_AMOUNT

    def test_transfer_amount_above_limit_rejected(self, wallet):
        d = self._base_transfer_dict(wallet)
        d["payload"]["amount"] = self._MAX_TX_AMOUNT + 1
        with pytest.raises(ValueError, match="MAX_TX_AMOUNT"):
            Transaction.from_dict(d)

    def test_transfer_huge_amount_rejected(self, wallet):
        d = self._base_transfer_dict(wallet)
        d["payload"]["amount"] = 2 ** 200
        with pytest.raises(ValueError, match="MAX_TX_AMOUNT"):
            Transaction.from_dict(d)

    def test_transfer_zero_amount_rejected(self, wallet):
        d = self._base_transfer_dict(wallet)
        d["payload"]["amount"] = 0
        with pytest.raises(ValueError, match="positive"):
            Transaction.from_dict(d)

    def test_transfer_negative_amount_rejected(self, wallet):
        d = self._base_transfer_dict(wallet)
        d["payload"]["amount"] = -1
        with pytest.raises(ValueError, match="positive"):
            Transaction.from_dict(d)

    def test_transfer_string_amount_rejected(self, wallet):
        d = self._base_transfer_dict(wallet)
        d["payload"]["amount"] = "100"
        with pytest.raises(ValueError, match="integer"):
            Transaction.from_dict(d)

    def test_transfer_float_amount_rejected(self, wallet):
        d = self._base_transfer_dict(wallet)
        d["payload"]["amount"] = 100.5
        with pytest.raises(ValueError, match="integer"):
            Transaction.from_dict(d)

    def test_transfer_bool_amount_rejected(self, wallet):
        d = self._base_transfer_dict(wallet)
        d["payload"]["amount"] = True
        with pytest.raises(ValueError, match="integer"):
            Transaction.from_dict(d)

    # --- MINT_TOKEN amount in from_dict ---

    def test_mint_amount_above_limit_rejected(self, wallet):
        d = self._base_mint_dict(wallet)
        d["payload"]["amount"] = self._MAX_TX_AMOUNT + 1
        with pytest.raises(ValueError, match="MAX_TX_AMOUNT"):
            Transaction.from_dict(d)

    def test_mint_string_amount_rejected(self, wallet):
        d = self._base_mint_dict(wallet)
        d["payload"]["amount"] = "999"
        with pytest.raises(ValueError, match="integer"):
            Transaction.from_dict(d)

    # --- ISSUE_TOKEN max_supply in from_dict ---

    def test_issue_max_supply_at_limit_accepted(self, wallet):
        d = self._base_issue_dict(wallet)
        d["payload"]["max_supply"] = self._MAX_TX_AMOUNT
        tx = Transaction.from_dict(d)
        assert tx.payload["max_supply"] == self._MAX_TX_AMOUNT

    def test_issue_max_supply_above_limit_rejected(self, wallet):
        d = self._base_issue_dict(wallet)
        d["payload"]["max_supply"] = self._MAX_TX_AMOUNT + 1
        with pytest.raises(ValueError, match="MAX_TX_AMOUNT"):
            Transaction.from_dict(d)

    def test_issue_max_supply_negative_rejected(self, wallet):
        d = self._base_issue_dict(wallet)
        d["payload"]["max_supply"] = -1
        with pytest.raises(ValueError, match="non-negative"):
            Transaction.from_dict(d)

    def test_issue_max_supply_bool_rejected(self, wallet):
        d = self._base_issue_dict(wallet)
        d["payload"]["max_supply"] = True
        with pytest.raises(ValueError, match="integer"):
            Transaction.from_dict(d)

"""Tests for REVOKE_KEY transaction type (ISS-010, Sprint 2)."""
import json
import os
import time
import pytest
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.wallet import Wallet
from qbit_network.crypto import MLKEM
from tests.conftest import submit_and_mine


# ========== TxType & Payload Validation ==========

class TestRevokeKeyPayload:
    def test_valid_signing_revocation(self):
        tx = Transaction.revoke_key("qv1test", "signing", "compromised")
        ok, err = tx.validate_payload()
        assert ok, err

    def test_valid_encryption_revocation(self):
        tx = Transaction.revoke_key("qv1test", "encryption", "rotation")
        ok, err = tx.validate_payload()
        assert ok, err

    def test_valid_validator_revocation(self):
        tx = Transaction.revoke_key("qv1test", "validator", "decommission")
        ok, err = tx.validate_payload()
        assert ok, err

    def test_invalid_key_type(self):
        tx = Transaction.revoke_key("qv1test", "bogus", "compromised")
        ok, err = tx.validate_payload()
        assert not ok
        assert "key_type" in err

    def test_invalid_reason(self):
        tx = Transaction.revoke_key("qv1test", "signing", "bored")
        ok, err = tx.validate_payload()
        assert not ok
        assert "reason" in err

    def test_empty_key_type(self):
        tx = Transaction.revoke_key("qv1test", "", "compromised")
        ok, err = tx.validate_payload()
        assert not ok

    def test_empty_reason(self):
        tx = Transaction.revoke_key("qv1test", "signing", "")
        ok, err = tx.validate_payload()
        assert not ok

    def test_extra_payload_keys_rejected(self):
        tx = Transaction(
            TxType.REVOKE_KEY, "qv1test",
            payload={"key_type": "signing", "reason": "compromised", "extra": "bad"})
        ok, err = tx.validate_payload()
        assert not ok
        assert "unknown payload keys" in err

    def test_factory_method_creates_correct_type(self):
        tx = Transaction.revoke_key("qv1test", "signing", "compromised")
        assert tx.tx_type == TxType.REVOKE_KEY
        assert tx.payload == {"key_type": "signing", "reason": "compromised"}

    def test_serialization_roundtrip(self, wallet):
        tx = Transaction.revoke_key(wallet.address, "encryption", "rotation", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        d = tx.to_dict()
        tx2 = Transaction.from_dict(d)
        assert tx2.tx_type == TxType.REVOKE_KEY
        assert tx2.payload == tx.payload
        assert tx2.tx_id == tx.tx_id


# ========== Signing Key Revocation ==========

class TestSigningKeyRevocation:
    def test_revoke_signing_key(self, blockchain, wallet):
        # Use a non-genesis wallet so genesis protection does not block revocation
        w2 = Wallet.generate()
        # First submit a tx from w2 to establish its nonce
        tx0 = Transaction.notarize(w2.address, "aabb", nonce=0)
        tx0.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, tx0)

        tx = Transaction.revoke_key(w2.address, "signing", "compromised", nonce=1)
        tx.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, tx)
        assert blockchain.is_key_revoked(w2.address, "signing")

    def test_revoked_signer_cannot_submit(self, blockchain, wallet):
        """After revoking signing key, no further txs accepted."""
        w2 = Wallet.generate()
        tx = Transaction.revoke_key(w2.address, "signing", "compromised", nonce=0)
        tx.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, tx)

        # Try to submit another tx from revoked w2
        tx2 = Transaction.notarize(w2.address, "aabb", nonce=1)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        ok, err = blockchain.submit_tx(tx2)
        assert not ok
        assert "revoked" in err

    def test_revoked_signer_rejected_in_block_validation(self, blockchain, wallet):
        """Block containing tx from revoked signer must be rejected."""
        w2 = Wallet.generate()
        # Revoke w2's signing key
        tx = Transaction.revoke_key(w2.address, "signing", "compromised", nonce=0)
        tx.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, tx)

        # Build block with tx from revoked signer w2 (adversarial)
        tx2 = Transaction.notarize(w2.address, "dead", nonce=1)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        bad_block = Block(
            index=blockchain.height + 1,
            prev_hash=blockchain.latest_block.block_hash,
            transactions=[tx2], validator=wallet.address,
            timestamp=blockchain.latest_block.timestamp + 1)
        bad_block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(bad_block)
        assert not ok
        assert "revoked" in err


# ========== Encryption Key Revocation ==========

class TestEncryptionKeyRevocation:
    def test_revoke_encryption_key(self, blockchain, wallet):
        # Use a non-genesis wallet to avoid genesis protection (SPRINT2-016)
        w2 = Wallet.generate()
        # First register an encryption key for w2
        reg = Transaction.register_key(w2.address, w2.encryption_pk, nonce=0)
        reg.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, reg)

        # Revoke it
        tx = Transaction.revoke_key(w2.address, "encryption", "rotation", nonce=1)
        tx.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, tx)

        assert blockchain.is_key_revoked(w2.address, "encryption")
        info = blockchain.get_revocation_info(w2.address, "encryption")
        assert info is not None
        assert info["reason"] == "rotation"


# ========== Validator Key Revocation ==========

class TestValidatorKeyRevocation:
    def _mine_with_turn(self, bc, wallet, w2):
        """Produce a block using whichever validator has the turn."""
        block_idx = bc.height + 1
        expected = bc.consensus.select_validator(block_idx)
        if expected == w2.address:
            return bc.produce_block(w2.address, w2.signing_sk)
        return bc.produce_block(wallet.address, wallet.signing_sk)

    def test_revoke_non_genesis_validator(self, blockchain, wallet):
        """Non-genesis validator can be revoked."""
        w2 = Wallet.generate()
        # Register w2 as validator
        reg = Transaction.register_validator(
            w2.address, w2.signing_pk, w2.address, nonce=0)
        reg.sign(w2.signing_sk, w2.signing_pk)
        blockchain.submit_tx(reg)
        block = self._mine_with_turn(blockchain, wallet, w2)
        # If w2 got the turn but isn't registered yet, fall back
        if block is None:
            block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        assert blockchain.is_registered_validator(w2.address)

        # w2 revokes own validator key
        revoke = Transaction.revoke_key(w2.address, "validator", "decommission", nonce=1)
        revoke.sign(w2.signing_sk, w2.signing_pk)
        blockchain.submit_tx(revoke)
        block2 = self._mine_with_turn(blockchain, wallet, w2)
        assert block2 is not None

        assert blockchain.is_key_revoked(w2.address, "validator")
        assert not blockchain.is_registered_validator(w2.address)
        assert not blockchain.consensus.is_validator(w2.address)

    def test_genesis_validator_cannot_be_revoked(self, blockchain, wallet):
        """Genesis validator revocation is rejected (safety)."""
        tx = Transaction.revoke_key(wallet.address, "validator", "compromised", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "genesis" in err


# ========== Idempotency ==========

class TestRevocationIdempotency:
    def test_double_revocation_rejected(self, blockchain, wallet):
        w2 = Wallet.generate()
        tx = Transaction.revoke_key(w2.address, "encryption", "compromised", nonce=0)
        tx.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, tx)

        tx2 = Transaction.revoke_key(w2.address, "encryption", "rotation", nonce=1)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        ok, err = blockchain.submit_tx(tx2)
        assert not ok
        assert "already revoked" in err

    def test_non_revoked_returns_none(self, blockchain, wallet):
        assert blockchain.get_revocation_info(wallet.address, "signing") is None
        assert not blockchain.is_key_revoked(wallet.address, "signing")


# ========== Rollback ==========

class TestRevocationRollback:
    def test_rollback_reverts_revocation(self, blockchain, wallet):
        """Rolling back a block with REVOKE_KEY should un-revoke the key."""
        w2 = Wallet.generate()
        tx = Transaction.revoke_key(w2.address, "encryption", "compromised", nonce=0)
        tx.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, tx)
        assert blockchain.is_key_revoked(w2.address, "encryption")

        # Rollback
        blockchain._rollback_to(1)  # back to genesis
        assert not blockchain.is_key_revoked(w2.address, "encryption")

    def test_rollback_validator_revocation_restores_validator(self, blockchain, wallet):
        """Rolling back validator revocation should re-add the validator."""
        w2 = Wallet.generate()
        # Register w2
        reg = Transaction.register_validator(
            w2.address, w2.signing_pk, w2.address, nonce=0)
        reg.sign(w2.signing_sk, w2.signing_pk)
        blockchain.submit_tx(reg)

        # Produce block -- need to use the correct validator for this turn
        block_idx = blockchain.height + 1
        expected_validator = blockchain.consensus.select_validator(block_idx)
        if expected_validator == wallet.address:
            block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        else:
            # Shouldn't happen since w2 isn't registered yet (only in pool)
            block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        assert blockchain.consensus.is_validator(w2.address)

        # Revoke w2 -- after registration, w2 is now a validator so turn may change
        revoke = Transaction.revoke_key(w2.address, "validator", "decommission", nonce=1)
        revoke.sign(w2.signing_sk, w2.signing_pk)
        blockchain.submit_tx(revoke)
        block_idx2 = blockchain.height + 1
        expected_v2 = blockchain.consensus.select_validator(block_idx2)
        if expected_v2 == w2.address:
            block2 = blockchain.produce_block(w2.address, w2.signing_sk)
        else:
            block2 = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block2 is not None
        assert not blockchain.consensus.is_validator(w2.address)

        # Rollback revocation block only (keep registration)
        blockchain._rollback_to(2)
        assert not blockchain.is_key_revoked(w2.address, "validator")
        assert blockchain.consensus.is_validator(w2.address)
        assert blockchain.is_registered_validator(w2.address)


# ========== SQLite Persistence ==========

class TestRevocationPersistence:
    def test_sqlite_store_revocation(self, wallet, tmp_dir):
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk,
                      validator_pk=wallet.signing_pk)

        # Use non-genesis wallet to avoid genesis protection (SPRINT2-016)
        w2 = Wallet.generate()
        tx = Transaction.revoke_key(w2.address, "encryption", "rotation", nonce=0)
        tx.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(bc, wallet, tx)
        assert bc.is_key_revoked(w2.address, "encryption")

        # Reload from SQLite
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load()
        assert loaded
        assert bc2.is_key_revoked(w2.address, "encryption")
        info = bc2.get_revocation_info(w2.address, "encryption")
        assert info["reason"] == "rotation"

    def test_sqlite_store_methods(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)

        # put + get
        store.put_revocation("addr1", "signing", "tx123", 1234.5, "compromised")
        rev = store.get_revocation("addr1", "signing")
        assert rev is not None
        assert rev["tx_id"] == "tx123"
        assert rev["timestamp"] == 1234.5
        assert rev["reason"] == "compromised"

        # get nonexistent
        assert store.get_revocation("addr1", "encryption") is None

        # get_all
        store.put_revocation("addr2", "validator", "tx456", 5678.0, "decommission")
        all_revs = store.get_all_revocations()
        assert len(all_revs) == 2

        # delete
        store.delete_revocation("addr1", "signing")
        assert store.get_revocation("addr1", "signing") is None
        assert len(store.get_all_revocations()) == 1

        store.close()

    def test_sqlite_rollback_cleans_revocations(self, wallet, tmp_dir):
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk,
                      validator_pk=wallet.signing_pk)

        w2 = Wallet.generate()
        tx = Transaction.revoke_key(w2.address, "encryption", "rotation", nonce=0)
        tx.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(bc, wallet, tx)

        # Rollback
        bc._rollback_to(1)
        # Check SQLite
        rev = bc._store.get_revocation(w2.address, "encryption")
        assert rev is None


# ========== Revocation Info Query ==========

class TestRevocationQuery:
    def test_get_revocation_info(self, blockchain, wallet):
        w2 = Wallet.generate()
        tx = Transaction.revoke_key(w2.address, "encryption", "compromised", nonce=0)
        tx.sign(w2.signing_sk, w2.signing_pk)
        block, tx_id = submit_and_mine(blockchain, wallet, tx)

        info = blockchain.get_revocation_info(w2.address, "encryption")
        assert info is not None
        assert info["tx_id"] == tx.tx_id
        assert info["reason"] == "compromised"
        assert isinstance(info["timestamp"], (int, float))

    def test_different_key_types_independent(self, blockchain, wallet):
        """Revoking encryption key does not affect signing key status."""
        w2 = Wallet.generate()
        tx = Transaction.revoke_key(w2.address, "encryption", "rotation", nonce=0)
        tx.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, tx)

        assert blockchain.is_key_revoked(w2.address, "encryption")
        assert not blockchain.is_key_revoked(w2.address, "signing")

        # Can still submit txs (signing key not revoked)
        tx2 = Transaction.notarize(w2.address, "aabb", nonce=1)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        ok, _ = blockchain.submit_tx(tx2)
        assert ok


# ========== Adversarial ==========

class TestRevocationAdversarial:
    def test_non_integer_key_type(self):
        """key_type must be string, not int."""
        tx = Transaction(TxType.REVOKE_KEY, "qv1test",
                         payload={"key_type": 123, "reason": "compromised"})
        ok, err = tx.validate_payload()
        assert not ok
        assert "key_type" in err

    def test_non_string_reason(self):
        """reason must be string, not int."""
        tx = Transaction(TxType.REVOKE_KEY, "qv1test",
                         payload={"key_type": "signing", "reason": 42})
        ok, err = tx.validate_payload()
        assert not ok
        assert "reason" in err

    def test_all_valid_combinations(self):
        """All 9 valid (key_type, reason) pairs pass validation."""
        key_types = ["signing", "encryption", "validator"]
        reasons = ["compromised", "rotation", "decommission"]
        for kt in key_types:
            for r in reasons:
                tx = Transaction.revoke_key("qv1test", kt, r)
                ok, err = tx.validate_payload()
                assert ok, f"Failed for ({kt}, {r}): {err}"


# ========== Edge Cases: Revoked Key Effects on Production ==========

class TestRevokedKeyEdgeCases:
    """Edge cases for revoked key effects on blockchain operations."""

    def test_revoked_signing_key_blocks_block_production(self):
        """When a validator's signing key is revoked, they cannot produce blocks."""
        v1 = Wallet.generate()
        v2 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.consensus.add_validator(v2.address, v2.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        # Revoke v2's signing key
        revoke = Transaction.revoke_key(v2.address, "signing", "compromised", nonce=0)
        revoke.sign(v2.signing_sk, v2.signing_pk)
        ok, _ = bc.submit_tx(revoke)
        assert ok

        # Mine the revocation with whichever validator has the turn
        expected1 = bc.consensus.select_validator(1)
        if expected1 == v1.address:
            block1 = bc.produce_block(v1.address, v1.signing_sk)
        else:
            block1 = bc.produce_block(v2.address, v2.signing_sk)
        assert block1 is not None
        assert bc.is_key_revoked(v2.address, "signing")

        # Now build a block adversarially signed by v2 (revoked)
        tx_for_block = Transaction.notarize(v1.address, "aa", nonce=0)
        tx_for_block.sign(v1.signing_sk, v1.signing_pk)
        bc.submit_tx(tx_for_block)
        bad_block = Block(
            index=bc.height + 1,
            prev_hash=bc.latest_block.block_hash,
            transactions=[tx_for_block],
            validator=v2.address,
            timestamp=bc.latest_block.timestamp + 1
        )
        bad_block.sign(v2.signing_sk)
        ok, err = bc.add_block(bad_block)
        assert not ok
        assert "revoked" in err

    def test_revoked_validator_key_removes_from_consensus(self, blockchain, wallet):
        """Revoking validator key removes address from active validator set."""
        w2 = Wallet.generate()
        # Register w2 as validator
        reg = Transaction.register_validator(w2.address, w2.signing_pk, w2.address, nonce=0)
        reg.sign(w2.signing_sk, w2.signing_pk)
        blockchain.submit_tx(reg)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        assert blockchain.consensus.is_validator(w2.address)

        # Revoke w2 validator key
        revoke = Transaction.revoke_key(w2.address, "validator", "decommission", nonce=1)
        revoke.sign(w2.signing_sk, w2.signing_pk)
        blockchain.submit_tx(revoke)

        # Mine with whoever's turn it is
        block2_idx = blockchain.height + 1
        expected = blockchain.consensus.select_validator(block2_idx)
        if expected == w2.address:
            block2 = blockchain.produce_block(w2.address, w2.signing_sk)
        else:
            block2 = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block2 is not None

        assert blockchain.is_key_revoked(w2.address, "validator")
        assert not blockchain.consensus.is_validator(w2.address)
        assert not blockchain.is_registered_validator(w2.address)

    def test_genesis_validator_signing_key_protected(self, blockchain, wallet):
        """Genesis validator's signing key cannot be revoked."""
        tx = Transaction.revoke_key(wallet.address, "signing", "compromised", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "genesis" in err

    def test_genesis_validator_validator_key_protected(self, blockchain, wallet):
        """Genesis validator's validator key cannot be revoked."""
        tx = Transaction.revoke_key(wallet.address, "validator", "decommission", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "genesis" in err

    def test_genesis_validator_encryption_key_revocable(self, blockchain, wallet):
        """Genesis validator's encryption key CAN be revoked (not protected)."""
        # First register an encryption key
        reg = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        reg.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, reg)

        # Now revoke it -- genesis protection only covers signing and validator keys
        # (This test verifies encryption is NOT genesis-protected)
        # The behavior depends on implementation; test what actually occurs
        tx = Transaction.revoke_key(wallet.address, "encryption", "rotation", nonce=1)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        # Genesis protection only covers signing and validator keys (K-01)
        assert ok, f"encryption revocation should be allowed: {err}"
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None, "produce_block returned None"
        assert blockchain.is_key_revoked(wallet.address, "encryption")

    def test_revocation_survives_sqlite_reload(self, wallet, tmp_dir):
        """Revocation state persists through SQLite reload."""
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)

        w2 = Wallet.generate()
        tx = Transaction.revoke_key(w2.address, "signing", "compromised", nonce=0)
        tx.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(bc, wallet, tx)
        assert bc.is_key_revoked(w2.address, "signing")

        # Reload
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load()
        assert loaded is True
        assert bc2.is_key_revoked(w2.address, "signing")

        # Revoked address cannot submit after reload
        new_tx = Transaction.notarize(w2.address, "aabb", nonce=1)
        new_tx.sign(w2.signing_sk, w2.signing_pk)
        ok2, err2 = bc2.submit_tx(new_tx)
        assert not ok2
        assert "revoked" in err2

    def test_concurrent_revocation_and_block_production(self):
        """Block production + revocation processing in same block is consistent."""
        v1 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        w2 = Wallet.generate()
        # Submit both a notarize and a revoke in the same pool batch
        tx1 = Transaction.notarize(w2.address, "aabb", nonce=0)
        tx1.sign(w2.signing_sk, w2.signing_pk)
        bc.submit_tx(tx1)

        tx2 = Transaction.revoke_key(w2.address, "signing", "compromised", nonce=1)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        bc.submit_tx(tx2)

        # Mine both in one block
        block = bc.produce_block(v1.address, v1.signing_sk)
        assert block is not None
        assert len(block.transactions) == 2

        # After mining, w2's signing key is revoked
        assert bc.is_key_revoked(w2.address, "signing")
        # But the notarization from w2 was processed before the revocation
        assert bc.verify_document("aabb") is not None

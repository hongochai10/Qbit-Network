"""Tests for qbit_network.core.blockchain — chain ops, consensus, persistence."""
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


# ========== Genesis & Init ==========

class TestGenesis:
    def test_init_chain(self, blockchain, wallet):
        assert blockchain.height == 0
        assert blockchain.chain[0].index == 0
        assert blockchain.chain[0].validator == wallet.address

    def test_double_init_noop(self, blockchain, wallet):
        blockchain.init_chain(wallet.address, wallet.signing_sk)
        assert blockchain.height == 0  # still 0, not 1


# ========== Transaction Pool ==========

class TestSubmitTx:
    def test_valid_tx(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, tx_id = blockchain.submit_tx(tx)
        assert ok
        assert tx_id == tx.tx_id

    def test_invalid_signature(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        # not signed
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "invalid signature" in err

    def test_duplicate_in_pool(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "already in pool" in err

    def test_wrong_nonce(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=99)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "invalid nonce" in err

    def test_future_timestamp_rejected(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.timestamp = int(time.time()) + 999999
        tx._cached_signable = None
        tx._cached_id = None
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "future" in err

    def test_old_timestamp_rejected(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.timestamp = int(time.time()) - 100000
        tx._cached_signable = None
        tx._cached_id = None
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "old" in err

    def test_share_requires_recipient(self, blockchain, wallet):
        tx = Transaction(TxType.SHARE, wallet.address,
                         payload={"cid": "Qm", "encapsulatedKey": "aabb", "expires": 0},
                         nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "recipient" in err

    def test_pool_limit(self, blockchain, wallet):
        from qbit_network.config import MAX_TX_POOL_SIZE
        for i in range(MAX_TX_POOL_SIZE):
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            blockchain.submit_tx(tx)
        tx = Transaction.notarize(wallet.address, "ff" * 32, nonce=MAX_TX_POOL_SIZE)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "pool full" in err


# ========== Block Production ==========

class TestProduceBlock:
    def test_produce_with_transactions(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        assert block.index == 1
        assert len(block.transactions) == 1
        assert len(blockchain.tx_pool) == 0

    def test_produce_empty_pool_returns_none(self, blockchain, wallet):
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is None

    def test_produce_timestamp_monotonic(self, blockchain, wallet):
        tx1 = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx1)
        b1 = blockchain.produce_block(wallet.address, wallet.signing_sk)

        tx2 = Transaction.notarize(wallet.address, "bb", nonce=1)
        tx2.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx2)
        b2 = blockchain.produce_block(wallet.address, wallet.signing_sk)

        assert b2.timestamp > b1.timestamp

    def test_produce_self_validates(self, blockchain, wallet):
        """Self-produced block must pass consensus validation."""
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        assert block.verify_signature(wallet.signing_pk)


# ========== Block Reception ==========

class TestAddBlock:
    def test_out_of_order_rejected(self, blockchain, wallet):
        fake = Block(index=5, prev_hash="ff" * 32,
                     transactions=[], validator=wallet.address,
                     timestamp=blockchain.chain[0].timestamp + 1)
        fake.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(fake)
        assert not ok
        assert "out-of-order" in err

    def test_cross_block_tx_replay_rejected(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx)

        replay_block = Block(
            index=blockchain.height + 1,
            prev_hash=blockchain.latest_block.block_hash,
            transactions=[tx], validator=wallet.address,
            timestamp=blockchain.latest_block.timestamp + 1)
        replay_block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(replay_block)
        assert not ok
        assert "already in chain" in err

    def test_empty_block_rejected(self, blockchain, wallet):
        empty = Block(index=1, prev_hash=blockchain.chain[0].block_hash,
                      transactions=[], validator=wallet.address,
                      timestamp=blockchain.chain[0].timestamp + 1)
        empty.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(empty)
        assert not ok
        assert "at least one" in err


# ========== Nonce Tracking ==========

class TestNonce:
    def test_nonce_increments(self, blockchain, wallet):
        assert blockchain.get_nonce(wallet.address) == 0
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx)
        assert blockchain.get_nonce(wallet.address) == 1

    def test_nonce_replay_in_block_rejected(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx)

        replay = Transaction.notarize(wallet.address, "bb", nonce=0)
        replay.sign(wallet.signing_sk, wallet.signing_pk)
        bad_block = Block(
            index=blockchain.height + 1,
            prev_hash=blockchain.latest_block.block_hash,
            transactions=[replay], validator=wallet.address,
            timestamp=blockchain.latest_block.timestamp + 1)
        bad_block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(bad_block)
        assert not ok
        assert "nonce" in err


# ========== Notarization ==========

class TestNotarization:
    def test_verify_document(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "deadbeef", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx)
        result = blockchain.verify_document("deadbeef")
        assert result is not None
        assert result["sender"] == wallet.address

    def test_first_notarization_preserved(self, blockchain, wallet):
        w2 = Wallet.generate()
        tx1 = Transaction.notarize(wallet.address, "aabbccdd11223344", nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx1)

        tx2 = Transaction.notarize(w2.address, "aabbccdd11223344", nonce=0)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, tx2)

        result = blockchain.verify_document("aabbccdd11223344")
        assert result["sender"] == wallet.address  # first notarizer wins

    def test_get_all_notarizations(self, blockchain, wallet):
        w2 = Wallet.generate()
        tx1 = Transaction.notarize(wallet.address, "ff00ff00aabbccdd", nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx1)

        tx2 = Transaction.notarize(w2.address, "ff00ff00aabbccdd", nonce=0)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, tx2)

        all_n = blockchain.get_all_notarizations("ff00ff00aabbccdd")
        assert len(all_n) == 2


# ========== Key Registry ==========

class TestKeyRegistry:
    def test_register_and_lookup(self, blockchain, wallet):
        tx = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx)
        assert blockchain.get_encryption_pk(wallet.address) == wallet.encryption_pk.hex()

    def test_key_history_preserved(self, blockchain, wallet):
        tx1 = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx1)

        _, new_pk = MLKEM.keygen()
        tx2 = Transaction.register_key(wallet.address, new_pk, nonce=1)
        tx2.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx2)

        assert len(blockchain._key_history[wallet.address]) == 2
        assert blockchain.get_encryption_pk(wallet.address) == new_pk.hex()


# ========== SHARE Expiry ==========

class TestShareExpiry:
    def test_expired_share_filtered(self, blockchain, wallet):
        w2 = Wallet.generate()
        ct, _ = MLKEM.encapsulate(w2.encryption_pk)
        tx = Transaction.share(wallet.address, w2.address, "QmX", ct, expires=1, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx)
        shares = blockchain.get_shared_with(w2.address)
        assert len(shares) == 0  # expired (1970)

    def test_active_share_returned(self, blockchain, wallet):
        w2 = Wallet.generate()
        ct, _ = MLKEM.encapsulate(w2.encryption_pk)
        future = int(time.time()) + 86400
        tx = Transaction.share(wallet.address, w2.address, "QmX", ct,
                               expires=future, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx)
        shares = blockchain.get_shared_with(w2.address)
        assert len(shares) == 1


# ========== Persistence ==========

class TestPersistence:
    def test_save_and_load(self, blockchain_on_disk, wallet, tmp_dir):
        bc = blockchain_on_disk
        tx = Transaction.notarize(wallet.address, "aabbccddee112233", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)
        bc.save()

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load()
        assert loaded is True
        assert bc2.height == 1
        assert bc2.verify_document("aabbccddee112233") is not None

    def test_corrupt_chain_json(self, tmp_dir):
        with open(os.path.join(tmp_dir, "chain.json"), "w") as f:
            f.write("{bad json")
        bc = Blockchain(data_dir=tmp_dir)
        assert bc.load() is False

    def test_empty_chain_json(self, tmp_dir):
        with open(os.path.join(tmp_dir, "chain.json"), "w") as f:
            json.dump([], f)
        bc = Blockchain(data_dir=tmp_dir)
        assert bc.load() is False

    def test_double_load_skips(self, blockchain_on_disk, wallet, tmp_dir):
        blockchain_on_disk.save()
        assert blockchain_on_disk.load() is True  # already loaded, returns True
        assert blockchain_on_disk.height == 0

    def test_tampered_tx_sig_rejected(self, wallet, tmp_dir):
        """Test that tampered chain data is rejected — via SQLite load path."""
        # Build valid chain and persist to SQLite
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        tx = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(wallet.address, wallet.signing_sk)
        # chain.db now has valid blocks via dual-write

        # Tamper the SQLite DB directly
        import sqlite3
        db_path = os.path.join(tmp_dir, "chain.db")
        conn = sqlite3.connect(db_path)
        # Corrupt block 1's data by changing a tx signature in the stored JSON
        row = conn.execute("SELECT data FROM blocks WHERE idx=1").fetchone()
        block_data = json.loads(row[0])
        block_data["transactions"][0]["signature"] = "ff" * 3309
        conn.execute("UPDATE blocks SET data=? WHERE idx=1",
                     (json.dumps(block_data, separators=(",", ":")),))
        conn.commit()
        conn.close()

        # Reload — should detect invalid signature on load (R24-004)
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="invalid signature"):
            bc2.load()


# ========== REGISTER_VALIDATOR ==========

class TestRegisterValidator:
    """Tests for REGISTER_VALIDATOR transaction type and on-chain validator registry."""

    def test_valid_validator_registration(self, blockchain, wallet):
        """A properly formed REGISTER_VALIDATOR tx registers the address."""
        w2 = Wallet.generate()
        reg = Transaction.register_validator(w2.address, w2.signing_pk, w2.address, nonce=0)
        reg.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, reg)
        assert blockchain.is_registered_validator(w2.address)
        assert blockchain.consensus.is_validator(w2.address)

    def test_address_derivation_mismatch_rejected(self, blockchain, wallet):
        """REGISTER_VALIDATOR with validator_address that doesn't match pubkey is rejected."""
        w2 = Wallet.generate()
        w3 = Wallet.generate()
        # Use w2's pubkey but claim w3's address
        tx = Transaction(
            TxType.REGISTER_VALIDATOR, w2.address,
            payload={"validator_pubkey": w2.signing_pk.hex(),
                     "validator_address": w3.address},  # mismatch!
            nonce=0
        )
        tx.sign(w2.signing_sk, w2.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        # Validation rejects address mismatch
        assert "validator_address" in err or "payload" in err

    def test_duplicate_registration_rejected_first_wins(self, blockchain, wallet):
        """Second REGISTER_VALIDATOR for same address is silently ignored (first-wins)."""
        w2 = Wallet.generate()
        reg1 = Transaction.register_validator(w2.address, w2.signing_pk, w2.address, nonce=0)
        reg1.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, reg1)
        assert blockchain.is_registered_validator(w2.address)

        # Second registration with same address (different key would fail addr check, so reuse)
        reg2 = Transaction.register_validator(w2.address, w2.signing_pk, w2.address, nonce=1)
        reg2.sign(w2.signing_sk, w2.signing_pk)
        ok, _ = blockchain.submit_tx(reg2)
        # Pool may accept it but block processing will skip (first-wins)
        if ok:
            # After w2 registered, round-robin may select either validator
            next_idx = blockchain.height + 1
            expected = blockchain.consensus.select_validator(next_idx)
            if expected == wallet.address:
                block = blockchain.produce_block(wallet.address, wallet.signing_sk)
            else:
                block = blockchain.produce_block(w2.address, w2.signing_sk)
            assert block is not None
        # Validator is still registered (unchanged)
        assert blockchain.is_registered_validator(w2.address)

    def test_only_registered_validators_can_produce_blocks(self):
        """An unregistered wallet cannot produce blocks."""
        impostor = Wallet.generate()
        v1 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        tx = Transaction.notarize(v1.address, "aa", nonce=0)
        tx.sign(v1.signing_sk, v1.signing_pk)
        bc.submit_tx(tx)

        # Impostor tries to produce block
        block = bc.produce_block(impostor.address, impostor.signing_sk)
        assert block is None  # only v1 is in validator set

    def test_validator_registration_rollback(self, blockchain, wallet):
        """Rolling back a REGISTER_VALIDATOR block removes the validator."""
        w2 = Wallet.generate()
        reg = Transaction.register_validator(w2.address, w2.signing_pk, w2.address, nonce=0)
        reg.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(blockchain, wallet, reg)
        assert blockchain.is_registered_validator(w2.address)

        # Rollback the registration block
        blockchain._rollback_to(1)
        assert not blockchain.is_registered_validator(w2.address)
        assert not blockchain.consensus.is_validator(w2.address)

    def test_validator_registry_survives_sqlite_reload(self, tmp_dir):
        """Validator registered on-chain persists through SQLite reload."""
        w1 = Wallet.generate()
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(w1.address, w1.signing_pk)
        bc.init_chain(w1.address, w1.signing_sk, validator_pk=w1.signing_pk)

        w2 = Wallet.generate()
        reg = Transaction.register_validator(w2.address, w2.signing_pk, w2.address, nonce=0)
        reg.sign(w2.signing_sk, w2.signing_pk)
        submit_and_mine(bc, w1, reg)
        assert bc.is_registered_validator(w2.address)

        # Reload
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(w1.address, w1.signing_pk)
        bc2.consensus.add_validator(w2.address, w2.signing_pk)
        loaded = bc2.load()
        assert loaded is True
        assert bc2.is_registered_validator(w2.address)

    def test_register_validator_invalid_pubkey_rejected(self, blockchain, wallet):
        """REGISTER_VALIDATOR with non-hex pubkey is rejected."""
        tx = Transaction(
            TxType.REGISTER_VALIDATOR, wallet.address,
            payload={"validator_pubkey": "not-hex!", "validator_address": wallet.address},
            nonce=0
        )
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok

    def test_register_validator_wrong_pubkey_size(self, blockchain, wallet):
        """REGISTER_VALIDATOR with wrong-size pubkey is rejected."""
        tx = Transaction(
            TxType.REGISTER_VALIDATOR, wallet.address,
            payload={"validator_pubkey": "ab" * 100, "validator_address": wallet.address},
            nonce=0
        )
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok


# ========== _ChainProxy (SQLite mode) ==========

class TestChainProxy:
    """Tests for _ChainProxy indexing in SQLite-backed blockchain."""

    def _build_sqlite_chain(self, tmp_dir, wallet, num_blocks=3):
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        for i in range(num_blocks):
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            bc.submit_tx(tx)
            bc.produce_block(wallet.address, wallet.signing_sk)
        return bc

    def test_len_after_genesis(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=0)
        assert len(bc.chain) == 1  # genesis only

    def test_len_after_blocks(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=3)
        assert len(bc.chain) == 4  # genesis + 3

    def test_bool_true_when_chain_has_blocks(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=0)
        assert bool(bc.chain) is True

    def test_positive_index(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=2)
        block = bc.chain[0]
        assert block.index == 0

    def test_negative_index(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=2)
        last = bc.chain[-1]
        assert last.index == 2  # genesis + 2 blocks -> index 2

    def test_slice_returns_list(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=3)
        sliced = bc.chain[1:3]
        assert isinstance(sliced, list)
        assert len(sliced) == 2

    def test_out_of_range_raises(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=1)
        with pytest.raises(IndexError):
            _ = bc.chain[99]

    def test_iteration(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=2)
        blocks = list(bc.chain)
        assert len(blocks) == 3  # genesis + 2

    def test_get_block_returns_none_for_out_of_range(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=1)
        assert bc.get_block(999) is None

    def test_latest_block_in_sync_after_append(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=0)
        initial_hash = bc.latest_block.block_hash

        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(wallet.address, wallet.signing_sk)

        assert bc.latest_block.block_hash != initial_hash
        assert bc.latest_block.index == 1

    def test_height_consistency_after_multiple_operations(self, wallet, tmp_dir):
        bc = self._build_sqlite_chain(tmp_dir, wallet, num_blocks=5)
        assert bc.height == 5
        assert len(bc.chain) == 6  # genesis + 5

    def test_db_lock_attribute_exists(self, wallet, tmp_dir):
        bc = Blockchain(data_dir=tmp_dir)
        assert hasattr(bc, '_db_lock')
        import threading
        assert isinstance(bc._db_lock, type(threading.Lock()))

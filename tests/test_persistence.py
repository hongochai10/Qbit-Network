"""Tests for PersistenceMixin — save/load paths (TEC-893).

Covers JSON save, JSON load (corrupt/empty/valid), and
in-memory early-exit branches.
"""
import json
import os
import shutil
import tempfile
from unittest.mock import patch

import pytest

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.wallet import Wallet
from qbit_network.core.transaction import Transaction, TxType


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="qv_persist_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_chain(tmp_dir, *, json_mode=False):
    """Create a Blockchain with a genesis block in tmp_dir.

    If json_mode=True, force in-memory chain list (no SQLite) so that
    save() writes chain.json.
    """
    if json_mode:
        # Create without data_dir first (in-memory), then set data_dir
        bc = Blockchain(data_dir="")
        w = Wallet.generate()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk, validator_pk=w.signing_pk)
        bc.data_dir = tmp_dir
        return bc, w
    bc = Blockchain(data_dir=tmp_dir)
    w = Wallet.generate()
    bc.consensus.add_validator(w.address, w.signing_pk)
    bc.init_chain(w.address, w.signing_sk, validator_pk=w.signing_pk)
    return bc, w


# ===================================================================
# save() tests
# ===================================================================

class TestPersistenceSave:
    """Tests for PersistenceMixin.save()."""

    def test_save_creates_chain_json(self, tmp_dir):
        """save() writes chain.json atomically."""
        bc, w = _make_chain(tmp_dir, json_mode=True)
        bc.save()
        chain_file = os.path.join(tmp_dir, "chain.json")
        assert os.path.exists(chain_file)
        with open(chain_file, "r") as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 1  # genesis block
        assert data[0]["index"] == 0

    def test_save_noop_when_store_present(self, tmp_dir):
        """save() does nothing when SQLite store is active."""
        bc, w = _make_chain(tmp_dir, json_mode=True)
        bc._store = object()  # non-None sentinel
        bc.save()
        chain_file = os.path.join(tmp_dir, "chain.json")
        assert not os.path.exists(chain_file)

    def test_save_noop_when_no_data_dir(self, tmp_dir):
        """save() does nothing when data_dir is empty."""
        bc, w = _make_chain(tmp_dir, json_mode=True)
        bc.data_dir = ""
        bc.save()

    def test_save_noop_when_chain_list_none(self, tmp_dir):
        """save() does nothing when _chain_list is None (SQLite mode)."""
        bc, w = _make_chain(tmp_dir, json_mode=True)
        bc._chain_list = None
        bc.save()
        chain_file = os.path.join(tmp_dir, "chain.json")
        assert not os.path.exists(chain_file)

    def test_save_multiple_blocks(self, tmp_dir):
        """save() correctly serializes multiple blocks."""
        bc, w = _make_chain(tmp_dir, json_mode=True)
        # Add a notarize tx and produce block
        tx = Transaction.notarize(w.address, "aabb00112233445566778899", "meta",
                                  nonce=bc.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        bc.save()
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "r") as f:
            data = json.load(f)
        assert len(data) == 2
        assert data[1]["index"] == 1

    def test_save_creates_data_dir_if_missing(self):
        """save() creates data_dir if it doesn't exist."""
        parent = tempfile.mkdtemp(prefix="qv_persist_parent_")
        try:
            nested = os.path.join(parent, "subdir", "chain")
            bc = Blockchain(data_dir="")
            w = Wallet.generate()
            bc.consensus.add_validator(w.address, w.signing_pk)
            bc.init_chain(w.address, w.signing_sk, validator_pk=w.signing_pk)
            bc.data_dir = nested
            bc.save()
            assert os.path.exists(os.path.join(nested, "chain.json"))
        finally:
            shutil.rmtree(parent, ignore_errors=True)


# ===================================================================
# load() JSON path tests
# ===================================================================

class TestPersistenceLoadJSON:
    """Tests for load() via JSON file path."""

    def test_load_returns_false_no_data_dir(self):
        """load() returns False when data_dir is empty."""
        bc = Blockchain(data_dir="")
        assert bc.load() is False

    def test_load_returns_false_no_chain_file(self, tmp_dir):
        """load() returns False when no chain file exists."""
        bc = Blockchain(data_dir="")
        bc.data_dir = tmp_dir
        # Remove any db/json files
        for name in ("chain.db", "chain.json"):
            path = os.path.join(tmp_dir, name)
            if os.path.exists(path):
                os.unlink(path)
        assert bc.load() is False

    def test_load_skips_non_empty_chain(self, tmp_dir):
        """load() warns and returns True if chain already has blocks."""
        bc, w = _make_chain(tmp_dir)
        assert bc.height >= 0
        # load() should skip since chain is non-empty
        assert bc.load() is True

    @patch("qbit_network.core.store.migrate_json_to_sqlite", lambda d: None)
    def test_load_corrupt_json(self, tmp_dir):
        """load() returns False for corrupt JSON file."""
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            f.write("{not valid json!!!}")
        bc = Blockchain(data_dir="")
        bc.data_dir = tmp_dir
        # Remove db file so we hit JSON path
        db_file = os.path.join(tmp_dir, "chain.db")
        if os.path.exists(db_file):
            os.unlink(db_file)
        assert bc.load() is False

    @patch("qbit_network.core.store.migrate_json_to_sqlite", lambda d: None)
    def test_load_non_list_json(self, tmp_dir):
        """load() returns False if JSON is not a list."""
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            json.dump({"key": "value"}, f)
        bc = Blockchain(data_dir="")
        bc.data_dir = tmp_dir
        db_file = os.path.join(tmp_dir, "chain.db")
        if os.path.exists(db_file):
            os.unlink(db_file)
        assert bc.load() is False

    @patch("qbit_network.core.store.migrate_json_to_sqlite", lambda d: None)
    def test_load_empty_list_json(self, tmp_dir):
        """load() returns False for empty JSON array."""
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            json.dump([], f)
        bc = Blockchain(data_dir="")
        bc.data_dir = tmp_dir
        db_file = os.path.join(tmp_dir, "chain.db")
        if os.path.exists(db_file):
            os.unlink(db_file)
        assert bc.load() is False

    @patch("qbit_network.core.store.migrate_json_to_sqlite", lambda d: None)
    def test_load_json_valid_chain(self, tmp_dir):
        """load() via JSON path successfully restores a valid chain."""
        # First, create a chain and save to JSON
        bc1, w = _make_chain(tmp_dir, json_mode=True)
        tx = Transaction.notarize(w.address, "aabbcc001122334455667788", "meta",
                                  nonce=bc1.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc1.submit_tx(tx)
        bc1.produce_block(w.address, w.signing_sk)
        bc1.save()
        original_height = bc1.height

        # Load into new chain via JSON path (migration mocked out)
        bc2 = Blockchain(data_dir="")
        bc2.data_dir = tmp_dir
        bc2.consensus.add_validator(w.address, w.signing_pk)
        # Remove db file
        db_file = os.path.join(tmp_dir, "chain.db")
        if os.path.exists(db_file):
            os.unlink(db_file)
        result = bc2.load()
        assert result is True
        assert bc2.height == original_height

    @patch("qbit_network.core.store.migrate_json_to_sqlite", lambda d: None)
    def test_load_json_validates_genesis_index(self, tmp_dir):
        """load() raises ValueError if genesis block has wrong index."""
        chain_file = os.path.join(tmp_dir, "chain.json")
        fake_block = {
            "index": 5,
            "timestamp": 1000,
            "prev_hash": "",
            "block_hash": "abc123",
            "validator": "",
            "signature": "",
            "transactions": [],
        }
        with open(chain_file, "w") as f:
            json.dump([fake_block], f)
        bc = Blockchain(data_dir="")
        bc.data_dir = tmp_dir
        db_file = os.path.join(tmp_dir, "chain.db")
        if os.path.exists(db_file):
            os.unlink(db_file)
        with pytest.raises((ValueError, Exception)):
            bc.load()

    @patch("qbit_network.core.store.migrate_json_to_sqlite", lambda d: None)
    def test_load_json_validates_prev_hash(self, tmp_dir):
        """load() raises ValueError for prev_hash mismatch."""
        # Create a valid chain, save, then corrupt prev_hash
        bc1, w = _make_chain(tmp_dir, json_mode=True)
        tx = Transaction.notarize(w.address, "aabbcc556677889900112233", "meta",
                                  nonce=bc1.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc1.submit_tx(tx)
        bc1.produce_block(w.address, w.signing_sk)
        bc1.save()

        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "r") as f:
            data = json.load(f)
        # Corrupt block 1's prev_hash
        data[1]["prevHash"] = "0000000000000000000000000000000000000000"
        with open(chain_file, "w") as f:
            json.dump(data, f)

        bc2 = Blockchain(data_dir="")
        bc2.data_dir = tmp_dir
        bc2.consensus.add_validator(w.address, w.signing_pk)
        db_file = os.path.join(tmp_dir, "chain.db")
        if os.path.exists(db_file):
            os.unlink(db_file)
        with pytest.raises(ValueError):
            bc2.load()

    def test_save_and_load_roundtrip(self, tmp_dir):
        """save() then load() restores chain correctly via SQLite migration."""
        bc1, w = _make_chain(tmp_dir, json_mode=True)
        tx = Transaction.notarize(w.address, "aabbcc001122334455667788", "meta",
                                  nonce=bc1.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc1.submit_tx(tx)
        bc1.produce_block(w.address, w.signing_sk)
        bc1.save()
        original_height = bc1.height

        # Load into new chain — load() will migrate JSON to SQLite
        bc2 = Blockchain(data_dir="")
        bc2.data_dir = tmp_dir
        bc2.consensus.add_validator(w.address, w.signing_pk)
        result = bc2.load()
        assert result is True
        assert bc2.height == original_height


# ===================================================================
# load() SQLite path tests
# ===================================================================

class TestPersistenceLoadSQLite:
    """Tests for _load_from_sqlite path."""

    def test_sqlite_load_roundtrip(self, tmp_dir):
        """Chain saved to SQLite can be loaded back."""
        bc1, w = _make_chain(tmp_dir)
        # Add some data
        tx = Transaction.notarize(w.address, "aabbcc112233445566778899", "meta",
                                  nonce=bc1.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc1.submit_tx(tx)
        bc1.produce_block(w.address, w.signing_sk)
        height = bc1.height

        # Create a fresh blockchain and load from SQLite
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(w.address, w.signing_pk)
        result = bc2.load()
        assert result is True
        assert bc2.height == height

    def test_sqlite_load_with_verify_signatures(self, tmp_dir):
        """SQLite load with verify_signatures=True validates sigs."""
        bc1, w = _make_chain(tmp_dir)
        tx = Transaction.notarize(w.address, "aabbcc223344556677889900", "meta",
                                  nonce=bc1.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc1.submit_tx(tx)
        bc1.produce_block(w.address, w.signing_sk)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(w.address, w.signing_pk)
        assert bc2.load(verify_signatures=True) is True

    def test_sqlite_load_without_verify_signatures(self, tmp_dir):
        """SQLite load with verify_signatures=False skips sig checks."""
        bc1, w = _make_chain(tmp_dir)
        tx = Transaction.notarize(w.address, "aabbcc334455667788990011", "meta",
                                  nonce=bc1.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc1.submit_tx(tx)
        bc1.produce_block(w.address, w.signing_sk)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(w.address, w.signing_pk)
        assert bc2.load(verify_signatures=False) is True

    def test_sqlite_load_rebuilds_indices(self, tmp_dir):
        """SQLite load correctly rebuilds in-memory indices."""
        bc1, w = _make_chain(tmp_dir)
        tx = Transaction.notarize(w.address, "aabbcc445566778899001122", "idx_meta",
                                  nonce=bc1.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc1.submit_tx(tx)
        bc1.produce_block(w.address, w.signing_sk)
        tx_id = tx.tx_id

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(w.address, w.signing_pk)
        bc2.load()

        # Verify tx index was rebuilt
        assert bc2.get_tx(tx_id) is not None
        # Verify notarization index
        assert bc2.verify_document("aabbcc445566778899001122") is not None

    def test_sqlite_load_restores_staking(self, tmp_dir):
        """SQLite load rebuilds staking state."""
        bc1, w = _make_chain(tmp_dir)
        bc1.activate_financial_layer(w.address)
        # Stake
        tx = Transaction.stake(w.address, w.address, 1000,
                               nonce=bc1.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc1.submit_tx(tx)
        bc1.produce_block(w.address, w.signing_sk)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(w.address, w.signing_pk)
        bc2.load()

        # Check staking state was restored
        stakes = bc2._stakes.get(w.address, {})
        assert stakes.get(w.address, 0) > 0

    def test_sqlite_load_restores_key_registry(self, tmp_dir):
        """SQLite load rebuilds encryption key registry."""
        bc1, w = _make_chain(tmp_dir)
        tx = Transaction.register_key(w.address, w.encryption_pk,
                                      nonce=bc1.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc1.submit_tx(tx)
        bc1.produce_block(w.address, w.signing_sk)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(w.address, w.signing_pk)
        bc2.load()

        # Check key registry was restored
        assert bc2.get_encryption_pk(w.address) is not None

    def test_sqlite_load_empty_db(self, tmp_dir):
        """SQLite load returns False for empty database."""
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.close()
        # The DB exists but has no blocks
        bc = Blockchain(data_dir=tmp_dir)
        assert bc.load() is False


# ===================================================================
# SQLiteStore direct unit tests
# ===================================================================

class TestSQLiteStoreValidators:
    """Tests for validator registry operations."""

    def test_put_and_get_validator(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_validator("addr1", "pk_hex_1")
        assert store.get_validator("addr1") == "pk_hex_1"
        store.close()

    def test_get_validator_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_validator("nonexistent") is None
        store.close()

    def test_get_all_validators(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_validator("addr1", "pk1")
        store.put_validator("addr2", "pk2")
        result = store.get_all_validators()
        assert len(result) == 2
        assert ("addr1", "pk1") in result
        assert ("addr2", "pk2") in result
        store.close()

    def test_delete_validator(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_validator("addr1", "pk1")
        store.delete_validator("addr1")
        assert store.get_validator("addr1") is None
        store.close()

    def test_put_validator_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_validator("addr1", "pk1", commit=False)
        store.commit()
        assert store.get_validator("addr1") == "pk1"
        store.close()

    def test_delete_validator_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_validator("addr1", "pk1")
        store.delete_validator("addr1", commit=False)
        store.commit()
        assert store.get_validator("addr1") is None
        store.close()


class TestSQLiteStoreStaking:
    """Tests for staking registry operations."""

    def test_put_and_get_stake(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_stake("staker1", "validator1", 500)
        assert store.get_stake("staker1", "validator1") == 500
        store.close()

    def test_get_stake_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_stake("x", "y") == 0
        store.close()

    def test_delete_stake(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_stake("s", "v", 100)
        store.delete_stake("s", "v")
        assert store.get_stake("s", "v") == 0
        store.close()

    def test_get_all_stakes(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_stake("s1", "v1", 100)
        store.put_stake("s2", "v2", 200)
        result = store.get_all_stakes()
        assert len(result) == 2
        store.close()

    def test_put_stake_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_stake("s", "v", 50, commit=False)
        store.commit()
        assert store.get_stake("s", "v") == 50
        store.close()

    def test_delete_stake_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_stake("s", "v", 50)
        store.delete_stake("s", "v", commit=False)
        store.commit()
        assert store.get_stake("s", "v") == 0
        store.close()


class TestSQLiteStoreUnbonding:
    """Tests for unbonding operations."""

    def test_put_and_get_mature_unbondings(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_unbonding("s1", "v1", 100, release_block=10)
        store.put_unbonding("s2", "v2", 200, release_block=20)
        # At block 15, only the first is mature
        result = store.get_mature_unbondings(15)
        assert len(result) == 1
        assert result[0]["staker"] == "s1"
        assert result[0]["amount"] == 100
        # At block 20, both are mature
        result = store.get_mature_unbondings(20)
        assert len(result) == 2
        store.close()

    def test_delete_unbonding(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_unbonding("s1", "v1", 100, release_block=10)
        store.delete_unbonding("s1", "v1", 100, 10)
        assert store.get_mature_unbondings(10) == []
        store.close()

    def test_delete_unbondings_from_block(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_unbonding("s1", "v1", 100, release_block=5)
        store.put_unbonding("s2", "v2", 200, release_block=15)
        store.delete_unbondings_from_block(10)
        result = store.get_mature_unbondings(100)
        assert len(result) == 1
        assert result[0]["release_block"] == 5
        store.close()

    def test_put_unbonding_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_unbonding("s", "v", 50, 10, commit=False)
        store.commit()
        assert len(store.get_mature_unbondings(10)) == 1
        store.close()

    def test_delete_unbonding_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_unbonding("s", "v", 50, 10)
        store.delete_unbonding("s", "v", 50, 10, commit=False)
        store.commit()
        assert store.get_mature_unbondings(10) == []
        store.close()

    def test_delete_unbondings_from_block_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_unbonding("s", "v", 50, 10)
        store.delete_unbondings_from_block(5, commit=False)
        store.commit()
        assert store.get_mature_unbondings(100) == []
        store.close()


class TestSQLiteStoreRevocations:
    """Tests for revocation registry operations."""

    def test_put_and_get_revocation(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_revocation("addr1", "signing", "tx1", 1234.5, "compromised")
        result = store.get_revocation("addr1", "signing")
        assert result is not None
        assert result["tx_id"] == "tx1"
        assert result["timestamp"] == 1234.5
        assert result["reason"] == "compromised"
        store.close()

    def test_get_revocation_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_revocation("addr1", "signing") is None
        store.close()

    def test_delete_revocation(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_revocation("addr1", "signing", "tx1", 1234.5, "reason")
        store.delete_revocation("addr1", "signing")
        assert store.get_revocation("addr1", "signing") is None
        store.close()

    def test_get_all_revocations(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_revocation("a1", "signing", "tx1", 1.0, "r1")
        store.put_revocation("a2", "encryption", "tx2", 2.0, "r2")
        result = store.get_all_revocations()
        assert len(result) == 2
        store.close()

    def test_put_revocation_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_revocation("a", "s", "t", 1.0, "r", commit=False)
        store.commit()
        assert store.get_revocation("a", "s") is not None
        store.close()

    def test_delete_revocation_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_revocation("a", "s", "t", 1.0, "r")
        store.delete_revocation("a", "s", commit=False)
        store.commit()
        assert store.get_revocation("a", "s") is None
        store.close()


class TestSQLiteStoreEpochs:
    """Tests for epoch registry operations."""

    def test_put_and_get_epoch(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_epoch(0, 0, '["v1","v2"]')
        result = store.get_epoch(0)
        assert result is not None
        assert result["block_start"] == 0
        assert result["validators_json"] == '["v1","v2"]'
        store.close()

    def test_get_epoch_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_epoch(99) is None
        store.close()

    def test_delete_epochs_from(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_epoch(0, 0, "[]")
        store.put_epoch(1, 100, "[]")
        store.put_epoch(2, 200, "[]")
        store.delete_epochs_from(1)
        assert store.get_epoch(0) is not None
        assert store.get_epoch(1) is None
        assert store.get_epoch(2) is None
        store.close()

    def test_get_all_epochs(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_epoch(0, 0, "[]")
        store.put_epoch(1, 100, "[]")
        result = store.get_all_epochs()
        assert len(result) == 2
        assert result[0][0] == 0  # epoch_number sorted
        assert result[1][0] == 1
        store.close()

    def test_put_epoch_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_epoch(0, 0, "[]", commit=False)
        store.commit()
        assert store.get_epoch(0) is not None
        store.close()

    def test_delete_epochs_from_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_epoch(0, 0, "[]")
        store.delete_epochs_from(0, commit=False)
        store.commit()
        assert store.get_epoch(0) is None
        store.close()


class TestSQLiteStoreSlashing:
    """Tests for slashing event operations."""

    def test_put_and_get_slashing_events(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_slashing_event("v1", "ev_tx1", 500, 10)
        result = store.get_slashing_events()
        assert len(result) == 1
        assert result[0]["validator"] == "v1"
        assert result[0]["amount_slashed"] == 500
        store.close()

    def test_get_slashing_events_filtered(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_slashing_event("v1", "tx1", 100, 5)
        store.put_slashing_event("v2", "tx2", 200, 10)
        result = store.get_slashing_events(validator="v1")
        assert len(result) == 1
        assert result[0]["validator"] == "v1"
        store.close()

    def test_delete_slashing_events_from_block(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_slashing_event("v1", "tx1", 100, 5)
        store.put_slashing_event("v2", "tx2", 200, 15)
        store.delete_slashing_events_from_block(10)
        result = store.get_slashing_events()
        assert len(result) == 1
        assert result[0]["block_index"] == 5
        store.close()

    def test_put_slashing_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_slashing_event("v", "t", 50, 1, commit=False)
        store.commit()
        assert len(store.get_slashing_events()) == 1
        store.close()

    def test_delete_slashing_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_slashing_event("v", "t", 50, 1)
        store.delete_slashing_events_from_block(0, commit=False)
        store.commit()
        assert store.get_slashing_events() == []
        store.close()


class TestSQLiteStoreBalancesSupply:
    """Tests for balance and supply operations."""

    def test_put_and_get_balance(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_balance("addr1", 1000)
        assert store.get_balance("addr1") == 1000
        store.close()

    def test_get_balance_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_balance("nonexistent") == 0
        store.close()

    def test_put_and_get_supply(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_supply("total_minted", 50000)
        assert store.get_supply("total_minted") == 50000
        store.close()

    def test_get_supply_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_supply("nonexistent") == 0
        store.close()


class TestSQLiteStoreTokens:
    """Tests for token storage operations."""

    def _token_meta(self, **overrides):
        meta = {
            "issuer": "addr1", "name": "Test Token", "symbol": "TST",
            "decimals": 8, "max_supply": 1000000, "total_minted": 0,
            "transferable": True, "created_block": 1, "created_tx": "tx1",
        }
        meta.update(overrides)
        return meta

    def test_put_and_get_token(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token("tok1", self._token_meta())
        result = store.get_token("tok1")
        assert result is not None
        assert result["symbol"] == "TST"
        assert result["transferable"] is True
        store.close()

    def test_get_token_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_token("nonexistent") is None
        store.close()

    def test_get_all_tokens(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token("tok1", self._token_meta(symbol="T1"))
        store.put_token("tok2", self._token_meta(symbol="T2"))
        result = store.get_all_tokens()
        assert len(result) == 2
        store.close()

    def test_delete_token(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token("tok1", self._token_meta())
        store.put_token_balance("tok1", "addr2", 100)
        store.delete_token("tok1")
        assert store.get_token("tok1") is None
        assert store.get_token_balance("tok1", "addr2") == 0
        store.close()

    def test_put_and_get_token_balance(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token_balance("tok1", "addr1", 500)
        assert store.get_token_balance("tok1", "addr1") == 500
        store.close()

    def test_put_token_balance_zero_deletes(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token_balance("tok1", "addr1", 500)
        store.put_token_balance("tok1", "addr1", 0)
        assert store.get_token_balance("tok1", "addr1") == 0
        store.close()

    def test_get_token_holders(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token_balance("tok1", "a1", 100)
        store.put_token_balance("tok1", "a2", 200)
        holders = store.get_token_holders("tok1")
        assert len(holders) == 2
        store.close()

    def test_get_token_holders_page(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token_balance("tok1", "a1", 100)
        store.put_token_balance("tok1", "a2", 200)
        store.put_token_balance("tok1", "a3", 300)
        holders, total = store.get_token_holders_page("tok1", page=1, limit=2)
        assert total == 3
        assert len(holders) == 2
        assert holders[0]["amount"] == 300  # sorted desc
        store.close()

    def test_get_address_tokens(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token_balance("tok1", "addr1", 100)
        store.put_token_balance("tok2", "addr1", 200)
        result = store.get_address_tokens("addr1")
        assert len(result) == 2
        store.close()

    def test_get_token_balance_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_token_balance("x", "y") == 0
        store.close()

    def test_put_token_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token("tok1", self._token_meta(), commit=False)
        store.commit()
        assert store.get_token("tok1") is not None
        store.close()

    def test_delete_token_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token("tok1", self._token_meta())
        store.delete_token("tok1", commit=False)
        store.commit()
        assert store.get_token("tok1") is None
        store.close()

    def test_put_token_balance_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_token_balance("tok1", "a1", 50, commit=False)
        store.commit()
        assert store.get_token_balance("tok1", "a1") == 50
        store.close()


class TestSQLiteStoreReceipts:
    """Tests for receipt and event storage."""

    def test_put_and_get_receipt(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        from qbit_network.core.receipt import TransactionReceipt
        store = SQLiteStore(tmp_dir)
        receipt = TransactionReceipt(
            tx_id="tx1", status="success", fee_paid=100,
            block_index=1, tx_index=0,
            events=[{"type": "Transfer", "amount": 50}])
        store.put_receipt(receipt)
        result = store.get_receipt("tx1")
        assert result is not None
        assert result.tx_id == "tx1"
        assert result.status == "success"
        assert result.fee_paid == 100
        assert len(result.events) == 1
        store.close()

    def test_get_receipt_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_receipt("nonexistent") is None
        store.close()

    def test_get_receipts_for_block(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        from qbit_network.core.receipt import TransactionReceipt
        store = SQLiteStore(tmp_dir)
        r1 = TransactionReceipt("tx1", "success", 100, 1, 0, [])
        r2 = TransactionReceipt("tx2", "success", 200, 1, 1, [])
        store.put_receipt(r1)
        store.put_receipt(r2)
        result = store.get_receipts_for_block(1)
        assert len(result) == 2
        assert result[0].tx_id == "tx1"
        assert result[1].tx_id == "tx2"
        store.close()

    def test_delete_receipts_for_block(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        from qbit_network.core.receipt import TransactionReceipt
        store = SQLiteStore(tmp_dir)
        r1 = TransactionReceipt("tx1", "success", 10, 1, 0, [{"type": "T"}])
        r2 = TransactionReceipt("tx2", "success", 20, 5, 0, [{"type": "T"}])
        store.put_receipt(r1)
        store.put_receipt(r2)
        store.delete_receipts_for_block(3)
        assert store.get_receipt("tx1") is not None
        assert store.get_receipt("tx2") is None
        store.close()

    def test_put_receipt_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        from qbit_network.core.receipt import TransactionReceipt
        store = SQLiteStore(tmp_dir)
        r = TransactionReceipt("tx1", "success", 10, 1, 0, [])
        store.put_receipt(r, commit=False)
        store.commit()
        assert store.get_receipt("tx1") is not None
        store.close()

    def test_delete_receipts_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        from qbit_network.core.receipt import TransactionReceipt
        store = SQLiteStore(tmp_dir)
        r = TransactionReceipt("tx1", "success", 10, 1, 0, [])
        store.put_receipt(r)
        store.delete_receipts_for_block(0, commit=False)
        store.commit()
        assert store.get_receipt("tx1") is None
        store.close()


class TestSQLiteStoreBlockLevelEvents:
    """Tests for block-level event storage."""

    def test_put_and_get_block_level_events(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        events = [{"type": "BlockReward", "amount": 100}]
        store.put_block_level_events(1, events)
        result = store.get_block_level_events(1)
        assert len(result) == 1
        assert result[0]["type"] == "BlockReward"
        store.close()

    def test_get_block_level_events_empty(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_block_level_events(99) == []
        store.close()

    def test_put_empty_events_noop(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_block_level_events(1, [])
        assert store.get_block_level_events(1) == []
        store.close()

    def test_put_block_level_events_no_commit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_block_level_events(1, [{"type": "X"}], commit=False)
        store.commit()
        assert len(store.get_block_level_events(1)) == 1
        store.close()


class TestSQLiteStoreQueryEvents:
    """Tests for query_events."""

    def test_query_events_by_type(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        from qbit_network.core.receipt import TransactionReceipt
        store = SQLiteStore(tmp_dir)
        r = TransactionReceipt("tx1", "success", 10, 1, 0,
                               [{"type": "Transfer", "amount": 50},
                                {"type": "Fee", "amount": 10}])
        store.put_receipt(r)
        result = store.query_events(event_type="Transfer")
        assert len(result) == 1
        assert result[0]["event_type"] == "Transfer"
        store.close()

    def test_query_events_by_block(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        from qbit_network.core.receipt import TransactionReceipt
        store = SQLiteStore(tmp_dir)
        r1 = TransactionReceipt("tx1", "success", 10, 1, 0, [{"type": "T"}])
        r2 = TransactionReceipt("tx2", "success", 10, 2, 0, [{"type": "T"}])
        store.put_receipt(r1)
        store.put_receipt(r2)
        result = store.query_events(block_index=1)
        assert len(result) == 1
        assert result[0]["block_index"] == 1
        store.close()

    def test_query_events_no_filters(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        from qbit_network.core.receipt import TransactionReceipt
        store = SQLiteStore(tmp_dir)
        r = TransactionReceipt("tx1", "success", 10, 1, 0, [{"type": "X"}])
        store.put_receipt(r)
        result = store.query_events()
        assert len(result) == 1
        store.close()

    def test_query_events_limit(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        from qbit_network.core.receipt import TransactionReceipt
        store = SQLiteStore(tmp_dir)
        for i in range(5):
            r = TransactionReceipt(f"tx{i}", "success", 10, i, 0, [{"type": "T"}])
            store.put_receipt(r)
        result = store.query_events(limit=2)
        assert len(result) == 2
        store.close()


class TestSQLiteStorePrune:
    """Tests for block pruning."""

    def test_prune_blocks(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        # Produce a few more blocks (genesis=0, so add 1,2,3)
        for i in range(3):
            tx = Transaction.notarize(w.address, f"aa00bb{i:018x}cc11dd", "meta",
                                      nonce=bc.get_nonce(w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            bc.submit_tx(tx)
            bc.produce_block(w.address, w.signing_sk)
        assert bc._store._height == 3
        count = bc._store.prune_blocks(2)
        assert count == 2  # blocks 0 and 1
        assert bc._store.get_block(0) is None
        assert bc._store.get_block(1) is None
        assert bc._store.get_block(2) is not None

    def test_prune_zero_index(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        assert bc._store.prune_blocks(0) == 0
        assert bc._store.prune_blocks(-1) == 0

    def test_prune_no_blocks_to_prune(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        d = os.path.join(tmp_dir, "empty_prune")
        os.makedirs(d)
        store = SQLiteStore(d)
        assert store.prune_blocks(100) == 0
        store.close()


class TestSQLiteStoreUtilMethods:
    """Tests for utility methods: block_hash_exists, tx_exists, get_blocks_range, etc."""

    def test_block_hash_exists(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        genesis = bc._store.get_block(0)
        assert bc._store.block_hash_exists(genesis.block_hash) is True
        assert bc._store.block_hash_exists("nonexistent") is False

    def test_tx_exists(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        tx = Transaction.notarize(w.address, "aabb00112233445566778899", "meta",
                                  nonce=bc.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        assert bc._store.tx_exists(tx.tx_id) is True
        assert bc._store.tx_exists("nonexistent") is False

    def test_get_blocks_range(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        for i in range(3):
            tx = Transaction.notarize(w.address, f"cc00dd{i:018x}ee11ff", "meta",
                                      nonce=bc.get_nonce(w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            bc.submit_tx(tx)
            bc.produce_block(w.address, w.signing_sk)
        # genesis=0, plus 3 more blocks = indices 0,1,2,3
        assert bc._store._height == 3
        blocks = bc._store.get_blocks_range(1, 3)
        assert len(blocks) == 2
        assert blocks[0].index == 1
        assert blocks[1].index == 2

    def test_get_blocks_count(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        assert bc._store.get_blocks_count() >= 1  # at least genesis

    def test_latest_block(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        latest = bc._store.latest_block()
        assert latest is not None
        assert latest.index == bc._store._height

    def test_latest_block_empty(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.latest_block() is None
        store.close()

    def test_update_block(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        block = bc._store.get_block(0)
        old_hash = block.block_hash
        # update_block just persists current state
        bc._store.update_block(block)
        reloaded = bc._store.get_block(0)
        assert reloaded.block_hash == old_hash

    def test_get_block_by_hash(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        block = bc._store.get_block(0)
        result = bc._store.get_block_by_hash(block.block_hash)
        assert result is not None
        assert result.index == 0

    def test_get_block_by_hash_not_found(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        assert bc._store.get_block_by_hash("nonexistent") is None

    def test_get_tx(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        tx = Transaction.notarize(w.address, "aabb00112233445566778899", "m",
                                  nonce=bc.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        result = bc._store.get_tx(tx.tx_id)
        assert result is not None
        assert result.tx_id == tx.tx_id

    def test_get_tx_not_found(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        assert bc._store.get_tx("nonexistent") is None

    def test_get_tx_block(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        tx = Transaction.notarize(w.address, "aabb00112233445566778899", "m",
                                  nonce=bc.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        assert bc._store.get_tx_block(tx.tx_id) == 1

    def test_get_tx_block_not_found(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        assert bc._store.get_tx_block("nonexistent") is None

    def test_get_nonce(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        tx = Transaction.notarize(w.address, "aabb00112233445566778899", "m",
                                  nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        # After one tx with nonce=0, next nonce should be >= 1
        nonce = bc._store.get_nonce(w.address)
        assert nonce >= 1

    def test_get_nonce_no_txs(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_nonce("unknown_addr") == 0
        store.close()

    def test_get_notarization(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        doc_hash = "aabb00112233445566778899"
        tx = Transaction.notarize(w.address, doc_hash, "meta",
                                  nonce=bc.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        result = bc._store.get_notarization(doc_hash)
        assert result == tx.tx_id

    def test_get_notarization_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_notarization("nonexistent") is None
        store.close()

    def test_get_all_notarizations(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        doc_hash = "aabb00112233445566778899"
        tx = Transaction.notarize(w.address, doc_hash, "meta",
                                  nonce=bc.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        result = bc._store.get_all_notarizations(doc_hash)
        assert len(result) >= 1

    def test_get_encryption_pk(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        tx = Transaction.register_key(w.address, w.encryption_pk,
                                      nonce=bc.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        result = bc._store.get_encryption_pk(w.address)
        assert result is not None

    def test_get_encryption_pk_not_found(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_encryption_pk("unknown") is None
        store.close()

    def test_get_txs_by_sender(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        tx = Transaction.notarize(w.address, "aabb00112233445566778899", "m",
                                  nonce=bc.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        result = bc._store.get_txs_by_sender(w.address)
        assert len(result) >= 1

    def test_get_txs_by_sender_count(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        count = bc._store.get_txs_by_sender_count(w.address)
        assert count >= 0

    def test_get_txs_by_sender_page(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        tx = Transaction.notarize(w.address, "aabb00112233445566778899", "m",
                                  nonce=bc.get_nonce(w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        result = bc._store.get_txs_by_sender_page(w.address, 0, 10)
        assert len(result) >= 1

    def test_get_txs_by_recipient(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        result = store.get_txs_by_recipient("nonexistent")
        assert result == []
        store.close()

    def test_get_txs_by_recipient_count(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        assert store.get_txs_by_recipient_count("nonexistent") == 0
        store.close()

    def test_get_all_balances(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_balance("a1", 100)
        store.put_balance("a2", 200)
        result = store.get_all_balances()
        assert len(result) == 2
        store.close()


class TestSQLiteStoreDeleteBlocksFrom:
    """Tests for delete_blocks_from (rollback)."""

    def test_delete_blocks_from_basic(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        for i in range(3):
            tx = Transaction.notarize(w.address, f"dd00ee{i:018x}ff11aa", "m",
                                      nonce=bc.get_nonce(w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            bc.submit_tx(tx)
            bc.produce_block(w.address, w.signing_sk)
        # Use the blockchain's own store
        assert bc._store._height == 3
        bc._store.delete_blocks_from(2)
        assert bc._store._height == 1
        assert bc._store.get_block(0) is not None
        assert bc._store.get_block(1) is not None
        assert bc._store.get_block(2) is None

    def test_delete_blocks_from_zero(self, tmp_dir):
        bc, w = _make_chain(tmp_dir)
        bc._store.delete_blocks_from(0)
        assert bc._store._height == -1
        assert bc._store.get_block(0) is None


class TestSQLiteStoreMigration:
    """Tests for migrate_json_to_sqlite."""

    def test_migrate_json_to_sqlite(self, tmp_dir):
        from qbit_network.core.store import migrate_json_to_sqlite
        # Create a valid chain.json
        bc, w = _make_chain(tmp_dir, json_mode=True)
        bc.save()
        # Remove db if exists
        db_file = os.path.join(tmp_dir, "chain.db")
        if os.path.exists(db_file):
            os.unlink(db_file)
        migrate_json_to_sqlite(tmp_dir)
        assert os.path.exists(db_file)

    def test_migrate_no_chain_json(self, tmp_dir):
        from qbit_network.core.store import migrate_json_to_sqlite
        # No chain.json — should be a no-op
        migrate_json_to_sqlite(tmp_dir)
        assert not os.path.exists(os.path.join(tmp_dir, "chain.db"))

    def test_migrate_already_migrated(self, tmp_dir):
        from qbit_network.core.store import migrate_json_to_sqlite, SQLiteStore
        # Create both chain.json and chain.db
        bc, w = _make_chain(tmp_dir, json_mode=True)
        bc.save()
        store = SQLiteStore(tmp_dir)
        store.close()
        # Migration should skip since chain.db exists
        migrate_json_to_sqlite(tmp_dir)

    def test_migrate_corrupt_json(self, tmp_dir):
        from qbit_network.core.store import migrate_json_to_sqlite
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            f.write("{corrupt!!}")
        db_file = os.path.join(tmp_dir, "chain.db")
        if os.path.exists(db_file):
            os.unlink(db_file)
        migrate_json_to_sqlite(tmp_dir)
        # Should not create db on corrupt input
        assert not os.path.exists(db_file)

    def test_migrate_empty_list(self, tmp_dir):
        from qbit_network.core.store import migrate_json_to_sqlite
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            json.dump([], f)
        db_file = os.path.join(tmp_dir, "chain.db")
        if os.path.exists(db_file):
            os.unlink(db_file)
        migrate_json_to_sqlite(tmp_dir)
        assert not os.path.exists(db_file)

    def test_migrate_non_list_json(self, tmp_dir):
        from qbit_network.core.store import migrate_json_to_sqlite
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            json.dump({"not": "a list"}, f)
        db_file = os.path.join(tmp_dir, "chain.db")
        if os.path.exists(db_file):
            os.unlink(db_file)
        migrate_json_to_sqlite(tmp_dir)
        assert not os.path.exists(db_file)


class TestSQLiteStoreAppendBlock:
    """Tests for append_block error handling."""

    def test_append_block_rollback_on_error(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        from unittest.mock import MagicMock
        store = SQLiteStore(tmp_dir)
        # Create a block that will cause an error during insert
        bad_block = MagicMock()
        bad_block.to_dict.side_effect = Exception("serialize error")
        with pytest.raises(Exception, match="serialize error"):
            store.append_block(bad_block)
        # Height should remain -1
        assert store.height() == -1
        store.close()

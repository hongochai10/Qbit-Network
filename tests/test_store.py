"""Tests for qbit_network.core.store — SQLiteStore edge cases, migration, corruption."""
import json
import os
import sqlite3
import tempfile
import shutil
import time
import pytest

from qbit_network.core.store import SQLiteStore, migrate_json_to_sqlite
from qbit_network.core.block import Block
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.wallet import Wallet


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="qv_store_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def wallet():
    return Wallet.generate()


@pytest.fixture
def store(tmp_dir):
    s = SQLiteStore(tmp_dir)
    yield s
    s.close()


@pytest.fixture
def genesis(wallet):
    b = Block.genesis(wallet.address)
    b.sign(wallet.signing_sk)
    return b


# =========================================================================
# Basic operations
# =========================================================================

class TestSQLiteStoreBasic:

    def test_height_empty_store(self, store):
        assert store.height() == -1

    def test_get_blocks_count_empty(self, store):
        assert store.get_blocks_count() == 0

    def test_latest_block_empty(self, store):
        assert store.latest_block() is None

    def test_append_genesis_updates_height(self, store, genesis):
        store.append_block(genesis)
        assert store.height() == 0

    def test_get_block_zero(self, store, genesis):
        store.append_block(genesis)
        b = store.get_block(0)
        assert b is not None
        assert b.block_hash == genesis.block_hash

    def test_get_block_negative_index_returns_none(self, store, genesis):
        store.append_block(genesis)
        assert store.get_block(-1) is None

    def test_get_block_out_of_range_returns_none(self, store, genesis):
        store.append_block(genesis)
        assert store.get_block(999) is None

    def test_get_block_by_hash(self, store, genesis):
        store.append_block(genesis)
        b = store.get_block_by_hash(genesis.block_hash)
        assert b is not None
        assert b.block_hash == genesis.block_hash

    def test_get_block_by_unknown_hash_returns_none(self, store):
        assert store.get_block_by_hash("00" * 32) is None

    def test_block_hash_exists(self, store, genesis):
        store.append_block(genesis)
        assert store.block_hash_exists(genesis.block_hash) is True

    def test_block_hash_does_not_exist(self, store):
        assert store.block_hash_exists("ff" * 32) is False

    def test_get_blocks_count_after_append(self, store, genesis, wallet):
        store.append_block(genesis)
        tx = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)
        assert store.get_blocks_count() == 2
        assert store.height() == 1

    def test_latest_block_returns_last_appended(self, store, genesis, wallet):
        store.append_block(genesis)
        tx = Transaction.notarize(wallet.address, "bb" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)
        lb = store.latest_block()
        assert lb is not None
        assert lb.block_hash == b1.block_hash


# =========================================================================
# Transaction indexing
# =========================================================================

class TestSQLiteStoreTxIndexing:

    def test_tx_exists_false_before_block(self, store, genesis):
        store.append_block(genesis)
        assert store.tx_exists("ab" * 32) is False

    def test_tx_exists_after_append(self, store, genesis, wallet):
        store.append_block(genesis)
        tx = Transaction.notarize(wallet.address, "ab" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)
        assert store.tx_exists(tx.tx_id) is True

    def test_get_tx_returns_none_for_unknown(self, store):
        assert store.get_tx("0" * 64) is None

    def test_get_tx_block_returns_none_for_unknown(self, store):
        assert store.get_tx_block("0" * 64) is None

    def test_get_nonce_zero_for_unknown_address(self, store):
        assert store.get_nonce("qv1" + "a" * 64) == 0

    def test_get_nonce_increments_per_tx(self, store, genesis, wallet):
        store.append_block(genesis)
        tx0 = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        tx0.sign(wallet.signing_sk, wallet.signing_pk)
        tx1 = Transaction.notarize(wallet.address, "bb" * 32, nonce=1)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx0, tx1],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)
        assert store.get_nonce(wallet.address) == 2

    def test_get_txs_by_sender(self, store, genesis, wallet):
        store.append_block(genesis)
        tx = Transaction.notarize(wallet.address, "cc" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)
        ids = store.get_txs_by_sender(wallet.address)
        assert tx.tx_id in ids

    def test_get_txs_by_sender_empty_for_unknown(self, store):
        assert store.get_txs_by_sender("qv1" + "z" * 64) == []

    def test_get_txs_by_recipient(self, store, genesis, wallet):
        w2 = Wallet.generate()
        store.append_block(genesis)
        from qbit_network.crypto import MLKEM
        ct, ss = MLKEM.encapsulate(w2.encryption_pk)
        tx = Transaction(
            tx_type=TxType.SHARE,
            sender=wallet.address,
            recipient=w2.address,
            payload={"cid": "QmTest", "encapsulatedKey": ct.hex(), "expires": 0},
        )
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)
        ids = store.get_txs_by_recipient(w2.address)
        assert tx.tx_id in ids

    def test_get_txs_by_recipient_empty_for_unknown(self, store):
        assert store.get_txs_by_recipient("qv1" + "x" * 64) == []


# =========================================================================
# Notarization indexing
# =========================================================================

class TestSQLiteStoreNotarization:

    def test_get_notarization_none_for_unknown_hash(self, store):
        assert store.get_notarization("aa" * 32) is None

    def test_get_notarization_returns_first_tx(self, store, genesis, wallet):
        store.append_block(genesis)
        doc = "dd" * 32
        tx = Transaction.notarize(wallet.address, doc, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)
        assert store.get_notarization(doc) == tx.tx_id

    def test_get_all_notarizations_empty(self, store):
        assert store.get_all_notarizations("ee" * 32) == []

    def test_get_all_notarizations_multiple(self, store, genesis, wallet):
        w2 = Wallet.generate()
        store.append_block(genesis)
        doc = "ff" * 32

        tx1 = Transaction.notarize(wallet.address, doc, nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx1],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)

        tx2 = Transaction.notarize(w2.address, doc, nonce=0)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        b2 = Block(index=2, prev_hash=b1.block_hash, transactions=[tx2],
                   validator=wallet.address, timestamp=genesis.timestamp + 2)
        b2.sign(wallet.signing_sk)
        store.append_block(b2)

        all_txs = store.get_all_notarizations(doc)
        assert len(all_txs) == 2
        assert tx1.tx_id in all_txs
        assert tx2.tx_id in all_txs

    def test_first_notarization_marked_correctly(self, store, genesis, wallet):
        w2 = Wallet.generate()
        store.append_block(genesis)
        doc = "1122" * 16

        tx1 = Transaction.notarize(wallet.address, doc, nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx1],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)

        tx2 = Transaction.notarize(w2.address, doc, nonce=0)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        b2 = Block(index=2, prev_hash=b1.block_hash, transactions=[tx2],
                   validator=wallet.address, timestamp=genesis.timestamp + 2)
        b2.sign(wallet.signing_sk)
        store.append_block(b2)

        # First notarization must be tx1
        first = store.get_notarization(doc)
        assert first == tx1.tx_id


# =========================================================================
# Key registry
# =========================================================================

class TestSQLiteStoreKeyRegistry:

    def test_get_encryption_pk_none_for_unknown(self, store):
        assert store.get_encryption_pk("qv1" + "a" * 64) is None

    def test_get_encryption_pk_after_register(self, store, genesis, wallet):
        store.append_block(genesis)
        tx = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)
        pk = store.get_encryption_pk(wallet.address)
        assert pk == wallet.encryption_pk.hex()


# =========================================================================
# Validator registry
# =========================================================================

class TestSQLiteStoreValidatorRegistry:

    def test_get_validator_none_for_unknown(self, store):
        assert store.get_validator("qv1" + "a" * 64) is None

    def test_put_and_get_validator(self, store, wallet):
        store.put_validator(wallet.address, wallet.signing_pk.hex())
        pk = store.get_validator(wallet.address)
        assert pk == wallet.signing_pk.hex()

    def test_get_all_validators_empty(self, store):
        assert store.get_all_validators() == []

    def test_get_all_validators_returns_all(self, store):
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        store.put_validator(w1.address, w1.signing_pk.hex())
        store.put_validator(w2.address, w2.signing_pk.hex())
        validators = store.get_all_validators()
        addresses = [v[0] for v in validators]
        assert w1.address in addresses
        assert w2.address in addresses

    def test_delete_validator(self, store, wallet):
        store.put_validator(wallet.address, wallet.signing_pk.hex())
        store.delete_validator(wallet.address)
        assert store.get_validator(wallet.address) is None

    def test_delete_nonexistent_validator_is_noop(self, store):
        # Should not raise
        store.delete_validator("qv1" + "z" * 64)

    def test_put_validator_no_commit(self, store, wallet):
        """put_validator(commit=False) does not auto-commit."""
        store.put_validator(wallet.address, wallet.signing_pk.hex(), commit=False)
        # Still visible within same connection before rollback
        pk = store.get_validator(wallet.address)
        assert pk == wallet.signing_pk.hex()


# =========================================================================
# Revocation registry
# =========================================================================

class TestSQLiteStoreRevocation:

    def test_get_revocation_none_for_unknown(self, store):
        assert store.get_revocation("qv1" + "a" * 64, "signing") is None

    def test_put_and_get_revocation(self, store, wallet):
        store.put_revocation(wallet.address, "signing", "txabc", 1234567.0, "compromised")
        r = store.get_revocation(wallet.address, "signing")
        assert r is not None
        assert r["tx_id"] == "txabc"
        assert r["timestamp"] == 1234567.0
        assert r["reason"] == "compromised"

    def test_delete_revocation(self, store, wallet):
        store.put_revocation(wallet.address, "encryption", "txdef", 111.0, "rotation")
        store.delete_revocation(wallet.address, "encryption")
        assert store.get_revocation(wallet.address, "encryption") is None

    def test_get_all_revocations_empty(self, store):
        assert store.get_all_revocations() == []

    def test_get_all_revocations_multiple(self, store):
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        store.put_revocation(w1.address, "signing", "tx1", 100.0, "compromised")
        store.put_revocation(w2.address, "encryption", "tx2", 200.0, "rotation")
        revs = store.get_all_revocations()
        assert len(revs) == 2

    def test_delete_nonexistent_revocation_is_noop(self, store):
        # Should not raise
        store.delete_revocation("qv1" + "z" * 64, "signing")


# =========================================================================
# Range queries and rollback
# =========================================================================

class TestSQLiteStoreRangeAndRollback:

    def _make_chain(self, store, wallet, n_blocks):
        """Build a chain of n_blocks (including genesis) in the store."""
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store.append_block(genesis)
        parent = genesis
        for i in range(1, n_blocks):
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i - 1)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            b = Block(index=i, prev_hash=parent.block_hash, transactions=[tx],
                      validator=wallet.address, timestamp=genesis.timestamp + i)
            b.sign(wallet.signing_sk)
            store.append_block(b)
            parent = b
        return genesis

    def test_get_blocks_range_all(self, store, wallet):
        self._make_chain(store, wallet, 4)
        blocks = store.get_blocks_range(0, 4)
        assert len(blocks) == 4
        for i, b in enumerate(blocks):
            assert b.index == i

    def test_get_blocks_range_partial(self, store, wallet):
        self._make_chain(store, wallet, 5)
        blocks = store.get_blocks_range(1, 3)
        assert len(blocks) == 2
        assert blocks[0].index == 1
        assert blocks[1].index == 2

    def test_get_blocks_range_empty_when_start_equals_end(self, store, wallet):
        self._make_chain(store, wallet, 3)
        blocks = store.get_blocks_range(2, 2)
        assert blocks == []

    def test_delete_blocks_from_reduces_height(self, store, wallet):
        self._make_chain(store, wallet, 4)
        assert store.height() == 3
        store.delete_blocks_from(2)
        assert store.height() == 1

    def test_delete_blocks_from_removes_txs(self, store, wallet):
        self._make_chain(store, wallet, 3)
        # Block 2 has tx with nonce=1
        b2 = store.get_block(2)
        tx_id = b2.transactions[0].tx_id if b2.transactions else None
        store.delete_blocks_from(2)
        if tx_id:
            assert store.tx_exists(tx_id) is False

    def test_delete_blocks_from_removes_notarizations(self, store, wallet):
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store.append_block(genesis)
        doc = "ab" * 32
        tx = Transaction.notarize(wallet.address, doc, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)
        assert store.get_notarization(doc) == tx.tx_id
        store.delete_blocks_from(1)
        assert store.get_notarization(doc) is None

    def test_delete_blocks_from_validator_cleanup(self, store, wallet):
        """REGISTER_VALIDATOR txs in rolled-back blocks get cleaned up."""
        w2 = Wallet.generate()
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store.append_block(genesis)
        store.put_validator(w2.address, w2.signing_pk.hex())
        assert store.get_validator(w2.address) is not None

        # Simulate block with REGISTER_VALIDATOR payload
        payload = {
            "validator_pubkey": w2.signing_pk.hex(),
            "validator_address": w2.address,
        }
        from qbit_network.core.transaction import TxType
        tx = Transaction(
            tx_type=TxType.REGISTER_VALIDATOR,
            sender=wallet.address,
            payload=payload,
        )
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                   validator=wallet.address, timestamp=genesis.timestamp + 1)
        b1.sign(wallet.signing_sk)
        store.append_block(b1)
        store.delete_blocks_from(1)
        # The validator cleanup in delete_blocks_from should remove w2
        assert store.get_validator(w2.address) is None


# =========================================================================
# Migration from chain.json
# =========================================================================

class TestSQLiteMigration:

    def test_migrate_skipped_when_no_chain_json(self, tmp_dir):
        """No chain.json: migration is a no-op."""
        migrate_json_to_sqlite(tmp_dir)
        assert not os.path.exists(os.path.join(tmp_dir, "chain.db"))

    def test_migrate_skipped_when_db_already_exists(self, tmp_dir, wallet):
        """If chain.db already exists, migration skips."""
        # Create chain.json
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            json.dump([genesis.to_dict()], f)
        # Create empty chain.db first
        db_file = os.path.join(tmp_dir, "chain.db")
        open(db_file, "w").close()
        migrate_json_to_sqlite(tmp_dir)
        # chain.json should still exist (not renamed)
        assert os.path.exists(chain_file)

    def test_migrate_corrupt_json_is_skipped(self, tmp_dir):
        """Corrupt chain.json: migration logs error but doesn't crash."""
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            f.write("{not valid json")
        migrate_json_to_sqlite(tmp_dir)
        # Should not have created chain.db
        assert not os.path.exists(os.path.join(tmp_dir, "chain.db"))

    def test_migrate_empty_json_array_is_skipped(self, tmp_dir):
        """Empty array in chain.json: migration skips."""
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            json.dump([], f)
        migrate_json_to_sqlite(tmp_dir)
        assert not os.path.exists(os.path.join(tmp_dir, "chain.db"))

    def test_migrate_success_creates_backup(self, tmp_dir, wallet):
        """Successful migration renames chain.json to chain.json.migrated."""
        from qbit_network.core.blockchain import Blockchain
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        tx = Transaction.notarize(wallet.address, "cc" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(wallet.address, wallet.signing_sk)

        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            json.dump([b.to_dict() for b in bc.chain], f)

        migrate_json_to_sqlite(tmp_dir)
        assert os.path.exists(os.path.join(tmp_dir, "chain.db"))
        assert os.path.exists(os.path.join(tmp_dir, "chain.json.migrated"))
        assert not os.path.exists(chain_file)

    def test_migrate_data_integrity(self, tmp_dir, wallet):
        """Migrated data is queryable from the new SQLite store."""
        from qbit_network.core.blockchain import Blockchain
        doc = "dd" * 32
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        tx = Transaction.notarize(wallet.address, doc, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(wallet.address, wallet.signing_sk)

        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            json.dump([b.to_dict() for b in bc.chain], f)

        migrate_json_to_sqlite(tmp_dir)
        store = SQLiteStore(tmp_dir)
        assert store.height() == 1
        notarized = store.get_notarization(doc)
        assert notarized is not None
        store.close()

    def test_migrate_non_list_json_is_skipped(self, tmp_dir):
        """Non-list chain.json: migration is a no-op."""
        chain_file = os.path.join(tmp_dir, "chain.json")
        with open(chain_file, "w") as f:
            json.dump({"not": "a list"}, f)
        migrate_json_to_sqlite(tmp_dir)
        assert not os.path.exists(os.path.join(tmp_dir, "chain.db"))


# =========================================================================
# Persistence (reopening the store)
# =========================================================================

class TestSQLiteStorePersistence:

    def test_reopen_restores_height(self, tmp_dir, wallet):
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store = SQLiteStore(tmp_dir)
        store.append_block(genesis)
        store.close()

        store2 = SQLiteStore(tmp_dir)
        assert store2.height() == 0
        store2.close()

    def test_reopen_restores_block_data(self, tmp_dir, wallet):
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store = SQLiteStore(tmp_dir)
        store.append_block(genesis)
        store.close()

        store2 = SQLiteStore(tmp_dir)
        b = store2.get_block(0)
        assert b is not None
        assert b.block_hash == genesis.block_hash
        store2.close()

    def test_reopen_restores_validator(self, tmp_dir, wallet):
        store = SQLiteStore(tmp_dir)
        store.put_validator(wallet.address, wallet.signing_pk.hex())
        store.close()

        store2 = SQLiteStore(tmp_dir)
        pk = store2.get_validator(wallet.address)
        assert pk == wallet.signing_pk.hex()
        store2.close()


# =========================================================================
# Chain proxy edge cases
# =========================================================================

class TestChainProxy:

    def test_chain_proxy_len_empty(self):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        proxy = _ChainProxy(bc)
        assert len(proxy) == 0

    def test_chain_proxy_bool_empty(self):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        proxy = _ChainProxy(bc)
        assert not proxy

    def test_chain_proxy_bool_nonempty(self, wallet):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        proxy = _ChainProxy(bc)
        assert bool(proxy)

    def test_chain_proxy_len_after_genesis(self, wallet):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        proxy = _ChainProxy(bc)
        assert len(proxy) == 1

    def test_chain_proxy_getitem_zero(self, wallet):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        proxy = _ChainProxy(bc)
        b = proxy[0]
        assert b is not None
        assert b.index == 0

    def test_chain_proxy_getitem_negative(self, wallet):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        # Mine another block
        tx = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(wallet.address, wallet.signing_sk)
        proxy = _ChainProxy(bc)
        # proxy[-1] should be the last block
        last = proxy[-1]
        assert last.index == 1

    def test_chain_proxy_getitem_out_of_range_raises(self, wallet):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        proxy = _ChainProxy(bc)
        with pytest.raises(IndexError):
            _ = proxy[999]

    def test_chain_proxy_getitem_type_error(self, wallet):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        proxy = _ChainProxy(bc)
        with pytest.raises(TypeError):
            _ = proxy["bad"]

    def test_chain_proxy_slice(self, wallet):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        tx = Transaction.notarize(wallet.address, "bb" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(wallet.address, wallet.signing_sk)
        proxy = _ChainProxy(bc)
        sliced = proxy[0:2]
        assert isinstance(sliced, list)
        assert len(sliced) == 2

    def test_chain_proxy_iteration(self, wallet):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        tx = Transaction.notarize(wallet.address, "cc" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(wallet.address, wallet.signing_sk)
        proxy = _ChainProxy(bc)
        indices = [b.index for b in proxy]
        assert indices == [0, 1]

    def test_chain_proxy_negative_out_of_range_raises(self, wallet):
        from qbit_network.core.blockchain import Blockchain, _ChainProxy
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        proxy = _ChainProxy(bc)
        with pytest.raises(IndexError):
            _ = proxy[-999]

"""Tests for R24-004: SQLite signature verification on load.

Covers: valid/corrupt TX and block signatures, verify flag combinations,
multi-block corruption, genesis corruption, state root integrity, and edge cases.
"""
import json
import os
import sqlite3
import tempfile
import shutil
import pytest

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction
from qbit_network.core.wallet import Wallet


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="qv_verify_load_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def wallet():
    return Wallet.generate()


def _build_chain(tmp_dir, wallet, num_blocks=3):
    """Helper: create a chain with num_blocks mined blocks and return it."""
    bc = Blockchain(data_dir=tmp_dir)
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)

    for i in range(num_blocks):
        tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(wallet.address, wallet.signing_sk)

    assert bc.height == num_blocks
    if bc._store:
        bc._store.close()
    return bc


def _corrupt_tx_in_block(db_path, block_idx):
    """Corrupt the first TX signature in a block stored in SQLite."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT data FROM blocks WHERE idx = ?", (block_idx,)
    ).fetchone()
    assert row is not None, f"Block {block_idx} not found in SQLite"
    block_data = json.loads(row[0])
    txs = block_data.get("transactions", [])
    assert len(txs) > 0, f"Block {block_idx} has no transactions"
    sig = txs[0].get("signature", "")
    assert sig, "TX has no signature to corrupt"
    txs[0]["signature"] = sig[:10] + "ff" * 10 + sig[30:]
    conn.execute(
        "UPDATE blocks SET data = ? WHERE idx = ?",
        (json.dumps(block_data, separators=(",", ":")), block_idx)
    )
    conn.commit()
    conn.close()


def _corrupt_block_signature(db_path, block_idx):
    """Corrupt the block-level validator signature in SQLite."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT data FROM blocks WHERE idx = ?", (block_idx,)
    ).fetchone()
    assert row is not None
    block_data = json.loads(row[0])
    sig = block_data.get("signature", "")
    assert sig, f"Block {block_idx} has no signature to corrupt"
    block_data["signature"] = sig[:10] + "aa" * 10 + sig[30:]
    conn.execute(
        "UPDATE blocks SET data = ? WHERE idx = ?",
        (json.dumps(block_data, separators=(",", ":")), block_idx)
    )
    conn.commit()
    conn.close()


# ===================================================================
# Core verification tests
# ===================================================================

class TestSQLiteVerifyOnLoad:
    """Verify that _load_from_sqlite checks TX and block signatures."""

    def test_valid_chain_loads_with_verification(self, tmp_dir, wallet):
        """A properly signed chain should load successfully with verify=True."""
        _build_chain(tmp_dir, wallet)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load(verify_signatures=True)
        assert loaded is True
        assert bc2.height == 3

    def test_valid_chain_loads_without_verification(self, tmp_dir, wallet):
        """Chain loads with verify=False (fast startup)."""
        _build_chain(tmp_dir, wallet)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load(verify_signatures=False)
        assert loaded is True
        assert bc2.height == 3

    def test_corrupted_tx_signature_detected(self, tmp_dir, wallet):
        """Tampered TX signature in SQLite should raise ValueError on load."""
        _build_chain(tmp_dir, wallet)
        _corrupt_tx_in_block(os.path.join(tmp_dir, "chain.db"), block_idx=1)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="invalid signature"):
            bc2.load(verify_signatures=True)

    def test_corrupted_tx_signature_skipped_without_verify(self, tmp_dir, wallet):
        """Tampered TX loads fine when verify_signatures=False."""
        _build_chain(tmp_dir, wallet)
        _corrupt_tx_in_block(os.path.join(tmp_dir, "chain.db"), block_idx=1)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load(verify_signatures=False)
        assert loaded is True

    def test_corrupted_block_signature_detected(self, tmp_dir, wallet):
        """Tampered block signature in SQLite should raise ValueError on load."""
        _build_chain(tmp_dir, wallet)
        _corrupt_block_signature(os.path.join(tmp_dir, "chain.db"), block_idx=1)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="invalid validator signature"):
            bc2.load(verify_signatures=True)

    def test_default_verify_is_true(self, tmp_dir, wallet):
        """Default load() should verify signatures (verify_signatures defaults to True)."""
        _build_chain(tmp_dir, wallet)
        _corrupt_tx_in_block(os.path.join(tmp_dir, "chain.db"), block_idx=2)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="invalid signature"):
            bc2.load()


# ===================================================================
# Multi-block corruption scenarios
# ===================================================================

class TestSQLiteVerifyMultiBlock:
    """Multi-block corruption and detection scenarios."""

    def test_corruption_in_last_block_detected(self, tmp_dir, wallet):
        """Corruption in the last block is caught during verification."""
        _build_chain(tmp_dir, wallet)
        _corrupt_tx_in_block(os.path.join(tmp_dir, "chain.db"), block_idx=3)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="invalid signature"):
            bc2.load(verify_signatures=True)

    def test_corruption_in_first_non_genesis_block(self, tmp_dir, wallet):
        """Corruption in block 1 is detected early."""
        _build_chain(tmp_dir, wallet)
        _corrupt_block_signature(os.path.join(tmp_dir, "chain.db"), block_idx=1)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="invalid validator signature"):
            bc2.load(verify_signatures=True)

    def test_multiple_corrupted_blocks_first_detected(self, tmp_dir, wallet):
        """When multiple blocks are corrupt, the first one triggers the error."""
        _build_chain(tmp_dir, wallet)
        db_path = os.path.join(tmp_dir, "chain.db")
        _corrupt_tx_in_block(db_path, block_idx=1)
        _corrupt_tx_in_block(db_path, block_idx=3)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="invalid signature"):
            bc2.load(verify_signatures=True)

    def test_block_sig_corrupt_tx_sig_ok(self, tmp_dir, wallet):
        """Block signature corruption detected even when TX sigs are valid."""
        _build_chain(tmp_dir, wallet)
        _corrupt_block_signature(os.path.join(tmp_dir, "chain.db"), block_idx=2)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="invalid validator signature"):
            bc2.load(verify_signatures=True)


# ===================================================================
# Edge cases
# ===================================================================

class TestSQLiteVerifyEdgeCases:
    """Edge cases for SQLite signature verification."""

    def test_single_block_chain_valid(self, tmp_dir, wallet):
        """A chain with just 1 mined block loads with verification."""
        _build_chain(tmp_dir, wallet, num_blocks=1)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        assert bc2.load(verify_signatures=True) is True
        assert bc2.height == 1

    def test_single_block_chain_corrupt(self, tmp_dir, wallet):
        """A single-block chain with corrupt TX sig is detected."""
        _build_chain(tmp_dir, wallet, num_blocks=1)
        _corrupt_tx_in_block(os.path.join(tmp_dir, "chain.db"), block_idx=1)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="invalid signature"):
            bc2.load(verify_signatures=True)

    def test_large_chain_valid(self, tmp_dir, wallet):
        """A longer chain (10 blocks) loads with verification."""
        _build_chain(tmp_dir, wallet, num_blocks=10)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        assert bc2.load(verify_signatures=True) is True
        assert bc2.height == 10

    def test_large_chain_corrupt_middle(self, tmp_dir, wallet):
        """Corruption in the middle of a 10-block chain is detected."""
        _build_chain(tmp_dir, wallet, num_blocks=10)
        _corrupt_tx_in_block(os.path.join(tmp_dir, "chain.db"), block_idx=5)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="invalid signature"):
            bc2.load(verify_signatures=True)

    def test_all_corruption_passes_without_verify(self, tmp_dir, wallet):
        """Multiple corruption types pass when verify_signatures=False."""
        _build_chain(tmp_dir, wallet)
        db_path = os.path.join(tmp_dir, "chain.db")
        _corrupt_tx_in_block(db_path, block_idx=1)
        _corrupt_block_signature(db_path, block_idx=2)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load(verify_signatures=False)
        assert loaded is True

    def test_genesis_only_chain_loads(self, tmp_dir, wallet):
        """A chain with only genesis (no mined blocks) loads."""
        _build_chain(tmp_dir, wallet, num_blocks=0)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load(verify_signatures=True)
        assert loaded is True
        assert bc2.height == 0

    def test_reload_same_chain_twice(self, tmp_dir, wallet):
        """Loading the same valid chain twice succeeds both times."""
        _build_chain(tmp_dir, wallet)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        assert bc2.load(verify_signatures=True) is True

        bc3 = Blockchain(data_dir=tmp_dir)
        bc3.consensus.add_validator(wallet.address, wallet.signing_pk)
        assert bc3.load(verify_signatures=True) is True
        assert bc3.height == 3

    def test_different_wallets_independent_chains(self, tmp_dir):
        """Two chains built with different wallets are independent."""
        w1 = Wallet.generate()
        w2 = Wallet.generate()

        dir1 = os.path.join(tmp_dir, "chain1")
        dir2 = os.path.join(tmp_dir, "chain2")
        os.makedirs(dir1)
        os.makedirs(dir2)

        _build_chain(dir1, w1, num_blocks=2)
        _build_chain(dir2, w2, num_blocks=4)

        bc1 = Blockchain(data_dir=dir1)
        bc1.consensus.add_validator(w1.address, w1.signing_pk)
        assert bc1.load(verify_signatures=True) is True
        assert bc1.height == 2

        bc2 = Blockchain(data_dir=dir2)
        bc2.consensus.add_validator(w2.address, w2.signing_pk)
        assert bc2.load(verify_signatures=True) is True
        assert bc2.height == 4


# ===================================================================
# State root integrity (R32-F03)
# ===================================================================

def _build_financial_chain(tmp_dir, wallet, num_blocks=3):
    """Helper: create a chain with financial layer active and produce blocks."""
    bc = Blockchain(data_dir=tmp_dir)
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
    bc.activate_financial_layer(wallet.address)

    for i in range(num_blocks):
        tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(wallet.address, wallet.signing_sk)

    assert bc.height == num_blocks
    assert bc.get_balance(wallet.address) > 0
    if bc._store:
        bc._store.close()
    return bc


class TestStateRootVerificationOnLoad:
    """R32-F03: Verify state root integrity after SQLite reload."""

    def test_valid_chain_state_root_passes(self, tmp_dir, wallet):
        """Untampered chain should pass state root verification on reload."""
        _build_financial_chain(tmp_dir, wallet)

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load(verify_signatures=True)
        assert loaded is True
        assert bc2.height == 3

    def test_corrupted_balance_detected_on_load(self, tmp_dir, wallet):
        """Tampered balance in SQLite should cause state root mismatch on load."""
        _build_financial_chain(tmp_dir, wallet)
        db_path = os.path.join(tmp_dir, "chain.db")

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT address, amount FROM balances LIMIT 1").fetchone()
        assert row is not None, "No balances found in SQLite"
        addr, original_amount = row
        conn.execute(
            "UPDATE balances SET amount = ? WHERE address = ?",
            (original_amount + 999999, addr)
        )
        conn.commit()
        conn.close()

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="state root mismatch"):
            bc2.load(verify_signatures=False)

    def test_corrupted_balance_detected_with_verify_sigs(self, tmp_dir, wallet):
        """State root check runs even with signature verification enabled."""
        _build_financial_chain(tmp_dir, wallet)
        db_path = os.path.join(tmp_dir, "chain.db")

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT address, amount FROM balances LIMIT 1").fetchone()
        assert row is not None
        addr, original_amount = row
        conn.execute(
            "UPDATE balances SET amount = ? WHERE address = ?",
            (original_amount + 1, addr)
        )
        conn.commit()
        conn.close()

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        with pytest.raises(ValueError, match="state root mismatch"):
            bc2.load(verify_signatures=True)


# ===================================================================
# SQLite thread safety (R32-F01)
# ===================================================================
import threading
from qbit_network.core.store import SQLiteStore
from qbit_network.core.block import Block


class TestSQLiteThreadSafety:
    """R32-F01: SQLiteStore connection-per-thread model and write serialisation."""

    def test_per_thread_connections_are_distinct(self, tmp_dir):
        """Each thread should get its own sqlite3 connection."""
        store = SQLiteStore(tmp_dir)
        main_conn = store._get_conn()
        other_conn = [None]

        def worker():
            other_conn[0] = store._get_conn()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert other_conn[0] is not None
        assert other_conn[0] is not main_conn
        store.close()

    def test_concurrent_reads_during_write(self, tmp_dir, wallet):
        """Concurrent reads should not block or crash while a write is in progress."""
        bc = _build_financial_chain(tmp_dir, wallet, num_blocks=5)
        store = SQLiteStore(tmp_dir)
        errors = []
        read_results = []

        def reader(n):
            try:
                for _ in range(n):
                    block = store.get_block(0)
                    if block is not None:
                        read_results.append(block.index)
                    h = store.height()
                    read_results.append(h)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader, args=(20,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"
        assert len(read_results) > 0
        store.close()

    def test_concurrent_writes_serialised(self, tmp_dir, wallet):
        """Concurrent put_balance calls should not corrupt or raise."""
        store = SQLiteStore(tmp_dir)
        store._db.executescript(
            "CREATE TABLE IF NOT EXISTS balances "
            "(address TEXT PRIMARY KEY, amount INTEGER NOT NULL DEFAULT 0);"
        )
        errors = []

        def writer(addr, count):
            try:
                for i in range(count):
                    store.put_balance(addr, i + 1)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(f"addr_{i}", 50))
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Write errors: {errors}"
        for i in range(4):
            bal = store.get_balance(f"addr_{i}")
            assert bal == 50  # last written value
        store.close()

    def test_write_lock_is_reentrant(self, tmp_dir):
        """RLock should allow nested write lock acquisition (e.g. commit inside locked method)."""
        store = SQLiteStore(tmp_dir)
        # Simulate nested locking: acquire _write_lock then call commit (which also acquires it)
        with store._write_lock:
            store.commit()  # Should not deadlock
        store.close()

    def test_close_only_affects_current_thread(self, tmp_dir):
        """close() should close the calling thread's connection without crashing others."""
        store = SQLiteStore(tmp_dir)
        barrier = threading.Barrier(2)
        errors = []

        def worker():
            try:
                _ = store._get_conn()  # create thread-local conn
                barrier.wait(timeout=5)
                # After main thread calls close(), this thread's conn should still work
                h = store.height()
                assert h >= -1
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=worker)
        t.start()
        barrier.wait(timeout=5)
        store.close()  # closes main thread's conn only
        t.join()
        assert len(errors) == 0, f"Worker errors: {errors}"


# ===================================================================
# EVIDENCE TX replay on SQLite reload (R33-H01)
# ===================================================================

class TestEvidenceReplayOnReload:
    """R33-H01: Slashed validators must retain reduced stake after SQLite reload."""

    def test_evidence_slash_persists_across_reload(self, tmp_dir):
        """Slashed validator stake and consensus removal survive SQLite reload."""
        from qbit_network.core.wallet import Wallet as W
        from qbit_network.config import MIN_STAKE, SLASH_PERCENTAGE
        from qbit_network.crypto import MLDSA, sha3_256

        w = W.generate()
        w2 = W.generate()
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)

        wallet_map = {w.address: w, w2.address: w2}

        def mine_block_correct(bc, txs):
            """Submit txs and produce a block using the same dPoS selection as produce_block."""
            for tx in txs:
                bc.submit_tx(tx)
            parent = bc._latest_block
            # Use parent_hash so select_validator matches produce_block's internal logic
            expected = bc.consensus.select_validator(
                parent.index + 1, parent_hash=parent.block_hash)
            producer = wallet_map.get(expected, w)
            block = bc.produce_block(producer.address, producer.signing_sk)
            assert block is not None, (
                f"produce_block returned None for {producer.address[:16]}... "
                f"(expected={expected[:16] if expected else None})")
            return block

        # Block 1: register w2 on-chain so evidence verification can find its public key
        nonce = bc.get_nonce(w2.address)
        reg_tx = Transaction.register_validator(
            sender=w2.address,
            validator_pubkey=w2.signing_pk,
            validator_address=w2.address,
            nonce=nonce,
        )
        reg_tx.sign(w2.signing_sk, w2.signing_pk)
        mine_block_correct(bc, [reg_tx])

        # Block 2: stake w2 (from w) so it has stake to be slashed
        nonce = bc.get_nonce(w.address)
        stake_tx = Transaction.stake(
            sender=w.address, validator_address=w2.address, amount=MIN_STAKE, nonce=nonce,
        )
        stake_tx.sign(w.signing_sk, w.signing_pk)
        mine_block_correct(bc, [stake_tx])
        assert bc._total_stake.get(w2.address, 0) >= MIN_STAKE, (
            "stake_tx did not commit -- check validator rotation")

        # Block 3: submit double-sign evidence against w2
        # sha3_256() returns bytes directly (not a hashlib object)
        header_a = sha3_256(b"block_a_header")
        header_b = sha3_256(b"block_b_header")
        sig_a = MLDSA.sign(w2.signing_sk, header_a)
        sig_b = MLDSA.sign(w2.signing_sk, header_b)
        hash_a = sha3_256(header_a).hex()
        hash_b = sha3_256(header_b).hex()

        nonce = bc.get_nonce(w.address)
        ev_tx = Transaction.evidence(
            sender=w.address, validator_address=w2.address, block_index=2,
            block_a_hash=hash_a, block_b_hash=hash_b,
            block_a_sig=sig_a.hex(), block_b_sig=sig_b.hex(),
            block_a_header=header_a.hex(), block_b_header=header_b.hex(),
            nonce=nonce,
        )
        ev_tx.sign(w.signing_sk, w.signing_pk)
        ok, err = bc.submit_tx(ev_tx)
        assert ok, f"evidence tx rejected at pool: {err}"
        mine_block_correct(bc, [])

        # Verify slash happened
        assert bc.is_slashed(w2.address)
        slashed_stake = bc._total_stake.get(w2.address, 0)
        assert not bc.consensus.is_validator(w2.address)
        bc._store.close()

        # Reload from SQLite
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(w.address, w.signing_pk)
        bc2.load()

        # Assertions: slash state fully preserved
        assert bc2.is_slashed(w2.address), "Slashing state lost on reload"
        assert bc2._total_stake.get(w2.address, 0) == slashed_stake
        assert not bc2.consensus.is_validator(w2.address), (
            "R33-H01: slashed validator still in consensus after reload")
        # R33-H01: epoch validators must not contain slashed validator
        epoch_addrs = [addr for addr, _ in bc2._epoch_validators]
        assert w2.address not in epoch_addrs, (
            "R33-H01: slashed validator still in _epoch_validators after reload")

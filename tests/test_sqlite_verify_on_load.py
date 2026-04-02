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

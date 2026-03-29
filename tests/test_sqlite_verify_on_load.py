"""Tests for R24-004: SQLite signature verification on load."""
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
    # Corrupt the first TX signature
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

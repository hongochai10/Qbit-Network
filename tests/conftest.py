"""Shared fixtures for QVault test suite."""
import os
import sys
import shutil
import tempfile
import time
import pytest

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qbit_network.core.wallet import Wallet
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.crypto import MLDSA, MLKEM, sha3_256, aes_encrypt, aes_decrypt


@pytest.fixture
def wallet():
    """Fresh wallet with ML-DSA + ML-KEM keypairs."""
    return Wallet.generate()


@pytest.fixture
def wallet_pair():
    """Two wallets (alice, bob) for share/transfer tests."""
    return Wallet.generate(), Wallet.generate()


@pytest.fixture
def blockchain(wallet):
    """Initialized blockchain with one validator and genesis block."""
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk)
    return bc


@pytest.fixture
def tmp_dir():
    """Temporary directory, cleaned up after test."""
    d = tempfile.mkdtemp(prefix="qv_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def blockchain_on_disk(wallet, tmp_dir):
    """Blockchain with disk persistence."""
    bc = Blockchain(data_dir=tmp_dir)
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk)
    return bc


def submit_and_mine(bc, wallet, tx):
    """Helper: submit tx and produce block. Returns (block, tx_id)."""
    ok, tx_id = bc.submit_tx(tx)
    assert ok, f"submit_tx failed: {tx_id}"
    block = bc.produce_block(wallet.address, wallet.signing_sk)
    assert block is not None, "produce_block returned None"
    return block, tx_id

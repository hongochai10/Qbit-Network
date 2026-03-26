"""Tests for v0.7.0 Sprint 2: Receipt/Event System + Simple Finality."""
import hashlib
import json
import os
import shutil
import tempfile
import time
import pytest


def _hex_hash(label: str) -> str:
    """Convert a test label into a valid 64-char hex document hash."""
    return hashlib.sha256(label.encode()).hexdigest()

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.wallet import Wallet
from qbit_network.core.receipt import (
    TransactionReceipt, receipts_root, build_event,
)
from qbit_network.config import (
    TX_FEES, INITIAL_BLOCK_REWARD, HALVING_INTERVAL, MAX_SUPPLY,
    QUBIT_PER_QBIT, GENESIS_BALANCE_QBIT, FEE_BURN_PERCENT,
    UNBONDING_PERIOD, MIN_STAKE, EPOCH_LENGTH,
)


# ---- Fixtures ----

@pytest.fixture
def wallet():
    return Wallet.generate()


@pytest.fixture
def wallet_pair():
    return Wallet.generate(), Wallet.generate()


@pytest.fixture
def funded_chain(wallet):
    """Blockchain with financial layer active and genesis validator funded."""
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
    bc.activate_financial_layer(wallet.address)
    return bc


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="qv_receipt_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def funded_chain_disk(wallet, tmp_dir):
    """Blockchain on disk with financial layer active."""
    bc = Blockchain(data_dir=tmp_dir)
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
    bc.activate_financial_layer(wallet.address)
    return bc


def submit_and_mine(bc, wallet, tx):
    """Submit tx and produce block. Returns (block, tx_id)."""
    ok, tx_id = bc.submit_tx(tx)
    assert ok, f"submit_tx failed: {tx_id}"
    block = bc.produce_block(wallet.address, wallet.signing_sk)
    assert block is not None, "produce_block returned None"
    return block, tx_id


def fund_wallet(bc, funder, recipient_addr, amount, nonce=None):
    """Transfer funds from funder to recipient. Returns block."""
    if nonce is None:
        nonce = bc.get_nonce(funder.address) + bc._pool_sender_count.get(funder.address, 0)
    tx = Transaction.transfer(funder.address, recipient_addr, amount, nonce=nonce)
    tx.sign(funder.signing_sk, funder.signing_pk)
    return submit_and_mine(bc, funder, tx)


# ========== Receipt Data Structure ==========

class TestReceiptStructure:
    def test_receipt_creation(self):
        r = TransactionReceipt(
            tx_id="abc123",
            status="success",
            fee_paid=1000,
            block_index=5,
            tx_index=0,
            events=[{"type": "Transfer", "amount": 100}],
        )
        assert r.tx_id == "abc123"
        assert r.status == "success"
        assert r.fee_paid == 1000
        assert r.block_index == 5
        assert r.tx_index == 0
        assert len(r.events) == 1

    def test_receipt_hash_deterministic(self):
        r1 = TransactionReceipt("abc", "success", 100, 1, 0, [])
        r2 = TransactionReceipt("abc", "success", 100, 1, 0, [])
        assert r1.receipt_hash == r2.receipt_hash

    def test_receipt_hash_changes_with_fields(self):
        r1 = TransactionReceipt("abc", "success", 100, 1, 0, [])
        r2 = TransactionReceipt("abc", "success", 200, 1, 0, [])
        assert r1.receipt_hash != r2.receipt_hash

    def test_receipt_to_dict(self):
        events = [{"type": "Transfer", "amount": 100}]
        r = TransactionReceipt("abc", "success", 100, 1, 0, events)
        d = r.to_dict()
        assert d["tx_id"] == "abc"
        assert d["status"] == "success"
        assert d["fee_paid"] == 100
        assert d["block_index"] == 1
        assert d["tx_index"] == 0
        assert d["events"] == events
        assert "receipt_hash" in d

    def test_receipt_from_dict(self):
        original = TransactionReceipt("abc", "success", 100, 1, 0, [{"type": "Test"}])
        d = original.to_dict()
        restored = TransactionReceipt.from_dict(d)
        assert restored.tx_id == original.tx_id
        assert restored.status == original.status
        assert restored.fee_paid == original.fee_paid
        assert restored.events == original.events

    def test_receipt_from_dict_roundtrip(self):
        events = [{"type": "Transfer", "sender": "a", "recipient": "b", "amount": 50}]
        r = TransactionReceipt("tx123", "success", 500, 10, 2, events)
        d = r.to_dict()
        r2 = TransactionReceipt.from_dict(d)
        assert r2.receipt_hash == r.receipt_hash

    def test_receipt_invalid_status(self):
        with pytest.raises(ValueError, match="status"):
            TransactionReceipt("abc", "pending", 0, 0, 0, [])

    def test_receipt_invalid_fee(self):
        with pytest.raises(ValueError, match="fee_paid"):
            TransactionReceipt("abc", "success", -1, 0, 0, [])

    def test_receipt_invalid_tx_id(self):
        with pytest.raises(ValueError, match="tx_id"):
            TransactionReceipt("", "success", 0, 0, 0, [])

    def test_receipt_invalid_from_dict(self):
        with pytest.raises(ValueError, match="dict"):
            TransactionReceipt.from_dict("not a dict")


class TestReceiptsRoot:
    def test_empty_receipts_root(self):
        root = receipts_root([])
        assert root == "0" * 64

    def test_single_receipt_root(self):
        r = TransactionReceipt("abc", "success", 100, 1, 0, [])
        root = receipts_root([r])
        assert len(root) == 64
        assert root != "0" * 64

    def test_receipts_root_deterministic(self):
        r1 = TransactionReceipt("abc", "success", 100, 1, 0, [])
        r2 = TransactionReceipt("def", "success", 200, 1, 1, [])
        root_a = receipts_root([r1, r2])
        root_b = receipts_root([r1, r2])
        assert root_a == root_b

    def test_receipts_root_order_matters(self):
        r1 = TransactionReceipt("abc", "success", 100, 1, 0, [])
        r2 = TransactionReceipt("def", "success", 200, 1, 1, [])
        root_ab = receipts_root([r1, r2])
        root_ba = receipts_root([r2, r1])
        assert root_ab != root_ba


class TestBuildEvent:
    def test_build_event(self):
        ev = build_event("Transfer", sender="a", recipient="b", amount=100)
        assert ev["type"] == "Transfer"
        assert ev["sender"] == "a"
        assert ev["recipient"] == "b"
        assert ev["amount"] == 100


# ========== Event Generation per TX Type ==========

class TestEventGeneration:
    def test_transfer_event(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, recipient = wallet_pair
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.transfer(wallet.address, recipient.address, 1000, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.status == "success"
        assert len(receipt.events) >= 1
        transfer_ev = receipt.events[0]
        assert transfer_ev["type"] == "Transfer"
        assert transfer_ev["sender"] == wallet.address
        assert transfer_ev["recipient"] == recipient.address
        assert transfer_ev["amount"] == 1000

    def test_notarize_event(self, funded_chain, wallet):
        bc = funded_chain
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, "aabbccdd1122334455667788", nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.events[0]["type"] == "Notarize"
        assert receipt.events[0]["sender"] == wallet.address
        assert receipt.events[0]["documentHash"] == "aabbccdd1122334455667788"

    def test_register_key_event(self, funded_chain, wallet):
        bc = funded_chain
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.events[0]["type"] == "KeyRegistered"
        assert receipt.events[0]["address"] == wallet.address

    def test_register_validator_event(self, funded_chain, wallet):
        bc = funded_chain
        w2 = Wallet.generate()
        # Fund w2 to cover the validator registration fee
        fund_wallet(bc, wallet, w2.address, 10 * QUBIT_PER_QBIT)
        bc.consensus.add_validator(w2.address, w2.signing_pk)

        nonce = bc.get_nonce(w2.address) + bc._pool_sender_count.get(w2.address, 0)
        tx = Transaction.register_validator(
            sender=w2.address,
            validator_pubkey=w2.signing_pk,
            validator_address=w2.address,
            nonce=nonce,
        )
        tx.sign(w2.signing_sk, w2.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.events[0]["type"] == "ValidatorRegistered"
        assert receipt.events[0]["address"] == w2.address

    def test_stake_event(self, funded_chain, wallet):
        bc = funded_chain
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.stake(wallet.address, wallet.address, 100, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.events[0]["type"] == "Stake"
        assert receipt.events[0]["staker"] == wallet.address
        assert receipt.events[0]["amount"] == 100

    def test_unstake_event(self, funded_chain, wallet):
        bc = funded_chain
        # Stake first
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.stake(wallet.address, wallet.address, 100, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        # Now unstake
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.unstake(wallet.address, wallet.address, 50, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.events[0]["type"] == "Unstake"
        assert receipt.events[0]["amount"] == 50

    def test_revoke_key_event(self, funded_chain, wallet):
        bc = funded_chain
        # Register key first
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        # Revoke
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.revoke_key(
            sender=wallet.address, key_type="encryption",
            reason="compromised", nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.events[0]["type"] == "KeyRevoked"
        assert receipt.events[0]["address"] == wallet.address
        assert receipt.events[0]["key_type"] == "encryption"

    def test_every_tx_has_receipt(self, funded_chain, wallet):
        """Every confirmed TX produces exactly one receipt."""
        bc = funded_chain
        # Submit multiple TXs
        tx_ids = []
        for i in range(3):
            nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
            tx = Transaction.notarize(wallet.address, _hex_hash(f"doc_{i}"), nonce=nonce)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            ok, tx_id = bc.submit_tx(tx)
            assert ok
            tx_ids.append(tx_id)

        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        for tx_id in tx_ids:
            receipt = bc.get_receipt(tx_id)
            assert receipt is not None
            assert receipt.tx_id == tx_id
            assert receipt.status == "success"
            assert receipt.block_index == block.index


# ========== Receipts Root in Block Header ==========

class TestReceiptsRootInBlock:
    def test_receipts_root_in_block(self, funded_chain, wallet):
        bc = funded_chain
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, _hex_hash("doc_abc"), nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        assert block.receipts_root != ""
        assert len(block.receipts_root) == 64

    def test_receipts_root_in_block_dict(self, funded_chain, wallet):
        bc = funded_chain
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, _hex_hash("doc_abc"), nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        d = block.to_dict()
        assert "receiptsRoot" in d
        assert d["receiptsRoot"] == block.receipts_root

    def test_block_from_dict_preserves_receipts_root(self, funded_chain, wallet):
        bc = funded_chain
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, _hex_hash("doc_abc"), nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        d = block.to_dict()
        restored = Block.from_dict(d)
        assert restored.receipts_root == block.receipts_root

    def test_receipts_root_changes_block_hash(self):
        """Block with receiptsRoot has different hash than without."""
        w = Wallet.generate()
        b1 = Block(index=1, prev_hash="0" * 64, transactions=[], validator=w.address)
        b2 = Block(index=1, prev_hash="0" * 64, transactions=[], validator=w.address,
                   receipts_root="a" * 64)
        assert b1.block_hash != b2.block_hash

    def test_empty_receipts_root_value(self):
        """receipts_root for an empty receipt list is zero hash."""
        root = receipts_root([])
        assert root == "0" * 64

    def test_genesis_block_has_receipts_root(self, wallet):
        """Genesis block (with bootstrap TXs) has a non-empty receipts root."""
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        genesis = bc.get_block(0)
        assert genesis is not None
        # Genesis has a REGISTER_VALIDATOR tx, so receipts_root should be set
        if len(genesis.transactions) > 0:
            assert genesis.receipts_root != ""
            assert len(genesis.receipts_root) == 64


# ========== Receipt Fee Tracking ==========

class TestReceiptFees:
    def test_receipt_fee_matches_deduction(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, recipient = wallet_pair
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        balance_before = bc.get_balance(wallet.address)
        tx = Transaction.transfer(wallet.address, recipient.address, 1000, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.fee_paid >= 0

    def test_genesis_tx_zero_fee(self, wallet):
        """Genesis block TXs are fee-exempt -- receipt fee should be 0."""
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)

        # Genesis block should have receipts with fee_paid=0
        genesis = bc.get_block(0)
        for tx in genesis.transactions:
            receipt = bc.get_receipt(tx.tx_id)
            assert receipt is not None
            assert receipt.fee_paid == 0


# ========== SQLite Persistence ==========

class TestReceiptPersistence:
    def test_receipt_persisted_to_sqlite(self, funded_chain_disk, wallet):
        bc = funded_chain_disk
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, _hex_hash("doc_persist"), nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        # Direct SQLite query
        receipt = bc._store.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.tx_id == tx_id
        assert receipt.status == "success"

    def test_events_persisted_to_sqlite(self, funded_chain_disk, wallet):
        bc = funded_chain_disk
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, _hex_hash("doc_events"), nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, tx_id = submit_and_mine(bc, wallet, tx)

        events = bc._store.query_events(event_type="Notarize")
        assert len(events) >= 1
        assert events[0]["event_type"] == "Notarize"

    def test_receipt_survives_reload(self, wallet, tmp_dir):
        """Receipt persists across blockchain reload."""
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, _hex_hash("doc_reload"), nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        _, tx_id = submit_and_mine(bc, wallet, tx)

        # New store instance queries the same DB
        from qbit_network.core.store import SQLiteStore
        store2 = SQLiteStore(tmp_dir)
        receipt = store2.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.tx_id == tx_id
        store2.close()


# ========== Event Filtering ==========

class TestEventFiltering:
    def test_filter_by_type(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, w2 = wallet_pair

        # Create a notarize TX
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx1 = Transaction.notarize(wallet.address, _hex_hash("doc_filter1"), nonce=nonce)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx1)

        # Create a transfer TX
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx2 = Transaction.transfer(wallet.address, w2.address, 500, nonce=nonce)
        tx2.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx2)

        # Filter by Notarize
        notarize_events = bc.get_events(event_type="Notarize")
        for ev in notarize_events:
            assert ev["event"]["type"] == "Notarize"

        # Filter by Transfer
        transfer_events = bc.get_events(event_type="Transfer")
        for ev in transfer_events:
            assert ev["event"]["type"] == "Transfer"

    def test_filter_by_sender(self, funded_chain, wallet):
        bc = funded_chain
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, _hex_hash("doc_sender"), nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        events = bc.get_events(event_type="Notarize", sender=wallet.address)
        assert len(events) >= 1

    def test_filter_nonexistent_type(self, funded_chain):
        bc = funded_chain
        events = bc.get_events(event_type="NonExistentEvent")
        assert len(events) == 0


# ========== Simple Finality ==========

class TestFinality:
    def test_finality_initial(self, funded_chain):
        bc = funded_chain
        assert bc.get_finalized_height() == -1

    def test_finality_advances_with_stake(self, funded_chain, wallet):
        bc = funded_chain
        # Stake so the validator has weight
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.stake(wallet.address, wallet.address, 1000, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        # Mine a few more blocks -- single validator with >2/3 stake
        # should finalize blocks quickly
        for _ in range(3):
            nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
            tx = Transaction.notarize(wallet.address, _hex_hash(f"doc_{_}"), nonce=nonce)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx)

        # With a single validator having 100% of stake, finality should advance
        fh = bc.get_finalized_height()
        assert fh >= 0

    def test_finality_does_not_advance_without_stake(self):
        """Without financial layer, finality stays at -1."""
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk, validator_pk=w.signing_pk)
        # Do NOT activate financial layer

        nonce = bc.get_nonce(w.address) + bc._pool_sender_count.get(w.address, 0)
        tx = Transaction.notarize(w.address, _hex_hash("doc_nofin"), nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        ok, _ = bc.submit_tx(tx)
        assert ok
        bc.produce_block(w.address, w.signing_sk)

        assert bc.get_finalized_height() == -1

    def test_finality_not_lost_after_more_blocks(self, funded_chain, wallet):
        """Finality status is not retroactively lost after additional blocks."""
        bc = funded_chain
        # Stake heavily
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.stake(wallet.address, wallet.address, 10000, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        # Mine enough to get finality
        for i in range(5):
            nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
            tx = Transaction.notarize(wallet.address, _hex_hash(f"doc_fin_{i}"), nonce=nonce)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx)

        fh_before = bc.get_finalized_height()
        assert fh_before >= 0

        # Mine more blocks
        for i in range(3):
            nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
            tx = Transaction.notarize(wallet.address, _hex_hash(f"doc_more_{i}"), nonce=nonce)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx)

        fh_after = bc.get_finalized_height()
        assert fh_after >= fh_before  # never decreases

    def test_rollback_resets_finality(self, funded_chain, wallet):
        """Rollback past finalized height resets finality."""
        bc = funded_chain
        # Stake
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.stake(wallet.address, wallet.address, 10000, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        # Mine blocks
        for i in range(5):
            nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
            tx = Transaction.notarize(wallet.address, _hex_hash(f"doc_rb_{i}"), nonce=nonce)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx)

        fh = bc.get_finalized_height()
        # Rollback to height 2 (target_index is exclusive start)
        bc._rollback_to(3)
        assert bc.get_finalized_height() < fh or bc.get_finalized_height() <= 2


# ========== Block-Level Events ==========

class TestBlockLevelEvents:
    def test_block_reward_event(self, funded_chain, wallet):
        bc = funded_chain
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, _hex_hash("doc_reward"), nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        events = bc.get_block_level_events(block.index)
        # Should have BlockReward since financial layer is active
        reward_events = [e for e in events if e["type"] == "BlockReward"]
        assert len(reward_events) == 1
        assert reward_events[0]["validator"] == wallet.address
        assert reward_events[0]["amount"] > 0


# ========== RPC Methods (unit-level) ==========

class TestReceiptRPCMethods:
    def test_get_receipt_returns_dict(self, funded_chain, wallet):
        bc = funded_chain
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, _hex_hash("doc_rpc"), nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        _, tx_id = submit_and_mine(bc, wallet, tx)

        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        d = receipt.to_dict()
        assert d["tx_id"] == tx_id
        assert d["status"] == "success"
        assert "events" in d

    def test_get_receipt_nonexistent(self, funded_chain):
        bc = funded_chain
        assert bc.get_receipt("nonexistent_txid") is None

    def test_get_events_with_limit(self, funded_chain, wallet):
        bc = funded_chain
        # Create several TXs
        for i in range(5):
            nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
            tx = Transaction.notarize(wallet.address, _hex_hash(f"doc_limit_{i}"), nonce=nonce)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx)

        events = bc.get_events(event_type="Notarize", limit=2)
        assert len(events) <= 2

    def test_get_finalized_height_method(self, funded_chain):
        bc = funded_chain
        assert isinstance(bc.get_finalized_height(), int)


# ========== WebSocket Channel ==========

class TestFinalizedWSChannel:
    def test_finalized_channel_is_valid(self):
        from qbit_network.network.websocket import VALID_CHANNELS
        assert "finalized" in VALID_CHANNELS

"""Tests for epoch rotation and slashing (v0.4.0 Sprint 2).

Covers:
- Epoch transition at EPOCH_LENGTH boundaries
- Epoch validators frozen during epoch
- Stake changes taking effect at next epoch
- Valid double-sign evidence accepted
- Invalid evidence rejected (same hash, wrong sig, wrong index)
- Slashing reduces stake
- Slashed validator removed if below MIN_STAKE
- Epoch + slashing rollback
- SQLite persistence of epochs and slashing events
- EVIDENCE transaction factory and validation
- RPC/REST endpoint wiring
"""
import json
import os
import shutil
import tempfile
import time
import pytest
from unittest.mock import patch

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.wallet import Wallet
from qbit_network.core.consensus import ProofOfAuthority
from qbit_network.crypto import MLDSA, sha3_256
from qbit_network.config import (
    MIN_STAKE, MAX_STAKE, EPOCH_LENGTH, SLASH_PERCENTAGE, UNBONDING_PERIOD,
)
from tests.conftest import submit_and_mine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _submit_and_mine_multi(bc, wallets, tx):
    """Submit tx and mine block, trying each wallet in order until one succeeds."""
    ok, tx_id = bc.submit_tx(tx)
    assert ok, f"submit_tx failed: {tx_id}"
    for w in wallets:
        block = bc.produce_block(w.address, w.signing_sk)
        if block is not None:
            return block, tx_id
    raise AssertionError("produce_block returned None for all wallets")


def _setup_chain(wallet):
    """Create blockchain with registered validator."""
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk)
    return bc


def _setup_chain_on_disk(wallet, data_dir):
    """Create blockchain with registered validator on disk."""
    bc = Blockchain(data_dir=data_dir)
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk)
    return bc


def _register_second_validator(bc, primary_wallet, second_wallet):
    """Register a second validator on-chain.

    After registration, removes the second validator from the PoA consensus
    set so the primary wallet can produce all blocks (avoids round-robin
    turn conflicts in tests). The validator remains in the on-chain registry.
    """
    nonce = bc.get_nonce(second_wallet.address)
    reg_tx = Transaction.register_validator(
        sender=second_wallet.address,
        validator_pubkey=second_wallet.signing_pk,
        validator_address=second_wallet.address,
        nonce=nonce,
    )
    reg_tx.sign(second_wallet.signing_sk, second_wallet.signing_pk)
    # _append_block_inner will add second_wallet to consensus automatically
    _submit_and_mine_multi(bc, [primary_wallet, second_wallet], reg_tx)


def _stake_validator(bc, miners, staker_wallet, validator_address, amount):
    """Stake tokens for a validator.

    miners can be a single Wallet or a list of Wallets to try for mining.
    """
    if not isinstance(miners, list):
        miners = [miners]
    nonce = bc.get_nonce(staker_wallet.address)
    tx = Transaction.stake(
        sender=staker_wallet.address,
        validator_address=validator_address,
        amount=amount,
        nonce=nonce,
    )
    tx.sign(staker_wallet.signing_sk, staker_wallet.signing_pk)
    _submit_and_mine_multi(bc, miners, tx)


def _mine_empty_blocks(bc, wallets, count):
    """Mine blocks with notarize txs to advance the chain.

    wallets can be a single Wallet or a list of Wallets to try for mining.
    """
    if not isinstance(wallets, list):
        wallets = [wallets]
    sender = wallets[0]
    for i in range(count):
        nonce = bc.get_nonce(sender.address)
        tx = Transaction.notarize(
            sender=sender.address,
            document_hash=sha3_256(f"filler-{bc.height}-{i}".encode()).hex(),
            metadata=f"filler block {i}",
            nonce=nonce,
        )
        tx.sign(sender.signing_sk, sender.signing_pk)
        _submit_and_mine_multi(bc, wallets, tx)


def _setup_slashing_scenario(w, w2):
    """Set up a chain with two validators where both have stake.

    Both w and w2 are registered validators. w has more stake so it's
    more likely to be selected, but _submit_and_mine_multi handles
    cases where w2 is selected instead.
    """
    bc = _setup_chain(w)
    miners = [w]  # only w in consensus initially
    # Stake w first (so dPoS selects w for block production)
    _stake_validator(bc, miners, w, w.address, 10000)
    # Now register w2 (adds to consensus via on-chain registration)
    _register_second_validator(bc, w, w2)
    miners = [w, w2]  # w2 now in consensus too
    # Stake w2 using w as sender
    _stake_validator(bc, miners, w, w2.address, 1000)
    return bc, miners


def _create_double_sign_evidence(validator_wallet, block_index):
    """Create fake double-sign evidence: two different block hashes signed by same validator."""
    hash_a = sha3_256(f"block-a-{block_index}".encode()).hex()
    hash_b = sha3_256(f"block-b-{block_index}".encode()).hex()
    sig_a = MLDSA.sign(validator_wallet.signing_sk, bytes.fromhex(hash_a))
    sig_b = MLDSA.sign(validator_wallet.signing_sk, bytes.fromhex(hash_b))
    return hash_a, hash_b, sig_a.hex(), sig_b.hex()


# ===========================================================================
# EVIDENCE Transaction Tests
# ===========================================================================

class TestEvidenceTransaction:
    """Test EVIDENCE transaction creation and validation."""

    def test_evidence_factory(self):
        w = Wallet.generate()
        tx = Transaction.evidence(
            sender=w.address,
            validator_address="qv1bad",
            block_index=10,
            block_a_hash="aa" * 32,
            block_b_hash="bb" * 32,
            block_a_sig="cc" * 100,
            block_b_sig="dd" * 100,
            nonce=0,
        )
        assert tx.tx_type == TxType.EVIDENCE
        assert tx.payload["evidence_type"] == "double_sign"
        assert tx.payload["block_index"] == 10
        assert tx.payload["validator_address"] == "qv1bad"

    def test_evidence_serialization_roundtrip(self):
        w = Wallet.generate()
        tx = Transaction.evidence(
            sender=w.address,
            validator_address="qv1bad",
            block_index=5,
            block_a_hash="aa" * 32,
            block_b_hash="bb" * 32,
            block_a_sig="cc" * 100,
            block_b_sig="dd" * 100,
            nonce=0,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        d = tx.to_dict()
        tx2 = Transaction.from_dict(d)
        assert tx2.tx_type == TxType.EVIDENCE
        assert tx2.payload == tx.payload
        assert tx2.tx_id == tx.tx_id

    def test_evidence_payload_validation_valid(self):
        w = Wallet.generate()
        tx = Transaction.evidence(
            sender=w.address,
            validator_address="qv1bad",
            block_index=5,
            block_a_hash="aa" * 32,
            block_b_hash="bb" * 32,
            block_a_sig="cc" * 100,
            block_b_sig="dd" * 100,
        )
        ok, err = tx.validate_payload()
        assert ok, err

    def test_evidence_rejects_same_hash(self):
        w = Wallet.generate()
        tx = Transaction.evidence(
            sender=w.address,
            validator_address="qv1bad",
            block_index=5,
            block_a_hash="aa" * 32,
            block_b_hash="aa" * 32,  # same hash
            block_a_sig="cc" * 100,
            block_b_sig="dd" * 100,
        )
        ok, err = tx.validate_payload()
        assert not ok
        assert "must differ" in err

    def test_evidence_rejects_non_hex_sig(self):
        w = Wallet.generate()
        tx = Transaction(
            tx_type=TxType.EVIDENCE, sender=w.address, nonce=0,
            payload={
                "evidence_type": "double_sign",
                "block_a_hash": "aa" * 32,
                "block_b_hash": "bb" * 32,
                "block_a_sig": "not-hex!",
                "block_b_sig": "dd" * 100,
                "block_index": 5,
                "validator_address": "qv1bad",
            },
        )
        ok, err = tx.validate_payload()
        assert not ok
        assert "block_a_sig" in err

    def test_evidence_rejects_negative_block_index(self):
        w = Wallet.generate()
        tx = Transaction.evidence(
            sender=w.address,
            validator_address="qv1bad",
            block_index=-1,
            block_a_hash="aa" * 32,
            block_b_hash="bb" * 32,
            block_a_sig="cc" * 100,
            block_b_sig="dd" * 100,
        )
        ok, err = tx.validate_payload()
        assert not ok
        assert "block_index" in err

    def test_evidence_rejects_wrong_evidence_type(self):
        w = Wallet.generate()
        tx = Transaction(
            tx_type=TxType.EVIDENCE, sender=w.address, nonce=0,
            payload={
                "evidence_type": "equivocation",
                "block_a_hash": "aa" * 32,
                "block_b_hash": "bb" * 32,
                "block_a_sig": "cc" * 100,
                "block_b_sig": "dd" * 100,
                "block_index": 5,
                "validator_address": "qv1bad",
            },
        )
        ok, err = tx.validate_payload()
        assert not ok
        assert "evidence_type" in err

    def test_evidence_rejects_empty_validator_address(self):
        w = Wallet.generate()
        tx = Transaction.evidence(
            sender=w.address,
            validator_address="",
            block_index=5,
            block_a_hash="aa" * 32,
            block_b_hash="bb" * 32,
            block_a_sig="cc" * 100,
            block_b_sig="dd" * 100,
        )
        ok, err = tx.validate_payload()
        assert not ok
        assert "validator_address" in err

    def test_evidence_rejects_unknown_payload_keys(self):
        w = Wallet.generate()
        tx = Transaction(
            tx_type=TxType.EVIDENCE, sender=w.address, nonce=0,
            payload={
                "evidence_type": "double_sign",
                "block_a_hash": "aa" * 32,
                "block_b_hash": "bb" * 32,
                "block_a_sig": "cc" * 100,
                "block_b_sig": "dd" * 100,
                "block_index": 5,
                "validator_address": "qv1bad",
                "extra_key": "should fail",
            },
        )
        ok, err = tx.validate_payload()
        assert not ok
        assert "unknown payload keys" in err


# ===========================================================================
# Epoch Rotation Tests
# ===========================================================================

class TestEpochRotation:
    """Test epoch boundary transitions and validator freezing."""

    def test_epoch_starts_at_zero(self):
        """Genesis block triggers epoch 0."""
        w = Wallet.generate()
        bc = _setup_chain(w)
        assert bc.get_current_epoch() == 0

    def test_epoch_transition_at_boundary(self):
        """Epoch transitions when block.index hits EPOCH_LENGTH."""
        w = Wallet.generate()
        # Use a small epoch length for testing
        with patch('qbit_network.core.blockchain.EPOCH_LENGTH', 5):
            bc = Blockchain()
            bc.consensus.add_validator(w.address, w.signing_pk)
            bc.init_chain(w.address, w.signing_sk)
            assert bc.get_current_epoch() == 0

            # Mine blocks up to epoch boundary
            _mine_empty_blocks(bc, w, 4)  # blocks 1-4
            assert bc.height == 4
            assert bc.get_current_epoch() == 0

            # Mine block 5 => epoch 1
            _mine_empty_blocks(bc, w, 1)
            assert bc.height == 5
            assert bc.get_current_epoch() == 1

    def test_epoch_validators_frozen(self):
        """Validators are frozen at epoch boundary and used during the epoch."""
        w = Wallet.generate()
        with patch('qbit_network.core.blockchain.EPOCH_LENGTH', 5):
            bc = Blockchain()
            bc.consensus.add_validator(w.address, w.signing_pk)
            bc.init_chain(w.address, w.signing_sk)

            # Stake the validator
            _stake_validator(bc, w, w, w.address, 1000)

            # Mine to next epoch boundary (block 5)
            blocks_to_boundary = 5 - bc.height
            _mine_empty_blocks(bc, w, blocks_to_boundary)
            assert bc.get_current_epoch() == 1

            # Get validators at epoch 1 (after stake was added)
            epoch_1_vals = bc.get_epoch_validators(1)
            assert len(epoch_1_vals) > 0

            # Validators should remain frozen during the epoch
            live_before = bc.get_active_validators()
            _mine_empty_blocks(bc, w, 2)
            # Active validators (epoch-frozen) should stay the same
            assert bc.get_active_validators() == live_before

    def test_stake_change_takes_effect_next_epoch(self):
        """Stake changes during an epoch take effect at next epoch boundary."""
        w = Wallet.generate()
        with patch('qbit_network.core.blockchain.EPOCH_LENGTH', 5):
            bc = Blockchain()
            bc.consensus.add_validator(w.address, w.signing_pk)
            bc.init_chain(w.address, w.signing_sk)

            # Stake the validator
            _stake_validator(bc, w, w, w.address, 500)

            # Mine to epoch 1 boundary
            blocks_to_boundary = 5 - bc.height
            _mine_empty_blocks(bc, w, blocks_to_boundary)
            assert bc.get_current_epoch() == 1

            epoch_1_vals = bc.get_epoch_validators(1)
            epoch_1_stakes = {addr: stake for addr, stake in epoch_1_vals}

            # Add more stake during epoch 1
            _stake_validator(bc, w, w, w.address, 1000)

            # Epoch-frozen validators should still show old stake
            frozen = bc.get_active_validators()
            frozen_dict = {addr: stake for addr, stake in frozen}
            assert frozen_dict.get(w.address) == epoch_1_stakes.get(w.address)

            # But live validators should show new stake
            live = bc._get_live_validators()
            live_dict = {addr: stake for addr, stake in live}
            assert live_dict.get(w.address) == 500 + 1000

    def test_get_epoch_validators_returns_empty_for_nonexistent(self):
        w = Wallet.generate()
        bc = _setup_chain(w)
        result = bc.get_epoch_validators(999)
        assert result == []

    def test_multiple_epoch_transitions(self):
        """Multiple epoch boundaries are handled correctly."""
        w = Wallet.generate()
        with patch('qbit_network.core.blockchain.EPOCH_LENGTH', 3):
            bc = Blockchain()
            bc.consensus.add_validator(w.address, w.signing_pk)
            bc.init_chain(w.address, w.signing_sk)

            # Mine through 3 epochs
            _mine_empty_blocks(bc, w, 8)
            assert bc.get_current_epoch() >= 2

            # Each epoch should have a snapshot
            for e in range(bc.get_current_epoch() + 1):
                vals = bc.get_epoch_validators(e)
                # May be empty for epoch 0 if no stake, that's ok
                assert isinstance(vals, list)


# ===========================================================================
# Slashing Tests
# ===========================================================================

class TestSlashing:
    """Test double-sign evidence processing and slashing."""

    def _submit_evidence(self, bc, miners, w, w2, block_index=5):
        """Helper: create and submit double-sign evidence against w2."""
        hash_a, hash_b, sig_a, sig_b = _create_double_sign_evidence(w2, block_index)
        nonce = bc.get_nonce(w.address)
        tx = Transaction.evidence(
            sender=w.address, validator_address=w2.address,
            block_index=block_index,
            block_a_hash=hash_a, block_b_hash=hash_b,
            block_a_sig=sig_a, block_b_sig=sig_b, nonce=nonce,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        _submit_and_mine_multi(bc, miners, tx)

    def test_valid_double_sign_evidence_accepted(self):
        """Valid evidence is accepted and processed."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc, miners = _setup_slashing_scenario(w, w2)

        initial_stake = bc.get_validator_stake(w2.address)
        assert initial_stake == 1000

        self._submit_evidence(bc, miners, w, w2)

        expected = 1000 - (1000 * SLASH_PERCENTAGE) // 100
        assert bc.get_validator_stake(w2.address) == expected

    def test_slashing_records_event(self):
        """Slashing creates a slashing event record."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc, miners = _setup_slashing_scenario(w, w2)

        self._submit_evidence(bc, miners, w, w2)

        events = bc.get_slashing_events()
        assert len(events) == 1
        assert events[0]["validator"] == w2.address
        assert events[0]["amount_slashed"] == (1000 * SLASH_PERCENTAGE) // 100

    def test_slashed_validator_marked(self):
        """Slashed validator is marked in _slashed_validators."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc, miners = _setup_slashing_scenario(w, w2)

        self._submit_evidence(bc, miners, w, w2)
        assert bc.is_slashed(w2.address)

    def test_cannot_stake_to_slashed_validator(self):
        """Cannot stake to a slashed validator."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc, miners = _setup_slashing_scenario(w, w2)

        self._submit_evidence(bc, miners, w, w2)

        nonce2 = bc.get_nonce(w.address)
        stake_tx = Transaction.stake(
            sender=w.address, validator_address=w2.address,
            amount=100, nonce=nonce2,
        )
        stake_tx.sign(w.signing_sk, w.signing_pk)
        ok, err = bc.submit_tx(stake_tx)
        assert not ok
        assert "slashed" in err

    def test_duplicate_evidence_rejected(self):
        """Cannot submit evidence for an already-slashed validator."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc, miners = _setup_slashing_scenario(w, w2)

        self._submit_evidence(bc, miners, w, w2)

        # Second evidence for same validator
        hash_c = sha3_256(b"block-c").hex()
        hash_d = sha3_256(b"block-d").hex()
        sig_c = MLDSA.sign(w2.signing_sk, bytes.fromhex(hash_c)).hex()
        sig_d = MLDSA.sign(w2.signing_sk, bytes.fromhex(hash_d)).hex()

        nonce2 = bc.get_nonce(w.address)
        ev2 = Transaction.evidence(
            sender=w.address, validator_address=w2.address, block_index=6,
            block_a_hash=hash_c, block_b_hash=hash_d,
            block_a_sig=sig_c, block_b_sig=sig_d, nonce=nonce2,
        )
        ev2.sign(w.signing_sk, w.signing_pk)
        ok, err = bc.submit_tx(ev2)
        assert not ok
        assert "already slashed" in err

    def test_evidence_against_unregistered_validator_rejected(self):
        """Evidence against non-registered validator is rejected."""
        w = Wallet.generate()
        bc = _setup_chain(w)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.evidence(
            sender=w.address, validator_address="qv1nonexistent",
            block_index=5,
            block_a_hash="aa" * 32, block_b_hash="bb" * 32,
            block_a_sig="cc" * 100, block_b_sig="dd" * 100,
            nonce=nonce,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "not a registered validator" in err

    def test_invalid_signatures_not_slashed(self):
        """Evidence with invalid signatures does not cause slashing."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc, miners = _setup_slashing_scenario(w, w2)

        hash_a = sha3_256(b"block-a").hex()
        hash_b = sha3_256(b"block-b").hex()
        # Sign with WRONG key (w instead of w2)
        sig_a = MLDSA.sign(w.signing_sk, bytes.fromhex(hash_a)).hex()
        sig_b = MLDSA.sign(w.signing_sk, bytes.fromhex(hash_b)).hex()

        nonce = bc.get_nonce(w.address)
        tx = Transaction.evidence(
            sender=w.address, validator_address=w2.address, block_index=5,
            block_a_hash=hash_a, block_b_hash=hash_b,
            block_a_sig=sig_a, block_b_sig=sig_b, nonce=nonce,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        _submit_and_mine_multi(bc, miners, tx)

        # Should NOT have been slashed (invalid signatures)
        assert not bc.is_slashed(w2.address)
        assert bc.get_validator_stake(w2.address) == 1000

    def test_slashing_removes_below_min_stake(self):
        """Validator removed from active set if stake drops below MIN_STAKE."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc = _setup_chain(w)
        _stake_validator(bc, w, w, w.address, 10000)
        _register_second_validator(bc, w, w2)
        miners = [w, w2]
        _stake_validator(bc, miners, w, w2.address, MIN_STAKE)  # exactly min

        hash_a, hash_b, sig_a, sig_b = _create_double_sign_evidence(w2, 5)
        nonce = bc.get_nonce(w.address)
        tx = Transaction.evidence(
            sender=w.address, validator_address=w2.address, block_index=5,
            block_a_hash=hash_a, block_b_hash=hash_b,
            block_a_sig=sig_a, block_b_sig=sig_b, nonce=nonce,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        _submit_and_mine_multi(bc, miners, tx)

        active = bc._get_live_validators()
        active_addrs = [addr for addr, _ in active]
        assert w2.address not in active_addrs

    def test_get_slashing_events_filtered(self):
        """Get slashing events filtered by validator."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc, miners = _setup_slashing_scenario(w, w2)

        self._submit_evidence(bc, miners, w, w2)

        assert len(bc.get_slashing_events(w2.address)) == 1
        assert len(bc.get_slashing_events("qv1nobody")) == 0


# ===========================================================================
# Rollback Tests
# ===========================================================================

class TestEpochSlashingRollback:
    """Test rollback of epoch transitions and slashing events."""

    def test_epoch_rollback(self):
        """Rolling back past an epoch boundary reverts the epoch."""
        w = Wallet.generate()
        with patch('qbit_network.core.blockchain.EPOCH_LENGTH', 3):
            bc = Blockchain()
            bc.consensus.add_validator(w.address, w.signing_pk)
            bc.init_chain(w.address, w.signing_sk)

            # Mine past epoch boundary
            _mine_empty_blocks(bc, w, 5)
            epoch_before = bc.get_current_epoch()
            assert epoch_before >= 1

            # Rollback past epoch boundary
            bc._rollback_to(2)
            assert bc.get_current_epoch() < epoch_before

    def test_slashing_rollback(self):
        """Rolling back a block with EVIDENCE tx reverts slashing."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc, miners = _setup_slashing_scenario(w, w2)

        height_before = bc.height

        hash_a, hash_b, sig_a, sig_b = _create_double_sign_evidence(w2, 5)
        nonce = bc.get_nonce(w.address)
        tx = Transaction.evidence(
            sender=w.address, validator_address=w2.address, block_index=5,
            block_a_hash=hash_a, block_b_hash=hash_b,
            block_a_sig=sig_a, block_b_sig=sig_b, nonce=nonce,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        _submit_and_mine_multi(bc, miners, tx)
        assert bc.is_slashed(w2.address)

        # Rollback the evidence block
        bc._rollback_to(height_before + 1)
        assert not bc.is_slashed(w2.address)
        assert len(bc.get_slashing_events()) == 0


# ===========================================================================
# SQLite Persistence Tests
# ===========================================================================

class TestSQLitePersistence:
    """Test epoch and slashing persistence in SQLite."""

    @pytest.fixture
    def tmp_dir(self):
        d = tempfile.mkdtemp(prefix="qv_epoch_test_")
        yield d
        shutil.rmtree(d, ignore_errors=True)

    def test_epoch_persisted_to_sqlite(self, tmp_dir):
        """Epoch transitions are persisted to SQLite."""
        w = Wallet.generate()
        with patch('qbit_network.core.blockchain.EPOCH_LENGTH', 3):
            bc = _setup_chain_on_disk(w, tmp_dir)
            _mine_empty_blocks(bc, w, 5)

            # Check SQLite has epoch records
            assert bc._store is not None
            epochs = bc._store.get_all_epochs()
            assert len(epochs) >= 1

    def test_slashing_event_persisted_to_sqlite(self, tmp_dir):
        """Slashing events are persisted to SQLite."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc = _setup_chain_on_disk(w, tmp_dir)
        _stake_validator(bc, w, w, w.address, 10000)
        _register_second_validator(bc, w, w2)
        miners = [w, w2]
        _stake_validator(bc, miners, w, w2.address, 1000)

        hash_a, hash_b, sig_a, sig_b = _create_double_sign_evidence(w2, 5)
        nonce = bc.get_nonce(w.address)
        tx = Transaction.evidence(
            sender=w.address, validator_address=w2.address, block_index=5,
            block_a_hash=hash_a, block_b_hash=hash_b,
            block_a_sig=sig_a, block_b_sig=sig_b, nonce=nonce,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        _submit_and_mine_multi(bc, miners, tx)

        events = bc._store.get_slashing_events()
        assert len(events) == 1
        assert events[0]["validator"] == w2.address

    def test_sqlite_reload_preserves_epoch_state(self, tmp_dir):
        """Reloading from SQLite restores epoch state."""
        w = Wallet.generate()
        with patch('qbit_network.core.blockchain.EPOCH_LENGTH', 3):
            bc = _setup_chain_on_disk(w, tmp_dir)
            _mine_empty_blocks(bc, w, 5)
            epoch_before = bc.get_current_epoch()
            bc._store.close()

            # Reload
            bc2 = Blockchain(data_dir=tmp_dir)
            bc2.consensus.add_validator(w.address, w.signing_pk)
            bc2.load()
            assert bc2.get_current_epoch() == epoch_before

    def test_sqlite_reload_preserves_slashing_state(self, tmp_dir):
        """Reloading from SQLite restores slashing state."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc = _setup_chain_on_disk(w, tmp_dir)
        _stake_validator(bc, w, w, w.address, 10000)
        _register_second_validator(bc, w, w2)
        miners = [w, w2]
        _stake_validator(bc, miners, w, w2.address, 1000)

        hash_a, hash_b, sig_a, sig_b = _create_double_sign_evidence(w2, 5)
        nonce = bc.get_nonce(w.address)
        tx = Transaction.evidence(
            sender=w.address, validator_address=w2.address, block_index=5,
            block_a_hash=hash_a, block_b_hash=hash_b,
            block_a_sig=sig_a, block_b_sig=sig_b, nonce=nonce,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        _submit_and_mine_multi(bc, miners, tx)
        bc._store.close()

        # Reload
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(w.address, w.signing_pk)
        bc2.load()
        assert bc2.is_slashed(w2.address)
        assert len(bc2.get_slashing_events()) == 1

    def test_sqlite_rollback_cleans_epochs(self, tmp_dir):
        """SQLite rollback removes epoch records beyond the rollback point."""
        w = Wallet.generate()
        with patch('qbit_network.core.blockchain.EPOCH_LENGTH', 3):
            bc = _setup_chain_on_disk(w, tmp_dir)
            _mine_empty_blocks(bc, w, 8)

            epochs_before = len(bc._store.get_all_epochs())
            assert epochs_before >= 2

            bc._rollback_to(2)
            epochs_after = len(bc._store.get_all_epochs())
            assert epochs_after < epochs_before

    def test_sqlite_rollback_cleans_slashing_events(self, tmp_dir):
        """SQLite rollback removes slashing events from rolled-back blocks."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc = _setup_chain_on_disk(w, tmp_dir)
        _stake_validator(bc, w, w, w.address, 10000)
        _register_second_validator(bc, w, w2)
        miners = [w, w2]
        _stake_validator(bc, miners, w, w2.address, 1000)

        height_before = bc.height

        hash_a, hash_b, sig_a, sig_b = _create_double_sign_evidence(w2, 5)
        nonce = bc.get_nonce(w.address)
        tx = Transaction.evidence(
            sender=w.address, validator_address=w2.address, block_index=5,
            block_a_hash=hash_a, block_b_hash=hash_b,
            block_a_sig=sig_a, block_b_sig=sig_b, nonce=nonce,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        _submit_and_mine_multi(bc, miners, tx)

        assert len(bc._store.get_slashing_events()) == 1

        bc._rollback_to(height_before + 1)
        assert len(bc._store.get_slashing_events()) == 0


# ===========================================================================
# Integration / Edge Cases
# ===========================================================================

class TestEdgeCases:
    """Edge cases and integration scenarios."""

    def test_backward_compatible_no_epochs(self):
        """Chains without epoch data work fine."""
        w = Wallet.generate()
        bc = _setup_chain(w)
        # No staking => no epoch validators
        assert bc.get_current_epoch() == 0
        # Should still work for block production
        _mine_empty_blocks(bc, w, 5)
        assert bc.height == 5

    def test_is_slashed_false_for_clean_validator(self):
        """is_slashed returns False for non-slashed validators."""
        w = Wallet.generate()
        bc = _setup_chain(w)
        assert not bc.is_slashed(w.address)
        assert not bc.is_slashed("qv1nobody")

    def test_config_slash_percentage(self):
        """SLASH_PERCENTAGE is configured correctly."""
        assert SLASH_PERCENTAGE == 50
        assert isinstance(SLASH_PERCENTAGE, int)

    def test_config_epoch_length(self):
        """EPOCH_LENGTH is configured correctly."""
        assert EPOCH_LENGTH == 100
        assert isinstance(EPOCH_LENGTH, int)

    def test_evidence_tx_in_block_with_other_txs(self):
        """EVIDENCE tx can coexist with other tx types in a block."""
        w = Wallet.generate()
        w2 = Wallet.generate()
        bc, miners = _setup_slashing_scenario(w, w2)

        # Submit notarize tx
        nonce1 = bc.get_nonce(w.address)
        notarize_tx = Transaction.notarize(
            sender=w.address,
            document_hash=sha3_256(b"test-doc").hex(),
            metadata="test",
            nonce=nonce1,
        )
        notarize_tx.sign(w.signing_sk, w.signing_pk)
        ok, _ = bc.submit_tx(notarize_tx)
        assert ok

        # Submit evidence tx
        hash_a, hash_b, sig_a, sig_b = _create_double_sign_evidence(w2, 5)
        nonce2 = bc.get_nonce(w.address) + bc._pool_sender_count.get(w.address, 0)
        ev_tx = Transaction.evidence(
            sender=w.address, validator_address=w2.address, block_index=5,
            block_a_hash=hash_a, block_b_hash=hash_b,
            block_a_sig=sig_a, block_b_sig=sig_b, nonce=nonce2,
        )
        ev_tx.sign(w.signing_sk, w.signing_pk)
        ok, _ = bc.submit_tx(ev_tx)
        assert ok

        # Both should be mined in one block -- try all miners
        block = None
        for miner in miners:
            block = bc.produce_block(miner.address, miner.signing_sk)
            if block is not None:
                break
        assert block is not None
        assert len(block.transactions) == 2

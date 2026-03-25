"""Tests for dPoS consensus: staking, delegation, unstaking, weighted selection, unbonding, rollback."""
import time
import pytest
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.wallet import Wallet
from qbit_network.core.consensus import ProofOfAuthority
from qbit_network.config import MIN_STAKE, MAX_STAKE, UNBONDING_PERIOD
from tests.conftest import submit_and_mine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_chain_with_registered_validator(wallet):
    """Create blockchain with registered validator (via REGISTER_VALIDATOR tx)."""
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk)
    # Genesis block now includes a REGISTER_VALIDATOR tx, so the
    # validator is already registered on-chain.
    return bc


def _register_second_validator(bc, primary_wallet, second_wallet):
    """Register a second validator on an existing chain."""
    bc.consensus.add_validator(second_wallet.address, second_wallet.signing_pk)
    nonce = bc.get_nonce(second_wallet.address)
    reg_tx = Transaction.register_validator(
        sender=second_wallet.address,
        validator_pubkey=second_wallet.signing_pk,
        validator_address=second_wallet.address,
        nonce=nonce,
    )
    reg_tx.sign(second_wallet.signing_sk, second_wallet.signing_pk)
    submit_and_mine(bc, primary_wallet, reg_tx)


# ===========================================================================
# Transaction factory tests
# ===========================================================================

class TestStakingTransactionTypes:
    """Test STAKE, DELEGATE, UNSTAKE transaction creation and validation."""

    def test_stake_factory(self):
        w = Wallet.generate()
        tx = Transaction.stake(w.address, "qv1abc", 100, nonce=0)
        assert tx.tx_type == TxType.STAKE
        assert tx.payload["amount"] == 100
        assert tx.payload["validator_address"] == "qv1abc"

    def test_delegate_factory(self):
        w = Wallet.generate()
        tx = Transaction.delegate(w.address, "qv1abc", 50, nonce=0)
        assert tx.tx_type == TxType.DELEGATE
        assert tx.payload["amount"] == 50
        assert tx.payload["validator_address"] == "qv1abc"

    def test_unstake_factory(self):
        w = Wallet.generate()
        tx = Transaction.unstake(w.address, "qv1abc", 25, nonce=0)
        assert tx.tx_type == TxType.UNSTAKE
        assert tx.payload["amount"] == 25

    def test_stake_serialization_roundtrip(self):
        w = Wallet.generate()
        tx = Transaction.stake(w.address, "qv1abc", 100, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        d = tx.to_dict()
        tx2 = Transaction.from_dict(d)
        assert tx2.tx_type == TxType.STAKE
        assert tx2.payload == tx.payload
        assert tx2.tx_id == tx.tx_id

    def test_delegate_serialization_roundtrip(self):
        w = Wallet.generate()
        tx = Transaction.delegate(w.address, "qv1abc", 50, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        d = tx.to_dict()
        tx2 = Transaction.from_dict(d)
        assert tx2.tx_type == TxType.DELEGATE
        assert tx2.payload == tx.payload

    def test_unstake_serialization_roundtrip(self):
        w = Wallet.generate()
        tx = Transaction.unstake(w.address, "qv1abc", 25, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        d = tx.to_dict()
        tx2 = Transaction.from_dict(d)
        assert tx2.tx_type == TxType.UNSTAKE


# ===========================================================================
# Payload validation tests
# ===========================================================================

class TestStakingPayloadValidation:
    """Validate STAKE/DELEGATE/UNSTAKE payload constraints."""

    def test_stake_valid_payload(self):
        w = Wallet.generate()
        tx = Transaction.stake(w.address, "qv1abc", 100)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_stake_zero_amount_rejected(self):
        w = Wallet.generate()
        tx = Transaction(TxType.STAKE, w.address,
                         {"amount": 0, "validator_address": "qv1abc"})
        ok, err = tx.validate_payload()
        assert not ok
        assert "amount" in err

    def test_stake_negative_amount_rejected(self):
        w = Wallet.generate()
        tx = Transaction(TxType.STAKE, w.address,
                         {"amount": -5, "validator_address": "qv1abc"})
        ok, err = tx.validate_payload()
        assert not ok

    def test_stake_too_large_rejected(self):
        w = Wallet.generate()
        tx = Transaction(TxType.STAKE, w.address,
                         {"amount": MAX_STAKE + 1, "validator_address": "qv1abc"})
        ok, err = tx.validate_payload()
        assert not ok
        assert "amount" in err

    def test_stake_string_amount_rejected(self):
        w = Wallet.generate()
        tx = Transaction(TxType.STAKE, w.address,
                         {"amount": "100", "validator_address": "qv1abc"})
        ok, err = tx.validate_payload()
        assert not ok

    def test_stake_missing_validator_address(self):
        w = Wallet.generate()
        tx = Transaction(TxType.STAKE, w.address,
                         {"amount": 100})
        ok, err = tx.validate_payload()
        assert not ok

    def test_stake_empty_validator_address(self):
        w = Wallet.generate()
        tx = Transaction(TxType.STAKE, w.address,
                         {"amount": 100, "validator_address": ""})
        ok, err = tx.validate_payload()
        assert not ok

    def test_delegate_valid(self):
        w = Wallet.generate()
        tx = Transaction.delegate(w.address, "qv1abc", 50)
        ok, err = tx.validate_payload()
        assert ok

    def test_unstake_valid(self):
        w = Wallet.generate()
        tx = Transaction.unstake(w.address, "qv1abc", 25)
        ok, err = tx.validate_payload()
        assert ok

    def test_stake_extra_keys_rejected(self):
        w = Wallet.generate()
        tx = Transaction(TxType.STAKE, w.address,
                         {"amount": 100, "validator_address": "qv1abc", "extra": "bad"})
        ok, err = tx.validate_payload()
        assert not ok
        assert "unknown payload keys" in err

    def test_stake_min_amount(self):
        w = Wallet.generate()
        tx = Transaction.stake(w.address, "qv1abc", MIN_STAKE)
        ok, err = tx.validate_payload()
        assert ok

    def test_stake_max_amount(self):
        w = Wallet.generate()
        tx = Transaction.stake(w.address, "qv1abc", MAX_STAKE)
        ok, err = tx.validate_payload()
        assert ok


# ===========================================================================
# Blockchain staking state tests
# ===========================================================================

class TestStakingState:
    """Test staking state management in blockchain."""

    def test_stake_updates_state(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        assert bc.get_validator_stake(w.address) == 100
        assert bc.get_staker_info(w.address, w.address) == 100

    def test_delegate_updates_state(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        delegator = Wallet.generate()
        nonce = bc.get_nonce(delegator.address)
        tx = Transaction.delegate(delegator.address, w.address, 50, nonce=nonce)
        tx.sign(delegator.signing_sk, delegator.signing_pk)
        submit_and_mine(bc, w, tx)

        assert bc.get_validator_stake(w.address) == 50
        assert bc.get_staker_info(delegator.address, w.address) == 50

    def test_multiple_stakes_accumulate(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        for i in range(3):
            nonce = bc.get_nonce(w.address)
            tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
            tx.sign(w.signing_sk, w.signing_pk)
            submit_and_mine(bc, w, tx)

        assert bc.get_validator_stake(w.address) == 300
        assert bc.get_staker_info(w.address, w.address) == 300

    def test_unstake_reduces_stake(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        # Stake 100
        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # Unstake 40
        nonce = bc.get_nonce(w.address)
        tx = Transaction.unstake(w.address, w.address, 40, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        assert bc.get_validator_stake(w.address) == 60
        assert bc.get_staker_info(w.address, w.address) == 60

    def test_unstake_creates_unbonding(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        # Stake
        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # Unstake
        nonce = bc.get_nonce(w.address)
        tx = Transaction.unstake(w.address, w.address, 50, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        block, _ = submit_and_mine(bc, w, tx)

        assert len(bc._unbonding) == 1
        entry = bc._unbonding[0]
        assert entry["staker"] == w.address
        assert entry["validator"] == w.address
        assert entry["amount"] == 50
        assert entry["release_block"] == block.index + UNBONDING_PERIOD

    def test_unstake_more_than_staked_rejected(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        # Stake 50
        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 50, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # Try to unstake 100
        nonce = bc.get_nonce(w.address)
        tx = Transaction.unstake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "insufficient stake" in err

    def test_stake_unregistered_validator_rejected(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)

        # w is not registered via REGISTER_VALIDATOR tx
        # but is a PoA validator -- stake should require on-chain registration
        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, "qv1nonexistent", 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "validator not registered" in err

    def test_delegate_unregistered_validator_rejected(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.delegate(w.address, "qv1nonexistent", 50, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "validator not registered" in err

    def test_get_active_validators_empty(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        assert bc.get_active_validators() == []

    def test_get_active_validators_with_stake(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 200, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        active = bc.get_active_validators()
        assert len(active) == 1
        assert active[0] == (w.address, 200)

    def test_unstake_all_removes_from_active(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        # Stake 100
        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # Unstake all
        nonce = bc.get_nonce(w.address)
        tx = Transaction.unstake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        active = bc.get_active_validators()
        assert len(active) == 0

    def test_get_all_stakes(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        all_stakes = bc.get_all_stakes()
        assert w.address in all_stakes
        assert all_stakes[w.address][w.address] == 100


# ===========================================================================
# Consensus selection tests
# ===========================================================================

class TestDPoSConsensusSelection:
    """Test stake-weighted validator selection in consensus."""

    def test_poa_fallback_no_stake(self):
        """Without any staked validators, PoA round-robin is used."""
        consensus = ProofOfAuthority()
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        consensus.add_validator(w1.address, w1.signing_pk)
        consensus.add_validator(w2.address, w2.signing_pk)

        # Without parent_hash, always PoA
        result = consensus.select_validator(0)
        assert result is not None

    def test_poa_fallback_with_parent_hash_but_no_stake(self):
        """With parent_hash but no active stakers, still uses PoA."""
        consensus = ProofOfAuthority()
        consensus._get_active_validators = lambda: []
        w1 = Wallet.generate()
        consensus.add_validator(w1.address, w1.signing_pk)

        result = consensus.select_validator(1, parent_hash="abc123")
        assert result == w1.address

    def test_dpos_single_validator(self):
        """With one staked validator, always selects them."""
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 1000, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        parent = bc.latest_block
        for i in range(10):
            selected = bc.consensus.select_validator(
                parent.index + 1 + i,
                parent_hash=parent.block_hash)
            assert selected == w.address

    def test_dpos_deterministic(self):
        """Same inputs always produce same selection."""
        consensus = ProofOfAuthority()
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        active = sorted([(w1.address, 500), (w2.address, 500)], key=lambda x: x[0])
        consensus._get_active_validators = lambda: active
        consensus.add_validator(w1.address, w1.signing_pk)
        consensus.add_validator(w2.address, w2.signing_pk)

        parent_hash = "a" * 64
        results = []
        for _ in range(5):
            r = consensus.select_validator(42, parent_hash=parent_hash)
            results.append(r)
        assert all(r == results[0] for r in results)

    def test_dpos_different_hashes_vary_selection(self):
        """Different parent hashes should produce different selections over many trials."""
        consensus = ProofOfAuthority()
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        active = sorted([(w1.address, 500), (w2.address, 500)], key=lambda x: x[0])
        consensus._get_active_validators = lambda: active
        consensus.add_validator(w1.address, w1.signing_pk)
        consensus.add_validator(w2.address, w2.signing_pk)

        selections = set()
        for i in range(100):
            r = consensus.select_validator(1, parent_hash=f"{i:064x}")
            selections.add(r)
        # With equal stake, both should be selected at least once
        assert len(selections) == 2

    def test_dpos_higher_stake_more_selections(self):
        """Validator with higher stake should be selected more often."""
        consensus = ProofOfAuthority()
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        # w1 has 90% stake, w2 has 10%
        active = sorted([(w1.address, 900), (w2.address, 100)], key=lambda x: x[0])
        consensus._get_active_validators = lambda: active
        consensus.add_validator(w1.address, w1.signing_pk)
        consensus.add_validator(w2.address, w2.signing_pk)

        counts = {w1.address: 0, w2.address: 0}
        for i in range(1000):
            r = consensus.select_validator(i, parent_hash=f"{i:064x}")
            counts[r] += 1

        # w1 should get roughly 90% of selections
        w1_ratio = counts[w1.address] / 1000
        assert w1_ratio > 0.8, f"w1 got {w1_ratio:.2%}, expected ~90%"
        assert w1_ratio < 0.98, f"w1 got {w1_ratio:.2%}, expected ~90%"

    def test_dpos_select_static_method(self):
        """Direct test of _select_dpos static method."""
        active = [("addr_a", 100), ("addr_b", 200), ("addr_c", 300)]
        result = ProofOfAuthority._select_dpos(1, "abc" * 21 + "a", active)
        assert result in ("addr_a", "addr_b", "addr_c")


# ===========================================================================
# Rollback tests
# ===========================================================================

class TestStakingRollback:
    """Test that staking state is correctly rolled back."""

    def test_rollback_stake(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        assert bc.get_validator_stake(w.address) == 100

        # Rollback the stake block
        bc._rollback_to(bc.height)
        assert bc.get_validator_stake(w.address) == 0

    def test_rollback_delegate(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)
        delegator = Wallet.generate()

        nonce = bc.get_nonce(delegator.address)
        tx = Transaction.delegate(delegator.address, w.address, 75, nonce=nonce)
        tx.sign(delegator.signing_sk, delegator.signing_pk)
        submit_and_mine(bc, w, tx)
        assert bc.get_validator_stake(w.address) == 75

        bc._rollback_to(bc.height)
        assert bc.get_validator_stake(w.address) == 0
        assert bc.get_staker_info(delegator.address, w.address) == 0

    def test_rollback_unstake_restores_stake(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        # Stake 100
        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # Unstake 40
        nonce = bc.get_nonce(w.address)
        tx = Transaction.unstake(w.address, w.address, 40, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        assert bc.get_validator_stake(w.address) == 60
        assert len(bc._unbonding) == 1

        # Rollback unstake
        bc._rollback_to(bc.height)
        assert bc.get_validator_stake(w.address) == 100
        assert len(bc._unbonding) == 0

    def test_rollback_multiple_stakes(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)
        height_before = bc.height

        # 3 stakes
        for i in range(3):
            nonce = bc.get_nonce(w.address)
            tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
            tx.sign(w.signing_sk, w.signing_pk)
            submit_and_mine(bc, w, tx)

        assert bc.get_validator_stake(w.address) == 300

        # Rollback all 3 stake blocks
        bc._rollback_to(height_before + 1)
        assert bc.get_validator_stake(w.address) == 0


# ===========================================================================
# Unbonding period tests
# ===========================================================================

class TestUnbondingPeriod:
    """Test unbonding period mechanics."""

    def test_unbonding_entry_created(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.unstake(w.address, w.address, 50, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        block, _ = submit_and_mine(bc, w, tx)

        assert len(bc._unbonding) == 1
        assert bc._unbonding[0]["release_block"] == block.index + UNBONDING_PERIOD

    def test_unbonding_not_released_early(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.unstake(w.address, w.address, 50, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # Mine a few more blocks -- unbonding should still be there
        for i in range(5):
            nonce = bc.get_nonce(w.address)
            tx = Transaction.notarize(w.address, f"{i:064x}", nonce=nonce)
            tx.sign(w.signing_sk, w.signing_pk)
            submit_and_mine(bc, w, tx)

        assert len(bc._unbonding) == 1  # still unbonding


# ===========================================================================
# SQLite persistence tests
# ===========================================================================

class TestStakingPersistence:
    """Test staking state persistence in SQLite."""

    def test_stake_persisted_to_sqlite(self, tmp_dir):
        w = Wallet.generate()
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk, validator_pk=w.signing_pk)
        # Genesis block registers the validator on-chain

        # Stake
        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 500, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # Verify in SQLite store directly (500 + MIN_STAKE from genesis auto-stake)
        assert bc._store.get_stake(w.address, w.address) == 500 + MIN_STAKE

    def test_unstake_persisted_to_sqlite(self, tmp_dir):
        w = Wallet.generate()
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk, validator_pk=w.signing_pk)
        # Genesis block registers the validator on-chain

        # Stake
        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 500, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # Unstake
        nonce = bc.get_nonce(w.address)
        tx = Transaction.unstake(w.address, w.address, 200, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # 500 + MIN_STAKE - 200 = 300 + MIN_STAKE
        assert bc._store.get_stake(w.address, w.address) == 300 + MIN_STAKE


# ===========================================================================
# Store unit tests
# ===========================================================================

class TestStakingStore:
    """Test SQLite store staking methods directly."""

    def test_put_get_stake(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_stake("staker1", "validator1", 100)
        assert store.get_stake("staker1", "validator1") == 100
        store.close()

    def test_delete_stake(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_stake("staker1", "validator1", 100)
        store.delete_stake("staker1", "validator1")
        assert store.get_stake("staker1", "validator1") == 0
        store.close()

    def test_get_all_stakes(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_stake("s1", "v1", 100)
        store.put_stake("s2", "v1", 200)
        store.put_stake("s1", "v2", 50)
        all_stakes = store.get_all_stakes()
        assert len(all_stakes) == 3
        store.close()

    def test_put_get_unbonding(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_unbonding("staker1", "validator1", 50, 200)
        mature = store.get_mature_unbondings(200)
        assert len(mature) == 1
        assert mature[0]["amount"] == 50
        store.close()

    def test_delete_unbonding(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_unbonding("staker1", "validator1", 50, 200)
        store.delete_unbonding("staker1", "validator1", 50, 200)
        mature = store.get_mature_unbondings(200)
        assert len(mature) == 0
        store.close()

    def test_mature_unbondings_not_early(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_unbonding("staker1", "validator1", 50, 200)
        mature = store.get_mature_unbondings(199)
        assert len(mature) == 0
        store.close()

    def test_stake_upsert(self, tmp_dir):
        from qbit_network.core.store import SQLiteStore
        store = SQLiteStore(tmp_dir)
        store.put_stake("s1", "v1", 100)
        store.put_stake("s1", "v1", 300)
        assert store.get_stake("s1", "v1") == 300
        store.close()


# ===========================================================================
# Integration: dPoS block production
# ===========================================================================

class TestDPoSBlockProduction:
    """Test that staked validators can produce blocks via dPoS selection."""

    def test_staked_validator_produces_blocks(self):
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        # Stake
        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 1000, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # With stake, produce more blocks
        for i in range(5):
            nonce = bc.get_nonce(w.address)
            tx = Transaction.notarize(w.address, f"{i:064x}", nonce=nonce)
            tx.sign(w.signing_sk, w.signing_pk)
            submit_and_mine(bc, w, tx)

        # All blocks produced successfully
        # genesis(0) + stake(1) + 5 notarize(2-6) = height 6
        assert bc.height >= 6

    def test_backward_compat_no_stake_poa(self):
        """Without staking, PoA round-robin still works."""
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)

        # No staking -- pure PoA
        tx = Transaction.notarize(w.address, "aabb", nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        assert bc.height == 1
        assert bc.get_active_validators() == []


# ===========================================================================
# Edge cases
# ===========================================================================

class TestStakingEdgeCases:
    """Edge cases for staking operations."""

    def test_unstake_zero_is_rejected_by_payload(self):
        w = Wallet.generate()
        tx = Transaction(TxType.UNSTAKE, w.address,
                         {"amount": 0, "validator_address": "qv1abc"})
        ok, err = tx.validate_payload()
        assert not ok

    def test_stake_float_amount_rejected(self):
        w = Wallet.generate()
        tx = Transaction(TxType.STAKE, w.address,
                         {"amount": 1.5, "validator_address": "qv1abc"})
        ok, err = tx.validate_payload()
        assert not ok

    def test_delegate_to_self_is_valid(self):
        """Self-delegation works the same as staking."""
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        nonce = bc.get_nonce(w.address)
        tx = Transaction.delegate(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        assert bc.get_validator_stake(w.address) == 100

    def test_stake_and_delegate_combine(self):
        """STAKE and DELEGATE from different senders combine total."""
        w = Wallet.generate()
        bc = _setup_chain_with_registered_validator(w)

        # Self-stake
        nonce = bc.get_nonce(w.address)
        tx = Transaction.stake(w.address, w.address, 100, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)

        # Delegation from another address
        delegator = Wallet.generate()
        nonce = bc.get_nonce(delegator.address)
        tx = Transaction.delegate(delegator.address, w.address, 50, nonce=nonce)
        tx.sign(delegator.signing_sk, delegator.signing_pk)
        submit_and_mine(bc, w, tx)

        assert bc.get_validator_stake(w.address) == 150
        assert bc.get_staker_info(w.address, w.address) == 100
        assert bc.get_staker_info(delegator.address, w.address) == 50

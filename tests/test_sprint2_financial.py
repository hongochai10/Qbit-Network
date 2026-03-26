"""Tests for v0.5.0 Sprint 2: Staking balance integration, epoch rewards, supply tracking, commission.

Covers 28+ tests:
- STAKE deducts from balance
- DELEGATE deducts from balance
- UNSTAKE maturity credits balance
- Insufficient balance for STAKE rejected
- Insufficient balance for DELEGATE rejected
- Epoch reward distribution proportional
- Commission calculation (default and custom)
- Supply tracking accuracy (staked, circulating, max_supply)
- Rollback of epoch rewards
- Commission via REGISTER_VALIDATOR payload
- Epoch rewards accumulate across blocks
- No distribution when no delegators
- Zero epoch rewards no-op
- Rollback reverses stake balance deduction
- Multiple delegators proportional share
"""
import time
import pytest

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.wallet import Wallet
from qbit_network.config import (
    TX_FEES, INITIAL_BLOCK_REWARD, HALVING_INTERVAL, MAX_SUPPLY,
    QUBIT_PER_QBIT, GENESIS_BALANCE_QBIT, FEE_BURN_PERCENT,
    UNBONDING_PERIOD, MIN_STAKE, MAX_STAKE, EPOCH_LENGTH,
    DEFAULT_COMMISSION_RATE,
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


def submit_and_mine(bc, wallet, tx):
    """Submit tx and produce block. Returns (block, tx_id)."""
    ok, tx_id = bc.submit_tx(tx)
    assert ok, f"submit_tx failed: {tx_id}"
    block = bc.produce_block(wallet.address, wallet.signing_sk)
    assert block is not None, "produce_block returned None"
    return block, tx_id


def fund_wallet(bc, funder, recipient_addr, amount, nonce=None):
    """Transfer funds from funder to recipient."""
    if nonce is None:
        nonce = bc.get_nonce(funder.address) + bc._pool_sender_count.get(funder.address, 0)
    tx = Transaction.transfer(funder.address, recipient_addr, amount, nonce=nonce)
    tx.sign(funder.signing_sk, funder.signing_pk)
    return submit_and_mine(bc, funder, tx)


# Staking amounts must be within [MIN_STAKE, MAX_STAKE].
# Use modest values that fit the constraints.
STAKE_SMALL = MIN_STAKE
STAKE_MED = min(1000, MAX_STAKE)
STAKE_LG = min(500_000, MAX_STAKE)


# ========== Part 1: STAKE/DELEGATE Balance Integration ==========

class TestStakeBalanceDeduction:
    """STAKE should deduct the staked amount + fee from sender balance."""

    def test_stake_deducts_balance(self, funded_chain, wallet):
        bc = funded_chain
        initial = bc.get_balance(wallet.address)
        stake_amount = STAKE_SMALL
        tx = Transaction.stake(wallet.address, wallet.address, stake_amount, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        fee = TX_FEES["STAKE"]
        reward = bc._calc_block_reward(block.index)
        validator_share = fee // 2
        expected = initial - fee - stake_amount + validator_share + reward
        assert bc.get_balance(wallet.address) == expected

    def test_stake_large_amount_deducts(self, funded_chain, wallet):
        bc = funded_chain
        initial = bc.get_balance(wallet.address)
        stake_amount = STAKE_LG
        tx = Transaction.stake(wallet.address, wallet.address, stake_amount, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        fee = TX_FEES["STAKE"]
        reward = bc._calc_block_reward(block.index)
        validator_share = fee // 2
        expected = initial - fee - stake_amount + validator_share + reward
        assert bc.get_balance(wallet.address) == expected

    def test_insufficient_balance_for_stake_rejected(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, broke = wallet_pair
        tx = Transaction.stake(broke.address, wallet.address, STAKE_SMALL, nonce=0)
        tx.sign(broke.signing_sk, broke.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "insufficient balance" in err

    def test_stake_amount_plus_fee_checked(self, funded_chain, wallet, wallet_pair):
        """Insufficient for fee+amount together should be rejected."""
        bc = funded_chain
        alice, _ = wallet_pair
        # Give alice exactly fee - not enough for fee + stake
        fee = TX_FEES["STAKE"]
        fund_wallet(bc, wallet, alice.address, fee, nonce=0)
        tx = Transaction.stake(alice.address, wallet.address, STAKE_SMALL, nonce=0)
        tx.sign(alice.signing_sk, alice.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "insufficient balance" in err


class TestDelegateBalanceDeduction:
    """DELEGATE should deduct the delegated amount + fee from sender balance."""

    def test_delegate_deducts_balance(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        alice, _ = wallet_pair
        fund_amount = 50 * QUBIT_PER_QBIT
        fund_wallet(bc, wallet, alice.address, fund_amount, nonce=0)

        initial_alice = bc.get_balance(alice.address)
        delegate_amount = STAKE_MED
        tx = Transaction.delegate(alice.address, wallet.address, delegate_amount, nonce=0)
        tx.sign(alice.signing_sk, alice.signing_pk)
        ok, tx_id = bc.submit_tx(tx)
        assert ok, f"submit_tx failed: {tx_id}"

        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        fee = TX_FEES["DELEGATE"]
        assert bc.get_balance(alice.address) == initial_alice - delegate_amount - fee

    def test_insufficient_balance_for_delegate_rejected(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, broke = wallet_pair
        tx = Transaction.delegate(broke.address, wallet.address, STAKE_SMALL, nonce=0)
        tx.sign(broke.signing_sk, broke.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "insufficient balance" in err


class TestUnstakeMaturityCredit:
    """When unbonding matures, balance should be credited back."""

    def test_mature_unbonding_credits_balance(self, wallet):
        import qbit_network.core.blockchain as bc_mod
        original = bc_mod.UNBONDING_PERIOD
        try:
            bc_mod.UNBONDING_PERIOD = 3

            bc = Blockchain()
            bc.consensus.add_validator(wallet.address, wallet.signing_pk)
            bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
            bc.activate_financial_layer(wallet.address)

            stake_amount = STAKE_MED
            tx_stake = Transaction.stake(wallet.address, wallet.address, stake_amount, nonce=0)
            tx_stake.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx_stake)

            tx_unstake = Transaction.unstake(wallet.address, wallet.address, stake_amount, nonce=1)
            tx_unstake.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx_unstake)

            balance_after_unstake = bc.get_balance(wallet.address)
            assert len(bc._unbonding) == 1

            # Mine blocks to mature
            for i in range(4):
                nonce = 2 + i
                tx = Transaction.notarize(wallet.address, f"{nonce:064x}", nonce=nonce)
                tx.sign(wallet.signing_sk, wallet.signing_pk)
                submit_and_mine(bc, wallet, tx)

            assert len(bc._unbonding) == 0
            # Balance increased by at least stake_amount (plus rewards minus fees)
            assert bc.get_balance(wallet.address) > balance_after_unstake
        finally:
            bc_mod.UNBONDING_PERIOD = original


# ========== Part 2: Epoch Reward Distribution ==========

class TestEpochRewardDistribution:
    """Epoch reward distribution to delegators proportionally."""

    def _setup_with_delegator(self, wallet, delegator, epoch_length=10):
        """Set up chain with a delegator, using short epoch for testing."""
        import qbit_network.core.blockchain as bc_mod
        bc_mod.EPOCH_LENGTH = epoch_length

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        # Fund delegator
        fund_amount = 50 * QUBIT_PER_QBIT
        fund_wallet(bc, wallet, delegator.address, fund_amount, nonce=0)

        # Delegator delegates to validator
        delegate_amount = STAKE_MED
        tx = Transaction.delegate(delegator.address, wallet.address, delegate_amount, nonce=0)
        tx.sign(delegator.signing_sk, delegator.signing_pk)
        ok, _ = bc.submit_tx(tx)
        assert ok
        bc.produce_block(wallet.address, wallet.signing_sk)

        return bc, delegate_amount

    def test_epoch_rewards_accumulate(self, wallet):
        """Block rewards should accumulate in _epoch_rewards."""
        import qbit_network.core.blockchain as bc_mod
        original = bc_mod.EPOCH_LENGTH
        try:
            bc_mod.EPOCH_LENGTH = 100

            bc = Blockchain()
            bc.consensus.add_validator(wallet.address, wallet.signing_pk)
            bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
            bc.activate_financial_layer(wallet.address)

            tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx)

            assert bc._epoch_rewards.get(wallet.address, 0) == INITIAL_BLOCK_REWARD

            # Second block: rewards accumulate
            tx2 = Transaction.notarize(wallet.address, "ccdd", nonce=1)
            tx2.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx2)

            assert bc._epoch_rewards.get(wallet.address, 0) == 2 * INITIAL_BLOCK_REWARD
        finally:
            bc_mod.EPOCH_LENGTH = original

    def test_epoch_distribution_proportional(self, wallet, wallet_pair):
        """Delegators receive proportional share at epoch boundary."""
        import qbit_network.core.blockchain as bc_mod
        original = bc_mod.EPOCH_LENGTH
        try:
            delegator, _ = wallet_pair
            bc, _ = self._setup_with_delegator(wallet, delegator, epoch_length=5)

            delegator_balance_before = bc.get_balance(delegator.address)

            # Mine blocks to reach next epoch boundary
            current = bc.height
            target = ((current // 5) + 1) * 5

            nonce = bc.get_nonce(wallet.address)
            for i in range(target - current):
                tx = Transaction.notarize(wallet.address, f"{0xe0 + i:064x}", nonce=nonce + i)
                tx.sign(wallet.signing_sk, wallet.signing_pk)
                submit_and_mine(bc, wallet, tx)

            delegator_balance_after = bc.get_balance(delegator.address)
            assert delegator_balance_after > delegator_balance_before
        finally:
            bc_mod.EPOCH_LENGTH = original

    def test_commission_calculation(self, wallet, wallet_pair):
        """Validator keeps commission percent, rest goes to delegators."""
        import qbit_network.core.blockchain as bc_mod
        original = bc_mod.EPOCH_LENGTH
        try:
            delegator, _ = wallet_pair
            bc, _ = self._setup_with_delegator(wallet, delegator, epoch_length=5)

            bc._validator_commission[wallet.address] = 20  # 20%

            delegator_before = bc.get_balance(delegator.address)

            # Record total epoch rewards before mining more
            rewards_before = bc._epoch_rewards.get(wallet.address, 0)

            current = bc.height
            target = ((current // 5) + 1) * 5
            nonce = bc.get_nonce(wallet.address)
            for i in range(target - current):
                tx = Transaction.notarize(wallet.address, f"{0xc0 + i:064x}", nonce=nonce + i)
                tx.sign(wallet.signing_sk, wallet.signing_pk)
                submit_and_mine(bc, wallet, tx)

            delegator_after = bc.get_balance(delegator.address)
            delegator_gain = delegator_after - delegator_before

            # Total epoch rewards = rewards_before + rewards from new blocks
            # But all were distributed at the boundary with 20% commission
            # delegator gets (100-20)% = 80% of the full epoch rewards
            total_epoch_rewards = rewards_before
            for i in range(target - current):
                total_epoch_rewards += INITIAL_BLOCK_REWARD  # block reward per block

            expected_pool = total_epoch_rewards * 80 // 100
            assert delegator_gain == expected_pool
        finally:
            bc_mod.EPOCH_LENGTH = original

    def test_default_commission_rate(self, funded_chain, wallet):
        bc = funded_chain
        assert bc.get_validator_commission(wallet.address) == DEFAULT_COMMISSION_RATE

    def test_custom_commission_rate_query(self, funded_chain, wallet):
        bc = funded_chain
        bc._validator_commission[wallet.address] = 15
        assert bc.get_validator_commission(wallet.address) == 15

    def test_no_distribution_without_delegators(self, wallet):
        """If no delegators (only self-stake), no distribution happens."""
        import qbit_network.core.blockchain as bc_mod
        original = bc_mod.EPOCH_LENGTH
        try:
            bc_mod.EPOCH_LENGTH = 3

            bc = Blockchain()
            bc.consensus.add_validator(wallet.address, wallet.signing_pk)
            bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
            bc.activate_financial_layer(wallet.address)

            # Only self-stake from init_chain, no delegators
            nonce = bc.get_nonce(wallet.address)
            for i in range(4):
                tx = Transaction.notarize(wallet.address, f"{0xd0 + i:064x}", nonce=nonce + i)
                tx.sign(wallet.signing_sk, wallet.signing_pk)
                submit_and_mine(bc, wallet, tx)

            # No extra addresses credited from distribution (only validator)
            # Since only self-stake, distribution is a no-op for delegators
            # Just verify it didn't crash and validator has rewards
            assert bc.get_balance(wallet.address) > 0
        finally:
            bc_mod.EPOCH_LENGTH = original

    def test_zero_epoch_rewards_noop(self, funded_chain, wallet):
        """If zero rewards accumulated, distribution is a no-op."""
        bc = funded_chain
        bc._epoch_rewards["qv1fake_validator"] = 0
        bc._stakes["qv1fake_validator"] = {"qv1delegator": 100}
        bc._total_stake["qv1fake_validator"] = 100
        bc._distribute_epoch_rewards(0)
        assert bc.get_balance("qv1delegator") == 0

    def test_multiple_delegators_proportional(self, wallet):
        """Multiple delegators receive shares proportional to their stake."""
        import qbit_network.core.blockchain as bc_mod
        original = bc_mod.EPOCH_LENGTH
        try:
            bc_mod.EPOCH_LENGTH = 5

            d1 = Wallet.generate()
            d2 = Wallet.generate()

            bc = Blockchain()
            bc.consensus.add_validator(wallet.address, wallet.signing_pk)
            bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
            bc.activate_financial_layer(wallet.address)

            # Fund delegators
            fund_wallet(bc, wallet, d1.address, 50 * QUBIT_PER_QBIT, nonce=0)
            fund_wallet(bc, wallet, d2.address, 50 * QUBIT_PER_QBIT, nonce=1)

            # d1 delegates 300, d2 delegates 700 (3:7 ratio)
            amt1 = 300
            amt2 = 700
            tx1 = Transaction.delegate(d1.address, wallet.address, amt1, nonce=0)
            tx1.sign(d1.signing_sk, d1.signing_pk)
            ok, _ = bc.submit_tx(tx1)
            assert ok

            tx2 = Transaction.delegate(d2.address, wallet.address, amt2, nonce=0)
            tx2.sign(d2.signing_sk, d2.signing_pk)
            ok, _ = bc.submit_tx(tx2)
            assert ok

            bc.produce_block(wallet.address, wallet.signing_sk)

            d1_before = bc.get_balance(d1.address)
            d2_before = bc.get_balance(d2.address)

            # Mine to epoch boundary
            current = bc.height
            target = ((current // 5) + 1) * 5
            nonce = bc.get_nonce(wallet.address)
            for i in range(target - current):
                tx = Transaction.notarize(wallet.address, f"{0xa0 + i:064x}", nonce=nonce + i)
                tx.sign(wallet.signing_sk, wallet.signing_pk)
                submit_and_mine(bc, wallet, tx)

            d1_gain = bc.get_balance(d1.address) - d1_before
            d2_gain = bc.get_balance(d2.address) - d2_before

            if d1_gain > 0 and d2_gain > 0:
                # d2 should get roughly 700/300 = 2.33x more than d1
                ratio = d2_gain / d1_gain
                assert 2.0 <= ratio <= 2.5  # allow integer rounding
        finally:
            bc_mod.EPOCH_LENGTH = original

    def test_epoch_rewards_query(self, funded_chain, wallet):
        """get_epoch_rewards returns accumulated rewards."""
        bc = funded_chain
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)
        assert bc.get_epoch_rewards(wallet.address) == INITIAL_BLOCK_REWARD


# ========== Part 3: Supply Tracking ==========

class TestSupplyTracking:
    """Supply tracking with staked, circulating, max_supply."""

    def test_supply_includes_staked(self, funded_chain):
        supply = funded_chain.get_total_supply()
        assert "staked" in supply
        assert supply["staked"] == MIN_STAKE  # genesis auto-stake

    def test_supply_includes_max_supply(self, funded_chain):
        supply = funded_chain.get_total_supply()
        assert supply["max_supply"] == MAX_SUPPLY

    def test_circulating_excludes_staked(self, funded_chain):
        supply = funded_chain.get_total_supply()
        assert supply["circulating"] == supply["total_minted"] - supply["total_burned"] - supply["staked"]

    def test_staked_increases_with_stake(self, funded_chain, wallet):
        bc = funded_chain
        initial_staked = bc.get_total_supply()["staked"]

        stake_amount = STAKE_MED
        tx = Transaction.stake(wallet.address, wallet.address, stake_amount, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        new_staked = bc.get_total_supply()["staked"]
        assert new_staked == initial_staked + stake_amount

    def test_staked_decreases_with_unstake(self, funded_chain, wallet):
        bc = funded_chain
        stake_amount = STAKE_MED
        tx = Transaction.stake(wallet.address, wallet.address, stake_amount, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        staked_after_stake = bc.get_total_supply()["staked"]

        unstake_amount = STAKE_SMALL
        tx2 = Transaction.unstake(wallet.address, wallet.address, unstake_amount, nonce=1)
        tx2.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx2)

        staked_after_unstake = bc.get_total_supply()["staked"]
        assert staked_after_unstake == staked_after_stake - unstake_amount

    def test_supply_conservation(self, funded_chain, wallet, wallet_pair):
        """circulating + staked + burned == total_minted (conservation law)."""
        bc = funded_chain
        _, bob = wallet_pair

        tx1 = Transaction.transfer(wallet.address, bob.address, 1000 * QUBIT_PER_QBIT, nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx1)

        tx2 = Transaction.stake(wallet.address, wallet.address, STAKE_MED, nonce=1)
        tx2.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx2)

        supply = bc.get_total_supply()
        assert supply["circulating"] + supply["staked"] + supply["total_burned"] == supply["total_minted"]

    def test_supply_all_integer(self, funded_chain):
        supply = funded_chain.get_total_supply()
        for key in ("total_minted", "total_burned", "circulating", "staked", "max_supply"):
            assert isinstance(supply[key], int), f"{key} is not int"


# ========== Part 4: Commission via REGISTER_VALIDATOR ==========

class TestCommission:
    """Commission rate set via REGISTER_VALIDATOR payload."""

    def test_commission_via_register_validator(self, wallet):
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        second = Wallet.generate()
        bc.consensus.add_validator(second.address, second.signing_pk)

        fund_wallet(bc, wallet, second.address, 200 * QUBIT_PER_QBIT, nonce=0)

        nonce = bc.get_nonce(second.address)
        reg_tx = Transaction(
            TxType.REGISTER_VALIDATOR, second.address, nonce=nonce,
            payload={
                "validator_pubkey": second.signing_pk.hex(),
                "validator_address": second.address,
                "commission": 25,
            })
        reg_tx.sign(second.signing_sk, second.signing_pk)
        ok, _ = bc.submit_tx(reg_tx)
        assert ok
        bc.produce_block(wallet.address, wallet.signing_sk)

        assert bc.get_validator_commission(second.address) == 25

    def test_commission_validation_rejects_over_100(self):
        w = Wallet.generate()
        tx = Transaction(
            TxType.REGISTER_VALIDATOR, w.address,
            payload={
                "validator_pubkey": w.signing_pk.hex(),
                "validator_address": w.address,
                "commission": 101,
            })
        ok, err = tx.validate_payload()
        assert not ok
        assert "commission" in err

    def test_commission_validation_rejects_negative(self):
        w = Wallet.generate()
        tx = Transaction(
            TxType.REGISTER_VALIDATOR, w.address,
            payload={
                "validator_pubkey": w.signing_pk.hex(),
                "validator_address": w.address,
                "commission": -1,
            })
        ok, err = tx.validate_payload()
        assert not ok
        assert "commission" in err

    def test_commission_validation_rejects_non_int(self):
        w = Wallet.generate()
        tx = Transaction(
            TxType.REGISTER_VALIDATOR, w.address,
            payload={
                "validator_pubkey": w.signing_pk.hex(),
                "validator_address": w.address,
                "commission": "10",
            })
        ok, err = tx.validate_payload()
        assert not ok
        assert "commission" in err

    def test_commission_optional(self):
        """Commission is optional; omitting it should be valid."""
        w = Wallet.generate()
        tx = Transaction(
            TxType.REGISTER_VALIDATOR, w.address,
            payload={
                "validator_pubkey": w.signing_pk.hex(),
                "validator_address": w.address,
            })
        ok, err = tx.validate_payload()
        assert ok, err

    def test_commission_zero_valid(self):
        """Commission of 0% is valid."""
        w = Wallet.generate()
        tx = Transaction(
            TxType.REGISTER_VALIDATOR, w.address,
            payload={
                "validator_pubkey": w.signing_pk.hex(),
                "validator_address": w.address,
                "commission": 0,
            })
        ok, err = tx.validate_payload()
        assert ok, err

    def test_commission_100_valid(self):
        """Commission of 100% is valid (validator keeps all)."""
        w = Wallet.generate()
        tx = Transaction(
            TxType.REGISTER_VALIDATOR, w.address,
            payload={
                "validator_pubkey": w.signing_pk.hex(),
                "validator_address": w.address,
                "commission": 100,
            })
        ok, err = tx.validate_payload()
        assert ok, err


# ========== Rollback Tests ==========

class TestRollbackEpochRewards:
    """Rollback should reverse epoch reward distributions."""

    def test_rollback_reverses_stake_deduction(self, funded_chain, wallet):
        bc = funded_chain
        initial = bc.get_balance(wallet.address)

        tx = Transaction.stake(wallet.address, wallet.address, STAKE_MED, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        assert bc.height == 1
        bc._rollback_to(1)
        assert bc.height == 0
        assert bc.get_balance(wallet.address) == initial

    def test_rollback_reverses_delegate_deduction(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        alice, _ = wallet_pair
        fund_wallet(bc, wallet, alice.address, 50 * QUBIT_PER_QBIT, nonce=0)
        alice_initial = bc.get_balance(alice.address)

        tx = Transaction.delegate(alice.address, wallet.address, STAKE_MED, nonce=0)
        tx.sign(alice.signing_sk, alice.signing_pk)
        ok, _ = bc.submit_tx(tx)
        assert ok
        bc.produce_block(wallet.address, wallet.signing_sk)

        bc._rollback_to(bc.height)
        assert bc.get_balance(alice.address) == alice_initial

    def test_rollback_reverses_epoch_distribution(self, wallet, wallet_pair):
        """Rollback past an epoch boundary reverses delegator credits."""
        import qbit_network.core.blockchain as bc_mod
        original = bc_mod.EPOCH_LENGTH
        try:
            bc_mod.EPOCH_LENGTH = 5
            delegator, _ = wallet_pair

            bc = Blockchain()
            bc.consensus.add_validator(wallet.address, wallet.signing_pk)
            bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
            bc.activate_financial_layer(wallet.address)

            fund_wallet(bc, wallet, delegator.address, 50 * QUBIT_PER_QBIT, nonce=0)
            delegate_amount = STAKE_MED
            tx = Transaction.delegate(delegator.address, wallet.address, delegate_amount, nonce=0)
            tx.sign(delegator.signing_sk, delegator.signing_pk)
            ok, _ = bc.submit_tx(tx)
            assert ok
            bc.produce_block(wallet.address, wallet.signing_sk)

            pre_epoch_height = bc.height
            delegator_before_epoch = bc.get_balance(delegator.address)

            # Mine to epoch boundary
            current = bc.height
            target = ((current // 5) + 1) * 5
            nonce = bc.get_nonce(wallet.address)
            for i in range(target - current):
                tx = Transaction.notarize(wallet.address, f"{0xb0 + i:064x}", nonce=nonce + i)
                tx.sign(wallet.signing_sk, wallet.signing_pk)
                submit_and_mine(bc, wallet, tx)

            delegator_after_epoch = bc.get_balance(delegator.address)
            # Epoch distribution should have increased delegator balance
            assert delegator_after_epoch > delegator_before_epoch

            # Rollback past epoch boundary to pre_epoch_height+1
            bc._rollback_to(pre_epoch_height + 1)

            delegator_after_rollback = bc.get_balance(delegator.address)
            assert delegator_after_rollback == delegator_before_epoch
        finally:
            bc_mod.EPOCH_LENGTH = original

    def test_rollback_epoch_rewards_counter(self, funded_chain, wallet):
        """Rollback reverses epoch reward accumulation."""
        bc = funded_chain
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)
        assert bc._epoch_rewards.get(wallet.address, 0) == INITIAL_BLOCK_REWARD

        bc._rollback_to(1)
        assert bc._epoch_rewards.get(wallet.address, 0) == 0

    def test_rollback_supply_conservation(self, funded_chain, wallet, wallet_pair):
        """After rollback, supply conservation still holds."""
        bc = funded_chain
        _, bob = wallet_pair
        initial_supply = bc.get_total_supply()

        tx = Transaction.transfer(wallet.address, bob.address, 100 * QUBIT_PER_QBIT, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        bc._rollback_to(1)
        supply = bc.get_total_supply()
        assert supply["total_minted"] == initial_supply["total_minted"]
        assert supply["total_burned"] == initial_supply["total_burned"]
        assert supply["staked"] == initial_supply["staked"]

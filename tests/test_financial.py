"""Tests for v0.5.0 financial layer: balance ledger, TRANSFER, fees, block rewards."""
import json
import os
import shutil
import tempfile
import time
import pytest

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.wallet import Wallet
from qbit_network.config import (
    TX_FEES, INITIAL_BLOCK_REWARD, HALVING_INTERVAL, MAX_SUPPLY,
    QUBIT_PER_QBIT, GENESIS_BALANCE_QBIT, FEE_BURN_PERCENT,
    UNBONDING_PERIOD, MIN_STAKE,
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
    d = tempfile.mkdtemp(prefix="qv_fin_test_")
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


# ========== Credit/Debit Primitives ==========

class TestCreditDebit:
    def test_credit_basic(self, funded_chain):
        bc = funded_chain
        addr = "qv1test_credit"
        bc._credit(addr, 1000)
        assert bc.get_balance(addr) == 1000

    def test_credit_accumulates(self, funded_chain):
        bc = funded_chain
        addr = "qv1test_credit_acc"
        bc._credit(addr, 500)
        bc._credit(addr, 300)
        assert bc.get_balance(addr) == 800

    def test_credit_zero_is_noop(self, funded_chain):
        bc = funded_chain
        addr = "qv1test_zero"
        bc._credit(addr, 0)
        assert bc.get_balance(addr) == 0

    def test_credit_negative_raises(self, funded_chain):
        with pytest.raises(ValueError, match="non-negative"):
            funded_chain._credit("qv1test", -1)

    def test_debit_basic(self, funded_chain):
        bc = funded_chain
        addr = "qv1test_debit"
        bc._credit(addr, 1000)
        bc._debit(addr, 400)
        assert bc.get_balance(addr) == 600

    def test_debit_exact_balance(self, funded_chain):
        bc = funded_chain
        addr = "qv1test_exact"
        bc._credit(addr, 500)
        bc._debit(addr, 500)
        assert bc.get_balance(addr) == 0

    def test_debit_insufficient_raises(self, funded_chain):
        bc = funded_chain
        addr = "qv1test_insuf"
        bc._credit(addr, 100)
        with pytest.raises(ValueError, match="insufficient balance"):
            bc._debit(addr, 101)

    def test_debit_zero_balance_raises(self, funded_chain):
        with pytest.raises(ValueError, match="insufficient balance"):
            funded_chain._debit("qv1nonexistent", 1)

    def test_debit_negative_raises(self, funded_chain):
        with pytest.raises(ValueError, match="non-negative"):
            funded_chain._debit("qv1test", -1)

    def test_debit_zero_is_noop(self, funded_chain):
        bc = funded_chain
        addr = "qv1test_dzero"
        bc._credit(addr, 100)
        bc._debit(addr, 0)
        assert bc.get_balance(addr) == 100

    def test_get_balance_unknown_addr(self, funded_chain):
        assert funded_chain.get_balance("qv1unknown") == 0


# ========== TRANSFER ==========

class TestTransfer:
    def test_valid_transfer(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, bob = wallet_pair
        initial = bc.get_balance(wallet.address)
        amount = 5_000_000_000  # 50 QBIT
        tx = Transaction.transfer(wallet.address, bob.address, amount, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        fee = TX_FEES["TRANSFER"]
        assert bc.get_balance(bob.address) == amount
        # Sender lost amount + fee, but gained block reward + validator fee share
        reward = bc._calc_block_reward(block.index)
        validator_fee_share = fee // 2
        expected = initial - amount - fee + reward + validator_fee_share
        assert bc.get_balance(wallet.address) == expected

    def test_transfer_with_memo(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, bob = wallet_pair
        tx = Transaction.transfer(wallet.address, bob.address, 1000, memo="test payment", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)
        assert bc.get_balance(bob.address) == 1000

    def test_transfer_insufficient_balance(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, bob = wallet_pair
        # Try to transfer more than available
        huge = bc.get_balance(wallet.address) + 1
        tx = Transaction.transfer(wallet.address, bob.address, huge, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "insufficient balance" in err

    def test_self_transfer_rejected(self, funded_chain, wallet):
        bc = funded_chain
        tx = Transaction.transfer(wallet.address, wallet.address, 100, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "self" in err.lower()

    def test_transfer_no_recipient_rejected(self, funded_chain, wallet):
        bc = funded_chain
        tx = Transaction(
            TxType.TRANSFER, wallet.address,
            payload={"amount": 100}, nonce=0, recipient="")
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok

    def test_transfer_zero_amount_rejected(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, bob = wallet_pair
        tx = Transaction.transfer(wallet.address, bob.address, 0, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok

    def test_transfer_negative_amount_rejected(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, bob = wallet_pair
        tx = Transaction.transfer(wallet.address, bob.address, -100, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok

    def test_transfer_memo_too_long(self, funded_chain, wallet, wallet_pair):
        bc = funded_chain
        _, bob = wallet_pair
        tx = Transaction.transfer(wallet.address, bob.address, 100, memo="x" * 257, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "memo" in err.lower()

    def test_double_spend_within_block(self, funded_chain, wallet, wallet_pair):
        """Two transfers in pool that would exceed balance together."""
        bc = funded_chain
        _, bob = wallet_pair
        balance = bc.get_balance(wallet.address)
        fee = TX_FEES["TRANSFER"]
        # First tx uses most of the balance
        amount1 = balance - fee - 1000
        tx1 = Transaction.transfer(wallet.address, bob.address, amount1, nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        ok1, _ = bc.submit_tx(tx1)
        assert ok1

        # Second tx tries to spend more than remaining (pending debits check)
        tx2 = Transaction.transfer(wallet.address, bob.address, 2000, nonce=1)
        tx2.sign(wallet.signing_sk, wallet.signing_pk)
        ok2, err2 = bc.submit_tx(tx2)
        assert not ok2
        assert "insufficient balance" in err2


# ========== Fee Deduction & Burn ==========

class TestFees:
    def test_notarize_fee_deducted(self, funded_chain, wallet):
        bc = funded_chain
        initial = bc.get_balance(wallet.address)
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        fee = TX_FEES["NOTARIZE"]
        reward = bc._calc_block_reward(block.index)
        validator_share = fee // 2
        burn = fee - validator_share
        # Validator is also the sender: -fee + validator_share + reward
        expected = initial - fee + validator_share + reward
        assert bc.get_balance(wallet.address) == expected
        # Check burn accounting
        assert bc._total_burned == burn

    def test_store_fee_deducted(self, funded_chain, wallet):
        bc = funded_chain
        initial = bc.get_balance(wallet.address)
        tx = Transaction.store(wallet.address, "aabb", "cid123", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)
        fee = TX_FEES["STORE"]
        reward = bc._calc_block_reward(block.index)
        validator_share = fee // 2
        expected = initial - fee + validator_share + reward
        assert bc.get_balance(wallet.address) == expected

    def test_revoke_key_free(self, funded_chain, wallet):
        """REVOKE_KEY has zero fee."""
        bc = funded_chain
        initial = bc.get_balance(wallet.address)
        # First register encryption key so we can revoke it
        reg_tx = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        reg_tx.sign(wallet.signing_sk, wallet.signing_pk)
        block1, _ = submit_and_mine(bc, wallet, reg_tx)

        balance_after_reg = bc.get_balance(wallet.address)
        # Now revoke
        rev_tx = Transaction.revoke_key(wallet.address, "encryption", "compromised", nonce=1)
        rev_tx.sign(wallet.signing_sk, wallet.signing_pk)
        block2, _ = submit_and_mine(bc, wallet, rev_tx)

        # REVOKE_KEY fee is 0, so only reward is added
        reward2 = bc._calc_block_reward(block2.index)
        assert bc.get_balance(wallet.address) == balance_after_reg + reward2

    def test_fee_burn_accounting(self, funded_chain, wallet):
        bc = funded_chain
        tx = Transaction.notarize(wallet.address, "ccdd", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        fee = TX_FEES["NOTARIZE"]
        validator_share = fee // 2
        burn = fee - validator_share
        assert bc._total_burned == burn

    def test_supply_after_fees(self, funded_chain, wallet):
        bc = funded_chain
        tx = Transaction.notarize(wallet.address, "eeff", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        supply = bc.get_total_supply()
        genesis_balance = GENESIS_BALANCE_QBIT * QUBIT_PER_QBIT
        reward = bc._calc_block_reward(block.index)
        assert supply["total_minted"] == genesis_balance + reward
        fee = TX_FEES["NOTARIZE"]
        burn = fee - (fee // 2)
        assert supply["total_burned"] == burn
        assert supply["circulating"] == supply["total_minted"] - supply["total_burned"] - supply["staked"]
        assert supply["max_supply"] == MAX_SUPPLY

    def test_insufficient_balance_for_fee_rejected(self, funded_chain, wallet, wallet_pair):
        """A wallet with zero balance cannot submit fee-bearing txs."""
        bc = funded_chain
        _, broke = wallet_pair
        # broke has 0 balance
        tx = Transaction.notarize(broke.address, "aabb", nonce=0)
        tx.sign(broke.signing_sk, broke.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "insufficient balance" in err


# ========== Block Reward & Halving ==========

class TestBlockReward:
    def test_block_reward_applied(self, funded_chain, wallet):
        bc = funded_chain
        initial = bc.get_balance(wallet.address)
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        # Block reward should have been applied
        reward = INITIAL_BLOCK_REWARD
        fee = TX_FEES["NOTARIZE"]
        validator_share = fee // 2
        expected = initial - fee + validator_share + reward
        assert bc.get_balance(wallet.address) == expected

    def test_block_reward_halving(self, funded_chain):
        bc = funded_chain
        # _calc_block_reward returns raw reward; the idx>0 guard is in _append_block_inner.
        # So _calc_block_reward(0) does return INITIAL_BLOCK_REWARD but it's never applied at genesis.
        assert bc._calc_block_reward(1) == INITIAL_BLOCK_REWARD
        assert bc._calc_block_reward(HALVING_INTERVAL - 1) == INITIAL_BLOCK_REWARD
        assert bc._calc_block_reward(HALVING_INTERVAL) == INITIAL_BLOCK_REWARD // 2
        assert bc._calc_block_reward(2 * HALVING_INTERVAL) == INITIAL_BLOCK_REWARD // 4

    def test_supply_cap_enforcement(self, funded_chain):
        bc = funded_chain
        # Set _total_minted close to MAX_SUPPLY
        bc._total_minted = MAX_SUPPLY - 100
        reward = bc._calc_block_reward(1)
        assert reward == 100  # capped at remaining

        bc._total_minted = MAX_SUPPLY
        reward = bc._calc_block_reward(1)
        assert reward == 0  # no more minting

    def test_genesis_block_no_reward(self, funded_chain):
        """Genesis block (index 0) does not receive block reward."""
        bc = funded_chain
        assert bc._calc_block_reward(0) == 0

    def test_total_minted_increases_per_block(self, funded_chain, wallet):
        bc = funded_chain
        genesis_minted = bc._total_minted
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)
        assert bc._total_minted == genesis_minted + INITIAL_BLOCK_REWARD


# ========== Balance Persistence (SQLite) ==========

class TestBalancePersistence:
    def test_balance_survives_reload(self, wallet, tmp_dir):
        """Balance persists through SQLite save/load cycle."""
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        genesis_balance = GENESIS_BALANCE_QBIT * QUBIT_PER_QBIT
        assert bc.get_balance(wallet.address) == genesis_balance

        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        balance_before = bc.get_balance(wallet.address)
        minted_before = bc._total_minted
        burned_before = bc._total_burned

        # Reload
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load()
        assert loaded
        assert bc2.get_balance(wallet.address) == balance_before
        assert bc2._total_minted == minted_before
        assert bc2._total_burned == burned_before
        assert bc2._financial_active is True

    def test_supply_counters_persist(self, wallet, tmp_dir):
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        tx = Transaction.notarize(wallet.address, "ccdd", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        supply_before = bc.get_total_supply()

        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc2.load()

        supply_after = bc2.get_total_supply()
        assert supply_after == supply_before


# ========== Rollback ==========

class TestRollback:
    def test_rollback_reverses_transfer(self, funded_chain, wallet, wallet_pair):
        """Rollback should reverse all balance changes."""
        bc = funded_chain
        _, bob = wallet_pair
        initial_sender = bc.get_balance(wallet.address)
        initial_bob = bc.get_balance(bob.address)
        initial_minted = bc._total_minted
        initial_burned = bc._total_burned

        # Do a transfer
        tx = Transaction.transfer(wallet.address, bob.address, 1_000_000, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        # Verify balances changed
        assert bc.get_balance(bob.address) > initial_bob
        assert bc.height == 1

        # Rollback
        bc._rollback_to(1)  # roll back to before block 1

        assert bc.height == 0
        assert bc.get_balance(wallet.address) == initial_sender
        assert bc.get_balance(bob.address) == initial_bob
        assert bc._total_minted == initial_minted
        assert bc._total_burned == initial_burned

    def test_rollback_reverses_fees(self, funded_chain, wallet):
        bc = funded_chain
        initial = bc.get_balance(wallet.address)
        initial_burned = bc._total_burned

        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        assert bc._total_burned > initial_burned

        bc._rollback_to(1)
        assert bc.get_balance(wallet.address) == initial
        assert bc._total_burned == initial_burned

    def test_rollback_reverses_reward(self, funded_chain, wallet):
        bc = funded_chain
        initial_minted = bc._total_minted

        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        assert bc._total_minted > initial_minted

        bc._rollback_to(1)
        assert bc._total_minted == initial_minted


# ========== STAKE deducts balance ==========

class TestStakeBalance:
    def test_stake_deducts_balance(self, funded_chain, wallet):
        """STAKE should debit the staked amount from the sender's balance."""
        bc = funded_chain
        initial = bc.get_balance(wallet.address)
        stake_amount = MIN_STAKE
        tx = Transaction.stake(wallet.address, wallet.address, stake_amount, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        fee = TX_FEES["STAKE"]
        reward = bc._calc_block_reward(block.index)
        validator_share = fee // 2
        # Sender pays fee + stake_amount, gets validator_share + reward
        expected = initial - fee - stake_amount + validator_share + reward
        assert bc.get_balance(wallet.address) == expected

    def test_insufficient_balance_for_stake(self, funded_chain, wallet, wallet_pair):
        """Cannot stake more than available balance (minus fee)."""
        bc = funded_chain
        _, broke = wallet_pair
        # broke has 0 balance
        tx = Transaction.stake(broke.address, wallet.address, MIN_STAKE, nonce=0)
        tx.sign(broke.signing_sk, broke.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "insufficient balance" in err


# ========== Mature Unbonding Credits ==========

class TestUnbondingCredit:
    def test_mature_unbonding_credits_balance(self, wallet):
        """When unbonding matures, balance should be credited back.

        Uses monkeypatching to set a short unbonding period.
        """
        import qbit_network.core.blockchain as bc_mod
        original = bc_mod.UNBONDING_PERIOD
        try:
            bc_mod.UNBONDING_PERIOD = 3  # short for testing

            bc = Blockchain()
            bc.consensus.add_validator(wallet.address, wallet.signing_pk)
            bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
            bc.activate_financial_layer(wallet.address)

            # Stake first
            stake_amount = MIN_STAKE
            tx_stake = Transaction.stake(wallet.address, wallet.address, stake_amount, nonce=0)
            tx_stake.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx_stake)

            # Unstake
            tx_unstake = Transaction.unstake(wallet.address, wallet.address, stake_amount, nonce=1)
            tx_unstake.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx_unstake)

            balance_after_unstake = bc.get_balance(wallet.address)
            assert len(bc._unbonding) == 1

            # Mine blocks to mature the unbonding (short period = 3)
            for i in range(4):
                nonce = 2 + i
                tx = Transaction.notarize(wallet.address, f"{nonce:064x}", nonce=nonce)
                tx.sign(wallet.signing_sk, wallet.signing_pk)
                submit_and_mine(bc, wallet, tx)

            # Unbonding should be matured and balance credited
            assert len(bc._unbonding) == 0
            # Balance should have increased (stake_amount credited back + rewards)
            assert bc.get_balance(wallet.address) > balance_after_unstake
        finally:
            bc_mod.UNBONDING_PERIOD = original


# ========== Genesis Balance ==========

class TestGenesisBalance:
    def test_genesis_balance_allocated(self, funded_chain, wallet):
        genesis_balance = GENESIS_BALANCE_QBIT * QUBIT_PER_QBIT
        assert bc_genesis_balance(funded_chain, wallet) >= genesis_balance

    def test_genesis_balance_value(self, funded_chain, wallet):
        genesis_balance = GENESIS_BALANCE_QBIT * QUBIT_PER_QBIT
        # Only genesis balance, no blocks produced yet
        assert funded_chain.get_balance(wallet.address) == genesis_balance

    def test_no_financial_layer_no_balance(self, wallet):
        """Without activate_financial_layer, no balance is allocated."""
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        assert bc.get_balance(wallet.address) == 0
        assert bc._financial_active is False


def bc_genesis_balance(bc, wallet):
    """Helper to get balance at genesis."""
    return bc.get_balance(wallet.address)


# ========== Get Total Supply ==========

class TestSupply:
    def test_initial_supply(self, funded_chain):
        supply = funded_chain.get_total_supply()
        genesis_balance = GENESIS_BALANCE_QBIT * QUBIT_PER_QBIT
        assert supply["total_minted"] == genesis_balance
        assert supply["total_burned"] == 0
        assert supply["staked"] == MIN_STAKE  # genesis validator auto-staked
        assert supply["circulating"] == genesis_balance - MIN_STAKE
        assert supply["max_supply"] == MAX_SUPPLY

    def test_supply_after_blocks(self, funded_chain, wallet):
        bc = funded_chain
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block, _ = submit_and_mine(bc, wallet, tx)

        supply = bc.get_total_supply()
        genesis_balance = GENESIS_BALANCE_QBIT * QUBIT_PER_QBIT
        reward = INITIAL_BLOCK_REWARD
        fee = TX_FEES["NOTARIZE"]
        burn = fee - (fee // 2)
        assert supply["total_minted"] == genesis_balance + reward
        assert supply["total_burned"] == burn
        assert supply["staked"] == MIN_STAKE
        assert supply["max_supply"] == MAX_SUPPLY


# ========== TRANSFER Payload Validation ==========

class TestTransferPayloadValidation:
    def test_transfer_amount_not_int(self):
        tx = Transaction(
            TxType.TRANSFER, "qv1sender", payload={"amount": "100"},
            recipient="qv1recipient")
        ok, err = tx.validate_payload()
        assert not ok
        assert "positive integer" in err

    def test_transfer_amount_float(self):
        tx = Transaction(
            TxType.TRANSFER, "qv1" + "a" * 64, payload={"amount": 1.5},
            recipient="qv1" + "b" * 64)
        ok, err = tx.validate_payload()
        assert not ok

    def test_transfer_amount_zero(self):
        tx = Transaction(
            TxType.TRANSFER, "qv1" + "a" * 64, payload={"amount": 0},
            recipient="qv1" + "b" * 64)
        ok, err = tx.validate_payload()
        assert not ok
        assert "positive" in err

    def test_transfer_no_recipient(self):
        tx = Transaction(
            TxType.TRANSFER, "qv1" + "a" * 64, payload={"amount": 100},
            recipient="")
        ok, err = tx.validate_payload()
        assert not ok
        assert "recipient" in err.lower()

    def test_transfer_self_recipient(self):
        tx = Transaction(
            TxType.TRANSFER, "qv1" + "a" * 64, payload={"amount": 100},
            recipient="qv1" + "a" * 64)
        ok, err = tx.validate_payload()
        assert not ok
        assert "self" in err.lower()

    def test_transfer_memo_not_string(self):
        tx = Transaction(
            TxType.TRANSFER, "qv1" + "a" * 64,
            payload={"amount": 100, "memo": 123},
            recipient="qv1" + "b" * 64)
        ok, err = tx.validate_payload()
        assert not ok
        assert "memo" in err.lower()

    def test_transfer_valid_payload(self):
        tx = Transaction(
            TxType.TRANSFER, "qv1" + "a" * 64,
            payload={"amount": 100, "memo": "test"},
            recipient="qv1" + "b" * 64)
        ok, err = tx.validate_payload()
        assert ok

    def test_transfer_unknown_key_rejected(self):
        tx = Transaction(
            TxType.TRANSFER, "qv1" + "a" * 64,
            payload={"amount": 100, "extra_field": "bad"},
            recipient="qv1" + "b" * 64)
        ok, err = tx.validate_payload()
        assert not ok
        assert "unknown" in err.lower()


# ========== Factory Method ==========

class TestTransferFactory:
    def test_factory_basic(self):
        tx = Transaction.transfer("qv1sender", "qv1recipient", 5000, nonce=3)
        assert tx.tx_type == TxType.TRANSFER
        assert tx.sender == "qv1sender"
        assert tx.recipient == "qv1recipient"
        assert tx.payload["amount"] == 5000
        assert tx.nonce == 3

    def test_factory_with_memo(self):
        tx = Transaction.transfer("qv1sender", "qv1recipient", 100, memo="hello")
        assert tx.payload["memo"] == "hello"

    def test_factory_no_memo(self):
        tx = Transaction.transfer("qv1sender", "qv1recipient", 100)
        assert "memo" not in tx.payload

    def test_transfer_serialization_roundtrip(self, wallet, wallet_pair):
        _, bob = wallet_pair
        tx = Transaction.transfer(wallet.address, bob.address, 42000, memo="test", nonce=5)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        d = tx.to_dict()
        tx2 = Transaction.from_dict(d)
        assert tx2.tx_type == TxType.TRANSFER
        assert tx2.payload["amount"] == 42000
        assert tx2.payload["memo"] == "test"
        assert tx2.tx_id == tx.tx_id


# ========== Integer Arithmetic ==========

class TestIntegerArithmetic:
    def test_no_float_in_balance(self, funded_chain, wallet, wallet_pair):
        """Balances must always be integers."""
        bc = funded_chain
        _, bob = wallet_pair
        tx = Transaction.transfer(wallet.address, bob.address, 1, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)
        assert isinstance(bc.get_balance(wallet.address), int)
        assert isinstance(bc.get_balance(bob.address), int)

    def test_fee_burn_is_integer(self, funded_chain, wallet):
        bc = funded_chain
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)
        assert isinstance(bc._total_burned, int)
        assert isinstance(bc._total_minted, int)

    def test_supply_values_are_integers(self, funded_chain):
        supply = funded_chain.get_total_supply()
        assert isinstance(supply["total_minted"], int)
        assert isinstance(supply["total_burned"], int)
        assert isinstance(supply["circulating"], int)


# ========== Financial Layer Activation ==========

class TestFinancialActivation:
    def test_activate_idempotent(self, wallet):
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)
        balance1 = bc.get_balance(wallet.address)
        # Calling again should not double-allocate
        bc.activate_financial_layer(wallet.address)
        assert bc.get_balance(wallet.address) == balance1

    def test_no_fees_without_activation(self, wallet, wallet_pair):
        """Without financial layer, txs do not pay fees."""
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        assert not bc._financial_active
        # Should succeed even without balance
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, _ = bc.submit_tx(tx)
        assert ok

    def test_fees_enforced_with_activation(self, funded_chain, wallet, wallet_pair):
        """With financial layer active, fees are enforced."""
        bc = funded_chain
        _, broke = wallet_pair
        assert bc._financial_active
        tx = Transaction.notarize(broke.address, "aabb", nonce=0)
        tx.sign(broke.signing_sk, broke.signing_pk)
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "insufficient balance" in err


# ========== Multiple Transfers ==========

class TestMultipleTransfers:
    def test_chain_of_transfers(self, funded_chain, wallet):
        """Transfer from A to B, then B to C, verifying balances at each step."""
        bc = funded_chain
        bob = Wallet.generate()
        carol = Wallet.generate()

        # A -> B
        amount1 = 10_000_000_000  # 100 QBIT
        tx1 = Transaction.transfer(wallet.address, bob.address, amount1, nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx1)

        assert bc.get_balance(bob.address) == amount1

        # B -> C (B needs to register as non-validator sender)
        amount2 = 5_000_000_000  # 50 QBIT
        tx2 = Transaction.transfer(bob.address, carol.address, amount2, nonce=0)
        tx2.sign(bob.signing_sk, bob.signing_pk)
        ok, _ = bc.submit_tx(tx2)
        assert ok

        # Mine the block
        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        fee = TX_FEES["TRANSFER"]
        assert bc.get_balance(carol.address) == amount2
        assert bc.get_balance(bob.address) == amount1 - amount2 - fee

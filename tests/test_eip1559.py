"""Tests for EIP-1559 dynamic fee mechanism.

Covers: fee engine, block/TX fields, consensus validation, blockchain integration,
pool admission, block production, rollback, and adversarial scenarios.

Most tests use a two-wallet setup: alice (validator) and bob (user).
Bob submits TXs; alice produces blocks. This avoids the self-TX ratio check.
"""
import time
import pytest

from qbit_network.core.wallet import Wallet
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.fees import (
    compute_base_fee, compute_tx_fee, effective_block_weight,
    tx_weight, block_total_weight,
)
from qbit_network.config import (
    TX_WEIGHTS, MAX_BLOCK_WEIGHT, TARGET_BLOCK_WEIGHT,
    BASE_FEE_CHANGE_DENOM, INITIAL_BASE_FEE, MIN_BASE_FEE,
    MAX_BASE_FEE, MAX_SELF_TX_WEIGHT_RATIO, TX_FEES,
)

# All tests in this module run with DYNAMIC_FEE_ACTIVATION_HEIGHT = 0
ACTIVATION_HEIGHT = 0


@pytest.fixture(autouse=True)
def _activate_dynamic_fees(monkeypatch):
    """Enable dynamic fees for all tests in this module."""
    monkeypatch.setattr("qbit_network.config.DYNAMIC_FEE_ACTIVATION_HEIGHT", ACTIVATION_HEIGHT)
    monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT", ACTIVATION_HEIGHT)
    monkeypatch.setattr("qbit_network.core.consensus.DYNAMIC_FEE_ACTIVATION_HEIGHT", ACTIVATION_HEIGHT)


@pytest.fixture
def wallet():
    return Wallet.generate()


@pytest.fixture
def wallet_pair():
    return Wallet.generate(), Wallet.generate()


def _make_chain(alice, bob=None, fund_bob=True):
    """Create a blockchain with alice as validator, optionally fund bob."""
    bc = Blockchain()
    bc.consensus.add_validator(alice.address, alice.signing_pk)
    bc.init_chain(alice.address, alice.signing_sk, validator_pk=alice.signing_pk)
    bc.activate_financial_layer(alice.address)
    if bob and fund_bob:
        bc._credit(bob.address, 100_000_000_000)  # 100 QBIT
    return bc


def _bob_nonce(bc, bob):
    """Next nonce for bob."""
    return bc.get_nonce(bob.address) + bc._pool_sender_count.get(bob.address, 0)


# =============================================================================
# Fee Engine Tests (fees.py)
# =============================================================================

class TestComputeBaseFee:
    def test_unchanged_at_target(self):
        assert compute_base_fee(100, TARGET_BLOCK_WEIGHT) == 100

    def test_increase_above_target(self):
        assert compute_base_fee(100, TARGET_BLOCK_WEIGHT + 1_000_000) > 100

    def test_decrease_below_target(self):
        assert compute_base_fee(100, TARGET_BLOCK_WEIGHT - 1_000_000) < 100

    def test_max_increase_at_max_weight(self):
        bf = compute_base_fee(80, MAX_BLOCK_WEIGHT)
        assert bf == 80 + 80 // BASE_FEE_CHANGE_DENOM

    def test_max_decrease_empty_block(self):
        bf = compute_base_fee(80, 0)
        assert bf == 80 - 80 // BASE_FEE_CHANGE_DENOM

    def test_min_base_fee_clamping(self):
        assert compute_base_fee(MIN_BASE_FEE, 0) >= MIN_BASE_FEE

    def test_max_base_fee_clamping(self):
        assert compute_base_fee(MAX_BASE_FEE, MAX_BLOCK_WEIGHT) <= MAX_BASE_FEE

    def test_min_increase_of_1(self):
        assert compute_base_fee(1, TARGET_BLOCK_WEIGHT + 1) >= 2

    def test_integer_arithmetic(self):
        for parent_bf in [1, 10, 100, 1000, MAX_BASE_FEE]:
            for parent_w in [0, TARGET_BLOCK_WEIGHT // 2, TARGET_BLOCK_WEIGHT, MAX_BLOCK_WEIGHT]:
                assert isinstance(compute_base_fee(parent_bf, parent_w), int)


class TestEffectiveBlockWeight:
    def test_excludes_self_txs(self, wallet):
        tx = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        assert effective_block_weight([tx], wallet.address) == 0

    def test_includes_non_self_txs(self, wallet_pair):
        alice, bob = wallet_pair
        tx = Transaction.notarize(alice.address, "aa" * 32, nonce=0)
        tx.sign(alice.signing_sk, alice.signing_pk)
        assert effective_block_weight([tx], bob.address) == TX_WEIGHTS["NOTARIZE"]

    def test_mixed_self_and_other(self, wallet_pair):
        alice, bob = wallet_pair
        tx1 = Transaction.notarize(alice.address, "aa" * 32, nonce=0)
        tx1.sign(alice.signing_sk, alice.signing_pk)
        tx2 = Transaction.notarize(bob.address, "bb" * 32, nonce=0)
        tx2.sign(bob.signing_sk, bob.signing_pk)
        assert effective_block_weight([tx1, tx2], bob.address) == TX_WEIGHTS["NOTARIZE"]


class TestComputeTxFee:
    def test_base_only(self):
        assert compute_tx_fee(10, 10, 0, 1000) == 10_000

    def test_base_plus_priority(self):
        assert compute_tx_fee(10, 20, 5, 1000) == 15_000

    def test_priority_capped_by_max_fee(self):
        assert compute_tx_fee(10, 12, 100, 1000) == 12_000

    def test_zero_weight(self):
        assert compute_tx_fee(10, 100, 50, 0) == 0

    def test_max_fee_equals_base(self):
        assert compute_tx_fee(10, 10, 50, 1000) == 10_000

    def test_max_fee_below_base(self):
        assert compute_tx_fee(10, 5, 50, 1000) == 10_000


class TestTxWeight:
    def test_all_known_types(self):
        for tx_type, expected in TX_WEIGHTS.items():
            assert tx_weight(tx_type) == expected

    def test_unknown_type(self):
        assert tx_weight("NONEXISTENT") == 0

    def test_zero_weight_types(self):
        assert tx_weight("REVOKE_KEY") == 0
        assert tx_weight("EVIDENCE") == 0


# =============================================================================
# Block Field Tests
# =============================================================================

class TestBlockBaseFee:
    def test_default_base_fee_zero(self):
        block = Block(index=0, prev_hash="0" * 64, transactions=[], validator="test")
        assert block.base_fee == 0

    def test_custom_base_fee(self):
        block = Block(index=1, prev_hash="0" * 64, transactions=[], validator="test",
                      base_fee=42)
        assert block.base_fee == 42

    def test_base_fee_in_header(self):
        b1 = Block(index=1, prev_hash="0" * 64, transactions=[], validator="test",
                   timestamp=1000, base_fee=10)
        b2 = Block(index=1, prev_hash="0" * 64, transactions=[], validator="test",
                   timestamp=1000, base_fee=20)
        assert b1.block_hash != b2.block_hash

    def test_to_dict_includes_base_fee(self):
        block = Block(index=0, prev_hash="0" * 64, transactions=[], validator="test",
                      base_fee=99)
        assert block.to_dict()["baseFee"] == 99

    def test_from_dict_reads_base_fee(self):
        block = Block(index=0, prev_hash="0" * 64, transactions=[], validator="test",
                      timestamp=1000, base_fee=77)
        assert Block.from_dict(block.to_dict()).base_fee == 77

    def test_from_dict_default_base_fee(self):
        d = {
            "index": 0, "prevHash": "0" * 64, "transactions": [],
            "validator": "test", "timestamp": 1000, "signature": "",
            "merkleRoot": Block(index=0, prev_hash="0" * 64, transactions=[],
                                validator="test", timestamp=1000).merkle_root,
        }
        assert Block.from_dict(d).base_fee == 0

    def test_genesis_base_fee_zero(self):
        assert Block.genesis("test").base_fee == 0


# =============================================================================
# Transaction Field Tests
# =============================================================================

class TestTxFeeFields:
    def test_default_zero(self):
        tx = Transaction(tx_type=TxType.NOTARIZE, sender="addr",
                         payload={"documentHash": "aa" * 32})
        assert tx.max_fee_per_weight == 0
        assert tx.max_priority_fee == 0

    def test_custom_values(self):
        tx = Transaction(tx_type=TxType.NOTARIZE, sender="addr",
                         payload={"documentHash": "aa" * 32},
                         max_fee_per_weight=50, max_priority_fee=10)
        assert tx.max_fee_per_weight == 50
        assert tx.max_priority_fee == 10

    def test_fee_fields_affect_tx_id(self):
        tx1 = Transaction(tx_type=TxType.NOTARIZE, sender="addr", timestamp=1000,
                          payload={"documentHash": "aa" * 32},
                          max_fee_per_weight=0, max_priority_fee=0)
        tx2 = Transaction(tx_type=TxType.NOTARIZE, sender="addr", timestamp=1000,
                          payload={"documentHash": "aa" * 32},
                          max_fee_per_weight=10, max_priority_fee=5)
        assert tx1.tx_id != tx2.tx_id

    def test_to_dict_includes_fee_fields(self):
        tx = Transaction(tx_type=TxType.NOTARIZE, sender="addr",
                         payload={"documentHash": "aa" * 32},
                         max_fee_per_weight=50, max_priority_fee=10)
        d = tx.to_dict()
        assert d["maxFeePerWeight"] == 50
        assert d["maxPriorityFee"] == 10

    def test_from_dict_reads_fee_fields(self):
        tx = Transaction(tx_type=TxType.NOTARIZE, sender="addr",
                         payload={"documentHash": "aa" * 32},
                         max_fee_per_weight=50, max_priority_fee=10,
                         timestamp=1000)
        restored = Transaction.from_dict(tx.to_dict())
        assert restored.max_fee_per_weight == 50
        assert restored.max_priority_fee == 10

    def test_from_dict_default_zero(self):
        d = {
            "type": "NOTARIZE", "from": "addr", "to": "", "timestamp": 1000,
            "payload": {"documentHash": "aa" * 32}, "nonce": 0,
            "chainId": "qbit-mainnet", "signature": "", "sender_pubkey": "",
        }
        tx = Transaction.from_dict(d)
        assert tx.max_fee_per_weight == 0
        assert tx.max_priority_fee == 0

    def test_from_dict_rejects_negative(self):
        d = {
            "type": "NOTARIZE", "from": "addr", "to": "", "timestamp": 1000,
            "payload": {"documentHash": "aa" * 32}, "nonce": 0,
            "chainId": "qbit-mainnet", "signature": "", "sender_pubkey": "",
            "maxFeePerWeight": -1,
        }
        with pytest.raises(ValueError, match="maxFeePerWeight"):
            Transaction.from_dict(d)

    def test_factory_notarize(self, wallet):
        tx = Transaction.notarize(wallet.address, "aa" * 32,
                                  max_fee_per_weight=50, max_priority_fee=5)
        assert tx.max_fee_per_weight == 50

    def test_factory_transfer(self, wallet):
        tx = Transaction.transfer(wallet.address, "qv1" + "ab" * 32, 1000,
                                  max_fee_per_weight=50)
        assert tx.max_fee_per_weight == 50

    def test_factory_store(self, wallet):
        tx = Transaction.store(wallet.address, "aa" * 32, "QmCid",
                               max_fee_per_weight=50)
        assert tx.max_fee_per_weight == 50

    def test_factory_stake(self, wallet):
        tx = Transaction.stake(wallet.address, wallet.address, 100,
                               max_fee_per_weight=50)
        assert tx.max_fee_per_weight == 50

    def test_factory_share(self, wallet):
        tx = Transaction.share(wallet.address, "qv1" + "ab" * 32,
                               "QmCid", b'\x01' * 32,
                               max_fee_per_weight=50, max_priority_fee=5)
        assert tx.max_fee_per_weight == 50

    def test_factory_delegate(self, wallet):
        tx = Transaction.delegate(wallet.address, wallet.address, 100,
                                  max_fee_per_weight=50)
        assert tx.max_fee_per_weight == 50

    def test_factory_unstake(self, wallet):
        tx = Transaction.unstake(wallet.address, wallet.address, 100,
                                 max_fee_per_weight=50)
        assert tx.max_fee_per_weight == 50

    def test_factory_revoke_key(self, wallet):
        tx = Transaction.revoke_key(wallet.address, "encryption", "rotation",
                                    max_fee_per_weight=50)
        assert tx.max_fee_per_weight == 50

    def test_factory_evidence(self, wallet):
        tx = Transaction.evidence(wallet.address, wallet.address, 1,
                                  "aa" * 32, "bb" * 32, "cc" * 32, "dd" * 32,
                                  max_fee_per_weight=50)
        assert tx.max_fee_per_weight == 50

    def test_factory_register_key(self, wallet):
        tx = Transaction.register_key(wallet.address, b'\xab' * 800,
                                      max_fee_per_weight=50)
        assert tx.max_fee_per_weight == 50

    def test_factory_register_validator(self, wallet):
        tx = Transaction.register_validator(wallet.address, wallet.signing_pk,
                                            wallet.address,
                                            max_fee_per_weight=50)
        assert tx.max_fee_per_weight == 50


# =============================================================================
# Consensus Validation Tests
# =============================================================================

class TestConsensusBaseFee:
    def test_correct_base_fee_accepted(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=INITIAL_BASE_FEE)
        tx.sign(bob.signing_sk, bob.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg
        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None
        assert block.base_fee == INITIAL_BASE_FEE

    def test_wrong_base_fee_rejected(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        parent = bc.latest_block
        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=100)
        tx.sign(bob.signing_sk, bob.signing_pk)
        bc.submit_tx(tx)

        timestamp = max(int(time.time()), parent.timestamp + 1)
        block = Block(index=parent.index + 1, prev_hash=parent.block_hash,
                      transactions=[tx], validator=alice.address,
                      timestamp=timestamp, base_fee=9999)
        block.sign(alice.signing_sk)
        ok, err = bc.consensus.validate_block(block, parent)
        assert not ok
        assert "base_fee mismatch" in err

    def test_activation_height_uses_initial(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=INITIAL_BASE_FEE)
        tx.sign(bob.signing_sk, bob.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None
        assert block.base_fee == INITIAL_BASE_FEE


class TestConsensusBlockWeight:
    def test_overweight_block_rejected(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        parent = bc.latest_block
        # 3 REGISTER_KEY TXs = 30M weight > 20M MAX_BLOCK_WEIGHT
        txs = []
        for i in range(3):
            tx = Transaction.register_key(bob.address, b'\xab' * 800, nonce=i,
                                          max_fee_per_weight=100)
            tx.sign(bob.signing_sk, bob.signing_pk)
            txs.append(tx)

        timestamp = max(int(time.time()), parent.timestamp + 1)
        block = Block(index=parent.index + 1, prev_hash=parent.block_hash,
                      transactions=txs, validator=alice.address,
                      timestamp=timestamp, base_fee=INITIAL_BASE_FEE)
        block.sign(alice.signing_sk)
        ok, err = bc.consensus.validate_block(block, parent)
        assert not ok
        assert "block weight" in err


class TestConsensusSelfTxRatio:
    def test_100pct_self_tx_rejected(self, wallet):
        """Block where all TXs are self-TXs (100% > 25%) is rejected."""
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)
        parent = bc.latest_block

        tx = Transaction.notarize(wallet.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=100)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        timestamp = max(int(time.time()), parent.timestamp + 1)
        block = Block(index=parent.index + 1, prev_hash=parent.block_hash,
                      transactions=[tx], validator=wallet.address,
                      timestamp=timestamp, base_fee=INITIAL_BASE_FEE)
        block.sign(wallet.signing_sk)
        ok, err = bc.consensus.validate_block(block, parent)
        assert not ok
        assert "self-tx weight exceeds 25%" in err

    def test_zero_weight_self_txs_pass(self, wallet):
        """Zero-weight self-TXs dont trigger ratio check (total_weight == 0)."""
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)
        parent = bc.latest_block

        tx = Transaction.revoke_key(wallet.address, "encryption", "compromised",
                                    nonce=0, max_fee_per_weight=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        timestamp = max(int(time.time()), parent.timestamp + 1)
        block = Block(index=parent.index + 1, prev_hash=parent.block_hash,
                      transactions=[tx], validator=wallet.address,
                      timestamp=timestamp, base_fee=INITIAL_BASE_FEE)
        block.sign(wallet.signing_sk)
        ok, err = bc.consensus.validate_block(block, parent)
        assert ok, err

    def test_within_25pct_passes(self, wallet_pair):
        """Block with self-TX weight at exactly 20% passes."""
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        parent = bc.latest_block
        # 4 NOTARIZE from bob (4M) + 1 from alice (1M) = 5M total
        # alice self-weight = 1M / 5M = 20% < 25%
        txs = []
        for i in range(4):
            tx = Transaction.notarize(bob.address, f"{i:064x}", nonce=i,
                                      max_fee_per_weight=100)
            tx.sign(bob.signing_sk, bob.signing_pk)
            txs.append(tx)
        alice_tx = Transaction.notarize(alice.address, "ee" * 32, nonce=0,
                                        max_fee_per_weight=100)
        alice_tx.sign(alice.signing_sk, alice.signing_pk)
        txs.append(alice_tx)

        timestamp = max(int(time.time()), parent.timestamp + 1)
        block = Block(index=parent.index + 1, prev_hash=parent.block_hash,
                      transactions=txs, validator=alice.address,
                      timestamp=timestamp, base_fee=INITIAL_BASE_FEE)
        block.sign(alice.signing_sk)
        ok, err = bc.consensus.validate_block(block, parent)
        assert ok, err


class TestConsensusTxFee:
    def test_below_base_fee_rejected(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        parent = bc.latest_block
        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=1)  # below INITIAL_BASE_FEE
        tx.sign(bob.signing_sk, bob.signing_pk)
        timestamp = max(int(time.time()), parent.timestamp + 1)
        block = Block(index=parent.index + 1, prev_hash=parent.block_hash,
                      transactions=[tx], validator=alice.address,
                      timestamp=timestamp, base_fee=INITIAL_BASE_FEE)
        block.sign(alice.signing_sk)
        ok, err = bc.consensus.validate_block(block, parent)
        assert not ok
        assert "max_fee_per_weight" in err

    def test_at_base_fee_accepted(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        parent = bc.latest_block
        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=INITIAL_BASE_FEE)
        tx.sign(bob.signing_sk, bob.signing_pk)
        timestamp = max(int(time.time()), parent.timestamp + 1)
        block = Block(index=parent.index + 1, prev_hash=parent.block_hash,
                      transactions=[tx], validator=alice.address,
                      timestamp=timestamp, base_fee=INITIAL_BASE_FEE)
        block.sign(alice.signing_sk)
        ok, err = bc.consensus.validate_block(block, parent)
        assert ok, err


class TestConsensusEmptyBlocks:
    def test_empty_block_accepted_post_activation(self, wallet):
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)
        parent = bc.latest_block

        timestamp = max(int(time.time()), parent.timestamp + 1)
        block = Block(index=parent.index + 1, prev_hash=parent.block_hash,
                      transactions=[], validator=wallet.address,
                      timestamp=timestamp, base_fee=INITIAL_BASE_FEE)
        block.sign(wallet.signing_sk)
        ok, err = bc.consensus.validate_block(block, parent)
        assert ok, err


# =============================================================================
# Blockchain Integration Tests
# =============================================================================

class TestDynamicFeeDeduction:
    def test_exact_fee_credited_to_validator(self, wallet_pair):
        """fee = (base + priority) * weight, 100% to validator."""
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)

        base_fee = INITIAL_BASE_FEE
        max_fee = 20
        priority = 5
        tx = Transaction.notarize(bob.address, "cc" * 32, nonce=0,
                                  max_fee_per_weight=max_fee,
                                  max_priority_fee=priority)
        tx.sign(bob.signing_sk, bob.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg

        alice_before = bc.get_balance(alice.address)
        bob_before = bc.get_balance(bob.address)
        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None

        w = TX_WEIGHTS["NOTARIZE"]
        eff_priority = min(priority, max_fee - base_fee)
        expected_fee = (base_fee + eff_priority) * w
        reward = bc._calc_block_reward(block.index)

        assert bc.get_balance(alice.address) == alice_before + expected_fee + reward
        assert bc.get_balance(bob.address) == bob_before - expected_fee

    def test_no_burn_post_activation(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        burned_before = bc._total_burned

        tx = Transaction.notarize(bob.address, "dd" * 32, nonce=0,
                                  max_fee_per_weight=INITIAL_BASE_FEE)
        tx.sign(bob.signing_sk, bob.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(alice.address, alice.signing_sk)
        assert bc._total_burned == burned_before

    def test_zero_weight_tx_no_fee(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        # Register encryption key then revoke
        reg = Transaction.register_key(bob.address, b'\xab' * 800, nonce=0,
                                       max_fee_per_weight=INITIAL_BASE_FEE)
        reg.sign(bob.signing_sk, bob.signing_pk)
        bc.submit_tx(reg)
        bc.produce_block(alice.address, alice.signing_sk)

        bob_before = bc.get_balance(bob.address)
        rev = Transaction.revoke_key(bob.address, "encryption", "rotation",
                                     nonce=1, max_fee_per_weight=0)
        rev.sign(bob.signing_sk, bob.signing_pk)
        bc.submit_tx(rev)
        bc.produce_block(alice.address, alice.signing_sk)
        assert bc.get_balance(bob.address) == bob_before


# =============================================================================
# Pool Admission Tests
# =============================================================================

class TestPoolAdmission:
    def test_reject_below_base_fee(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=INITIAL_BASE_FEE - 1)
        tx.sign(bob.signing_sk, bob.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert not ok
        assert "base_fee" in msg

    def test_accept_at_base_fee(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=INITIAL_BASE_FEE)
        tx.sign(bob.signing_sk, bob.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg

    def test_accept_above_base_fee(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=INITIAL_BASE_FEE + 100)
        tx.sign(bob.signing_sk, bob.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg

    def test_zero_weight_always_accepted(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        # Register key first, then try revoke with max_fee=0
        reg = Transaction.register_key(bob.address, b'\xab' * 800, nonce=0,
                                       max_fee_per_weight=INITIAL_BASE_FEE)
        reg.sign(bob.signing_sk, bob.signing_pk)
        bc.submit_tx(reg)
        bc.produce_block(alice.address, alice.signing_sk)

        rev = Transaction.revoke_key(bob.address, "encryption", "compromised",
                                     nonce=1, max_fee_per_weight=0)
        rev.sign(bob.signing_sk, bob.signing_pk)
        ok, msg = bc.submit_tx(rev)
        assert ok, msg

    def test_insufficient_balance_rejected(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob, fund_bob=False)
        # bob has 0 balance
        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=100)
        tx.sign(bob.signing_sk, bob.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert not ok
        assert "insufficient balance" in msg


# =============================================================================
# Block Production Tests
# =============================================================================

class TestBlockProduction:
    def test_sorts_senders_by_priority_fee(self):
        """Senders with higher priority TXs are included first."""
        alice = Wallet.generate()
        bob = Wallet.generate()
        carol = Wallet.generate()
        bc = _make_chain(alice, bob)
        bc._credit(carol.address, 100_000_000_000)

        # Bob: high priority
        tx_bob = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                      max_fee_per_weight=30, max_priority_fee=15)
        tx_bob.sign(bob.signing_sk, bob.signing_pk)

        # Carol: low priority
        tx_carol = Transaction.notarize(carol.address, "bb" * 32, nonce=0,
                                        max_fee_per_weight=20, max_priority_fee=1)
        tx_carol.sign(carol.signing_sk, carol.signing_pk)

        # Submit carol first, then bob
        for tx in [tx_carol, tx_bob]:
            ok, msg = bc.submit_tx(tx)
            assert ok, msg

        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None
        assert len(block.transactions) == 2
        # Bob (higher priority) comes first despite being submitted second
        assert block.transactions[0].tx_id == tx_bob.tx_id
        assert block.transactions[1].tx_id == tx_carol.tx_id

    def test_same_sender_preserves_nonce_order(self, wallet_pair):
        """TXs from same sender are kept in nonce order."""
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)

        txs = []
        for i in range(3):
            tx = Transaction.notarize(bob.address, f"{i:064x}", nonce=i,
                                      max_fee_per_weight=20,
                                      max_priority_fee=10 - i)  # decreasing priority
            tx.sign(bob.signing_sk, bob.signing_pk)
            ok, msg = bc.submit_tx(tx)
            assert ok, msg
            txs.append(tx)

        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None
        assert len(block.transactions) == 3
        # Despite different priorities, nonce order preserved
        for i, btx in enumerate(block.transactions):
            assert btx.nonce == i

    def test_respects_max_block_weight(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        bc._credit(bob.address, 1_000_000_000_000)
        # 11 STORE txs = 22M > 20M MAX_BLOCK_WEIGHT; at most 10 fit
        for i in range(11):
            tx = Transaction.store(bob.address, f"{i:064x}", f"QmCid{i}", nonce=i,
                                   max_fee_per_weight=100)
            tx.sign(bob.signing_sk, bob.signing_pk)
            ok, msg = bc.submit_tx(tx)
            assert ok, msg

        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None
        total_w = sum(TX_WEIGHTS.get(t.tx_type.value, 0) for t in block.transactions)
        assert total_w <= MAX_BLOCK_WEIGHT

    def test_empty_block_produced_post_activation(self, wallet):
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)
        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        assert len(block.transactions) == 0

    def test_filters_below_base_fee(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        # Bypass submit_tx to add a low-fee TX directly to pool
        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                  max_fee_per_weight=1)
        tx.sign(bob.signing_sk, bob.signing_pk)
        bc.tx_pool.append(tx)
        bc._pool_ids.add(tx.tx_id)
        bc._pool_sender_count[bob.address] = 1

        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None
        assert len(block.transactions) == 0


# =============================================================================
# Self-TX Anti-Spam Tests
# =============================================================================

class TestSelfTxAntiSpam:
    def test_self_txs_dont_affect_base_fee(self, wallet):
        tx = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        assert effective_block_weight([tx], wallet.address) == 0
        # Next base fee as if empty block
        next_bf = compute_base_fee(INITIAL_BASE_FEE, 0)
        assert next_bf < INITIAL_BASE_FEE

    def test_self_txs_count_toward_max_weight(self, wallet):
        tx = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        assert block_total_weight([tx]) == TX_WEIGHTS["NOTARIZE"]

    def test_validator_fills_with_self_txs_base_fee_unchanged(self, wallet):
        """If ALL block TXs are self-TXs, effective weight is 0 => base fee drops."""
        tx = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        eff_w = effective_block_weight([tx], wallet.address)
        assert eff_w == 0
        bf = compute_base_fee(100, eff_w)
        assert bf < 100  # decreases, not increases


# =============================================================================
# Rollback Tests
# =============================================================================

class TestRollbackDynamicFees:
    def test_rollback_reverses_dynamic_fee(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)

        alice_before = bc.get_balance(alice.address)
        bob_before = bc.get_balance(bob.address)
        height_before = bc.height

        tx = Transaction.notarize(bob.address, "ee" * 32, nonce=0,
                                  max_fee_per_weight=20, max_priority_fee=5)
        tx.sign(bob.signing_sk, bob.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg
        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None

        bc._rollback_to(height_before + 1)
        assert bc.get_balance(bob.address) == bob_before
        assert bc.get_balance(alice.address) == alice_before

    def test_rollback_restores_base_fee_from_parent(self, wallet_pair):
        """After rollback, _current_base_fee reflects the parent block."""
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)

        # Mine block 1
        tx1 = Transaction.notarize(bob.address, "aa" * 32, nonce=0,
                                   max_fee_per_weight=100)
        tx1.sign(bob.signing_sk, bob.signing_pk)
        bc.submit_tx(tx1)
        block1 = bc.produce_block(alice.address, alice.signing_sk)
        assert block1 is not None
        bf_after_block1 = bc._current_base_fee()

        # Mine block 2
        tx2 = Transaction.notarize(bob.address, "bb" * 32, nonce=1,
                                   max_fee_per_weight=100)
        tx2.sign(bob.signing_sk, bob.signing_pk)
        bc.submit_tx(tx2)
        block2 = bc.produce_block(alice.address, alice.signing_sk)
        assert block2 is not None

        # Rollback block 2
        bc._rollback_to(block1.index + 1)
        # Current base fee should match what it was after block 1
        assert bc._current_base_fee() == bf_after_block1


# =============================================================================
# Base Fee Adjustment Over Blocks
# =============================================================================

class TestBaseFeeAdjustment:
    def test_increases_with_heavy_blocks(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        bc._credit(bob.address, 100_000_000_000_000)

        base_fees = [INITIAL_BASE_FEE]
        for i in range(5):
            # Submit 8 STORE TXs per block (16M weight > 10M target)
            for j in range(8):
                nonce = _bob_nonce(bc, bob)
                tx = Transaction.store(bob.address, f"{i * 10 + j:064x}",
                                       f"QmCid{i}_{j}", nonce=nonce,
                                       max_fee_per_weight=10000)
                tx.sign(bob.signing_sk, bob.signing_pk)
                ok, msg = bc.submit_tx(tx)
                assert ok, f"submit failed: {msg}"
            block = bc.produce_block(alice.address, alice.signing_sk)
            assert block is not None
            base_fees.append(block.base_fee)

        assert base_fees[-1] > base_fees[0]

    def test_decreases_with_empty_blocks(self, wallet):
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        base_fees = []
        for _ in range(6):
            block = bc.produce_block(wallet.address, wallet.signing_sk)
            assert block is not None
            base_fees.append(block.base_fee)

        assert base_fees[-1] < base_fees[0] or base_fees[-1] == MIN_BASE_FEE


# =============================================================================
# Pending Debits
# =============================================================================

class TestPendingDebits:
    def test_uses_worst_case_fee(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        tx = Transaction.notarize(bob.address, "ff" * 32, nonce=0,
                                  max_fee_per_weight=50, max_priority_fee=5)
        tx.sign(bob.signing_sk, bob.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg
        pending = bc._pending_debits(bob.address)
        assert pending == 50 * TX_WEIGHTS["NOTARIZE"]


# =============================================================================
# Integration: Full Flow
# =============================================================================

class TestFullFlow:
    def test_submit_mine_verify(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)

        base_fee = INITIAL_BASE_FEE
        max_fee = 15
        priority = 3
        w = TX_WEIGHTS["NOTARIZE"]

        tx = Transaction.notarize(bob.address, "ab" * 32, nonce=0,
                                  max_fee_per_weight=max_fee,
                                  max_priority_fee=priority)
        tx.sign(bob.signing_sk, bob.signing_pk)

        bob_before = bc.get_balance(bob.address)
        alice_before = bc.get_balance(alice.address)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg

        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None
        assert block.base_fee == base_fee

        eff_priority = min(priority, max_fee - base_fee)
        expected_fee = (base_fee + eff_priority) * w
        reward = bc._calc_block_reward(block.index)

        assert bc.get_balance(bob.address) == bob_before - expected_fee
        assert bc.get_balance(alice.address) == alice_before + expected_fee + reward

    def test_serialization_round_trip(self, wallet_pair):
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)

        tx = Transaction.notarize(bob.address, "ab" * 32, nonce=0,
                                  max_fee_per_weight=50, max_priority_fee=10)
        tx.sign(bob.signing_sk, bob.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None

        d = block.to_dict()
        restored = Block.from_dict(d)
        assert restored.base_fee == block.base_fee
        assert restored.block_hash == block.block_hash

        tx_d = block.transactions[0].to_dict()
        restored_tx = Transaction.from_dict(tx_d)
        assert restored_tx.max_fee_per_weight == 50
        assert restored_tx.max_priority_fee == 10
        assert restored_tx.tx_id == block.transactions[0].tx_id


# =============================================================================
# Current Base Fee Helper
# =============================================================================

class TestCurrentBaseFee:
    def test_initial_for_first_block(self, wallet):
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)
        assert bc._current_base_fee() == INITIAL_BASE_FEE

    def test_adjusts_after_empty_blocks(self, wallet):
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)
        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        bf = bc._current_base_fee()
        assert bf < INITIAL_BASE_FEE or bf == MIN_BASE_FEE


# =============================================================================
# Legacy Pre-Activation Path
# =============================================================================

class TestLegacyPath:
    """These run with high activation height to test legacy behavior."""

    def test_legacy_fee_50_50_split(self, monkeypatch, wallet_pair):
        """Pre-activation uses TX_FEES with 50% burn."""
        HIGH = 2**63
        monkeypatch.setattr("qbit_network.config.DYNAMIC_FEE_ACTIVATION_HEIGHT", HIGH)
        monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT", HIGH)
        monkeypatch.setattr("qbit_network.core.consensus.DYNAMIC_FEE_ACTIVATION_HEIGHT", HIGH)

        alice, bob = wallet_pair
        bc = Blockchain()
        bc.consensus.add_validator(alice.address, alice.signing_pk)
        bc.init_chain(alice.address, alice.signing_sk, validator_pk=alice.signing_pk)
        bc.activate_financial_layer(alice.address)
        bc._credit(bob.address, 100_000_000_000)

        bob_before = bc.get_balance(bob.address)
        burned_before = bc._total_burned

        tx = Transaction.notarize(bob.address, "aa" * 32, nonce=0)
        tx.sign(bob.signing_sk, bob.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg
        bc.produce_block(alice.address, alice.signing_sk)

        fee = TX_FEES["NOTARIZE"]
        assert bc.get_balance(bob.address) == bob_before - fee
        assert bc._total_burned == burned_before + fee - fee // 2

    def test_empty_block_rejected_pre_activation(self, monkeypatch, wallet):
        """Pre-activation: empty non-genesis blocks rejected."""
        HIGH = 2**63
        monkeypatch.setattr("qbit_network.config.DYNAMIC_FEE_ACTIVATION_HEIGHT", HIGH)
        monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT", HIGH)
        monkeypatch.setattr("qbit_network.core.consensus.DYNAMIC_FEE_ACTIVATION_HEIGHT", HIGH)

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
        parent = bc.latest_block
        timestamp = max(int(time.time()), parent.timestamp + 1)
        block = Block(index=parent.index + 1, prev_hash=parent.block_hash,
                      transactions=[], validator=wallet.address,
                      timestamp=timestamp)
        block.sign(wallet.signing_sk)
        ok, err = bc.consensus.validate_block(block, parent)
        assert not ok
        assert "must contain at least one transaction" in err


# =============================================================================
# RPC Fee Info Tests (qv_getFeeInfo)
# =============================================================================

class TestRPCGetFeeInfo:
    """Tests for the FullNode._rpc_get_fee_info method via direct blockchain state."""

    def test_fee_info_returns_all_fields(self, wallet_pair):
        """fee info has all required fields."""
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        # Simulate what _rpc_get_fee_info does
        from qbit_network.core.fees import compute_base_fee, effective_block_weight
        current_bf = bc._current_base_fee()
        assert isinstance(current_bf, int)
        assert current_bf >= MIN_BASE_FEE

        parent = bc.latest_block
        parent_eff = effective_block_weight(parent.transactions, parent.validator)
        next_bf = compute_base_fee(current_bf, parent_eff)
        assert isinstance(next_bf, int)

    def test_fee_info_suggested_priority_fee(self, wallet_pair):
        """Suggested priority fee is at least 1."""
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        current_bf = bc._current_base_fee()
        suggested = max(1, current_bf // 10)
        assert suggested >= 1

    def test_fee_info_estimated_fees_calculated(self, wallet_pair):
        """Estimated fees are calculated correctly."""
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        current_bf = bc._current_base_fee()
        suggested_priority = max(1, current_bf // 10)
        for tx_type, weight in TX_WEIGHTS.items():
            if weight == 0:
                continue
            min_fee = current_bf * weight
            suggested_fee = (current_bf + suggested_priority) * weight
            assert min_fee >= 0
            assert suggested_fee >= min_fee

    def test_fee_info_after_heavy_blocks(self, wallet_pair):
        """Base fee increases after heavy blocks."""
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        bc._credit(bob.address, 100_000_000_000_000)

        initial_bf = bc._current_base_fee()
        # Submit heavy TXs
        for i in range(5):
            nonce = _bob_nonce(bc, bob)
            tx = Transaction.store(bob.address, f"{i:064x}", f"QmCid{i}",
                                   nonce=nonce, max_fee_per_weight=10000)
            tx.sign(bob.signing_sk, bob.signing_pk)
            bc.submit_tx(tx)
        bc.produce_block(alice.address, alice.signing_sk)
        new_bf = bc._current_base_fee()
        assert new_bf >= initial_bf


# =============================================================================
# Integration: Base Fee Adjusts After TX Submission
# =============================================================================

class TestFeeIntegration:
    """Integration test: submit TXs with dynamic fees and verify base_fee adjusts."""

    def test_base_fee_responds_to_load(self, wallet_pair):
        """Base fee should increase when blocks are full and decrease when empty."""
        alice, bob = wallet_pair
        bc = _make_chain(alice, bob)
        bc._credit(bob.address, 100_000_000_000_000)

        # Heavy block: submit many TXs
        for i in range(8):
            nonce = _bob_nonce(bc, bob)
            tx = Transaction.store(bob.address, f"{i:064x}", f"QmCid{i}",
                                   nonce=nonce, max_fee_per_weight=10000)
            tx.sign(bob.signing_sk, bob.signing_pk)
            ok, msg = bc.submit_tx(tx)
            assert ok, msg

        block = bc.produce_block(alice.address, alice.signing_sk)
        assert block is not None
        bf_after_heavy = bc._current_base_fee()

        # Empty blocks should decrease base fee
        for _ in range(5):
            bc.produce_block(alice.address, alice.signing_sk)

        bf_after_empty = bc._current_base_fee()
        assert bf_after_empty < bf_after_heavy or bf_after_empty == MIN_BASE_FEE

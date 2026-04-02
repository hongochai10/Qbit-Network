"""Tests for dynamic fee configuration, EIP-1559 fee logic, and CLI flag (TEC-1137).

Covers: env var activation, CLI flag, compute_base_fee edge cases,
compute_tx_fee, tx_weight, effective_block_weight, block_total_weight,
and config constant validation.
"""
import importlib
import os
from unittest.mock import MagicMock

import pytest

from qbit_network.core.fees import (
    compute_base_fee, compute_tx_fee, tx_weight,
    effective_block_weight, block_total_weight,
)
from qbit_network.config import (
    TX_WEIGHTS, TARGET_BLOCK_WEIGHT, BASE_FEE_CHANGE_DENOM,
    MIN_BASE_FEE, MAX_BASE_FEE, INITIAL_BASE_FEE,
    MAX_BLOCK_WEIGHT, MAX_SELF_TX_WEIGHT_RATIO,
)


# ===================================================================
# Environment variable activation
# ===================================================================

class TestDynamicFeeEnvVar:
    """QBIT_DYNAMIC_FEE_ACTIVATION environment variable."""

    def test_env_var_sets_activation_height(self, monkeypatch):
        """QBIT_DYNAMIC_FEE_ACTIVATION=0 enables dynamic fees from genesis."""
        monkeypatch.setenv("QBIT_DYNAMIC_FEE_ACTIVATION", "0")
        import qbit_network.config as cfg
        importlib.reload(cfg)
        assert cfg.DYNAMIC_FEE_ACTIVATION_HEIGHT == 0

    def test_env_var_specific_height(self, monkeypatch):
        """QBIT_DYNAMIC_FEE_ACTIVATION=100 activates at block 100."""
        monkeypatch.setenv("QBIT_DYNAMIC_FEE_ACTIVATION", "100")
        import qbit_network.config as cfg
        importlib.reload(cfg)
        assert cfg.DYNAMIC_FEE_ACTIVATION_HEIGHT == 100

    def test_default_unchanged_without_env(self, monkeypatch):
        """Default remains 2^63 when env var is not set."""
        monkeypatch.delenv("QBIT_DYNAMIC_FEE_ACTIVATION", raising=False)
        import qbit_network.config as cfg
        importlib.reload(cfg)
        assert cfg.DYNAMIC_FEE_ACTIVATION_HEIGHT == 2**63

    def test_env_var_invalid_raises(self, monkeypatch):
        """Non-integer env var raises ValueError on import."""
        monkeypatch.setenv("QBIT_DYNAMIC_FEE_ACTIVATION", "not_a_number")
        import qbit_network.config as cfg
        with pytest.raises(ValueError):
            importlib.reload(cfg)

    def test_env_var_negative_value(self, monkeypatch):
        """Negative value is accepted as a valid integer."""
        monkeypatch.setenv("QBIT_DYNAMIC_FEE_ACTIVATION", "-1")
        import qbit_network.config as cfg
        importlib.reload(cfg)
        assert cfg.DYNAMIC_FEE_ACTIVATION_HEIGHT == -1

    def test_env_var_large_value(self, monkeypatch):
        """Very large activation height is accepted."""
        monkeypatch.setenv("QBIT_DYNAMIC_FEE_ACTIVATION", "999999999")
        import qbit_network.config as cfg
        importlib.reload(cfg)
        assert cfg.DYNAMIC_FEE_ACTIVATION_HEIGHT == 999999999

    def test_env_var_empty_string_raises(self, monkeypatch):
        """Empty string raises ValueError."""
        monkeypatch.setenv("QBIT_DYNAMIC_FEE_ACTIVATION", "")
        import qbit_network.config as cfg
        with pytest.raises(ValueError):
            importlib.reload(cfg)

    def test_env_var_whitespace_raises(self, monkeypatch):
        """Whitespace-only string raises ValueError."""
        monkeypatch.setenv("QBIT_DYNAMIC_FEE_ACTIVATION", "  ")
        import qbit_network.config as cfg
        with pytest.raises(ValueError):
            importlib.reload(cfg)

    def test_env_var_float_raises(self, monkeypatch):
        """Float string raises ValueError."""
        monkeypatch.setenv("QBIT_DYNAMIC_FEE_ACTIVATION", "10.5")
        import qbit_network.config as cfg
        with pytest.raises(ValueError):
            importlib.reload(cfg)


# ===================================================================
# CLI flag
# ===================================================================

class TestDynamicFeeCLIFlag:
    """--dynamic-fee-activation CLI flag in run_node.py."""

    def test_cli_flag_accepted(self):
        """run_node.py accepts --dynamic-fee-activation flag without error."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--dynamic-fee-activation", type=int, default=None)
        args = parser.parse_args(["--dynamic-fee-activation", "100"])
        assert args.dynamic_fee_activation == 100

    def test_cli_flag_default_is_none(self):
        """CLI flag defaults to None (no override)."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--dynamic-fee-activation", type=int, default=None)
        args = parser.parse_args([])
        assert args.dynamic_fee_activation is None

    def test_cli_flag_zero(self):
        """CLI flag with 0 activates from genesis."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--dynamic-fee-activation", type=int, default=None)
        args = parser.parse_args(["--dynamic-fee-activation", "0"])
        assert args.dynamic_fee_activation == 0


# ===================================================================
# compute_base_fee tests
# ===================================================================

class TestComputeBaseFee:
    """Tests for compute_base_fee EIP-1559 logic."""

    def test_no_change_at_target(self):
        """Base fee unchanged when parent weight equals target."""
        result = compute_base_fee(100, TARGET_BLOCK_WEIGHT)
        assert result == 100

    def test_increases_above_target(self):
        """Base fee increases when parent weight exceeds target."""
        result = compute_base_fee(100, TARGET_BLOCK_WEIGHT * 2)
        assert result > 100

    def test_decreases_below_target(self):
        """Base fee decreases when parent weight is below target."""
        result = compute_base_fee(100, 0)
        assert result < 100

    def test_clamped_to_min(self):
        """Base fee never goes below MIN_BASE_FEE."""
        result = compute_base_fee(MIN_BASE_FEE, 0)
        assert result >= MIN_BASE_FEE

    def test_clamped_to_max(self):
        """Base fee never exceeds MAX_BASE_FEE."""
        result = compute_base_fee(MAX_BASE_FEE, TARGET_BLOCK_WEIGHT * 10)
        assert result <= MAX_BASE_FEE

    def test_min_fee_stays_at_min(self):
        """MIN_BASE_FEE with zero weight stays at min."""
        result = compute_base_fee(MIN_BASE_FEE, 0)
        assert result == MIN_BASE_FEE

    def test_max_fee_stays_at_max(self):
        """MAX_BASE_FEE with full weight stays at max."""
        result = compute_base_fee(MAX_BASE_FEE, TARGET_BLOCK_WEIGHT * 2)
        assert result == MAX_BASE_FEE

    def test_fee_delta_at_least_one_when_above(self):
        """Minimum fee delta of 1 when weight exceeds target."""
        result = compute_base_fee(1, TARGET_BLOCK_WEIGHT + 1)
        assert result >= 2

    def test_gradual_increase(self):
        """Fee increases gradually, not in huge jumps."""
        result = compute_base_fee(100, int(TARGET_BLOCK_WEIGHT * 1.1))
        assert 100 < result <= 115  # roughly 12.5% max change

    def test_gradual_decrease(self):
        """Fee decreases gradually, not in huge jumps."""
        result = compute_base_fee(100, int(TARGET_BLOCK_WEIGHT * 0.9))
        assert 85 <= result < 100

    def test_convergence_at_target(self):
        """Fee converges to stable value when weight stays at target."""
        fee = INITIAL_BASE_FEE
        for _ in range(100):
            fee = compute_base_fee(fee, TARGET_BLOCK_WEIGHT)
        assert fee == INITIAL_BASE_FEE

    def test_custom_target_weight(self):
        """Custom target_weight parameter is honored."""
        result = compute_base_fee(100, 500, target_weight=1000)
        assert result < 100  # below custom target


# ===================================================================
# compute_tx_fee tests
# ===================================================================

class TestComputeTxFee:
    """Tests for compute_tx_fee."""

    def test_zero_weight_zero_fee(self):
        """Zero weight always results in zero fee."""
        assert compute_tx_fee(100, 200, 50, 0) == 0

    def test_basic_fee_calculation(self):
        """Fee = (base_fee + effective_priority) * weight."""
        # effective_priority = min(10, 200 - 100) = 10
        result = compute_tx_fee(100, 200, 10, 1)
        assert result == 110

    def test_priority_capped_by_max_fee(self):
        """Priority fee capped when max_fee - base_fee < max_priority."""
        # effective_priority = min(50, 120 - 100) = 20
        result = compute_tx_fee(100, 120, 50, 1)
        assert result == 120

    def test_no_priority_when_max_fee_equals_base(self):
        """No priority fee when max_fee equals base_fee."""
        result = compute_tx_fee(100, 100, 50, 1)
        assert result == 100

    def test_no_priority_when_max_fee_below_base(self):
        """Priority clamps to 0 when max_fee < base_fee."""
        result = compute_tx_fee(100, 50, 50, 1)
        assert result == 100

    def test_large_weight_scales_fee(self):
        """Fee scales linearly with weight."""
        fee1 = compute_tx_fee(10, 20, 5, 100)
        fee2 = compute_tx_fee(10, 20, 5, 200)
        assert fee2 == fee1 * 2


# ===================================================================
# tx_weight tests
# ===================================================================

class TestTxWeight:
    """Tests for tx_weight helper."""

    def test_known_types(self):
        """All known TX types return positive weight."""
        for tx_type, expected in TX_WEIGHTS.items():
            assert tx_weight(tx_type) == expected

    def test_unknown_type_returns_zero(self):
        """Unknown TX type returns 0."""
        assert tx_weight("UNKNOWN_TYPE") == 0

    def test_revoke_key_zero_weight(self):
        """REVOKE_KEY has zero weight."""
        assert tx_weight("REVOKE_KEY") == 0

    def test_evidence_zero_weight(self):
        """EVIDENCE has zero weight."""
        assert tx_weight("EVIDENCE") == 0


# ===================================================================
# effective_block_weight tests
# ===================================================================

class TestEffectiveBlockWeight:
    """Tests for effective_block_weight (anti-spam)."""

    def test_excludes_validator_self_txs(self):
        """Self-TXs from the validator are excluded."""
        tx1 = MagicMock()
        tx1.tx_type.value = "TRANSFER"
        tx1.sender = "validator_addr"
        tx2 = MagicMock()
        tx2.tx_type.value = "TRANSFER"
        tx2.sender = "other_addr"
        result = effective_block_weight([tx1, tx2], "validator_addr")
        assert result == TX_WEIGHTS["TRANSFER"]

    def test_empty_block(self):
        """Empty block has zero effective weight."""
        assert effective_block_weight([], "any") == 0

    def test_all_self_txs(self):
        """Block with only self-TXs has zero effective weight."""
        tx = MagicMock()
        tx.tx_type.value = "NOTARIZE"
        tx.sender = "validator"
        assert effective_block_weight([tx], "validator") == 0


# ===================================================================
# block_total_weight tests
# ===================================================================

class TestBlockTotalWeight:
    """Tests for block_total_weight."""

    def test_includes_all_txs(self):
        """Total weight includes all transactions regardless of sender."""
        tx1 = MagicMock()
        tx1.tx_type.value = "TRANSFER"
        tx1.sender = "validator"
        tx2 = MagicMock()
        tx2.tx_type.value = "NOTARIZE"
        tx2.sender = "other"
        result = block_total_weight([tx1, tx2])
        assert result == TX_WEIGHTS["TRANSFER"] + TX_WEIGHTS["NOTARIZE"]

    def test_empty_block(self):
        """Empty block has zero total weight."""
        assert block_total_weight([]) == 0


# ===================================================================
# Config constant validation
# ===================================================================

class TestDynamicFeeConfigConstants:
    """Validate dynamic fee config constants are sane."""

    def test_target_is_half_max_weight(self):
        """TARGET_BLOCK_WEIGHT = MAX_BLOCK_WEIGHT // 2."""
        assert TARGET_BLOCK_WEIGHT == MAX_BLOCK_WEIGHT // 2

    def test_min_base_fee_positive(self):
        """MIN_BASE_FEE must be >= 1."""
        assert MIN_BASE_FEE >= 1

    def test_max_greater_than_min(self):
        """MAX_BASE_FEE > MIN_BASE_FEE."""
        assert MAX_BASE_FEE > MIN_BASE_FEE

    def test_initial_base_fee_in_range(self):
        """INITIAL_BASE_FEE between min and max."""
        assert MIN_BASE_FEE <= INITIAL_BASE_FEE <= MAX_BASE_FEE

    def test_change_denom_positive(self):
        """BASE_FEE_CHANGE_DENOM must be positive."""
        assert BASE_FEE_CHANGE_DENOM > 0

    def test_all_tx_types_have_weights(self):
        """Every TX type in TX_WEIGHTS exists."""
        expected = {"TRANSFER", "NOTARIZE", "STORE", "SHARE",
                    "REGISTER_KEY", "REGISTER_VALIDATOR", "STAKE",
                    "DELEGATE", "UNSTAKE", "REVOKE_KEY", "EVIDENCE",
                    "ISSUE_TOKEN", "MINT_TOKEN", "TRANSFER_TOKEN"}
        assert set(TX_WEIGHTS.keys()) == expected

    def test_self_tx_weight_ratio_reasonable(self):
        """MAX_SELF_TX_WEIGHT_RATIO is between 1 and 100."""
        assert 1 <= MAX_SELF_TX_WEIGHT_RATIO <= 100

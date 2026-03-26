"""EIP-1559 dynamic fee calculation for QBit Network.

Provides base fee adjustment, transaction fee computation,
and block weight calculations with anti-spam protections.
"""
from ..config import (TX_WEIGHTS, TARGET_BLOCK_WEIGHT, BASE_FEE_CHANGE_DENOM,
                      MIN_BASE_FEE, MAX_BASE_FEE)


def tx_weight(tx_type: str) -> int:
    """Return the weight for a given transaction type."""
    return TX_WEIGHTS.get(tx_type, 0)


def effective_block_weight(transactions, validator_address: str) -> int:
    """Block weight EXCLUDING validator self-TXs (anti-spam).

    Self-TXs are excluded from the effective weight used for base fee
    calculation so that a validator cannot artificially inflate the
    base fee by filling blocks with their own transactions.
    """
    return sum(TX_WEIGHTS.get(tx.tx_type.value, 0)
               for tx in transactions if tx.sender != validator_address)


def block_total_weight(transactions) -> int:
    """Total block weight including all transactions (for MAX_BLOCK_WEIGHT check)."""
    return sum(TX_WEIGHTS.get(tx.tx_type.value, 0)
               for tx in transactions)


def compute_base_fee(parent_base_fee: int, parent_effective_weight: int,
                     target_weight: int = TARGET_BLOCK_WEIGHT) -> int:
    """Compute the base fee for the next block given parent block state.

    Adjusts by up to +/-12.5% (1/BASE_FEE_CHANGE_DENOM) per block,
    clamped to [MIN_BASE_FEE, MAX_BASE_FEE].
    """
    if parent_effective_weight == target_weight:
        new_fee = parent_base_fee
    elif parent_effective_weight > target_weight:
        delta = parent_effective_weight - target_weight
        fee_delta = max(1, parent_base_fee * delta // target_weight // BASE_FEE_CHANGE_DENOM)
        new_fee = parent_base_fee + fee_delta
    else:
        delta = target_weight - parent_effective_weight
        fee_delta = parent_base_fee * delta // target_weight // BASE_FEE_CHANGE_DENOM
        new_fee = parent_base_fee - fee_delta
    return max(MIN_BASE_FEE, min(new_fee, MAX_BASE_FEE))


def compute_tx_fee(base_fee: int, max_fee_per_weight: int,
                   max_priority_fee: int, weight: int) -> int:
    """Compute the actual fee for a transaction.

    fee = (base_fee + effective_priority) * weight
    effective_priority = min(max_priority_fee, max_fee_per_weight - base_fee)
    """
    if weight == 0:
        return 0
    effective_priority = max(0, min(max_priority_fee, max_fee_per_weight - base_fee))
    return (base_fee + effective_priority) * weight

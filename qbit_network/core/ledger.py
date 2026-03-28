"""Balance ledger and financial operations for the Blockchain.

Extracted as a mixin to reduce blockchain.py size while preserving
all method signatures and self.* access patterns.
"""
import logging
from .. import config as _config
from ..config import (TX_FEES, MAX_SUPPLY, INITIAL_BLOCK_REWARD,
                      HALVING_INTERVAL, GENESIS_BALANCE_QBIT, QUBIT_PER_QBIT)
from .fees import tx_weight
from .transaction import TxType

logger = logging.getLogger("qbit_network.chain")


class BalanceLedgerMixin:
    """Balance and financial operations mixed into Blockchain."""

    def _credit(self, address: str, amount: int):
        """Credit amount to address. Amount must be >= 0."""
        if amount < 0:
            raise ValueError("credit amount must be non-negative")
        if amount == 0:
            return
        self._balances[address] = self._balances.get(address, 0) + amount

    def _debit(self, address: str, amount: int):
        """Debit amount from address. Raises ValueError if insufficient."""
        if amount < 0:
            raise ValueError("debit amount must be non-negative")
        if amount == 0:
            return
        current = self._balances.get(address, 0)
        if current < amount:
            raise ValueError(f"insufficient balance: {current} < {amount}")
        new_balance = current - amount
        if new_balance == 0:
            self._balances.pop(address, None)
        else:
            self._balances[address] = new_balance

    def get_balance(self, address: str) -> int:
        """Return balance in qubits for an address."""
        return self._balances.get(address, 0)

    def get_total_supply(self) -> dict:
        """Return supply statistics."""
        total_staked = sum(self._total_stake.values())
        return {
            "total_minted": self._total_minted,
            "total_burned": self._total_burned,
            "circulating": self._total_minted - self._total_burned - total_staked,
            "staked": total_staked,
            "max_supply": MAX_SUPPLY,
        }

    def _calc_block_reward(self, block_index: int) -> int:
        """Deterministic block reward with halving. Genesis (index 0) gets no reward."""
        if block_index <= 0:
            return 0
        reward = INITIAL_BLOCK_REWARD >> (block_index // HALVING_INTERVAL)
        remaining = MAX_SUPPLY - self._total_minted
        return min(reward, max(0, remaining))

    def activate_financial_layer(self, validator_address: str = ""):
        """Activate the financial layer with genesis balance allocation.

        Must be called after init_chain(). Idempotent -- safe to call multiple times.
        When validator_address is provided, allocates GENESIS_BALANCE_QBIT to that address.
        """
        if self._financial_active:
            return
        self._financial_active = True
        if validator_address and self.get_balance(validator_address) == 0:
            genesis_balance = GENESIS_BALANCE_QBIT * QUBIT_PER_QBIT
            self._credit(validator_address, genesis_balance)
            self._total_minted += genesis_balance
            if self._store is not None:
                self._store.put_balance(validator_address, self.get_balance(validator_address))
                self._store.put_supply("total_minted", self._total_minted)
                self._store.put_supply("total_burned", self._total_burned)
            logger.info(
                f"Financial layer activated: {validator_address[:16]}... "
                f"allocated {genesis_balance} qubits")
        # Rebuild state trie so proofs reflect the genesis allocation
        self._rebuild_state_trie()
        # Update genesis snapshot so rollback restores correctly
        if self._height >= 0:
            self._state_snapshots[self._height] = self._state_trie.snapshot()

    def _pending_debits(self, address: str) -> int:
        """Sum of pending transfers + fees in the pool for an address."""
        dynamic_active = (self._height + 1 >= _config.DYNAMIC_FEE_ACTIVATION_HEIGHT
                          and self._height >= 0)
        total = 0
        for tx in self.tx_pool:
            if tx.sender != address:
                continue
            if dynamic_active:
                # Worst-case fee: max_fee_per_weight * weight
                w = tx_weight(tx.tx_type.value)
                fee = tx.max_fee_per_weight * w
            else:
                fee = TX_FEES.get(tx.tx_type.value, 0)
            total += fee
            if tx.tx_type == TxType.TRANSFER:
                total += tx.payload.get("amount", 0)
            elif tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
                total += tx.payload.get("amount", 0)
        return total

    def _persist_balances_after_block(self, block, idx: int,
                                      matured_stakers: set | None = None):
        """Persist balance changes to SQLite after processing a block."""
        if self._store is None:
            return
        # Late import to pick up monkeypatched value from blockchain module
        from . import blockchain as _bc_mod
        EPOCH_LENGTH = _bc_mod.EPOCH_LENGTH
        # Collect all addresses affected by this block
        affected: set[str] = set()
        affected.add(block.validator)  # receives fees + reward
        for tx in block.transactions:
            affected.add(tx.sender)
            if tx.recipient:
                affected.add(tx.recipient)
        # Include matured unbonding stakers (collected before processing)
        if matured_stakers:
            affected.update(matured_stakers)
        # Include delegators who received epoch rewards at epoch boundary
        if EPOCH_LENGTH > 0 and idx > 0 and idx % EPOCH_LENGTH == 0:
            for validator_addr, stakes in self._stakes.items():
                for delegator_addr in stakes:
                    if delegator_addr != validator_addr:
                        affected.add(delegator_addr)
        for addr in affected:
            self._store.put_balance(addr, self.get_balance(addr))
        self._store.put_supply("total_minted", self._total_minted)
        self._store.put_supply("total_burned", self._total_burned)

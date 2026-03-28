"""Staking, epoch rotation, and slashing operations for the Blockchain.

Extracted as a mixin to reduce blockchain.py size while preserving
all method signatures and self.* access patterns.
"""
import logging
from .. import config as _config
from ..config import (MIN_STAKE, SLASH_PERCENTAGE,
                      DEFAULT_COMMISSION_RATE)
from .transaction import Transaction, TxType
from .receipt import build_event

logger = logging.getLogger("qbit_network.chain")


class StakingMixin:
    """Staking, epoch, and slashing operations mixed into Blockchain."""

    # ---- Staking queries ----

    def get_validator_stake(self, validator_addr: str) -> int:
        """Total stake weight for a validator."""
        return self._total_stake.get(validator_addr, 0)

    def get_staker_info(self, staker: str, validator: str) -> int:
        """Specific stake amount from staker to validator."""
        return self._stakes.get(validator, {}).get(staker, 0)

    def get_active_validators(self) -> list[tuple[str, int]]:
        """Validators for dPoS selection. Uses epoch-frozen set when available,
        falls back to live stake for backward compatibility."""
        if self._epoch_validators:
            return list(self._epoch_validators)
        result = [(addr, total) for addr, total in self._total_stake.items() if total > 0]
        result.sort(key=lambda x: x[0])
        return result

    def _get_live_validators(self) -> list[tuple[str, int]]:
        """Live validators with stake > 0 (not epoch-frozen). Used for epoch snapshots."""
        result = [(addr, total) for addr, total in self._total_stake.items() if total > 0]
        result.sort(key=lambda x: x[0])
        return result

    def get_all_stakes(self) -> dict[str, dict[str, int]]:
        """Return full stakes mapping (validator -> {staker: amount})."""
        return dict(self._stakes)

    def get_current_epoch(self) -> int:
        """Return the current epoch number."""
        return self._current_epoch

    def get_epoch_validators(self, epoch: int | None = None) -> list[tuple[str, int]]:
        """Return the validator set for a given epoch (default: current epoch)."""
        if epoch is None:
            epoch = self._current_epoch
        return list(self._epochs.get(epoch, []))

    def get_slashing_events(self, validator: str = "") -> list[dict]:
        """Return slashing events, optionally filtered by validator."""
        if validator:
            return [e for e in self._slashing_events if e["validator"] == validator]
        return list(self._slashing_events)

    def is_slashed(self, address: str) -> bool:
        """Check if a validator has been slashed."""
        return address in self._slashed_validators

    # ---- Evidence / Slashing processing ----

    def _process_evidence_tx(self, tx: Transaction, block_index: int):
        """Process an EVIDENCE transaction: verify double-sign proof and slash."""
        vaddr = tx.payload.get("validator_address", "")
        if not vaddr or vaddr in self._processed_evidence:
            return  # already slashed or invalid

        # Get validator's public key for signature verification
        vpk = self._validator_registry.get(vaddr)
        if vpk is None:
            logger.warning(f"Evidence tx {tx.tx_id[:16]}...: validator {vaddr[:16]}... not registered")
            return

        block_a_sig_hex = tx.payload.get("block_a_sig", "")
        block_b_sig_hex = tx.payload.get("block_b_sig", "")
        block_a_hash = tx.payload.get("block_a_hash", "")
        block_b_hash = tx.payload.get("block_b_hash", "")
        evidence_block_index = tx.payload.get("block_index", -1)

        try:
            block_a_sig = bytes.fromhex(block_a_sig_hex)
            block_b_sig = bytes.fromhex(block_b_sig_hex)
        except ValueError:
            logger.warning(f"Evidence tx {tx.tx_id[:16]}...: invalid signature hex")
            return

        # Verify both signatures against the raw header bytes provided in evidence.
        # The headers are the exact bytes the validator signed when producing each block.
        from ..crypto import MLDSA
        block_a_header_hex = tx.payload.get("block_a_header", "")
        block_b_header_hex = tx.payload.get("block_b_header", "")
        try:
            header_a = bytes.fromhex(block_a_header_hex)
            header_b = bytes.fromhex(block_b_header_hex)
        except ValueError:
            logger.warning(f"Evidence tx {tx.tx_id[:16]}...: invalid header hex")
            return

        if not MLDSA.verify(vpk, header_a, block_a_sig):
            logger.warning(f"Evidence tx {tx.tx_id[:16]}...: block_a signature verification failed")
            return
        if not MLDSA.verify(vpk, header_b, block_b_sig):
            logger.warning(f"Evidence tx {tx.tx_id[:16]}...: block_b signature verification failed")
            return

        # Double-sign confirmed -- slash the validator
        total = self._total_stake.get(vaddr, 0)
        slash_amount = (total * SLASH_PERCENTAGE) // 100
        if slash_amount <= 0 and total > 0:
            slash_amount = 1  # always slash at least 1 if there is any stake

        # Reduce all stakers proportionally
        if vaddr in self._stakes:
            stakers = dict(self._stakes[vaddr])
            for staker, staker_amount in stakers.items():
                reduction = (staker_amount * SLASH_PERCENTAGE) // 100
                if reduction <= 0 and staker_amount > 0:
                    reduction = 1
                new_amount = max(0, staker_amount - reduction)
                if new_amount == 0:
                    self._stakes[vaddr].pop(staker, None)
                    if self._store is not None:
                        self._store.delete_stake(staker, vaddr)
                else:
                    self._stakes[vaddr][staker] = new_amount
                    if self._store is not None:
                        self._store.put_stake(staker, vaddr, new_amount)

        new_total = max(0, total - slash_amount)
        self._total_stake[vaddr] = new_total

        # If stake drops below MIN_STAKE, remove from active validators
        if new_total < MIN_STAKE:
            self._total_stake.pop(vaddr, None)
            self._stakes.pop(vaddr, None)
            if self._store is not None:
                # Clean up any remaining stake entries
                for staker in list(self._stakes.get(vaddr, {}).keys()):
                    self._store.delete_stake(staker, vaddr)

        # Record slashing
        self._slashed_validators.add(vaddr)
        self._processed_evidence.add(vaddr)
        event = {
            "validator": vaddr,
            "evidence_tx_id": tx.tx_id,
            "amount_slashed": slash_amount,
            "block_index": block_index,
        }
        self._slashing_events.append(event)

        if self._store is not None:
            self._store.put_slashing_event(vaddr, tx.tx_id, slash_amount, block_index)

        # Update epoch validators if they include this validator
        if self._epoch_validators:
            self._epoch_validators = [
                (addr, stake) for addr, stake in self._epoch_validators
                if addr != vaddr or new_total >= MIN_STAKE
            ]
            if new_total >= MIN_STAKE:
                self._epoch_validators = [
                    (addr, new_total) if addr == vaddr else (addr, stake)
                    for addr, stake in self._epoch_validators
                ]

        logger.info(
            f"SLASHED validator {vaddr[:16]}...: amount={slash_amount}, "
            f"remaining_stake={new_total} (block #{block_index})")

    # ---- Epoch rewards ----

    def _distribute_epoch_rewards(self, epoch_number: int):
        """Distribute validator block rewards to delegators proportionally.

        Debits the distributed amount from the validator's balance to maintain
        the supply conservation invariant (R16-003).
        """
        credits: list[tuple[str, int]] = []
        debits: list[tuple[str, int]] = []
        for validator_addr, stakes in self._stakes.items():
            if not stakes:
                continue
            # Get validator's commission rate (default DEFAULT_COMMISSION_RATE%)
            commission_rate = self._validator_commission.get(
                validator_addr, DEFAULT_COMMISSION_RATE)

            # Calculate total delegated (excluding self-stake)
            total_delegated = sum(
                amt for addr, amt in stakes.items() if addr != validator_addr)
            if total_delegated == 0:
                continue

            # Accumulated rewards for this validator this epoch
            epoch_rewards = self._epoch_rewards.get(validator_addr, 0)
            if epoch_rewards == 0:
                continue

            # Split: validator keeps commission
            validator_commission = epoch_rewards * commission_rate // 100
            delegator_pool = epoch_rewards - validator_commission

            # Distribute to delegators proportionally
            total_distributed = 0
            for delegator_addr, delegator_stake in stakes.items():
                if delegator_addr == validator_addr:
                    continue
                share = delegator_pool * delegator_stake // total_delegated
                if share > 0:
                    self._credit(delegator_addr, share)
                    total_distributed += share
                    credits.append((delegator_addr, share))

            # Debit the distributed amount from validator to maintain supply conservation
            if total_distributed > 0:
                val_bal = self._balances.get(validator_addr, 0)
                debit_amt = min(total_distributed, val_bal)
                if debit_amt > 0:
                    self._debit(validator_addr, debit_amt)
                    debits.append((validator_addr, debit_amt))

            # Reset epoch rewards for this validator
            self._epoch_rewards[validator_addr] = 0

        # Record for rollback
        self._last_epoch_distributions[epoch_number] = {
            "credits": credits,
            "debits": debits,
        }

        logger.info(f"Epoch {epoch_number}: delegator rewards distributed")

    def get_epoch_rewards(self, validator_addr: str) -> int:
        """Return accumulated epoch rewards for a validator."""
        return self._epoch_rewards.get(validator_addr, 0)

    def get_validator_commission(self, validator_addr: str) -> int:
        """Return commission rate for a validator (percent)."""
        return self._validator_commission.get(validator_addr, DEFAULT_COMMISSION_RATE)

    def _check_epoch_transition(self, block_index: int):
        """Check if we've crossed an epoch boundary and snapshot validators."""
        # Late import to pick up monkeypatched value from blockchain module
        from . import blockchain as _bc_mod
        EPOCH_LENGTH = _bc_mod.EPOCH_LENGTH
        if EPOCH_LENGTH <= 0:
            return

        new_epoch = block_index // EPOCH_LENGTH
        if new_epoch > self._current_epoch or (block_index == 0 and not self._epochs):
            # Distribute epoch rewards before transitioning (not on epoch 0)
            if new_epoch > 0 and self._financial_active:
                self._distribute_epoch_rewards(new_epoch - 1)

            self._current_epoch = new_epoch
            # Snapshot current live validators for this epoch
            live = self._get_live_validators()
            self._epoch_validators = list(live)
            self._epochs[new_epoch] = list(live)

            if self._store is not None:
                import json as _json
                validators_json = _json.dumps(live)
                self._store.put_epoch(new_epoch, new_epoch * EPOCH_LENGTH, validators_json)

            logger.info(
                f"Epoch transition: epoch={new_epoch}, block={block_index}, "
                f"validators={len(live)}")

    def _process_mature_unbondings(self, current_index: int):
        """Remove unbonding entries whose release_block has been reached."""
        remaining = []
        for entry in self._unbonding:
            if entry["release_block"] <= current_index:
                # Credit balance back to staker on maturity
                if self._financial_active:
                    self._credit(entry["staker"], entry["amount"])
                # Unbonding complete -- remove from store
                if self._store is not None:
                    self._store.delete_unbonding(
                        entry["staker"], entry["validator"],
                        entry["amount"], entry["release_block"])
            else:
                remaining.append(entry)
        self._unbonding = remaining

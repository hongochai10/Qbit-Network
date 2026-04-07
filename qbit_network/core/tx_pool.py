"""Transaction pool management mixin for the Blockchain class.

Extracted from blockchain.py to reduce file size. Contains submit_tx()
and _current_base_fee() which handle pool admission and dynamic fee
computation.

NOTE: Constants are imported lazily from the ``blockchain`` module so that
test monkeypatching (e.g. ``monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT", ...)``)
continues to work correctly.
"""
import time

from .transaction import TxType
from .fees import (compute_base_fee, compute_tx_fee, tx_weight,
                   effective_block_weight)


class TxPoolMixin:
    """Mixin providing transaction pool admission and base fee computation."""

    def submit_tx(self, tx) -> tuple[bool, str]:
        """Submit a signed transaction to the pool."""
        # Lazy import to read monkeypatched values from the blockchain module
        from . import blockchain as _bc_mod
        MAX_TX_POOL_SIZE = _bc_mod.MAX_TX_POOL_SIZE
        CHAIN_ID = _bc_mod.CHAIN_ID
        DYNAMIC_FEE_ACTIVATION_HEIGHT = _bc_mod.DYNAMIC_FEE_ACTIVATION_HEIGHT
        INITIAL_BASE_FEE = _bc_mod.INITIAL_BASE_FEE
        TX_FEES = _bc_mod.TX_FEES

        # Pool size limit (#S08)
        if len(self.tx_pool) >= MAX_TX_POOL_SIZE:
            return False, "tx pool full"

        # Chain ID validation (T-03) — before signature check
        if tx.chain_id != CHAIN_ID:
            return False, f"wrong chain_id: expected {CHAIN_ID}"

        # Revoked signing key cannot submit any transactions
        if self.is_key_revoked(tx.sender, "signing"):
            return False, "sender signing key has been revoked"

        if not tx.verify():
            return False, "invalid signature"

        # Payload validation (#S10, #S15)
        ok, err = tx.validate_payload()
        if not ok:
            return False, f"invalid payload: {err}"

        if tx.tx_id in self._tx_by_id:
            return False, "duplicate (already in chain)"

        if tx.tx_id in self._pool_ids:
            return False, "duplicate (already in pool)"

        # R30-001: Cap amounts to MAX_SUPPLY to prevent DoS via big-integer
        # processing in _pending_debits and balance calculations.
        from ..config import MAX_SUPPLY as _MAX_SUPPLY
        if tx.tx_type in (TxType.TRANSFER, TxType.STAKE, TxType.DELEGATE):
            _amount = tx.payload.get("amount", 0)
            if _amount > _MAX_SUPPLY:
                return False, (f"amount {_amount} exceeds MAX_SUPPLY "
                               f"({_MAX_SUPPLY})")
        if tx.tx_type == TxType.MINT_TOKEN:
            _amount = tx.payload.get("amount", 0)
            if _amount > _MAX_SUPPLY:
                return False, (f"mint amount {_amount} exceeds MAX_SUPPLY "
                               f"({_MAX_SUPPLY})")

        if tx.tx_type == TxType.SHARE and not tx.recipient:
            return False, "SHARE tx requires recipient"

        # STAKE / DELEGATE: target validator must be registered and not slashed
        if tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
            vaddr = tx.payload.get("validator_address", "")
            if not self.is_registered_validator(vaddr):
                return False, f"validator not registered: {vaddr[:16]}..."
            if vaddr in self._slashed_validators:
                return False, f"cannot stake to slashed validator: {vaddr[:16]}..."

        # UNSTAKE: target must be registered, sender must have enough stake
        if tx.tx_type == TxType.UNSTAKE:
            vaddr = tx.payload.get("validator_address", "")
            if not self.is_registered_validator(vaddr):
                return False, f"validator not registered: {vaddr[:16]}..."
            amount = tx.payload.get("amount", 0)
            current = self.get_staker_info(tx.sender, vaddr)
            if amount > current:
                return False, (f"insufficient stake: want to unstake {amount}, "
                               f"have {current}")

        # EIP-1559 pool admission: check max_fee_per_weight >= current base_fee
        _next_idx = self._height + 1
        _dynamic_active = (_next_idx >= DYNAMIC_FEE_ACTIVATION_HEIGHT
                           and self._height >= 0)
        if _dynamic_active:
            w = tx_weight(tx.tx_type.value)
            if w > 0:
                # Compute current base_fee from latest block
                current_bf = self._current_base_fee()
                if tx.max_fee_per_weight < current_bf:
                    return False, (f"max_fee_per_weight {tx.max_fee_per_weight} "
                                   f"< current base_fee {current_bf}")

        # Financial layer balance checks (only when financial layer is active)
        if self._financial_active:
            if _dynamic_active:
                # Dynamic fee balance check (all fee-bearing types)
                w = tx_weight(tx.tx_type.value)
                if w > 0:
                    worst_case_fee = tx.max_fee_per_weight * w
                    extra_debit = 0
                    if tx.tx_type == TxType.TRANSFER:
                        if not tx.recipient:
                            return False, "TRANSFER requires a recipient"
                        extra_debit = tx.payload.get("amount", 0)
                    elif tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
                        extra_debit = tx.payload.get("amount", 0)
                    pending = self._pending_debits(tx.sender)
                    available = self.get_balance(tx.sender) - pending
                    if available < worst_case_fee + extra_debit:
                        return False, (f"insufficient balance: need {worst_case_fee + extra_debit}, "
                                       f"available {available}")
                elif tx.tx_type == TxType.TRANSFER:
                    # Zero-weight TRANSFER still needs amount check
                    if not tx.recipient:
                        return False, "TRANSFER requires a recipient"
                    amount = tx.payload.get("amount", 0)
                    pending = self._pending_debits(tx.sender)
                    available = self.get_balance(tx.sender) - pending
                    if available < amount:
                        return False, (f"insufficient balance: need {amount}, "
                                       f"available {available}")
            else:
                # Legacy fixed fee balance checks
                # TRANSFER: balance check (fee + amount)
                if tx.tx_type == TxType.TRANSFER:
                    if not tx.recipient:
                        return False, "TRANSFER requires a recipient"
                    amount = tx.payload.get("amount", 0)
                    fee = TX_FEES.get("TRANSFER", 0)
                    pending = self._pending_debits(tx.sender)
                    available = self.get_balance(tx.sender) - pending
                    if available < amount + fee:
                        return False, (f"insufficient balance: need {amount + fee}, "
                                       f"available {available}")

                # Balance check for fee-bearing types (except TRANSFER handled above)
                if tx.tx_type != TxType.TRANSFER:
                    fee = TX_FEES.get(tx.tx_type.value, 0)
                    if fee > 0:
                        extra_debit = 0
                        if tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
                            extra_debit = tx.payload.get("amount", 0)
                        pending = self._pending_debits(tx.sender)
                        available = self.get_balance(tx.sender) - pending
                        if available < fee + extra_debit:
                            return False, (f"insufficient balance for fee: need {fee + extra_debit}, "
                                           f"available {available}")

        # EVIDENCE: validator must be registered and not already slashed
        if tx.tx_type == TxType.EVIDENCE:
            vaddr = tx.payload.get("validator_address", "")
            if not self.is_registered_validator(vaddr):
                return False, f"evidence target not a registered validator: {vaddr[:16]}..."
            if vaddr in self._processed_evidence:
                return False, f"validator already slashed: {vaddr[:16]}..."

        # REVOKE_KEY: idempotency + genesis validator safety
        if tx.tx_type == TxType.REVOKE_KEY:
            key_type = tx.payload.get("key_type", "")
            if self.is_key_revoked(tx.sender, key_type):
                return False, f"{key_type} key already revoked for this address"
            # Genesis validator cannot revoke signing or validator keys (K-01)
            if key_type in ("validator", "signing") and self._height >= 0:
                genesis_block = self._get_block_by_index(0)
                if genesis_block and tx.sender == genesis_block.validator:
                    return False, "cannot revoke genesis validator keys"

        # R23-004: Token operations gated by activation height at pool admission
        if tx.tx_type in (TxType.ISSUE_TOKEN, TxType.MINT_TOKEN, TxType.TRANSFER_TOKEN):
            from . import blockchain as _bc_mod
            if self._height + 1 < _bc_mod.TOKEN_ACTIVATION_HEIGHT:
                return False, "token operations not yet active"

        # R21-002: ISSUE_TOKEN symbol uniqueness check at pool admission
        if tx.tx_type == TxType.ISSUE_TOKEN:
            symbol = tx.payload.get("symbol", "")
            if symbol in self._token_by_symbol:
                return False, f"token symbol already exists: {symbol}"
            # Also check pending pool for conflicting ISSUE_TOKEN with same symbol
            for pool_tx in self.tx_pool:
                if (pool_tx.tx_type == TxType.ISSUE_TOKEN
                        and pool_tx.payload.get("symbol") == symbol):
                    return False, f"token symbol already pending in pool: {symbol}"

        # R21-003: MINT_TOKEN issuer + TRANSFER_TOKEN balance checks at pool admission
        if tx.tx_type == TxType.MINT_TOKEN:
            tid = tx.payload.get("token_id", "")
            reg = self._token_registry.get(tid)
            if reg is None:
                return False, f"token does not exist: {tid}"
            if reg["issuer"] != tx.sender:
                return False, "only the token issuer can mint"
            if reg["max_supply"] > 0:
                amount = tx.payload.get("amount", 0)
                # R32-003: account for pending MINT_TOKEN amounts in pool
                pending_mint = sum(
                    ptx.payload.get("amount", 0)
                    for ptx in self.tx_pool
                    if ptx.tx_type == TxType.MINT_TOKEN
                    and ptx.payload.get("token_id") == tid
                )
                if reg["total_minted"] + pending_mint + amount > reg["max_supply"]:
                    return False, (f"minting {amount} would exceed max_supply "
                                   f"{reg['max_supply']}")

        if tx.tx_type == TxType.TRANSFER_TOKEN:
            tid = tx.payload.get("token_id", "")
            reg = self._token_registry.get(tid)
            if reg is None:
                return False, f"token does not exist: {tid}"
            if not reg.get("transferable", True):
                return False, f"token {tid} is not transferable"
            amount = tx.payload.get("amount", 0)
            bal = self._token_balances.get((tid, tx.sender), 0)
            # R32-003: subtract pending TRANSFER_TOKEN debits from same sender+token
            pending_debit = sum(
                ptx.payload.get("amount", 0)
                for ptx in self.tx_pool
                if ptx.tx_type == TxType.TRANSFER_TOKEN
                and ptx.payload.get("token_id") == tid
                and ptx.sender == tx.sender
            )
            effective_bal = bal - pending_debit
            if effective_bal < amount:
                return False, (f"insufficient token balance: have {effective_bal}, "
                               f"need {amount}")

        # Nonce check -- O(1) via _pool_sender_count
        expected_nonce = self.get_nonce(tx.sender)
        pending_from_sender = self._pool_sender_count.get(tx.sender, 0)
        if tx.nonce != expected_nonce + pending_from_sender:
            return False, (f"invalid nonce: expected {expected_nonce + pending_from_sender}, "
                           f"got {tx.nonce}")

        # Timestamp sanity
        now = int(time.time())
        if tx.timestamp > now + 300:
            return False, "tx timestamp too far in future"
        if tx.timestamp < now - 86400:
            return False, "tx timestamp too old (>24h)"

        self.tx_pool.append(tx)
        self._pool_ids.add(tx.tx_id)
        self._pool_sender_count[tx.sender] = pending_from_sender + 1
        self._pending_debits_cache[tx.sender] = (
            self._pending_debits_cache.get(tx.sender, 0)
            + self._calc_tx_debit(tx, next_block_height=self._height + 1))
        t_val = tx.tx_type.value
        self._pool_type_counts[t_val] = self._pool_type_counts.get(t_val, 0) + 1
        return True, tx.tx_id

    def _current_base_fee(self) -> int:
        """Compute the base fee for the next block based on current chain state."""
        # Lazy import to read monkeypatched values from the blockchain module
        from . import blockchain as _bc_mod
        DYNAMIC_FEE_ACTIVATION_HEIGHT = _bc_mod.DYNAMIC_FEE_ACTIVATION_HEIGHT
        INITIAL_BASE_FEE = _bc_mod.INITIAL_BASE_FEE

        if self._height < 0:
            return INITIAL_BASE_FEE
        parent = self._latest_block
        next_idx = self._height + 1
        _parent_pre = (parent.index == 0
                       or parent.index < DYNAMIC_FEE_ACTIVATION_HEIGHT)
        if next_idx == DYNAMIC_FEE_ACTIVATION_HEIGHT or _parent_pre:
            return INITIAL_BASE_FEE
        parent_eff_weight = effective_block_weight(
            parent.transactions, parent.validator)
        return compute_base_fee(parent.base_fee, parent_eff_weight)

"""Rollback and fork evaluation operations for the Blockchain.

Extracted as a mixin to reduce blockchain.py size while preserving
all method signatures and self.* access patterns.
"""
import logging
from ..config import (UNBONDING_PERIOD, TX_FEES)
from .fees import tx_weight, compute_tx_fee
from .transaction import TxType

logger = logging.getLogger("qbit_network.chain")


class RollbackMixin:
    """Rollback, fork evaluation, and related helpers mixed into Blockchain."""

    def _rollback_to(self, target_index: int) -> list:
        """Pop blocks from tip down to target_index (exclusive). Returns displaced txs."""
        if self._store is not None:
            with self._db_lock:
                return self._rollback_to_inner(target_index)
        return self._rollback_to_inner(target_index)

    def _rollback_to_inner(self, target_index: int) -> list:
        """Inner rollback logic -- caller must hold _db_lock when _store is set."""
        # Snapshot supply invariant before rollback (R33-H02).
        # invariant = accounted - minted; must be unchanged after rollback.
        _unbonding_total = sum(e.get("amount", 0) for e in self._unbonding)
        _pre_invariant = (
            sum(self._balances.values())
            + sum(self._total_stake.values())
            + self._total_burned
            + _unbonding_total
            - self._total_minted
        ) if self._financial_active else None

        # In SQLite mode, collect blocks to roll back BEFORE deleting from SQLite
        blocks_to_rollback: list = []
        if self._chain_list is None:
            # SQLite mode: gather blocks from tip to target before deletion
            for i in range(self._height, target_index - 1, -1):
                block = self._get_block_by_index(i)
                if block is not None:
                    blocks_to_rollback.append(block)

        # Sync SQLite rollback
        if self._store is not None:
            self._store.delete_blocks_from(target_index)

        displaced = []

        if self._chain_list is not None:
            # In-memory mode: pop blocks from the list
            while len(self._chain_list) > target_index:
                block = self._chain_list.pop()
                self._rollback_block(block, displaced)
        else:
            # SQLite mode: use the pre-fetched blocks
            for block in blocks_to_rollback:
                self._rollback_block(block, displaced)

        # Post-rollback sanity: no account should have a negative balance.
        # Temporary negatives may occur mid-rollback (H-02 fix) but must
        # resolve once the full rollback sequence completes.
        negative_addrs = {
            addr: bal for addr, bal in self._balances.items() if bal < 0
        }
        if negative_addrs:
            details = ", ".join(
                f"{a[:16]}...={b}" for a, b in list(negative_addrs.items())[:5]
            )
            raise ValueError(
                f"Balance conservation violation after rollback: "
                f"{len(negative_addrs)} account(s) with negative balance "
                f"({details}). Chain re-sync required."
            )

        # Supply conservation invariant (R33-H02): the relationship between
        # accounted funds and total_minted must be unchanged by rollback.
        # A shift indicates a rollback bug (e.g. clamping amounts).
        if _pre_invariant is not None:
            _unbonding_after = sum(e.get("amount", 0) for e in self._unbonding)
            _post_invariant = (
                sum(self._balances.values())
                + sum(self._total_stake.values())
                + self._total_burned
                + _unbonding_after
                - self._total_minted
            )
            if _post_invariant != _pre_invariant:
                delta = _post_invariant - _pre_invariant
                raise ValueError(
                    f"Supply conservation violation after rollback: "
                    f"invariant shifted by {delta} qubits. "
                    f"Chain re-sync required."
                )

        return displaced

    def _find_validator_pk_in_chain(self, address: str) -> bytes | None:
        """Look up a validator's signing pubkey by address.

        Uses the in-memory _validator_registry (O(1)) first, then falls back
        to the SQLite validator_registry table or an in-memory chain scan.
        The chain scan is needed during rollback of REVOKE_KEY (validator) txs
        because the revocation processing removes the address from
        _validator_registry before rollback queries it.
        """
        # 1) In-memory registry (populated during block processing)
        pk = self._validator_registry.get(address)
        if pk is not None:
            return pk
        # 2) SQLite fallback (covers cold-start / post-reload scenarios)
        if self._store is not None:
            pk_hex = self._store.get_validator(address)
            if pk_hex:
                return bytes.fromhex(pk_hex)
        # 3) In-memory chain scan (covers rollback of validator revocations
        #    in non-SQLite mode: _validator_registry was cleared by the
        #    revocation tx processing, so we must find the REGISTER_VALIDATOR
        #    tx directly in the remaining blocks).
        if self._chain_list is not None:
            from .transaction import TxType as _TxType
            for block in self._chain_list:
                for tx in block.transactions:
                    if (tx.tx_type == _TxType.REGISTER_VALIDATOR
                            and tx.payload.get("validator_address") == address):
                        pk_hex = tx.payload.get("validator_pubkey", "")
                        if pk_hex:
                            try:
                                return bytes.fromhex(pk_hex)
                            except ValueError:
                                pass
        return None

    def _evaluate_fork(self, block) -> tuple[bool, str]:
        """Evaluate a single competing block at same height.
        First-seen wins -- a single block cannot replace an existing block.
        Use try_reorg() with a strictly longer chain to trigger reorganization."""
        return False, (f"first-seen wins: already have block at index {block.index}, "
                       f"use try_reorg() with a longer fork chain")

    def _get_blocks_range(self, start: int, end: int) -> list:
        """Fetch blocks [start, end) from the appropriate backend."""
        if self._chain_list is not None:
            return list(self._chain_list[start:end])
        if self._store is not None:
            return self._store.get_blocks_range(start, end)
        return []

    def _rollback_block(self, block, displaced: list):
        """Rollback a single block's indices and collect displaced transactions."""
        # Lazy import to read monkeypatched values from the blockchain module
        from . import blockchain as _bc_mod
        DYNAMIC_FEE_ACTIVATION_HEIGHT = _bc_mod.DYNAMIC_FEE_ACTIVATION_HEIGHT
        EPOCH_LENGTH = _bc_mod.EPOCH_LENGTH

        idx = block.index

        # --- Reverse receipts / events ---
        for tx in block.transactions:
            receipt = self._receipts.pop(tx.tx_id, None)
            if receipt:
                for ev in receipt.events:
                    ev_type = ev.get("type", "")
                    if ev_type and ev_type in self._events_by_type:
                        tx_list = self._events_by_type[ev_type]
                        if tx.tx_id in tx_list:
                            tx_list.remove(tx.tx_id)
        self._events_by_block.pop(idx, None)
        # Remove block-level events
        self._block_level_events.pop(idx, None)
        # Reset finalized height if we rolled back past it
        if self._finalized_height >= idx:
            self._finalized_height = idx - 1
        # Remove receipts from SQLite
        if self._store is not None:
            self._store.delete_receipts_for_block(idx)

        # --- Reverse balance changes (financial layer) ---
        if idx > 0 and self._financial_active:
            # Reverse mature unbonding credits that happened at this block
            # (unbonding entries with release_block == idx would have been
            #  credited and removed; we can't recover them here because they
            #  are already gone from _unbonding. The full stake rebuild in
            #  delete_blocks_from handles this, and _load_from_sqlite
            #  recomputes balances. For in-memory rollbacks during reorg,
            #  we re-scan to find unbonding entries that would have matured.)

            # Reverse block reward
            reward = self._calc_block_reward(idx)
            if reward > 0:
                self._rollback_debit(block.validator, reward)
                self._total_minted -= reward
                # Reverse epoch reward accumulation
                self._epoch_rewards[block.validator] = max(
                    0, self._epoch_rewards.get(block.validator, 0) - reward)

            # Reverse tx balance changes in reverse order
            _dyn = idx >= DYNAMIC_FEE_ACTIVATION_HEIGHT
            for tx in reversed(block.transactions):
                # Reverse type-specific balance changes
                if tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
                    amount = tx.payload.get("amount", 0)
                    if amount > 0:
                        self._credit(tx.sender, amount)
                elif tx.tx_type == TxType.TRANSFER:
                    amount = tx.payload["amount"]
                    # Reverse: debit full amount from recipient, credit sender.
                    # Use _rollback_debit to allow temporary negative balances
                    # during multi-block rollback (H-02 fix).
                    self._rollback_debit(tx.recipient, amount)
                    self._credit(tx.sender, amount)

                # Reverse fee
                if _dyn:
                    # EIP-1559: 100% was credited to validator
                    w = tx_weight(tx.tx_type.value)
                    if w > 0:
                        fee = compute_tx_fee(block.base_fee,
                                             tx.max_fee_per_weight,
                                             tx.max_priority_fee, w)
                        self._rollback_debit(block.validator, fee)
                        self._credit(tx.sender, fee)
                else:
                    fee = TX_FEES.get(tx.tx_type.value, 0)
                    if fee > 0:
                        validator_share = fee // 2
                        burn = fee - validator_share
                        # Reverse validator fee credit
                        self._rollback_debit(block.validator, validator_share)
                        # Reverse burn
                        self._total_burned -= burn
                        # Reverse sender fee debit
                        self._credit(tx.sender, fee)

        self._block_by_hash.pop(block.block_hash, None)
        for tx in block.transactions:
            self._tx_by_id.pop(tx.tx_id, None)
            self.consensus._chain_tx_ids.discard(tx.tx_id)

            # Revert sender/recipient indices
            sender_txs = self._txs_by_sender.get(tx.sender, [])
            if tx.tx_id in sender_txs:
                sender_txs.remove(tx.tx_id)
            if tx.recipient:
                recip_txs = self._txs_by_recipient.get(tx.recipient, [])
                if tx.tx_id in recip_txs:
                    recip_txs.remove(tx.tx_id)

            # Revert notarization indices
            if tx.tx_type == TxType.NOTARIZE:
                dh = tx.payload.get("documentHash", "")
                if dh:
                    if self._notarizations.get(dh) == tx.tx_id:
                        self._notarizations.pop(dh, None)
                    by_hash = self._notarizations_by_hash.get(dh, [])
                    if tx.tx_id in by_hash:
                        by_hash.remove(tx.tx_id)
                    # Restore first notarization from remaining entries
                    if dh not in self._notarizations and by_hash:
                        self._notarizations[dh] = by_hash[0]
                cnt = self._notarization_count.get(tx.sender, 1) - 1
                if cnt <= 0:
                    self._notarization_count.pop(tx.sender, None)
                else:
                    self._notarization_count[tx.sender] = cnt

            # Revert key registry
            elif tx.tx_type == TxType.REGISTER_KEY:
                epk = tx.payload.get("encryption_pk", "")
                if epk:
                    history = self._key_history.get(tx.sender, [])
                    if epk in history:
                        history.remove(epk)
                    if history:
                        self._key_registry[tx.sender] = history[-1]
                    else:
                        self._key_registry.pop(tx.sender, None)

            # Revert validator registry
            elif tx.tx_type == TxType.REGISTER_VALIDATOR:
                vaddr = tx.payload.get("validator_address", "")
                if vaddr:
                    self._validator_registry.pop(vaddr, None)
                    self.consensus.remove_validator(vaddr)
                    # SQLite cleanup is handled atomically by
                    # delete_blocks_from() -- no separate commit needed

            # Revert key revocations
            elif tx.tx_type == TxType.REVOKE_KEY:
                key_type = tx.payload.get("key_type", "")
                rev_key = f"{tx.sender}:{key_type}"
                revocation = self._revoked_keys.pop(rev_key, None)
                if revocation and key_type == "validator":
                    # Re-add validator if their registration is still
                    # in the remaining chain (scan up to current height)
                    vpk = self._find_validator_pk_in_chain(tx.sender)
                    if vpk:
                        self._validator_registry[tx.sender] = vpk
                        self.consensus.add_validator(tx.sender, vpk)
                    # SQLite revocation cleanup handled by delete_blocks_from()

            # Revert staking operations
            elif tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
                vaddr = tx.payload.get("validator_address", "")
                amount = tx.payload.get("amount", 0)
                if vaddr and amount > 0:
                    staker = tx.sender
                    if vaddr in self._stakes and staker in self._stakes[vaddr]:
                        self._stakes[vaddr][staker] -= amount
                        if self._stakes[vaddr][staker] <= 0:
                            self._stakes[vaddr].pop(staker, None)
                    self._total_stake[vaddr] = max(0, self._total_stake.get(vaddr, 0) - amount)
                    if self._total_stake.get(vaddr, 0) == 0:
                        self._total_stake.pop(vaddr, None)

            elif tx.tx_type == TxType.UNSTAKE:
                vaddr = tx.payload.get("validator_address", "")
                amount = tx.payload.get("amount", 0)
                if vaddr and amount > 0:
                    staker = tx.sender
                    # Restore stake that was removed by unstake
                    self._stakes.setdefault(vaddr, {})
                    self._stakes[vaddr][staker] = self._stakes[vaddr].get(staker, 0) + amount
                    self._total_stake[vaddr] = self._total_stake.get(vaddr, 0) + amount
                    # Remove the unbonding entry created by this tx
                    release_block = block.index + UNBONDING_PERIOD
                    self._unbonding = [
                        e for e in self._unbonding
                        if not (e["staker"] == staker and e["validator"] == vaddr
                                and e["amount"] == amount and e["release_block"] == release_block)
                    ]

            elif tx.tx_type == TxType.EVIDENCE:
                # Revert slashing -- this is complex so we just remove the record
                # and let the stake rebuild handle the rest
                vaddr = tx.payload.get("validator_address", "")
                if vaddr:
                    self._slashed_validators.discard(vaddr)
                    self._processed_evidence.discard(vaddr)
                    self._slashing_events = [
                        e for e in self._slashing_events
                        if e["evidence_tx_id"] != tx.tx_id
                    ]

            # Revert token operations
            elif tx.tx_type == TxType.ISSUE_TOKEN:
                from ..crypto import sha3_256 as _sha3
                symbol = tx.payload.get("symbol", "")
                token_id = _sha3(
                    (tx.sender + symbol + str(tx.nonce)).encode()
                ).hex()[:32]
                self._token_registry.pop(token_id, None)
                self._token_by_symbol.pop(symbol, None)
                # Clean up any balances and indices for this token
                to_del = [k for k in self._token_balances if k[0] == token_id]
                for k in to_del:
                    del self._token_balances[k]
                    self._update_token_index(token_id, k[1], 0)
                if self._store is not None:
                    self._store.delete_token(token_id, commit=False)

            elif tx.tx_type == TxType.MINT_TOKEN:
                tid = tx.payload.get("token_id", "")
                amount = tx.payload.get("amount", 0)
                if tid in self._token_registry:
                    self._token_registry[tid]["total_minted"] = max(
                        0, self._token_registry[tid]["total_minted"] - amount
                    )
                key = (tid, tx.recipient)
                bal = self._token_balances.get(key, 0)
                new_bal = bal - amount
                if new_bal <= 0:
                    self._token_balances.pop(key, None)
                else:
                    self._token_balances[key] = new_bal
                self._update_token_index(tid, tx.recipient, max(0, new_bal))
                if self._store is not None:
                    if tid in self._token_registry:
                        self._store.put_token(tid, self._token_registry[tid], commit=False)
                    self._store.put_token_balance(
                        tid, tx.recipient, max(0, new_bal), commit=False)

            elif tx.tx_type == TxType.TRANSFER_TOKEN:
                tid = tx.payload.get("token_id", "")
                amount = tx.payload.get("amount", 0)
                # Reverse: debit recipient, credit sender
                dst_key = (tid, tx.recipient)
                dst_bal = self._token_balances.get(dst_key, 0)
                new_dst = dst_bal - amount
                if new_dst < 0:
                    import logging as _log
                    _log.getLogger("qbit_network.chain").warning(
                        f"Token rollback: negative balance for {tid}:{tx.recipient}, clamping to 0")
                if new_dst <= 0:
                    self._token_balances.pop(dst_key, None)
                else:
                    self._token_balances[dst_key] = new_dst
                self._update_token_index(tid, tx.recipient, max(0, new_dst))
                src_key = (tid, tx.sender)
                self._token_balances[src_key] = self._token_balances.get(src_key, 0) + amount
                self._update_token_index(tid, tx.sender, self._token_balances[src_key])
                if self._store is not None:
                    self._store.put_token_balance(
                        tid, tx.recipient, max(0, new_dst), commit=False)
                    self._store.put_token_balance(
                        tid, tx.sender, self._token_balances[src_key], commit=False)

            displaced.append(tx)

        # Recompute sender nonces after rollback
        # Genesis block (index 0) txs do not consume user-facing nonces,
        # so exclude them from the max-nonce scan.
        for tx in block.transactions:
            remaining = self._txs_by_sender.get(tx.sender, [])
            if remaining:
                max_nonce = max(
                    (self.get_tx(tid).nonce for tid in remaining
                     if self.get_tx(tid) and self.get_tx_block(tid) != 0),
                    default=-1)
                self._sender_nonce[tx.sender] = max_nonce
            else:
                self._sender_nonce.pop(tx.sender, None)

        # Revert epoch if rolling back past an epoch boundary
        if EPOCH_LENGTH > 0 and block.index % EPOCH_LENGTH == 0:
            epoch_to_remove = block.index // EPOCH_LENGTH
            # Reverse epoch reward distributions (epoch_to_remove - 1 was distributed at this boundary)
            if epoch_to_remove > 0 and self._financial_active:
                dist_epoch = epoch_to_remove - 1
                dist_record = self._last_epoch_distributions.pop(dist_epoch, {})
                # Reverse delegator credits
                for delegator_addr, amount in dist_record.get("credits", []):
                    self._rollback_debit(delegator_addr, amount)
                # Reverse validator debits (re-credit validators)
                for validator_addr, amount in dist_record.get("debits", []):
                    self._credit(validator_addr, amount)
            self._epochs.pop(epoch_to_remove, None)
            if epoch_to_remove > 0:
                self._current_epoch = epoch_to_remove - 1
                self._epoch_validators = list(self._epochs.get(self._current_epoch, []))
            else:
                self._current_epoch = 0
                self._epoch_validators = []

        # Restore state trie to the previous block's snapshot
        self._state_snapshots.pop(block.index, None)
        prev_idx = block.index - 1
        if prev_idx >= 0 and prev_idx in self._state_snapshots:
            self._state_trie.restore(self._state_snapshots[prev_idx])
        else:
            # No snapshot available -- rebuild from current state
            self._rebuild_state_trie()

        # Update cached height and latest block
        self._height -= 1
        if self._height >= 0:
            if self._chain_list is not None:
                self._latest_block = self._chain_list[-1]
            elif self._store is not None:
                self._latest_block = self._store.get_block(self._height)
        else:
            self._latest_block = None

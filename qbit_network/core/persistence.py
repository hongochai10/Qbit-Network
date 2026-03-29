"""Persistence operations (save/load) for the Blockchain.

Extracted as a mixin to reduce blockchain.py size while preserving
all method signatures and self.* access patterns.
"""
import json
import os
import tempfile
import logging
from .block import Block
from .transaction import TxType
from ..config import UNBONDING_PERIOD

logger = logging.getLogger("qbit_network.chain")


class PersistenceMixin:
    """Save/load operations mixed into Blockchain."""

    def save(self):
        if self._store is not None:
            return  # SQLite handles persistence per-block
        if not self.data_dir:
            return
        if self._chain_list is None:
            return
        os.makedirs(self.data_dir, exist_ok=True)
        chain_data = [block.to_dict() for block in self._chain_list]

        target = os.path.join(self.data_dir, "chain.json")
        fd, tmp_path = tempfile.mkstemp(dir=self.data_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(chain_data, f)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        logger.info(f"Saved {self._height + 1} blocks")

    def load(self, verify_signatures: bool = True) -> bool:
        """Load chain from disk. Uses SQLite if available, falls back to chain.json.

        Args:
            verify_signatures: If True, verify TX and block signatures on load.
                Disable for fast startup when DB integrity is trusted.
        """
        if self._height >= 0:
            logger.warning("load() called on non-empty chain, skipping")
            return True
        if not self.data_dir:
            return False

        # Try SQLite first (auto-migrate from JSON if needed)
        from .store import SQLiteStore, migrate_json_to_sqlite
        migrate_json_to_sqlite(self.data_dir)
        db_file = os.path.join(self.data_dir, "chain.db")
        if os.path.exists(db_file):
            return self._load_from_sqlite(verify_signatures=verify_signatures)

        # Fall back to chain.json
        chain_file = os.path.join(self.data_dir, "chain.json")
        if not os.path.exists(chain_file):
            return False
        try:
            with open(chain_file, 'r') as f:
                chain_data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Corrupt chain file: {e}")
            return False

        if not isinstance(chain_data, list):
            logger.error("chain.json is not a JSON array")
            return False

        if len(chain_data) == 0:
            logger.warning("chain.json is empty, treating as fresh chain")
            return False

        # Validate all blocks into temp list before committing (atomic load)
        # Track validators discovered during load so later blocks can be verified
        load_validators: dict[str, bytes] = dict(self.consensus.validators)
        validated_blocks: list[Block] = []
        for i, bd in enumerate(chain_data):
            block = Block.from_dict(bd)

            if i == 0:
                if block.index != 0:
                    raise ValueError(f"Genesis block has wrong index: {block.index}")
            else:
                parent = validated_blocks[i - 1]
                if block.prev_hash != parent.block_hash:
                    raise ValueError(
                        f"Block #{block.index} prev_hash mismatch at position {i}")
                if block.index != parent.index + 1:
                    raise ValueError(
                        f"Block index gap at position {i}: "
                        f"expected {parent.index + 1}, got {block.index}")

            for tx in block.transactions:
                if not tx.verify():
                    raise ValueError(
                        f"Block #{block.index} contains tx with invalid signature: "
                        f"{tx.tx_id[:16]}...")

            if block.validator:
                pk = load_validators.get(block.validator)
                if pk:
                    if not block.verify_signature(pk):
                        raise ValueError(
                            f"Block #{block.index} has invalid validator signature")
                elif block.index > 0:
                    logger.warning(
                        f"Block #{block.index} validator {block.validator[:16]}... "
                        f"unknown -- signature not verified")

            # Track REGISTER_VALIDATOR / REVOKE_KEY txs so subsequent blocks
            # can be verified
            for tx in block.transactions:
                if tx.tx_type == TxType.REGISTER_VALIDATOR:
                    vpk_hex = tx.payload.get("validator_pubkey", "")
                    vaddr = tx.payload.get("validator_address", "")
                    if vpk_hex and vaddr:
                        load_validators[vaddr] = bytes.fromhex(vpk_hex)
                elif tx.tx_type == TxType.REVOKE_KEY:
                    if tx.payload.get("key_type") == "validator":
                        load_validators.pop(tx.sender, None)

            validated_blocks.append(block)

        # All validated -- commit to chain
        for block in validated_blocks:
            self._append_block(block)

        logger.info(f"Loaded {self._height + 1} blocks (validated from JSON)")
        return True

    def _load_from_sqlite(self, verify_signatures: bool = True) -> bool:
        """Load chain from SQLite, rebuild in-memory indices only (no in-memory block list).

        Args:
            verify_signatures: If True, verify TX and block signatures during load
                (matches JSON load path behaviour). Invalid signatures raise ValueError.
        """
        from .store import SQLiteStore
        # Close existing connection to avoid leaking (SPRINT2-006)
        if self._store is not None:
            self._store.close()
        self._store = SQLiteStore(self.data_dir)
        # SQLite mode: no in-memory chain list
        self._chain_list = None
        store_height = self._store.height()
        if store_height < 0:
            return False

        # Track validators discovered during load for block signature verification
        load_validators: dict[str, bytes] = dict(self.consensus.validators) if verify_signatures else {}

        prev_hash: str | None = None
        for i in range(store_height + 1):
            block = self._store.get_block(i)
            if block is None:
                logger.error(f"SQLite block #{i} missing")
                return False
            # Verify chain hash linkage (fast integrity check)
            if i == 0:
                if block.index != 0:
                    logger.error(f"SQLite genesis has wrong index: {block.index}")
                    return False
            else:
                if block.prev_hash != prev_hash:
                    logger.error(f"SQLite block #{i} prev_hash mismatch")
                    return False

            # Signature verification (R24-004)
            if verify_signatures:
                for tx in block.transactions:
                    if not tx.verify():
                        raise ValueError(
                            f"SQLite block #{block.index} contains tx with invalid "
                            f"signature: {tx.tx_id[:16]}...")

                if block.validator:
                    pk = load_validators.get(block.validator)
                    if pk:
                        if not block.verify_signature(pk):
                            raise ValueError(
                                f"SQLite block #{block.index} has invalid validator signature")
                    elif block.index > 0:
                        logger.warning(
                            f"SQLite block #{block.index} validator "
                            f"{block.validator[:16]}... unknown -- signature not verified")

                # Track REGISTER_VALIDATOR / REVOKE_KEY so subsequent blocks can be verified
                for tx in block.transactions:
                    if tx.tx_type == TxType.REGISTER_VALIDATOR:
                        vpk_hex = tx.payload.get("validator_pubkey", "")
                        vaddr = tx.payload.get("validator_address", "")
                        if vpk_hex and vaddr:
                            load_validators[vaddr] = bytes.fromhex(vpk_hex)
                    elif tx.tx_type == TxType.REVOKE_KEY:
                        if tx.payload.get("key_type") == "validator":
                            load_validators.pop(tx.sender, None)

            prev_hash = block.block_hash

            # Rebuild in-memory indices (without re-writing to SQLite)
            self._height = i
            self._latest_block = block
            self._block_by_hash[block.block_hash] = i
            for tx_idx, tx in enumerate(block.transactions):
                self._tx_by_id[tx.tx_id] = (i, tx_idx)
                self.consensus._chain_tx_ids.add(tx.tx_id)
                self._txs_by_sender.setdefault(tx.sender, []).append(tx.tx_id)
                if tx.recipient:
                    self._txs_by_recipient.setdefault(tx.recipient, []).append(tx.tx_id)
                # Genesis block txs do not consume a user-facing nonce slot
                if i > 0:
                    current = self._sender_nonce.get(tx.sender, -1)
                    if tx.nonce > current:
                        self._sender_nonce[tx.sender] = tx.nonce
                if tx.tx_type == TxType.NOTARIZE:
                    dh = tx.payload.get("documentHash", "")
                    if dh:
                        if dh not in self._notarizations:
                            self._notarizations[dh] = tx.tx_id
                        self._notarizations_by_hash.setdefault(dh, []).append(tx.tx_id)
                    self._notarization_count[tx.sender] = \
                        self._notarization_count.get(tx.sender, 0) + 1
                elif tx.tx_type == TxType.REGISTER_KEY:
                    epk = tx.payload.get("encryption_pk", "")
                    if epk:
                        self._key_history.setdefault(tx.sender, []).append(epk)
                        self._key_registry[tx.sender] = epk
                elif tx.tx_type == TxType.REGISTER_VALIDATOR:
                    vpk_hex = tx.payload.get("validator_pubkey", "")
                    vaddr = tx.payload.get("validator_address", "")
                    if vpk_hex and vaddr:
                        vpk_bytes = bytes.fromhex(vpk_hex)
                        self._validator_registry[vaddr] = vpk_bytes
                        self.consensus.add_validator(vaddr, vpk_bytes)
                        # Restore commission rate
                        commission = tx.payload.get("commission")
                        if isinstance(commission, int) and 0 <= commission <= 100:
                            self._validator_commission[vaddr] = commission
                elif tx.tx_type == TxType.REVOKE_KEY:
                    key_type = tx.payload.get("key_type", "")
                    reason = tx.payload.get("reason", "")
                    rev_key = f"{tx.sender}:{key_type}"
                    self._revoked_keys[rev_key] = {
                        "tx_id": tx.tx_id,
                        "timestamp": tx.timestamp,
                        "reason": reason,
                    }
                    if key_type == "validator":
                        self._validator_registry.pop(tx.sender, None)
                        self.consensus.remove_validator(tx.sender)
                elif tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
                    vaddr = tx.payload.get("validator_address", "")
                    amount = tx.payload.get("amount", 0)
                    if vaddr and amount > 0:
                        staker = tx.sender
                        self._stakes.setdefault(vaddr, {})
                        self._stakes[vaddr][staker] = self._stakes[vaddr].get(staker, 0) + amount
                        self._total_stake[vaddr] = self._total_stake.get(vaddr, 0) + amount
                elif tx.tx_type == TxType.UNSTAKE:
                    vaddr = tx.payload.get("validator_address", "")
                    amount = tx.payload.get("amount", 0)
                    if vaddr and amount > 0:
                        staker = tx.sender
                        current = self._stakes.get(vaddr, {}).get(staker, 0)
                        new_amount = max(0, current - amount)
                        if vaddr in self._stakes:
                            if new_amount == 0:
                                self._stakes[vaddr].pop(staker, None)
                            else:
                                self._stakes[vaddr][staker] = new_amount
                        self._total_stake[vaddr] = max(0, self._total_stake.get(vaddr, 0) - amount)
                        release_block = i + UNBONDING_PERIOD
                        if release_block > store_height:
                            self._unbonding.append({
                                "staker": staker,
                                "validator": vaddr,
                                "amount": amount,
                                "release_block": release_block,
                            })

        # Also load genesis validator from SQLite validator_registry table
        if self._store is not None:
            for vaddr, vpk_hex in self._store.get_all_validators():
                if vaddr not in self._validator_registry:
                    vpk_bytes = bytes.fromhex(vpk_hex)
                    self._validator_registry[vaddr] = vpk_bytes
                    self.consensus.add_validator(vaddr, vpk_bytes)
            # Load stakes from SQLite (covers stakes persisted but not yet replayed from txs)
            for staker, validator, amount in self._store.get_all_stakes():
                if validator not in self._stakes or staker not in self._stakes.get(validator, {}):
                    self._stakes.setdefault(validator, {})[staker] = amount
                    self._total_stake[validator] = self._total_stake.get(validator, 0) + amount

            # Load epochs from SQLite
            for epoch_num, block_start, validators_json in self._store.get_all_epochs():
                validators = json.loads(validators_json)
                # Convert to list of tuples
                epoch_vals = [(v[0], v[1]) for v in validators]
                self._epochs[epoch_num] = epoch_vals
                if epoch_num >= self._current_epoch:
                    self._current_epoch = epoch_num
                    self._epoch_validators = list(epoch_vals)

            # Load slashing events from SQLite
            for event in self._store.get_slashing_events():
                self._slashing_events.append(event)
                self._slashed_validators.add(event["validator"])
                self._processed_evidence.add(event["validator"])

            # Load balances from SQLite
            for addr, amount in self._store.get_all_balances():
                self._balances[addr] = amount
            self._total_minted = self._store.get_supply("total_minted")
            self._total_burned = self._store.get_supply("total_burned")
            if self._total_minted > 0:
                self._financial_active = True

        # Rebuild events indices from SQLite receipts (R19-SEC-002)
        if self._store is not None:
            for i in range(store_height + 1):
                receipts = self._store.get_receipts_for_block(i)
                for receipt in receipts:
                    self._receipts[receipt.tx_id] = receipt
                    for ev in receipt.events:
                        ev_type = ev.get("type", "")
                        if ev_type:
                            self._events_by_type.setdefault(ev_type, []).append(receipt.tx_id)
                    self._events_by_block.setdefault(i, []).append(receipt.tx_id)

        # Load token state from SQLite
        if self._store is not None:
            for token_meta in self._store.get_all_tokens():
                tid = token_meta["token_id"]
                self._token_registry[tid] = token_meta
                self._token_by_symbol[token_meta["symbol"]] = tid
                # Load holders for this token
                for addr, amount in self._store.get_token_holders(tid):
                    self._token_balances[(tid, addr)] = amount

        # Rebuild state trie from loaded balances and nonces (R19-SEC-001)
        self._rebuild_state_trie()

        verified_msg = " (signatures verified)" if verify_signatures else " (signature verification skipped)"
        logger.info(f"Loaded {self._height + 1} blocks from SQLite{verified_msg}")
        return True

"""Receipt generation, event queries, and finality tracking for the Blockchain.

Extracted as a mixin to reduce blockchain.py size while preserving
all method signatures and self.* access patterns.
"""
from ..config import SLASH_PERCENTAGE
from .transaction import Transaction, TxType
from .receipt import TransactionReceipt, build_event


class ReceiptMixin:
    """Receipt, event, and finality operations mixed into Blockchain."""

    def _build_tx_events(self, tx: Transaction, block_index: int) -> list[dict]:
        """Build event list for a transaction based on its type."""
        events: list[dict] = []
        tt = tx.tx_type

        if tt == TxType.TRANSFER:
            events.append(build_event(
                "Transfer",
                sender=tx.sender, recipient=tx.recipient,
                amount=tx.payload.get("amount", 0)))

        elif tt == TxType.NOTARIZE:
            events.append(build_event(
                "Notarize",
                sender=tx.sender,
                documentHash=tx.payload.get("documentHash", "")))

        elif tt == TxType.STORE:
            events.append(build_event(
                "Store",
                sender=tx.sender,
                documentHash=tx.payload.get("documentHash", ""),
                cid=tx.payload.get("cid", "")))

        elif tt == TxType.SHARE:
            events.append(build_event(
                "Share",
                sender=tx.sender,
                recipient=tx.recipient))

        elif tt == TxType.STAKE:
            events.append(build_event(
                "Stake",
                staker=tx.sender,
                validator=tx.payload.get("validator_address", ""),
                amount=tx.payload.get("amount", 0)))

        elif tt == TxType.DELEGATE:
            events.append(build_event(
                "Delegate",
                delegator=tx.sender,
                validator=tx.payload.get("validator_address", ""),
                amount=tx.payload.get("amount", 0)))

        elif tt == TxType.UNSTAKE:
            events.append(build_event(
                "Unstake",
                staker=tx.sender,
                validator=tx.payload.get("validator_address", ""),
                amount=tx.payload.get("amount", 0)))

        elif tt == TxType.REGISTER_KEY:
            events.append(build_event(
                "KeyRegistered",
                address=tx.sender))

        elif tt == TxType.REGISTER_VALIDATOR:
            events.append(build_event(
                "ValidatorRegistered",
                address=tx.payload.get("validator_address", tx.sender)))

        elif tt == TxType.REVOKE_KEY:
            events.append(build_event(
                "KeyRevoked",
                address=tx.sender,
                key_type=tx.payload.get("key_type", "")))

        elif tt == TxType.EVIDENCE:
            vaddr = tx.payload.get("validator_address", "")
            total = self._total_stake.get(vaddr, 0)
            # Compute approximate slash amount (same logic as _process_evidence_tx)
            slash_amount = (total * SLASH_PERCENTAGE) // 100
            if slash_amount <= 0 and total > 0:
                slash_amount = 1
            events.append(build_event(
                "Slashed",
                validator=vaddr,
                amount=slash_amount))

        elif tt == TxType.ISSUE_TOKEN:
            # Look up the token_id that was just created (deterministic derivation)
            from ..crypto import sha3_256 as _sha3
            symbol = tx.payload.get("symbol", "")
            token_id = _sha3(
                (tx.sender + symbol + str(tx.nonce)).encode()
            ).hex()[:32]
            events.append(build_event(
                "TokenIssued",
                token_id=token_id,
                symbol=symbol,
                name=tx.payload.get("name", ""),
                issuer=tx.sender,
                max_supply=tx.payload.get("max_supply", 0)))

        elif tt == TxType.MINT_TOKEN:
            tid = tx.payload.get("token_id", "")
            reg = getattr(self, '_token_registry', {}).get(tid, {})
            events.append(build_event(
                "TokenMinted",
                token_id=tid,
                amount=tx.payload.get("amount", 0),
                recipient=tx.recipient,
                total_minted=reg.get("total_minted", 0)))

        elif tt == TxType.TRANSFER_TOKEN:
            events.append(build_event(
                "TokenTransferred",
                token_id=tx.payload.get("token_id", ""),
                amount=tx.payload.get("amount", 0),
                sender=tx.sender,
                recipient=tx.recipient))

        return events

    def _update_finality(self, new_block_index: int):
        """Check if any unfinalized blocks can now be finalized.

        A block is finalized when subsequent blocks whose validators represent
        >2/3 of total stake have been built on top of it.
        """
        if not self._financial_active:
            return
        total_stake = sum(self._total_stake.values())
        if total_stake == 0:
            return

        # Walk backward from the new block, accumulating unique validator stake
        seen_validators: set[str] = set()
        cumulative_stake = 0
        for block_idx in range(new_block_index, max(self._finalized_height, -1), -1):
            block = self._get_block_by_index(block_idx)
            if not block:
                break
            v = block.validator
            if v not in seen_validators:
                seen_validators.add(v)
                v_stake = self._total_stake.get(v, 0)
                cumulative_stake += v_stake
            if cumulative_stake * 3 > total_stake * 2:  # >2/3
                # All blocks up to this point are finalized
                new_finalized = block_idx
                if new_finalized > self._finalized_height:
                    self._finalized_height = new_finalized
                break

    def get_finalized_height(self) -> int:
        """Return the height of the latest finalized block."""
        return self._finalized_height

    def get_receipt(self, tx_id: str) -> TransactionReceipt | None:
        """Look up a receipt by tx_id."""
        receipt = self._receipts.get(tx_id)
        if receipt:
            return receipt
        # Fallback to SQLite
        if self._store is not None:
            return self._store.get_receipt(tx_id)
        return None

    def get_events(self, event_type: str = "", block_index: int | None = None,
                   sender: str = "", limit: int = 20) -> list[dict]:
        """Query events with optional filters."""
        results: list[dict] = []
        limit = max(1, min(limit, 100))

        # Determine candidate tx_ids based on filters
        if event_type and event_type in self._events_by_type:
            candidate_tx_ids = self._events_by_type[event_type]
        elif block_index is not None and block_index in self._events_by_block:
            candidate_tx_ids = self._events_by_block[block_index]
        else:
            # All receipts
            candidate_tx_ids = list(self._receipts.keys())

        for tx_id in candidate_tx_ids:
            if len(results) >= limit:
                break
            receipt = self._receipts.get(tx_id)
            if not receipt:
                continue
            if block_index is not None and receipt.block_index != block_index:
                continue
            for ev in receipt.events:
                if len(results) >= limit:
                    break
                if event_type and ev.get("type") != event_type:
                    continue
                if sender:
                    # Check if sender matches any address field in the event
                    ev_sender = ev.get("sender", ev.get("staker",
                                 ev.get("delegator", ev.get("address", ""))))
                    if ev_sender != sender:
                        continue
                results.append({
                    "tx_id": tx_id,
                    "block_index": receipt.block_index,
                    "event": ev,
                })

        return results

    def get_block_level_events(self, block_index: int) -> list[dict]:
        """Return block-level events (BlockReward, EpochTransition) for a block."""
        ble = getattr(self, '_block_level_events', {})
        return ble.get(block_index, [])

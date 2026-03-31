"""Read-only query methods for the Blockchain.

Extracted as a mixin to reduce blockchain.py size while preserving
all method signatures and self.* access patterns.
"""
import time
from .transaction import TxType


class QueryMixin:
    """Read-only query methods mixed into Blockchain."""

    def get_encryption_pk(self, address: str) -> str | None:
        """Lookup on-chain registered encryption public key."""
        return self._key_registry.get(address)

    def get_validator_pk(self, address: str) -> bytes | None:
        """Lookup on-chain registered validator signing public key."""
        return self._validator_registry.get(address)

    def is_registered_validator(self, address: str) -> bool:
        """Check if an address has registered as a validator on-chain."""
        return address in self._validator_registry

    def is_key_revoked(self, address: str, key_type: str) -> bool:
        """Check if a key has been revoked on-chain."""
        return f"{address}:{key_type}" in self._revoked_keys

    def get_revocation_info(self, address: str, key_type: str) -> dict | None:
        """Get revocation details, or None if not revoked."""
        return self._revoked_keys.get(f"{address}:{key_type}")

    def get_notarization_count(self, address: str) -> int:
        """O(1) notarization count for an address."""
        return self._notarization_count.get(address, 0)

    def get_block(self, index_or_hash) -> 'Block | None':
        if isinstance(index_or_hash, int):
            return self._get_block_by_index(index_or_hash)
        elif isinstance(index_or_hash, str):
            idx = self._block_by_hash.get(index_or_hash)
            if idx is not None:
                return self._get_block_by_index(idx)
        return None

    def get_tx(self, tx_id: str) -> 'Transaction | None':
        loc = self._tx_by_id.get(tx_id)
        if loc is not None:
            block_idx, tx_idx = loc
            block = self._get_block_by_index(block_idx)
            if block is not None and tx_idx < len(block.transactions):
                return block.transactions[tx_idx]
        return None

    def get_tx_block(self, tx_id: str) -> int | None:
        loc = self._tx_by_id.get(tx_id)
        return loc[0] if loc else None

    def get_txs_by_sender(self, address: str) -> list[str]:
        return self._txs_by_sender.get(address, [])

    def get_txs_by_sender_count(self, address: str) -> int:
        return len(self._txs_by_sender.get(address, []))

    def get_txs_by_sender_page(self, address: str, offset: int, limit: int) -> list[str]:
        ids = self._txs_by_sender.get(address, [])
        return ids[offset:offset + limit]

    def get_txs_by_recipient(self, address: str) -> list[str]:
        return self._txs_by_recipient.get(address, [])

    def get_txs_by_recipient_count(self, address: str) -> int:
        return len(self._txs_by_recipient.get(address, []))

    def verify_document(self, document_hash: str) -> dict | None:
        tx_id = self._notarizations.get(document_hash)
        if not tx_id:
            return None
        tx = self.get_tx(tx_id)
        block_idx = self.get_tx_block(tx_id)
        if not tx or block_idx is None:
            return None
        block = self._get_block_by_index(block_idx)
        if block is None:
            return None
        return {
            "tx_id": tx_id,
            "block_index": block_idx,
            "block_hash": block.block_hash,
            "timestamp": tx.timestamp,
            "sender": tx.sender,
        }

    def get_all_notarizations(self, document_hash: str) -> list[dict]:
        """Get ALL notarizations for a document hash -- O(K) via reverse index."""
        results = []
        for tid in self._notarizations_by_hash.get(document_hash, []):
            tx = self.get_tx(tid)
            if tx:
                results.append({
                    "tx_id": tid,
                    "block_index": self.get_tx_block(tid),
                    "timestamp": tx.timestamp,
                    "sender": tx.sender,
                })
        return sorted(results, key=lambda r: r["timestamp"])

    def get_shared_with(self, address: str) -> list[dict]:
        """Get active (non-expired) SHARE transactions for an address."""
        now = int(time.time())
        result = []
        for tx_id in self.get_txs_by_recipient(address):
            tx = self.get_tx(tx_id)
            if tx and tx.tx_type == TxType.SHARE:
                expires = tx.payload.get("expires", 0)
                if expires == 0 or expires > now:
                    result.append(tx.to_dict())
        return result

    # ---- Token queries ----

    def get_token_info(self, token_id: str) -> dict | None:
        """Get token metadata by ID."""
        return self._token_registry.get(token_id)

    def get_token_by_symbol(self, symbol: str) -> dict | None:
        """Get token metadata by symbol."""
        tid = self._token_by_symbol.get(symbol)
        if tid:
            return self._token_registry.get(tid)
        return None

    def list_tokens(self, page: int = 1, limit: int = 50) -> list[dict]:
        """List all tokens with pagination."""
        tokens = list(self._token_registry.values())
        start = (page - 1) * limit
        return tokens[start:start + limit]

    def get_token_balance(self, token_id: str, address: str) -> int:
        """Get token balance for an address."""
        return self._token_balances.get((token_id, address), 0)

    def get_token_holders(self, token_id: str, page: int = 1,
                          limit: int = 100) -> list[dict]:
        """Get holders of a token with pagination (O(k) where k = holder count)."""
        page = max(1, page)
        limit = max(1, min(limit, 100))
        addrs = self._holders_by_token.get(token_id)
        if not addrs:
            return []
        holders = []
        for addr in addrs:
            amount = self._token_balances.get((token_id, addr), 0)
            if amount > 0:
                holders.append({"address": addr, "amount": amount})
        holders.sort(key=lambda h: h["amount"], reverse=True)
        start = (page - 1) * limit
        return holders[start:start + limit]

    def get_address_tokens(self, address: str, page: int = 1,
                           limit: int = 100) -> list[dict]:
        """Get all token balances for an address with pagination (O(k) where k = token count)."""
        page = max(1, page)
        limit = max(1, min(limit, 100))
        tids = self._tokens_by_address.get(address)
        if not tids:
            return []
        tokens = []
        for tid in tids:
            amount = self._token_balances.get((tid, address), 0)
            if amount > 0:
                reg = self._token_registry.get(tid, {})
                tokens.append({
                    "token_id": tid,
                    "symbol": reg.get("symbol", ""),
                    "name": reg.get("name", ""),
                    "decimals": reg.get("decimals", 0),
                    "amount": amount,
                })
        start = (page - 1) * limit
        return tokens[start:start + limit]

    # ---- Light client queries ----

    def get_block_headers(self, start: int = 0, count: int = 100) -> list[dict]:
        """Return block headers (no transactions) for a range."""
        count = min(count, 100)  # cap at 100 per request
        headers = []
        end = min(start + count, self._height + 1)
        for i in range(max(0, start), end):
            block = self._get_block_by_index(i)
            if block is not None:
                headers.append(block.to_header_dict())
        return headers

    def get_receipt_proof(self, tx_id: str) -> dict | None:
        """Generate a receipt Merkle inclusion proof for a confirmed TX."""
        from .receipt import receipt_proof as _receipt_proof
        receipt = self._receipts.get(tx_id)
        if receipt is None:
            return None
        block = self._get_block_by_index(receipt.block_index)
        if block is None:
            return None
        # Rebuild receipts for the block to generate proof
        block_receipts = []
        for tx in block.transactions:
            r = self._receipts.get(tx.tx_id)
            if r is not None:
                block_receipts.append(r)
        proof = _receipt_proof(block_receipts, receipt.tx_index)
        return {
            "tx_id": tx_id,
            "receipt": receipt.to_dict(),
            "proof": [(h.hex(), is_right) for h, is_right in proof],
            "receiptsRoot": block.receipts_root,
            "block_index": receipt.block_index,
        }

    def get_state_proof_at_block(self, key: str, block_index: int | None = None) -> dict | None:
        """Generate a state proof for a key, optionally at a specific block.

        Uses state snapshots when available (within PRUNING_RETENTION).
        Falls back to current state if no snapshot exists.
        """
        from .state_tree import StateTrie
        if block_index is not None:
            snapshot = self._state_snapshots.get(block_index)
            if snapshot is None:
                return None
            # Build a temporary trie from the snapshot
            temp_trie = StateTrie()
            temp_trie.restore(snapshot)
            proof = temp_trie.get_proof(key)
            if proof is not None:
                proof["block_index"] = block_index
            return proof
        # Current state
        proof = self._state_trie.get_proof(key)
        if proof is not None:
            proof["block_index"] = self._height
        return proof

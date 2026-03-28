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

    def get_txs_by_recipient(self, address: str) -> list[str]:
        return self._txs_by_recipient.get(address, [])

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

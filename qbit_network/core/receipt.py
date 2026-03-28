"""Transaction receipt and event system for QBit Network.

Each processed transaction generates a receipt recording its execution result,
including status, fee paid, and events emitted during processing.
"""
import json
from ..crypto import sha3_256


class TransactionReceipt:
    """Immutable record of a transaction's execution result."""

    __slots__ = ('tx_id', 'status', 'fee_paid', 'block_index', 'tx_index', 'events')

    def __init__(self, tx_id: str, status: str, fee_paid: int,
                 block_index: int, tx_index: int, events: list[dict]):
        if not isinstance(tx_id, str) or not tx_id:
            raise ValueError("tx_id must be a non-empty string")
        if status not in ("success", "failure"):
            raise ValueError("status must be 'success' or 'failure'")
        if not isinstance(fee_paid, int) or fee_paid < 0:
            raise ValueError("fee_paid must be a non-negative integer")
        if not isinstance(block_index, int) or block_index < 0:
            raise ValueError("block_index must be a non-negative integer")
        if not isinstance(tx_index, int) or tx_index < 0:
            raise ValueError("tx_index must be a non-negative integer")
        if not isinstance(events, list):
            raise ValueError("events must be a list")

        self.tx_id = tx_id
        self.status = status
        self.fee_paid = fee_paid
        self.block_index = block_index
        self.tx_index = tx_index
        self.events = events

    @property
    def receipt_hash(self) -> str:
        """SHA3-256 hash of the receipt for Merkle tree."""
        obj = {
            "blockIndex": self.block_index,
            "events": self.events,
            "feePaid": self.fee_paid,
            "status": self.status,
            "txId": self.tx_id,
            "txIndex": self.tx_index,
        }
        data = json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()
        return sha3_256(data).hex()

    def to_dict(self) -> dict:
        return {
            "tx_id": self.tx_id,
            "status": self.status,
            "fee_paid": self.fee_paid,
            "block_index": self.block_index,
            "tx_index": self.tx_index,
            "events": self.events,
            "receipt_hash": self.receipt_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TransactionReceipt':
        if not isinstance(data, dict):
            raise ValueError("receipt data must be a dict")
        return cls(
            tx_id=data["tx_id"],
            status=data["status"],
            fee_paid=data["fee_paid"],
            block_index=data["block_index"],
            tx_index=data["tx_index"],
            events=data.get("events", []),
        )


def receipts_root(receipts: list[TransactionReceipt]) -> str:
    """Compute Merkle root of receipt hashes.

    Uses the same SHA3-256 binary Merkle tree as the transaction Merkle root.
    Returns the hex-encoded root hash, or a zero hash for empty receipts.
    """
    from ..crypto import merkle_root as _merkle_root

    if not receipts:
        return "0" * 64

    leaves = [bytes.fromhex(r.receipt_hash) for r in receipts]
    return _merkle_root(leaves).hex()


def receipt_proof(receipts: list[TransactionReceipt], tx_index: int) -> list[tuple[bytes, bool]]:
    """Generate a Merkle inclusion proof for a receipt at tx_index."""
    from ..crypto import merkle_proof as _merkle_proof
    if not receipts or tx_index < 0 or tx_index >= len(receipts):
        return []
    leaves = [bytes.fromhex(r.receipt_hash) for r in receipts]
    return _merkle_proof(leaves, tx_index)


def verify_receipt_proof(receipt_hash: str, proof: list[tuple[bytes, bool]],
                         root: str) -> bool:
    """Verify a receipt Merkle inclusion proof."""
    from ..crypto import verify_merkle_proof as _verify
    return _verify(bytes.fromhex(receipt_hash), proof, bytes.fromhex(root))


def build_event(event_type: str, **kwargs) -> dict:
    """Construct a typed event dict."""
    event = {"type": event_type}
    event.update(kwargs)
    return event

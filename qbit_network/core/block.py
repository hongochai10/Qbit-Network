"""QVault block structure with Merkle tree."""
import json
import time
from ..crypto import MLDSA, sha3_256, merkle_root, merkle_proof, verify_merkle_proof
from .transaction import Transaction


class Block:
    """A block in the QVault chain."""

    __slots__ = (
        'index', 'prev_hash', 'transactions', 'validator',
        'timestamp', 'signature', 'merkle_root', 'base_fee',
        'state_root', 'receipts_root',
        '_cached_header', '_cached_hash',
    )

    def __init__(self, index: int, prev_hash: str, transactions: list[Transaction],
                 validator: str = "", timestamp: int = None,
                 signature: bytes = b'', base_fee: int = 0,
                 state_root: str = "", receipts_root: str = ""):
        self.index = index
        self.prev_hash = prev_hash
        self.transactions = transactions
        self.validator = validator
        self.timestamp = timestamp if timestamp is not None else int(time.time())
        self.signature = signature
        self.base_fee = base_fee
        self.state_root = state_root
        self.receipts_root = receipts_root
        self._cached_header: bytes | None = None
        self._cached_hash: str | None = None

        tx_hashes = [bytes.fromhex(tx.tx_id) for tx in transactions]
        self.merkle_root = merkle_root(tx_hashes).hex()

    def _header_bytes(self) -> bytes:
        if self._cached_header is None:
            obj = {
                "baseFee": self.base_fee,
                "index": self.index,
                "merkleRoot": self.merkle_root,
                "prevHash": self.prev_hash,
                "timestamp": self.timestamp,
                "txCount": len(self.transactions),
                "validator": self.validator,
            }
            # Include stateRoot/receiptsRoot only when present so that
            # pre-existing blocks produce the same hash as before.
            if self.receipts_root:
                obj["receiptsRoot"] = self.receipts_root
            if self.state_root:
                obj["stateRoot"] = self.state_root
            self._cached_header = json.dumps(
                obj, sort_keys=True, separators=(',', ':')).encode()
        return self._cached_header

    @property
    def block_hash(self) -> str:
        if self._cached_hash is None:
            self._cached_hash = sha3_256(self._header_bytes()).hex()
        return self._cached_hash

    def sign(self, signing_sk: bytes):
        self.signature = MLDSA.sign(signing_sk, self._header_bytes())

    def verify_signature(self, signing_pk: bytes) -> bool:
        return MLDSA.verify(signing_pk, self._header_bytes(), self.signature)

    def get_tx_proof(self, tx_index: int) -> list[tuple[bytes, bool]]:
        tx_hashes = [bytes.fromhex(tx.tx_id) for tx in self.transactions]
        return merkle_proof(tx_hashes, tx_index)

    def verify_tx_proof(self, tx_id: str, proof: list[tuple[bytes, bool]]) -> bool:
        return verify_merkle_proof(
            bytes.fromhex(tx_id), proof, bytes.fromhex(self.merkle_root))

    def to_header_dict(self) -> dict:
        """Return block header without transactions (for light clients)."""
        return {
            "hash": self.block_hash,
            "index": self.index,
            "timestamp": self.timestamp,
            "prevHash": self.prev_hash,
            "merkleRoot": self.merkle_root,
            "baseFee": self.base_fee,
            "stateRoot": self.state_root,
            "receiptsRoot": self.receipts_root,
            "validator": self.validator,
            "txCount": len(self.transactions),
            "signature": self.signature.hex(),
        }

    def to_dict(self) -> dict:
        d = {
            "hash": self.block_hash,
            "index": self.index,
            "timestamp": self.timestamp,
            "prevHash": self.prev_hash,
            "merkleRoot": self.merkle_root,
            "baseFee": self.base_fee,
            "stateRoot": self.state_root,
            "receiptsRoot": self.receipts_root,
            "validator": self.validator,
            "transactions": [tx.to_dict() for tx in self.transactions],
            "signature": self.signature.hex(),
        }
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'Block':
        if not isinstance(data.get("index"), int):
            raise ValueError("block index must be int")
        if not isinstance(data.get("timestamp"), int):
            raise ValueError("block timestamp must be int")
        txs = [Transaction.from_dict(t) for t in data.get("transactions", [])]
        block = cls(
            index=data["index"],
            prev_hash=data["prevHash"],
            transactions=txs,
            validator=data.get("validator", ""),
            timestamp=data["timestamp"],
            signature=bytes.fromhex(data.get("signature", "")),
            base_fee=data.get("baseFee", 0),
            state_root=data.get("stateRoot", ""),
            receipts_root=data.get("receiptsRoot", ""),
        )
        # Verify hash integrity if claimed hash is present
        claimed = data.get("hash")
        if claimed and block.block_hash != claimed:
            raise ValueError(
                f"block hash mismatch: computed {block.block_hash[:16]}... "
                f"!= claimed {claimed[:16]}...")
        return block

    @classmethod
    def genesis(cls, validator: str,
                transactions: list['Transaction'] | None = None) -> 'Block':
        return cls(
            index=0,
            prev_hash="0" * 64,
            transactions=transactions or [],
            validator=validator,
            base_fee=0,
        )

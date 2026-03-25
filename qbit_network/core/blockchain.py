"""QVault blockchain - chain management, block production, persistence."""
import json
import os
import tempfile
import time
import logging
from .block import Block
from .transaction import Transaction, TxType
from .consensus import ProofOfAuthority
from ..config import MAX_TX_PER_BLOCK, MAX_TX_POOL_SIZE

logger = logging.getLogger("qbit_network.chain")


class Blockchain:
    """The QVault blockchain."""

    def __init__(self, data_dir: str = ""):
        self.data_dir = data_dir
        self.chain: list[Block] = []
        self.tx_pool: list[Transaction] = []
        self._pool_ids: set[str] = set()

        # Indices
        self._block_by_hash: dict[str, int] = {}
        self._tx_by_id: dict[str, tuple[int, int]] = {}
        self._txs_by_sender: dict[str, list[str]] = {}
        self._txs_by_recipient: dict[str, list[str]] = {}
        self._notarizations: dict[str, str] = {}
        self._sender_nonce: dict[str, int] = {}
        self._key_registry: dict[str, str] = {}       # current key
        self._key_history: dict[str, list[str]] = {}  # address -> [pk_hex, ...]

        self.consensus = ProofOfAuthority()
        self.consensus._chain_nonces = self._sender_nonce
        self.consensus._chain_tx_ids = set()  # fed by _append_block

    @property
    def height(self) -> int:
        return len(self.chain) - 1 if self.chain else -1

    @property
    def latest_block(self) -> Block | None:
        return self.chain[-1] if self.chain else None

    def get_nonce(self, address: str) -> int:
        return self._sender_nonce.get(address, -1) + 1

    def get_encryption_pk(self, address: str) -> str | None:
        """Lookup on-chain registered encryption public key."""
        return self._key_registry.get(address)

    # ---- Genesis ----

    def init_chain(self, validator_address: str, validator_sk: bytes):
        if self.chain:
            return
        genesis = Block.genesis(validator_address)
        genesis.sign(validator_sk)
        self._append_block(genesis)
        logger.info(f"Genesis: {genesis.block_hash[:16]}...")

    # ---- Transaction pool ----

    def submit_tx(self, tx: Transaction) -> tuple[bool, str]:
        """Submit a signed transaction to the pool."""
        # Pool size limit (#S08)
        if len(self.tx_pool) >= MAX_TX_POOL_SIZE:
            return False, "tx pool full"

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

        if tx.tx_type == TxType.SHARE and not tx.recipient:
            return False, "SHARE tx requires recipient"

        # Nonce check
        expected_nonce = self.get_nonce(tx.sender)
        pending_from_sender = sum(1 for p in self.tx_pool if p.sender == tx.sender)
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
        return True, tx.tx_id

    # ---- Block production ----

    def produce_block(self, validator_address: str,
                      validator_sk: bytes) -> Block | None:
        if not self.chain:
            return None

        parent = self.latest_block
        txs = self.tx_pool[:MAX_TX_PER_BLOCK]

        if not txs:
            return None

        # Ensure timestamp is strictly after parent
        timestamp = max(int(time.time()), parent.timestamp + 1)

        block = Block(
            index=parent.index + 1,
            prev_hash=parent.block_hash,
            transactions=txs,
            validator=validator_address,
            timestamp=timestamp,
        )
        block.sign(validator_sk)

        # Validate own block through consensus before committing
        ok, err = self.consensus.validate_block(block, parent)
        if not ok:
            logger.error(f"Self-produced block failed validation: {err}")
            return None

        self._append_block(block)
        for tx in txs:
            self._pool_ids.discard(tx.tx_id)
        self.tx_pool = self.tx_pool[len(txs):]

        logger.info(
            f"Block #{block.index} | {block.block_hash[:16]}... | "
            f"{len(txs)} tx(s)"
        )
        return block

    # ---- Receive block from network ----

    def add_block(self, block: Block) -> tuple[bool, str]:
        if block.block_hash in self._block_by_hash:
            return False, "already have this block"

        # Block must be the next in sequence — reject out-of-order
        expected_index = len(self.chain)
        if block.index != expected_index:
            return False, (f"out-of-order block: expected index {expected_index}, "
                           f"got {block.index}")

        parent = self.chain[block.index - 1] if block.index > 0 else None
        ok, err = self.consensus.validate_block(block, parent)
        if not ok:
            return False, err

        self._append_block(block)

        mined_ids = {tx.tx_id for tx in block.transactions}
        self.tx_pool = [tx for tx in self.tx_pool if tx.tx_id not in mined_ids]
        self._pool_ids -= mined_ids

        return True, ""

    # ---- Internal ----

    def _append_block(self, block: Block):
        idx = len(self.chain)
        self.chain.append(block)
        self._block_by_hash[block.block_hash] = idx

        for tx_idx, tx in enumerate(block.transactions):
            self._tx_by_id[tx.tx_id] = (idx, tx_idx)
            self.consensus._chain_tx_ids.add(tx.tx_id)
            self._txs_by_sender.setdefault(tx.sender, []).append(tx.tx_id)
            if tx.recipient:
                self._txs_by_recipient.setdefault(tx.recipient, []).append(tx.tx_id)

            current = self._sender_nonce.get(tx.sender, -1)
            if tx.nonce > current:
                self._sender_nonce[tx.sender] = tx.nonce

            if tx.tx_type == TxType.NOTARIZE:
                doc_hash = tx.payload.get("documentHash", "")
                if doc_hash:
                    # Keep first notarization (immutable proof of earliest claim)
                    if doc_hash not in self._notarizations:
                        self._notarizations[doc_hash] = tx.tx_id

            elif tx.tx_type == TxType.REGISTER_KEY:
                epk = tx.payload.get("encryption_pk", "")
                if epk:
                    self._key_history.setdefault(tx.sender, []).append(epk)
                    self._key_registry[tx.sender] = epk

    # ---- Queries ----

    def get_block(self, index_or_hash) -> Block | None:
        if isinstance(index_or_hash, int):
            if 0 <= index_or_hash < len(self.chain):
                return self.chain[index_or_hash]
        elif isinstance(index_or_hash, str):
            idx = self._block_by_hash.get(index_or_hash)
            if idx is not None:
                return self.chain[idx]
        return None

    def get_tx(self, tx_id: str) -> Transaction | None:
        loc = self._tx_by_id.get(tx_id)
        if loc:
            block_idx, tx_idx = loc
            return self.chain[block_idx].transactions[tx_idx]
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
        block = self.chain[block_idx]
        return {
            "tx_id": tx_id,
            "block_index": block_idx,
            "block_hash": block.block_hash,
            "timestamp": tx.timestamp,
            "sender": tx.sender,
        }

    def get_all_notarizations(self, document_hash: str) -> list[dict]:
        """Get ALL notarizations for a document hash (not just the first)."""
        results = []
        for tx_id_list in self._txs_by_sender.values():
            for tid in tx_id_list:
                tx = self.get_tx(tid)
                if (tx and tx.tx_type == TxType.NOTARIZE
                        and tx.payload.get("documentHash") == document_hash):
                    block_idx = self.get_tx_block(tid)
                    results.append({
                        "tx_id": tid,
                        "block_index": block_idx,
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

    # ---- Persistence ----

    def save(self):
        if not self.data_dir:
            return
        os.makedirs(self.data_dir, exist_ok=True)
        chain_data = [block.to_dict() for block in self.chain]

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
        logger.info(f"Saved {len(self.chain)} blocks")

    def load(self) -> bool:
        """Load and validate chain from disk. Validates structure and signatures."""
        if self.chain:
            logger.warning("load() called on non-empty chain, skipping")
            return True
        if not self.data_dir:
            return False
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
                pk = self.consensus.validators.get(block.validator)
                if pk:
                    if not block.verify_signature(pk):
                        raise ValueError(
                            f"Block #{block.index} has invalid validator signature")
                elif block.index > 0:
                    # Non-genesis block from unknown validator — cannot verify sig
                    logger.warning(
                        f"Block #{block.index} validator {block.validator[:16]}... "
                        f"unknown — signature not verified")

            validated_blocks.append(block)

        # All validated — commit to chain
        for block in validated_blocks:
            self._append_block(block)

        logger.info(f"Loaded {len(self.chain)} blocks (validated)")
        return True

"""QBit Network blockchain - chain management, block production, fork resolution, persistence."""
import json
import os
import tempfile
import time
import logging
from .block import Block
from .transaction import Transaction, TxType
from .consensus import ProofOfAuthority
from ..config import MAX_TX_PER_BLOCK, MAX_TX_POOL_SIZE, MAX_REORG_DEPTH

logger = logging.getLogger("qbit_network.chain")


class Blockchain:
    """The QVault blockchain."""

    def __init__(self, data_dir: str = ""):
        self.data_dir = data_dir
        self.chain: list[Block] = []
        self.tx_pool: list[Transaction] = []
        self._pool_ids: set[str] = set()
        self._pool_sender_count: dict[str, int] = {}  # sender -> pending tx count (O(1) nonce calc)

        # Indices
        self._block_by_hash: dict[str, int] = {}
        self._tx_by_id: dict[str, tuple[int, int]] = {}
        self._txs_by_sender: dict[str, list[str]] = {}
        self._txs_by_recipient: dict[str, list[str]] = {}
        self._notarizations: dict[str, str] = {}
        self._sender_nonce: dict[str, int] = {}
        self._key_registry: dict[str, str] = {}       # current key
        self._key_history: dict[str, list[str]] = {}  # address -> [pk_hex, ...]
        self._notarizations_by_hash: dict[str, list[str]] = {}  # doc_hash -> [tx_id, ...] (all notarizations)

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

        # Nonce check — O(1) via _pool_sender_count
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
        self._drain_pool(block)

        logger.info(
            f"Block #{block.index} | {block.block_hash[:16]}... | "
            f"{len(txs)} tx(s)"
        )
        return block

    # ---- Authority scoring for fork resolution ----

    def _authority_score(self, block: Block) -> int:
        """Score 1 if block was produced by correct round-robin validator, else 0."""
        expected = self.consensus.select_validator(block.index)
        return 1 if expected and block.validator == expected else 0

    def _chain_score(self, from_index: int = 0) -> int:
        """Sum of authority scores from from_index to tip."""
        return sum(self._authority_score(self.chain[i])
                   for i in range(from_index, len(self.chain)))

    # ---- Receive block from network ----

    def add_block(self, block: Block) -> tuple[bool, str]:
        if block.block_hash in self._block_by_hash:
            return False, "already have this block"

        expected_index = len(self.chain)

        # Normal case: extends tip
        if block.index == expected_index:
            parent = self.chain[block.index - 1] if block.index > 0 else None
            ok, err = self.consensus.validate_block(block, parent)
            if not ok:
                return False, err
            self._append_block(block)
            self._drain_pool(block)
            return True, ""

        # Fork case: block at a height we already have (competing chain)
        if 0 < block.index < expected_index:
            return self._evaluate_fork(block)

        return False, (f"out-of-order block: expected index {expected_index}, "
                       f"got {block.index}")

    def try_reorg(self, fork_blocks: list[Block]) -> tuple[bool, str]:
        """Attempt reorganization with a competing chain fork.

        fork_blocks: list of blocks starting from the fork point +1.
        Returns (success, message).
        """
        if not fork_blocks:
            return False, "empty fork"

        fork_start = fork_blocks[0].index
        if fork_start == 0 or fork_start > len(self.chain):
            return False, "invalid fork start"

        depth = len(self.chain) - fork_start
        if depth > MAX_REORG_DEPTH:
            return False, f"reorg too deep: {depth} > {MAX_REORG_DEPTH}"

        # Validate fork chain links to our chain at fork_start - 1
        common_ancestor = self.chain[fork_start - 1]
        if fork_blocks[0].prev_hash != common_ancestor.block_hash:
            return False, "fork doesn't connect to our chain"

        # Compare authority scores BEFORE rollback
        our_score = sum(self._authority_score(self.chain[i])
                        for i in range(fork_start, len(self.chain)))
        fork_score = sum(
            1 if (self.consensus.select_validator(fb.index) and
                  fb.validator == self.consensus.select_validator(fb.index)) else 0
            for fb in fork_blocks)

        if fork_score < our_score:
            return False, f"fork score {fork_score} <= our score {our_score}"
        if fork_score == our_score and len(fork_blocks) <= depth:
            return False, "fork not better (same score, not longer)"

        # Rollback to common ancestor, then validate + apply fork
        # Save current chain for rollback-on-failure
        saved_chain = list(self.chain[fork_start:])
        displaced_txs = self._rollback_to(fork_start)

        # Validate and append fork blocks against the rolled-back state
        parent = self.chain[-1] if self.chain else None
        applied = []
        for fb in fork_blocks:
            ok, err = self.consensus.validate_block(fb, parent)
            if not ok:
                # Rollback any partially-applied fork blocks FIRST
                if applied:
                    self._rollback_to(fork_start)
                # Restore original chain
                for orig_block in saved_chain:
                    self._append_block(orig_block)
                return False, f"fork block #{fb.index} invalid: {err}"
            self._append_block(fb)
            applied.append(fb)
            parent = fb

        # Return displaced txs to pool (if still valid)
        mined_in_fork = set()
        for fb in fork_blocks:
            for tx in fb.transactions:
                mined_in_fork.add(tx.tx_id)

        # Return displaced txs to pool — validate nonce freshness first
        returned = 0
        for tx in displaced_txs:
            if tx.tx_id in mined_in_fork or tx.tx_id in self._tx_by_id:
                continue
            # Check nonce is still valid for current chain state
            expected_nonce = self.get_nonce(tx.sender) + \
                self._pool_sender_count.get(tx.sender, 0)
            if tx.nonce == expected_nonce:
                self.tx_pool.append(tx)
                self._pool_ids.add(tx.tx_id)
                self._pool_sender_count[tx.sender] = \
                    self._pool_sender_count.get(tx.sender, 0) + 1
                returned += 1

        logger.info(
            f"REORG: depth={depth}, fork_score={fork_score}, "
            f"our_score={our_score}, displaced={len(displaced_txs)}, "
            f"returned_to_pool={returned}")
        return True, f"reorg complete: {fork_start} → {len(self.chain) - 1}"

    def _evaluate_fork(self, block: Block) -> tuple[bool, str]:
        """Evaluate a single competing block — request full fork if promising."""
        # For single-block forks, compare authority scores directly
        our_block = self.chain[block.index]
        our_score = self._authority_score(our_block)
        fork_score = (1 if (self.consensus.select_validator(block.index) and
                           block.validator == self.consensus.select_validator(block.index))
                     else 0)

        if fork_score <= our_score:
            return False, "competing block not better than ours"

        # Single-block reorg at the tip
        if block.index == len(self.chain) - 1:
            parent = self.chain[block.index - 1]
            ok, err = self.consensus.validate_block(block, parent)
            if not ok:
                return False, f"competing block invalid: {err}"
            displaced = self._rollback_to(block.index)
            self._append_block(block)
            self._drain_pool(block)
            # Return valid displaced txs to pool
            new_tx_ids = {tx.tx_id for tx in block.transactions}
            for tx in displaced:
                if tx.tx_id not in new_tx_ids and tx.tx_id not in self._tx_by_id:
                    self.tx_pool.append(tx)
                    self._pool_ids.add(tx.tx_id)
                    self._pool_sender_count[tx.sender] = \
                        self._pool_sender_count.get(tx.sender, 0) + 1
            logger.info(f"TIP REORG: replaced block #{block.index}, "
                        f"{len(displaced)} displaced")
            return True, "tip replaced"

        return False, "multi-block fork needs try_reorg()"

    def _rollback_to(self, target_index: int) -> list[Transaction]:
        """Pop blocks from tip down to target_index (exclusive). Returns displaced txs."""
        displaced = []
        while len(self.chain) > target_index:
            block = self.chain.pop()
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

                displaced.append(tx)

            # Recompute sender nonces after rollback
            for tx in block.transactions:
                remaining = self._txs_by_sender.get(tx.sender, [])
                if remaining:
                    max_nonce = max(
                        (self.get_tx(tid).nonce for tid in remaining
                         if self.get_tx(tid)),
                        default=-1)
                    self._sender_nonce[tx.sender] = max_nonce
                else:
                    self._sender_nonce.pop(tx.sender, None)
        return displaced

    def _drain_pool(self, block: Block):
        """Remove mined transactions from pool after block append."""
        mined_ids = {tx.tx_id for tx in block.transactions}
        mined_senders: dict[str, int] = {}
        for tx in block.transactions:
            mined_senders[tx.sender] = mined_senders.get(tx.sender, 0) + 1
        self.tx_pool = [tx for tx in self.tx_pool if tx.tx_id not in mined_ids]
        self._pool_ids -= mined_ids
        for sender, cnt in mined_senders.items():
            remaining = self._pool_sender_count.get(sender, cnt) - cnt
            if remaining <= 0:
                self._pool_sender_count.pop(sender, None)
            else:
                self._pool_sender_count[sender] = remaining

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
                    if doc_hash not in self._notarizations:
                        self._notarizations[doc_hash] = tx.tx_id
                    self._notarizations_by_hash.setdefault(doc_hash, []).append(tx.tx_id)

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
        """Get ALL notarizations for a document hash — O(K) via reverse index."""
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

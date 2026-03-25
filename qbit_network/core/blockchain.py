"""QBit Network blockchain - chain management, block production, fork resolution, persistence.

Sprint 2: SQLite-primary storage. In-memory chain list removed for SQLite-backed
blockchains. In-memory mode (no data_dir) retains a list for tests/ephemeral use.
"""
import json
import os
import tempfile
import threading
import time
import logging
from .block import Block
from .transaction import Transaction, TxType
from .consensus import ProofOfAuthority
from ..config import MAX_TX_PER_BLOCK, MAX_TX_POOL_SIZE, MAX_REORG_DEPTH, CHAIN_ID, MIN_STAKE, UNBONDING_PERIOD

logger = logging.getLogger("qbit_network.chain")


class _ChainProxy:
    """Read-only proxy that makes SQLite-backed chains behave like a list.

    Supports ``len()``, ``bool()``, indexing (``[0]``, ``[-1]``), and
    iteration so that existing callers (tests, node.py, migration code)
    keep working without holding every block in memory.
    """

    __slots__ = ('_bc',)

    def __init__(self, blockchain: 'Blockchain'):
        self._bc = blockchain

    def __len__(self) -> int:
        h = self._bc._height
        return h + 1 if h >= 0 else 0

    def __bool__(self) -> bool:
        return self._bc._height >= 0

    def __getitem__(self, key):
        length = len(self)
        if isinstance(key, slice):
            start, stop, step = key.indices(length)
            return [self[i] for i in range(start, stop, step or 1)]
        if isinstance(key, int):
            if key < 0:
                key = length + key
            if 0 <= key < length:
                block = self._bc._get_block_by_index(key)
                if block is not None:
                    return block
            raise IndexError(f"block index {key} out of range")
        raise TypeError(f"indices must be integers or slices, not {type(key).__name__}")

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]


class Blockchain:
    """The QBit Network blockchain."""

    def __init__(self, data_dir: str = ""):
        self.data_dir = data_dir
        self._store = None

        # --- Storage mode ---
        # In-memory mode (no data_dir): blocks kept in _chain_list
        # SQLite mode (data_dir set): blocks only in SQLite, cached latest
        self._chain_list: list[Block] | None = None  # only used in memory mode
        self._latest_block: Block | None = None
        self._height: int = -1

        if data_dir:
            from .store import SQLiteStore
            os.makedirs(data_dir, exist_ok=True)
            self._store = SQLiteStore(data_dir)
        else:
            self._chain_list = []

        self.tx_pool: list[Transaction] = []
        self._pool_ids: set[str] = set()
        self._pool_sender_count: dict[str, int] = {}  # sender -> pending tx count (O(1) nonce calc)

        # Indices (always in-memory for fast validation)
        self._block_by_hash: dict[str, int] = {}
        self._tx_by_id: dict[str, tuple[int, int]] = {}
        self._txs_by_sender: dict[str, list[str]] = {}
        self._txs_by_recipient: dict[str, list[str]] = {}
        self._notarizations: dict[str, str] = {}
        self._sender_nonce: dict[str, int] = {}
        self._key_registry: dict[str, str] = {}       # current key
        self._key_history: dict[str, list[str]] = {}  # address -> [pk_hex, ...]
        self._notarizations_by_hash: dict[str, list[str]] = {}  # doc_hash -> [tx_id, ...] (all notarizations)
        self._notarization_count: dict[str, int] = {}  # sender_address -> count of NOTARIZE txs
        self._validator_registry: dict[str, bytes] = {}  # validator_address -> signing pubkey
        self._revoked_keys: dict[str, dict] = {}  # "address:key_type" -> {tx_id, timestamp, reason}

        # Staking / dPoS state
        self._stakes: dict[str, dict[str, int]] = {}      # validator_addr -> {staker_addr: amount}
        self._total_stake: dict[str, int] = {}             # validator_addr -> total stake
        self._unbonding: list[dict] = []                   # [{staker, validator, amount, release_block}]

        self._db_lock = threading.Lock()  # protects SQLite mutations (_append_block, _rollback_to)

        self.consensus = ProofOfAuthority()
        self.consensus._chain_nonces = self._sender_nonce
        self.consensus._chain_tx_ids = set()  # fed by _append_block
        self.consensus._revoked_keys = self._revoked_keys
        self.consensus._get_active_validators = self.get_active_validators

    # --- Backward-compatible chain property ---

    @property
    def chain(self):
        """Backward-compatible access to the chain as a list-like object.

        In-memory mode: returns the actual list.
        SQLite mode: returns a read-only proxy that fetches blocks on demand.
        """
        if self._chain_list is not None:
            return self._chain_list
        return _ChainProxy(self)

    @property
    def height(self) -> int:
        return self._height

    @property
    def latest_block(self) -> Block | None:
        return self._latest_block

    def _get_block_by_index(self, index: int) -> Block | None:
        """Internal: fetch block by index from the appropriate backend."""
        if self._chain_list is not None:
            if 0 <= index < len(self._chain_list):
                return self._chain_list[index]
            return None
        if self._store is not None:
            return self._store.get_block(index)
        return None

    def get_next_nonce(self, address: str) -> int:
        """Return the next expected nonce for an address (renamed from get_nonce, ISS-012)."""
        return self._sender_nonce.get(address, -1) + 1

    def get_nonce(self, address: str) -> int:
        """Return the next expected nonce for an address."""
        return self.get_next_nonce(address)

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

    # ---- Staking queries ----

    def get_validator_stake(self, validator_addr: str) -> int:
        """Total stake weight for a validator."""
        return self._total_stake.get(validator_addr, 0)

    def get_staker_info(self, staker: str, validator: str) -> int:
        """Specific stake amount from staker to validator."""
        return self._stakes.get(validator, {}).get(staker, 0)

    def get_active_validators(self) -> list[tuple[str, int]]:
        """Validators with stake > 0, sorted by address for determinism."""
        result = [(addr, total) for addr, total in self._total_stake.items() if total > 0]
        result.sort(key=lambda x: x[0])
        return result

    def get_all_stakes(self) -> dict[str, dict[str, int]]:
        """Return full stakes mapping (validator -> {staker: amount})."""
        return dict(self._stakes)

    # ---- Genesis ----

    def init_chain(self, validator_address: str, validator_sk: bytes,
                   validator_pk: bytes = b''):
        if self._height >= 0:
            return

        # Resolve validator public key: explicit param > consensus registry
        pk = validator_pk or self.consensus.validators.get(
            validator_address, b'')

        # Build genesis transactions -- register the genesis validator on-chain
        genesis_txs: list[Transaction] = []
        if pk:
            reg_tx = Transaction.register_validator(
                sender=validator_address,
                validator_pubkey=pk,
                validator_address=validator_address,
                nonce=0,
            )
            reg_tx.sign(validator_sk, pk)
            genesis_txs.append(reg_tx)

        genesis = Block.genesis(
            validator_address, transactions=genesis_txs)
        genesis.sign(validator_sk)
        # _append_block handles validator registration and persistence
        self._append_block(genesis)
        # Auto-stake MIN_STAKE so genesis validator can produce blocks under dPoS
        # Only when validator_pk was explicitly provided (production use)
        if validator_pk:
            self._stakes.setdefault(validator_address, {})[validator_address] = MIN_STAKE
            self._total_stake[validator_address] = MIN_STAKE
            if self._store is not None:
                self._store.put_stake(validator_address, validator_address, MIN_STAKE)
        logger.info(f"Genesis: {genesis.block_hash[:16]}...")

    # ---- Transaction pool ----

    def submit_tx(self, tx: Transaction) -> tuple[bool, str]:
        """Submit a signed transaction to the pool."""
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

        if tx.tx_type == TxType.SHARE and not tx.recipient:
            return False, "SHARE tx requires recipient"

        # STAKE / DELEGATE: target validator must be registered
        if tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
            vaddr = tx.payload.get("validator_address", "")
            if not self.is_registered_validator(vaddr):
                return False, f"validator not registered: {vaddr[:16]}..."

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
        return True, tx.tx_id

    # ---- Block production ----

    def produce_block(self, validator_address: str,
                      validator_sk: bytes) -> Block | None:
        if self._height < 0:
            return None

        # Early turn check (C-02) — skip expensive work if not our turn
        parent = self._latest_block
        expected = self.consensus.select_validator(
            parent.index + 1, parent_hash=parent.block_hash)
        if expected and expected != validator_address:
            return None  # not our turn

        # Revoked signing key cannot produce blocks (SPRINT2-014)
        if self.is_key_revoked(validator_address, "signing"):
            logger.error(f"Cannot produce block: signing key revoked for {validator_address[:16]}...")
            return None

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

    # ---- Receive block from network ----

    def add_block(self, block: Block) -> tuple[bool, str]:
        if block.block_hash in self._block_by_hash:
            return False, "already have this block"

        expected_index = self._height + 1

        # Normal case: extends tip
        if block.index == expected_index:
            parent = self._latest_block if block.index > 0 else None
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
        chain_len = self._height + 1
        if fork_start == 0 or fork_start > chain_len:
            return False, "invalid fork start"

        depth = chain_len - fork_start
        if depth > MAX_REORG_DEPTH:
            return False, f"reorg too deep: {depth} > {MAX_REORG_DEPTH}"

        # Validate fork chain links to our chain at fork_start - 1
        common_ancestor = self._get_block_by_index(fork_start - 1)
        if common_ancestor is None:
            return False, "common ancestor not found"
        if fork_blocks[0].prev_hash != common_ancestor.block_hash:
            return False, "fork doesn't connect to our chain"

        # Pure longest-chain: fork must be strictly longer
        if len(fork_blocks) <= depth:
            return False, (f"fork not longer: {len(fork_blocks)} blocks vs "
                           f"our {depth} blocks (first-seen wins on tie)")

        # Rollback to common ancestor, then validate + apply fork
        # Save current blocks for rollback-on-failure
        saved_chain = self._get_blocks_range(fork_start, chain_len)
        displaced_txs = self._rollback_to(fork_start)

        # Validate and append fork blocks against the rolled-back state
        parent = self._latest_block
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

        # Return displaced txs to pool -- sort by (sender, nonce) so lower nonces process first
        returned = 0
        displaced_txs.sort(key=lambda t: (t.sender, t.nonce))
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
            f"REORG: depth={depth}, displaced={len(displaced_txs)}, "
            f"returned_to_pool={returned}")
        return True, f"reorg complete: {fork_start} → {self._height}"

    def _evaluate_fork(self, block: Block) -> tuple[bool, str]:
        """Evaluate a single competing block at same height.
        First-seen wins -- a single block cannot replace an existing block.
        Use try_reorg() with a strictly longer chain to trigger reorganization."""
        return False, (f"first-seen wins: already have block at index {block.index}, "
                       f"use try_reorg() with a longer fork chain")

    def _get_blocks_range(self, start: int, end: int) -> list[Block]:
        """Fetch blocks [start, end) from the appropriate backend."""
        if self._chain_list is not None:
            return list(self._chain_list[start:end])
        if self._store is not None:
            return self._store.get_blocks_range(start, end)
        return []

    def _rollback_to(self, target_index: int) -> list[Transaction]:
        """Pop blocks from tip down to target_index (exclusive). Returns displaced txs."""
        if self._store is not None:
            with self._db_lock:
                return self._rollback_to_inner(target_index)
        return self._rollback_to_inner(target_index)

    def _rollback_to_inner(self, target_index: int) -> list[Transaction]:
        """Inner rollback logic -- caller must hold _db_lock when _store is set."""
        # In SQLite mode, collect blocks to roll back BEFORE deleting from SQLite
        blocks_to_rollback: list[Block] = []
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

        return displaced

    def _find_validator_pk_in_chain(self, address: str) -> bytes | None:
        """Scan chain for a REGISTER_VALIDATOR tx that registered the given
        address. Returns pubkey bytes or None."""
        for i in range(self._height + 1):
            block = self._get_block_by_index(i)
            if block is None:
                continue
            for tx in block.transactions:
                if tx.tx_type == TxType.REGISTER_VALIDATOR:
                    if tx.payload.get("validator_address") == address:
                        vpk_hex = tx.payload.get("validator_pubkey", "")
                        if vpk_hex:
                            return bytes.fromhex(vpk_hex)
        return None

    def _rollback_block(self, block: Block, displaced: list[Transaction]):
        """Rollback a single block's indices and collect displaced transactions."""
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

        # Update cached height and latest block
        self._height -= 1
        if self._height >= 0:
            if self._chain_list is not None:
                self._latest_block = self._chain_list[-1]
            elif self._store is not None:
                self._latest_block = self._store.get_block(self._height)
        else:
            self._latest_block = None

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
        if self._store is not None:
            with self._db_lock:
                self._append_block_inner(block)
        else:
            self._append_block_inner(block)

    def _append_block_inner(self, block: Block):
        idx = self._height + 1

        if self._chain_list is not None:
            self._chain_list.append(block)

        # Persist to SQLite if store available
        if self._store is not None:
            self._store.append_block(block)

        self._height = idx
        self._latest_block = block
        self._block_by_hash[block.block_hash] = idx

        for tx_idx, tx in enumerate(block.transactions):
            self._tx_by_id[tx.tx_id] = (idx, tx_idx)
            self.consensus._chain_tx_ids.add(tx.tx_id)
            self._txs_by_sender.setdefault(tx.sender, []).append(tx.tx_id)
            if tx.recipient:
                self._txs_by_recipient.setdefault(tx.recipient, []).append(tx.tx_id)

            # Genesis block (idx==0) transactions are bootstrapping ops;
            # they do not consume a user-facing nonce slot.
            if idx > 0:
                current = self._sender_nonce.get(tx.sender, -1)
                if tx.nonce > current:
                    self._sender_nonce[tx.sender] = tx.nonce

            if tx.tx_type == TxType.NOTARIZE:
                doc_hash = tx.payload.get("documentHash", "")
                if doc_hash:
                    if doc_hash not in self._notarizations:
                        self._notarizations[doc_hash] = tx.tx_id
                    self._notarizations_by_hash.setdefault(doc_hash, []).append(tx.tx_id)
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
                    # First-registration-wins: skip if already registered
                    if vaddr in self._validator_registry:
                        logger.warning(
                            f"Validator {vaddr[:16]}... already registered, "
                            f"skipping duplicate registration (block #{block.index})")
                    else:
                        vpk_bytes = bytes.fromhex(vpk_hex)
                        self._validator_registry[vaddr] = vpk_bytes
                        self.consensus.add_validator(vaddr, vpk_bytes)
                        # Persist to SQLite if available
                        if self._store is not None:
                            self._store.put_validator(vaddr, vpk_hex)
                        logger.info(
                            f"Validator registered on-chain: {vaddr[:16]}... "
                            f"(block #{block.index})")

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
                    if self._store is not None:
                        self._store.delete_validator(tx.sender)
                    logger.info(
                        f"Validator revoked: {tx.sender[:16]}... "
                        f"reason={reason} (block #{block.index})")
                elif key_type == "encryption":
                    logger.info(
                        f"Encryption key revoked: {tx.sender[:16]}... "
                        f"reason={reason} (block #{block.index})")
                elif key_type == "signing":
                    # Purge pool txs from the revoked sender (K-03)
                    revoked_addr = tx.sender
                    to_remove = [t for t in self.tx_pool if t.sender == revoked_addr]
                    for t in to_remove:
                        self.tx_pool.remove(t)
                        self._pool_ids.discard(t.tx_id)
                        self._pool_sender_count[t.sender] = max(
                            0, self._pool_sender_count.get(t.sender, 0) - 1)
                    if to_remove:
                        logger.info(
                            f"Purged {len(to_remove)} pool txs from revoked sender "
                            f"{revoked_addr[:16]}...")
                    logger.info(
                        f"Signing key revoked: {tx.sender[:16]}... "
                        f"reason={reason} (block #{block.index})")
                # Persist revocation to SQLite
                if self._store is not None:
                    self._store.put_revocation(
                        tx.sender, key_type, tx.tx_id,
                        float(tx.timestamp), reason)

            elif tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
                vaddr = tx.payload.get("validator_address", "")
                amount = tx.payload.get("amount", 0)
                if vaddr and amount > 0:
                    staker = tx.sender
                    self._stakes.setdefault(vaddr, {})
                    self._stakes[vaddr][staker] = self._stakes[vaddr].get(staker, 0) + amount
                    self._total_stake[vaddr] = self._total_stake.get(vaddr, 0) + amount
                    if self._store is not None:
                        self._store.put_stake(staker, vaddr, self._stakes[vaddr][staker])
                    logger.info(
                        f"{'Stake' if tx.tx_type == TxType.STAKE else 'Delegate'}: "
                        f"{staker[:16]}... -> {vaddr[:16]}... amount={amount} "
                        f"(block #{block.index})")

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
                    # Create unbonding entry
                    release_block = idx + UNBONDING_PERIOD
                    unbonding_entry = {
                        "staker": staker,
                        "validator": vaddr,
                        "amount": amount,
                        "release_block": release_block,
                    }
                    self._unbonding.append(unbonding_entry)
                    if self._store is not None:
                        if new_amount == 0:
                            self._store.delete_stake(staker, vaddr)
                        else:
                            self._store.put_stake(staker, vaddr, new_amount)
                        self._store.put_unbonding(staker, vaddr, amount, release_block)
                    logger.info(
                        f"Unstake: {staker[:16]}... from {vaddr[:16]}... "
                        f"amount={amount}, release_block={release_block} "
                        f"(block #{block.index})")

        # Process mature unbondings (release_block <= current block index)
        self._process_mature_unbondings(idx)

    def _process_mature_unbondings(self, current_index: int):
        """Remove unbonding entries whose release_block has been reached."""
        remaining = []
        for entry in self._unbonding:
            if entry["release_block"] <= current_index:
                # Unbonding complete -- remove from store
                if self._store is not None:
                    self._store.delete_unbonding(
                        entry["staker"], entry["validator"],
                        entry["amount"], entry["release_block"])
            else:
                remaining.append(entry)
        self._unbonding = remaining

    # ---- Queries ----

    def get_block(self, index_or_hash) -> Block | None:
        if isinstance(index_or_hash, int):
            return self._get_block_by_index(index_or_hash)
        elif isinstance(index_or_hash, str):
            idx = self._block_by_hash.get(index_or_hash)
            if idx is not None:
                return self._get_block_by_index(idx)
        return None

    def get_tx(self, tx_id: str) -> Transaction | None:
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

    # ---- Persistence ----

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

    def load(self) -> bool:
        """Load chain from disk. Uses SQLite if available, falls back to chain.json."""
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
            return self._load_from_sqlite()

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

    def _load_from_sqlite(self) -> bool:
        """Load chain from SQLite, rebuild in-memory indices only (no in-memory block list)."""
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

        logger.info(f"Loaded {self._height + 1} blocks from SQLite")
        return True

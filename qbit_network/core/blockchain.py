"""QBit Network blockchain - chain management, block production, fork resolution, persistence.

Sprint 2: SQLite-primary storage. In-memory chain list removed for SQLite-backed
blockchains. In-memory mode (no data_dir) retains a list for tests/ephemeral use.
"""
import os
import threading
import time
import logging
from .block import Block
from .transaction import Transaction, TxType
from .consensus import ProofOfAuthority
from .state_tree import StateTrie
from ..config import (MAX_TX_PER_BLOCK, MAX_TX_POOL_SIZE, MAX_REORG_DEPTH,
                      CHAIN_ID, MIN_STAKE, UNBONDING_PERIOD, EPOCH_LENGTH,
                      PRUNING_RETENTION,
                      TX_FEES, TOKEN_ACTIVATION_HEIGHT,
                      DYNAMIC_FEE_ACTIVATION_HEIGHT, INITIAL_BASE_FEE,
                      TX_WEIGHTS, MAX_BLOCK_WEIGHT)
from .fees import (compute_base_fee, compute_tx_fee, tx_weight,
                   effective_block_weight)
from .receipt import TransactionReceipt, receipts_root, build_event
from .ledger import BalanceLedgerMixin
from .staking import StakingMixin
from .query import QueryMixin
from .receipt_ops import ReceiptMixin
from .persistence import PersistenceMixin
from .rollback import RollbackMixin
from .tx_pool import TxPoolMixin
from .state_ops import StateTrieMixin

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


class Blockchain(BalanceLedgerMixin, StakingMixin, QueryMixin, ReceiptMixin,
                 PersistenceMixin, RollbackMixin, TxPoolMixin, StateTrieMixin):
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
        self._pending_debits_cache: dict[str, int] = {}  # sender -> total pending debit (O(1) balance check)

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

        # Epoch rotation state
        self._current_epoch: int = 0
        self._epoch_validators: list[tuple[str, int]] = []  # frozen validator set for current epoch
        self._epochs: dict[int, list[tuple[str, int]]] = {}  # epoch_number -> validators snapshot

        # Slashing state
        self._slashed_validators: set[str] = set()         # validators that have been slashed
        self._slashing_events: list[dict] = []             # [{validator, evidence_tx_id, amount_slashed, block_index}]
        self._processed_evidence: set[str] = set()         # validator_address values already slashed (dedup)

        # Balance ledger (integer arithmetic only -- amounts in qubits)
        self._balances: dict[str, int] = {}       # address -> balance in qubits
        self._total_minted: int = 0
        self._total_burned: int = 0
        self._financial_active: bool = False      # set by init_chain or load when genesis balance exists

        # Epoch reward distribution
        self._epoch_rewards: dict[str, int] = {}           # validator_addr -> accumulated rewards this epoch
        self._validator_commission: dict[str, int] = {}    # validator_addr -> commission percent (default 10)
        self._last_epoch_distributions: dict[int, dict] = {}  # epoch -> {"credits": [(addr, amount)], "debits": [(addr, amount)]} for rollback

        # Multi-asset token state
        self._token_registry: dict[str, dict] = {}          # token_id -> metadata
        self._token_balances: dict[tuple[str, str], int] = {}  # (token_id, address) -> amount
        self._token_by_symbol: dict[str, str] = {}           # symbol -> token_id (uniqueness)
        # Secondary indices for O(1) holder/token lookup (R28-007)
        self._holders_by_token: dict[str, set[str]] = {}   # token_id -> {address, ...}
        self._tokens_by_address: dict[str, set[str]] = {}  # address -> {token_id, ...}

        # State trie -- sorted key-value Merkle trie for state root in block header
        self._state_trie = StateTrie()
        self._state_snapshots: dict[int, dict[str, bytes]] = {}  # block_index -> trie snapshot (for rollback)

        # Receipt / event system
        self._receipts: dict[str, TransactionReceipt] = {}  # tx_id -> receipt
        self._events_by_type: dict[str, list[str]] = {}     # event_type -> [tx_id, ...]
        self._events_by_block: dict[int, list[str]] = {}    # block_index -> [tx_id, ...]
        self._block_level_events: dict[int, list[dict]] = {}  # block_index -> [BlockReward/EpochTransition events]

        # Simple finality rule
        self._finalized_height: int = -1

        # threading.Lock (not asyncio.Lock) is intentional here: SQLite operations
        # protected by this lock are synchronous and fast (sub-ms), so blocking the
        # event loop briefly is acceptable. Using asyncio.Lock would require every
        # caller (including from_dict replay, rollback, etc.) to be async, adding
        # unnecessary complexity for no practical benefit. (R15-003)
        self._db_lock = threading.Lock()

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

        # Stamp state_root and receipts_root on genesis, then re-sign.
        old_hash = genesis.block_hash
        genesis.state_root = self._state_trie.root().hex()
        genesis.receipts_root = getattr(self, '_last_computed_receipts_root', '')
        genesis._cached_header = None
        genesis._cached_hash = None
        genesis.sign(validator_sk)
        self._block_by_hash.pop(old_hash, None)
        self._block_by_hash[genesis.block_hash] = genesis.index
        if self._chain_list is not None and genesis.index < len(self._chain_list):
            self._chain_list[genesis.index] = genesis
        self._latest_block = genesis
        if self._store is not None:
            self._store.update_block(genesis)
        self.consensus.set_genesis_hash(genesis.block_hash)

        # Genesis balance allocation -- only when explicitly requested
        # (production code calls activate_financial_layer after init_chain)
        # This keeps existing tests backward-compatible.

        logger.info(f"Genesis: {genesis.block_hash[:16]}...")

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

        next_idx = parent.index + 1
        _dynamic_active = (next_idx >= DYNAMIC_FEE_ACTIVATION_HEIGHT
                           and next_idx > 0)

        if _dynamic_active:
            # Compute base_fee for this block
            _parent_pre = (parent.index == 0
                           or parent.index < DYNAMIC_FEE_ACTIVATION_HEIGHT)
            if next_idx == DYNAMIC_FEE_ACTIVATION_HEIGHT or _parent_pre:
                base_fee = INITIAL_BASE_FEE
            else:
                parent_eff_weight = effective_block_weight(
                    parent.transactions, parent.validator)
                base_fee = compute_base_fee(parent.base_fee, parent_eff_weight)

            # Filter pool: only TXs with max_fee_per_weight >= base_fee
            eligible = []
            for tx in self.tx_pool:
                w = TX_WEIGHTS.get(tx.tx_type.value, 0)
                if w == 0 or tx.max_fee_per_weight >= base_fee:
                    eligible.append(tx)

            # Group eligible TXs by sender, preserving nonce order
            from collections import defaultdict
            sender_queues: dict[str, list[Transaction]] = defaultdict(list)
            for tx in eligible:
                sender_queues[tx.sender].append(tx)
            # Sort each sender's queue by nonce (pool order should already be correct,
            # but be defensive)
            for q in sender_queues.values():
                q.sort(key=lambda t: t.nonce)

            # Compute effective priority per sender (use first TX's priority as representative)
            def _sender_priority(sender: str) -> int:
                q = sender_queues[sender]
                if not q:
                    return 0
                # Use the max effective priority across the sender's TXs
                best = 0
                for t in q:
                    w = TX_WEIGHTS.get(t.tx_type.value, 0)
                    if w > 0:
                        ep = max(0, min(t.max_priority_fee, t.max_fee_per_weight - base_fee))
                        best = max(best, ep)
                return best

            # Sort senders by descending priority
            sorted_senders = sorted(sender_queues.keys(),
                                    key=_sender_priority, reverse=True)

            # Select TXs respecting MAX_BLOCK_WEIGHT, MAX_TX_PER_BLOCK, and nonce order
            txs = []
            total_weight = 0
            # Round-robin from highest-priority senders, taking one TX at a time
            # to interleave fairly. But for simplicity and correctness, take all
            # TXs from each sender in order.
            for sender in sorted_senders:
                for tx in sender_queues[sender]:
                    if len(txs) >= MAX_TX_PER_BLOCK:
                        break
                    w = TX_WEIGHTS.get(tx.tx_type.value, 0)
                    if total_weight + w > MAX_BLOCK_WEIGHT:
                        break  # stop for this sender if next TX doesn't fit
                    txs.append(tx)
                    total_weight += w
        else:
            # Legacy: take from pool in order
            txs = self.tx_pool[:MAX_TX_PER_BLOCK]
            base_fee = 0

        # Post-activation: empty blocks are allowed
        if not txs and not _dynamic_active:
            return None

        # Ensure timestamp is strictly after parent
        timestamp = max(int(time.time()), parent.timestamp + 1)

        block = Block(
            index=next_idx,
            prev_hash=parent.block_hash,
            transactions=txs,
            validator=validator_address,
            timestamp=timestamp,
            base_fee=base_fee,
        )
        # Sign without state_root first so consensus can validate the block
        # structure (tx sigs, nonces, fees).  state_root="" is excluded from
        # the header, preserving backward-compatible validation.
        block.sign(validator_sk)

        # Validate own block through consensus before committing
        ok, err = self.consensus.validate_block(block, parent)
        if not ok:
            logger.error(f"Self-produced block failed validation: {err}")
            return None

        # _append_block processes TXs and rebuilds the state trie.
        # The block is appended with state_root="" initially.
        self._append_block(block)

        # Stamp state_root and receipts_root computed by _append_block_inner,
        # then re-sign.
        old_hash = block.block_hash
        block.state_root = getattr(self, '_last_computed_state_root', self._state_trie.root().hex())
        block.receipts_root = getattr(self, '_last_computed_receipts_root', '')
        block._cached_header = None
        block._cached_hash = None
        block.sign(validator_sk)

        # Update block hash index: remove old hash, register new one.
        self._block_by_hash.pop(old_hash, None)
        self._block_by_hash[block.block_hash] = block.index

        # Update stored block in SQLite with the new signature + hash.
        if self._store is not None:
            self._store.update_block(block)
        # Update in-memory chain list if applicable.
        if self._chain_list is not None and block.index < len(self._chain_list):
            self._chain_list[block.index] = block
        self._latest_block = block

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
            # RS-2: Received blocks MUST carry a state_root for strict validation.
            # Blocks with empty state_root bypass state integrity checks entirely,
            # allowing Byzantine validators to submit corrupted state undetected.
            if block.index > 0 and not block.state_root:
                return False, "missing state_root: received blocks must include state_root"
            try:
                self._append_block(block)
            except ValueError as exc:
                logger.warning("Block #%d rejected: %s", block.index, exc)
                return False, str(exc)
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
            # RS-2: fork blocks must also carry state_root
            if fb.index > 0 and not fb.state_root:
                if applied:
                    self._rollback_to(fork_start)
                for orig_block in saved_chain:
                    self._append_block(orig_block)
                return False, f"fork block #{fb.index} missing state_root"
            ok, err = self.consensus.validate_block(fb, parent)
            if not ok:
                # Rollback any partially-applied fork blocks FIRST
                if applied:
                    self._rollback_to(fork_start)
                # Restore original chain
                for orig_block in saved_chain:
                    self._append_block(orig_block)
                return False, f"fork block #{fb.index} invalid: {err}"
            try:
                self._append_block(fb)
            except ValueError as exc:
                # State/receipts root mismatch -- rollback and restore
                if applied:
                    self._rollback_to(fork_start)
                for orig_block in saved_chain:
                    self._append_block(orig_block)
                return False, f"fork block #{fb.index} rejected: {exc}"
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
                self._pending_debits_cache[tx.sender] = (
                    self._pending_debits_cache.get(tx.sender, 0)
                    + self._calc_tx_debit(tx))
                returned += 1

        # Rebuild cache: height changed during reorg, fee model may differ
        self._rebuild_pending_debits_cache()
        logger.info(
            f"REORG: depth={depth}, displaced={len(displaced_txs)}, "
            f"returned_to_pool={returned}")
        return True, f"reorg complete: {fork_start} → {self._height}"

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
        # Rebuild pending debits cache: height may have changed (fee model switch)
        self._rebuild_pending_debits_cache()

    # ---- Internal ----

    def _append_block(self, block: Block):
        if self._store is not None:
            with self._db_lock:
                self._append_block_inner_safe(block)
        else:
            self._append_block_inner_safe(block)

    def _update_token_index(self, token_id: str, address: str, new_balance: int):
        """Maintain _holders_by_token / _tokens_by_address after balance change."""
        if new_balance > 0:
            self._holders_by_token.setdefault(token_id, set()).add(address)
            self._tokens_by_address.setdefault(address, set()).add(token_id)
        else:
            holders = self._holders_by_token.get(token_id)
            if holders is not None:
                holders.discard(address)
                if not holders:
                    del self._holders_by_token[token_id]
            addr_tokens = self._tokens_by_address.get(address)
            if addr_tokens is not None:
                addr_tokens.discard(token_id)
                if not addr_tokens:
                    del self._tokens_by_address[address]

    def _append_block_inner_safe(self, block: Block):
        """Wrapper that rolls back partial state on failure (R23-001)."""
        try:
            self._append_block_inner(block)
        except ValueError:
            # _append_block_inner mutates state before validation checks.
            # On failure, undo all changes to prevent corrupted state.
            displaced: list = []
            self._rollback_block(block, displaced)
            # _rollback_block already decrements self._height (R34-H01 fix:
            # removed duplicate self._height -= 1 that caused double decrement).
            # Clean up chain_list / store since _rollback_block does not remove
            # the block from storage.
            if self._chain_list is not None and self._chain_list and self._chain_list[-1] is block:
                self._chain_list.pop()
            if self._store is not None:
                self._store.delete_blocks_from(block.index)
            if self._height >= 0:
                self._latest_block = self._get_block_by_index(self._height)
            else:
                self._latest_block = None
            raise

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

        # R32-F04: Process epoch transition and unbondings BEFORE block TXs
        # so that epoch reward distribution uses pre-TX balances, preventing
        # front-running via validator self-transfer in epoch-boundary blocks.
        _block_level_events: list[dict] = []

        # Collect matured stakers BEFORE processing removes them from _unbonding
        matured_stakers = {e["staker"] for e in self._unbonding
                          if e["release_block"] <= idx}

        # Process mature unbondings (release_block <= current block index)
        self._process_mature_unbondings(idx)

        # Epoch transition: snapshot validators at epoch boundaries
        epoch_before = self._current_epoch
        self._check_epoch_transition(idx)
        if self._current_epoch > epoch_before:
            _block_level_events.append(
                build_event("EpochTransition", epoch=self._current_epoch))

        # Receipt accumulator: tx_idx -> (fee_paid, events)
        _receipt_data: list[tuple[int, str, int, list[dict]]] = []

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

            # --- Fee deduction (sequential, checked against running balance) ---
            # Genesis block txs are fee-exempt (bootstrapping).
            # Financial layer only active when chain has minted supply.
            _tx_fee_paid = 0
            if idx > 0 and self._financial_active:
                _dyn = idx >= DYNAMIC_FEE_ACTIVATION_HEIGHT
                if _dyn:
                    # EIP-1559 dynamic fee: 100% to validator, 0% burned
                    w = tx_weight(tx.tx_type.value)
                    if w > 0:
                        fee = compute_tx_fee(block.base_fee,
                                             tx.max_fee_per_weight,
                                             tx.max_priority_fee, w)
                        self._debit(tx.sender, fee)
                        self._credit(block.validator, fee)
                        _tx_fee_paid = fee
                else:
                    # Legacy fixed fee: 50/50 split validator/burn
                    fee = TX_FEES.get(tx.tx_type.value, 0)
                    if fee > 0:
                        self._debit(tx.sender, fee)
                        validator_share = fee // 2
                        burn = fee - validator_share
                        self._credit(block.validator, validator_share)
                        self._total_burned += burn
                        _tx_fee_paid = fee

                # Type-specific balance changes
                if tx.tx_type == TxType.TRANSFER:
                    amount = tx.payload["amount"]
                    self._debit(tx.sender, amount)
                    self._credit(tx.recipient, amount)
                elif tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
                    amount = tx.payload.get("amount", 0)
                    if amount > 0:
                        self._debit(tx.sender, amount)

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
                        # Allow commission update on re-registration attempt
                        commission = tx.payload.get("commission")
                        if isinstance(commission, int) and 0 <= commission <= 100:
                            self._validator_commission[vaddr] = commission
                        logger.warning(
                            f"Validator {vaddr[:16]}... already registered, "
                            f"skipping duplicate registration (block #{block.index})")
                    else:
                        vpk_bytes = bytes.fromhex(vpk_hex)
                        self._validator_registry[vaddr] = vpk_bytes
                        self.consensus.add_validator(vaddr, vpk_bytes)
                        # Set commission rate if provided
                        commission = tx.payload.get("commission")
                        if isinstance(commission, int) and 0 <= commission <= 100:
                            self._validator_commission[vaddr] = commission
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
                    # R34-L02: Batch update sender count instead of per-item decrement
                    if to_remove:
                        count = self._pool_sender_count.get(revoked_addr, 0)
                        remaining = max(0, count - len(to_remove))
                        if remaining == 0:
                            self._pool_sender_count.pop(revoked_addr, None)
                        else:
                            self._pool_sender_count[revoked_addr] = remaining
                        # Clear pending debits for the revoked sender
                        self._pending_debits_cache.pop(revoked_addr, None)
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

            elif tx.tx_type == TxType.EVIDENCE:
                self._process_evidence_tx(tx, idx)

            elif tx.tx_type == TxType.ISSUE_TOKEN:
                if idx >= TOKEN_ACTIVATION_HEIGHT:
                    from ..crypto import sha3_256 as _sha3
                    symbol = tx.payload["symbol"]
                    if symbol in self._token_by_symbol:
                        raise ValueError(f"token symbol {symbol} already exists")
                    token_id = _sha3(
                        (tx.sender + symbol + str(tx.nonce)).encode()
                    ).hex()[:32]
                    # R21-004: collision check
                    if token_id in self._token_registry:
                        raise ValueError(f"token_id collision: {token_id}")
                    self._token_registry[token_id] = {
                        "issuer": tx.sender,
                        "name": tx.payload["name"],
                        "symbol": symbol,
                        "decimals": tx.payload["decimals"],
                        "max_supply": tx.payload.get("max_supply", 0),
                        "total_minted": 0,
                        "transferable": tx.payload.get("transferable", True),
                        "created_block": idx,
                        "created_tx": tx.tx_id,
                    }
                    self._token_by_symbol[symbol] = token_id
                    if self._store is not None:
                        self._store.put_token(token_id, self._token_registry[token_id])
                    logger.info(
                        f"Token issued: {symbol} (id={token_id[:16]}...) "
                        f"by {tx.sender[:16]}... (block #{idx})")

            elif tx.tx_type == TxType.MINT_TOKEN:
                if idx >= TOKEN_ACTIVATION_HEIGHT:
                    tid = tx.payload["token_id"]
                    if tid not in self._token_registry:
                        raise ValueError(f"token {tid} does not exist")
                    reg = self._token_registry[tid]
                    if reg["issuer"] != tx.sender:
                        raise ValueError(f"only issuer can mint token {tid}")
                    amount = tx.payload["amount"]
                    # R21-006: overflow protection for total_minted (8-byte state trie encoding)
                    _MAX_TOKEN_AMOUNT = 2**63 - 1
                    if reg["total_minted"] + amount > _MAX_TOKEN_AMOUNT:
                        raise ValueError(
                            f"minting {amount} would overflow total_minted")
                    if reg["max_supply"] > 0 and reg["total_minted"] + amount > reg["max_supply"]:
                        raise ValueError(
                            f"minting {amount} would exceed max_supply "
                            f"{reg['max_supply']} (minted: {reg['total_minted']})")
                    reg["total_minted"] += amount
                    key = (tid, tx.recipient)
                    self._token_balances[key] = self._token_balances.get(key, 0) + amount
                    self._update_token_index(tid, tx.recipient, self._token_balances[key])
                    if self._store is not None:
                        self._store.put_token(tid, reg)
                        self._store.put_token_balance(tid, tx.recipient,
                                                      self._token_balances[key])
                    logger.info(
                        f"Token minted: {amount} of {reg['symbol']} to "
                        f"{tx.recipient[:16]}... (block #{idx})")

            elif tx.tx_type == TxType.TRANSFER_TOKEN:
                if idx >= TOKEN_ACTIVATION_HEIGHT:
                    tid = tx.payload["token_id"]
                    if tid not in self._token_registry:
                        raise ValueError(f"token {tid} does not exist")
                    reg = self._token_registry[tid]
                    if not reg.get("transferable", True):
                        raise ValueError(f"token {tid} is not transferable")
                    amount = tx.payload["amount"]
                    src_key = (tid, tx.sender)
                    src_bal = self._token_balances.get(src_key, 0)
                    if src_bal < amount:
                        raise ValueError(
                            f"insufficient token balance: {src_bal} < {amount}")
                    self._token_balances[src_key] = src_bal - amount
                    if self._token_balances[src_key] == 0:
                        del self._token_balances[src_key]
                    self._update_token_index(tid, tx.sender, self._token_balances.get(src_key, 0))
                    dst_key = (tid, tx.recipient)
                    self._token_balances[dst_key] = self._token_balances.get(dst_key, 0) + amount
                    self._update_token_index(tid, tx.recipient, self._token_balances[dst_key])
                    if self._store is not None:
                        self._store.put_token_balance(
                            tid, tx.sender, self._token_balances.get(src_key, 0))
                        self._store.put_token_balance(
                            tid, tx.recipient, self._token_balances[dst_key])
                    logger.info(
                        f"Token transfer: {amount} of {reg['symbol']} "
                        f"{tx.sender[:16]}... -> {tx.recipient[:16]}... (block #{idx})")

            # --- Build events for receipt ---
            _tx_events = self._build_tx_events(tx, idx)
            _receipt_data.append((tx_idx, tx.tx_id, _tx_fee_paid, _tx_events))

        # --- Generate receipts for all TXs in block ---
        block_receipts: list[TransactionReceipt] = []
        for tx_idx_r, tx_id_r, fee_r, events_r in _receipt_data:
            receipt = TransactionReceipt(
                tx_id=tx_id_r,
                status="success",
                fee_paid=fee_r,
                block_index=idx,
                tx_index=tx_idx_r,
                events=events_r,
            )
            block_receipts.append(receipt)
            self._receipts[tx_id_r] = receipt
            # Index events by type and block
            for ev in events_r:
                ev_type = ev.get("type", "")
                if ev_type:
                    self._events_by_type.setdefault(ev_type, []).append(tx_id_r)
            self._events_by_block.setdefault(idx, []).append(tx_id_r)

        # Apply block reward (MINT is implicit -- not a user tx)
        # Only active when chain has minted supply (financial layer active)
        if idx > 0 and self._financial_active:
            reward = self._calc_block_reward(idx)
            if reward > 0:
                self._credit(block.validator, reward)
                self._total_minted += reward
                _block_level_events.append(
                    build_event("BlockReward", validator=block.validator, amount=reward))
                # Track reward for epoch distribution to delegators
                self._epoch_rewards[block.validator] = (
                    self._epoch_rewards.get(block.validator, 0) + reward
                )

        # Store block-level events (not tied to any specific TX)
        self._block_level_events[idx] = _block_level_events
        # Prune old block-level events beyond MAX_REORG_DEPTH (R19-SEC-005)
        ble_prune_before = idx - MAX_REORG_DEPTH - 1
        if ble_prune_before >= 0:
            self._block_level_events.pop(ble_prune_before, None)

        # --- Compute receiptsRoot from block receipts ---
        computed_receipts_root = receipts_root(block_receipts)
        # Cache the computed receipts root for produce_block to stamp later.
        self._last_computed_receipts_root = computed_receipts_root

        if block.receipts_root and block.receipts_root != computed_receipts_root:
            raise ValueError(
                f"Block #{idx} receiptsRoot mismatch: claimed "
                f"{block.receipts_root[:16]}... vs computed "
                f"{computed_receipts_root[:16]}...")

        # --- State trie: rebuild from current balances + nonces ---
        self._rebuild_state_trie()
        computed_root = self._state_trie.root().hex()
        # Cache for produce_block to reuse without recomputing (R19-PERF-001)
        self._last_computed_state_root = computed_root

        if block.state_root and block.state_root != computed_root:
            # Received block claims a state_root that disagrees with ours.
            raise ValueError(
                f"Block #{idx} state_root mismatch: claimed {block.state_root[:16]}... "
                f"vs computed {computed_root[:16]}...")

        # NOTE: we do NOT stamp state_root onto the block here.
        # produce_block() handles stamping + re-signing for self-produced blocks.
        # Received blocks already carry the producer's state_root.
        # Blocks without state_root (legacy/test) keep state_root="" which
        # is excluded from the header, preserving their original hash.

        # Save trie snapshot for rollback (lightweight dict copy)
        self._state_snapshots[idx] = self._state_trie.snapshot()
        # Prune old snapshots beyond MAX_REORG_DEPTH to prevent unbounded memory growth (R18-003)
        prune_before = idx - MAX_REORG_DEPTH - 1
        if prune_before >= 0:
            self._state_snapshots.pop(prune_before, None)

        # Prune _events_by_block and associated _events_by_type / _receipts (R33-M05, R35-M03)
        if prune_before >= 0:
            pruned_tx_ids = self._events_by_block.pop(prune_before, None)
            if pruned_tx_ids:
                pruned_set = set(pruned_tx_ids)
                # Remove pruned tx_ids from _events_by_type reverse index
                empty_types = []
                for ev_type, tx_list in self._events_by_type.items():
                    self._events_by_type[ev_type] = [
                        tid for tid in tx_list if tid not in pruned_set
                    ]
                    if not self._events_by_type[ev_type]:
                        empty_types.append(ev_type)
                for ev_type in empty_types:
                    del self._events_by_type[ev_type]
                # Remove pruned receipts from in-memory cache
                for tid in pruned_set:
                    self._receipts.pop(tid, None)

        # Prune _last_epoch_distributions beyond MAX_REORG_DEPTH (R33-L03)
        if prune_before >= 0 and self._last_epoch_distributions:
            stale_epochs = [
                ep for ep in self._last_epoch_distributions
                if ep < prune_before
            ]
            for ep in stale_epochs:
                del self._last_epoch_distributions[ep]

        # Persist balance changes to SQLite (after epoch distribution)
        if self._store is not None and self._financial_active:
            self._persist_balances_after_block(block, idx, matured_stakers)

        # Persist receipts and block-level events to SQLite (batch commit, R19-PERF-003)
        if self._store is not None:
            for receipt in block_receipts:
                self._store.put_receipt(receipt, commit=False)
            if _block_level_events:
                self._store.put_block_level_events(idx, _block_level_events, commit=False)
            self._store.commit()

        # Update finality
        self._update_finality(idx)

    # NOTE: _build_evidence_header() was removed in R15-001 fix.
    # Evidence payloads now include block_a_header / block_b_header fields
    # containing the hex-encoded raw header bytes that validators signed.
    # Signature verification in _process_evidence_tx uses those directly,
    # and validate_payload checks that each header hashes to its block hash.

    # ---- Pruning ----

    def prune(self, retention: int = PRUNING_RETENTION) -> int:
        """Prune old block data, keeping only the last `retention` blocks.

        Only works in SQLite mode. Removes raw block JSON and tx rows for
        blocks older than height - retention. All in-memory indices remain
        intact (they were built during load and stay valid for queries).
        Notarizations, key registry, validator registry, stakes, epochs,
        and slashing records are all preserved.

        Returns the number of blocks pruned.
        """
        if not isinstance(retention, int) or retention < 1:
            logger.warning(f"Invalid pruning retention: {retention}")
            return 0
        if self._store is None:
            logger.debug("Pruning skipped: no SQLite store (in-memory mode)")
            return 0
        if self._height < retention:
            return 0  # not enough blocks to prune

        before_index = self._height - retention + 1
        if before_index <= 0:
            return 0

        with self._db_lock:
            count = self._store.prune_blocks(before_index)

        if count > 0:
            logger.info(
                f"Pruned {count} blocks (retained last {retention}, "
                f"height={self._height})")
        return count


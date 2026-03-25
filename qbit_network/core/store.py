"""Chain storage — SQLite backend (zero external deps) and migration from chain.json."""
import json
import logging
import os
import sqlite3
from .block import Block
from .transaction import Transaction, TxType

logger = logging.getLogger("qbit_network.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    idx INTEGER PRIMARY KEY,
    hash TEXT UNIQUE NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS txs (
    tx_id TEXT PRIMARY KEY,
    block_idx INTEGER NOT NULL,
    tx_idx INTEGER NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT,
    tx_type TEXT NOT NULL,
    nonce INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notarizations (
    doc_hash TEXT NOT NULL,
    tx_id TEXT NOT NULL,
    is_first INTEGER DEFAULT 0,
    PRIMARY KEY (doc_hash, tx_id)
);
CREATE TABLE IF NOT EXISTS key_registry (
    address TEXT NOT NULL,
    encryption_pk TEXT NOT NULL,
    seq INTEGER NOT NULL,
    PRIMARY KEY (address, seq)
);
CREATE TABLE IF NOT EXISTS validator_registry (
    address TEXT PRIMARY KEY,
    pubkey TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revoked_keys (
    address TEXT NOT NULL,
    key_type TEXT NOT NULL,
    tx_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (address, key_type)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stakes (
    staker TEXT NOT NULL,
    validator TEXT NOT NULL,
    amount INTEGER NOT NULL,
    PRIMARY KEY (staker, validator)
);
CREATE TABLE IF NOT EXISTS unbonding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staker TEXT NOT NULL,
    validator TEXT NOT NULL,
    amount INTEGER NOT NULL,
    release_block INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_txs_sender ON txs(sender, nonce);
CREATE INDEX IF NOT EXISTS idx_txs_recipient ON txs(recipient);
CREATE INDEX IF NOT EXISTS idx_notarizations_first ON notarizations(doc_hash, is_first);
CREATE INDEX IF NOT EXISTS idx_unbonding_release ON unbonding(release_block);
"""


class SQLiteStore:
    """SQLite-backed chain storage. Zero external dependencies."""

    def __init__(self, data_dir: str):
        db_path = os.path.join(data_dir, "chain.db")
        self._db = sqlite3.connect(db_path, isolation_level="DEFERRED")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        self._height = -1
        row = self._db.execute("SELECT MAX(idx) FROM blocks").fetchone()
        if row and row[0] is not None:
            self._height = row[0]

    def height(self) -> int:
        return self._height

    def append_block(self, block: Block):
        """Append block + all indices in one transaction (atomic)."""
        idx = self._height + 1
        block_json = json.dumps(block.to_dict(), separators=(",", ":"))

        c = self._db.cursor()
        try:
            c.execute("INSERT INTO blocks (idx, hash, data) VALUES (?, ?, ?)",
                       (idx, block.block_hash, block_json))

            for tx_idx, tx in enumerate(block.transactions):
                c.execute(
                    "INSERT OR IGNORE INTO txs (tx_id, block_idx, tx_idx, sender, recipient, tx_type, nonce) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (tx.tx_id, idx, tx_idx, tx.sender, tx.recipient or "",
                     tx.tx_type.value, tx.nonce))

                if tx.tx_type == TxType.NOTARIZE:
                    dh = tx.payload.get("documentHash", "")
                    if dh:
                        existing = c.execute(
                            "SELECT 1 FROM notarizations WHERE doc_hash=? AND is_first=1",
                            (dh,)).fetchone()
                        is_first = 0 if existing else 1
                        c.execute(
                            "INSERT OR IGNORE INTO notarizations (doc_hash, tx_id, is_first) "
                            "VALUES (?, ?, ?)", (dh, tx.tx_id, is_first))

                elif tx.tx_type == TxType.REGISTER_KEY:
                    epk = tx.payload.get("encryption_pk", "")
                    if epk:
                        seq = c.execute(
                            "SELECT COUNT(*) FROM key_registry WHERE address=?",
                            (tx.sender,)).fetchone()[0]
                        c.execute(
                            "INSERT OR IGNORE INTO key_registry (address, encryption_pk, seq) "
                            "VALUES (?, ?, ?)", (tx.sender, epk, seq))

            self._db.commit()
            self._height = idx
        except Exception:
            self._db.rollback()
            raise

    def get_block(self, index: int) -> Block | None:
        row = self._db.execute("SELECT data FROM blocks WHERE idx=?", (index,)).fetchone()
        return Block.from_dict(json.loads(row[0])) if row else None

    def get_block_by_hash(self, h: str) -> Block | None:
        row = self._db.execute("SELECT data FROM blocks WHERE hash=?", (h,)).fetchone()
        return Block.from_dict(json.loads(row[0])) if row else None

    def get_tx(self, tx_id: str) -> Transaction | None:
        row = self._db.execute("SELECT block_idx, tx_idx FROM txs WHERE tx_id=?",
                                (tx_id,)).fetchone()
        if not row:
            return None
        block = self.get_block(row[0])
        if block and row[1] < len(block.transactions):
            return block.transactions[row[1]]
        return None

    def get_tx_block(self, tx_id: str) -> int | None:
        row = self._db.execute("SELECT block_idx FROM txs WHERE tx_id=?",
                                (tx_id,)).fetchone()
        return row[0] if row else None

    def get_nonce(self, address: str) -> int:
        row = self._db.execute(
            "SELECT MAX(nonce) FROM txs WHERE sender=?", (address,)).fetchone()
        if row and row[0] is not None:
            return row[0] + 1
        return 0

    def get_notarization(self, doc_hash: str) -> str | None:
        row = self._db.execute(
            "SELECT tx_id FROM notarizations WHERE doc_hash=? AND is_first=1",
            (doc_hash,)).fetchone()
        return row[0] if row else None

    def get_all_notarizations(self, doc_hash: str) -> list[str]:
        rows = self._db.execute(
            "SELECT tx_id FROM notarizations WHERE doc_hash=?",
            (doc_hash,)).fetchall()
        return [r[0] for r in rows]

    def get_encryption_pk(self, address: str) -> str | None:
        row = self._db.execute(
            "SELECT encryption_pk FROM key_registry WHERE address=? ORDER BY seq DESC LIMIT 1",
            (address,)).fetchone()
        return row[0] if row else None

    def get_txs_by_sender(self, address: str) -> list[str]:
        rows = self._db.execute(
            "SELECT tx_id FROM txs WHERE sender=? ORDER BY nonce",
            (address,)).fetchall()
        return [r[0] for r in rows]

    def get_txs_by_recipient(self, address: str) -> list[str]:
        rows = self._db.execute(
            "SELECT tx_id FROM txs WHERE recipient=? ORDER BY block_idx, tx_idx",
            (address,)).fetchall()
        return [r[0] for r in rows]

    def put_validator(self, address: str, pubkey_hex: str, commit: bool = True):
        """Register or update a validator's signing pubkey.

        When commit=False, the caller is responsible for committing the
        transaction (e.g. during reorg operations for atomicity).
        """
        self._db.execute(
            "INSERT OR REPLACE INTO validator_registry (address, pubkey) VALUES (?, ?)",
            (address, pubkey_hex))
        if commit:
            self._db.commit()

    def get_validator(self, address: str) -> str | None:
        """Get a validator's signing pubkey hex, or None."""
        row = self._db.execute(
            "SELECT pubkey FROM validator_registry WHERE address=?",
            (address,)).fetchone()
        return row[0] if row else None

    def get_all_validators(self) -> list[tuple[str, str]]:
        """Return all registered validators as (address, pubkey_hex) pairs."""
        rows = self._db.execute(
            "SELECT address, pubkey FROM validator_registry").fetchall()
        return [(r[0], r[1]) for r in rows]

    def delete_validator(self, address: str, commit: bool = True):
        """Remove a validator from the registry (for rollback).

        When commit=False, the caller is responsible for committing the
        transaction (e.g. during reorg operations for atomicity).
        """
        self._db.execute(
            "DELETE FROM validator_registry WHERE address=?", (address,))
        if commit:
            self._db.commit()

    # ---- Staking registry ----

    def put_stake(self, staker: str, validator: str, amount: int,
                  commit: bool = True):
        """Insert or update a stake entry."""
        self._db.execute(
            "INSERT OR REPLACE INTO stakes (staker, validator, amount) "
            "VALUES (?, ?, ?)", (staker, validator, amount))
        if commit:
            self._db.commit()

    def get_stake(self, staker: str, validator: str) -> int:
        """Get stake amount for a staker/validator pair."""
        row = self._db.execute(
            "SELECT amount FROM stakes WHERE staker=? AND validator=?",
            (staker, validator)).fetchone()
        return row[0] if row else 0

    def delete_stake(self, staker: str, validator: str, commit: bool = True):
        """Remove a stake entry."""
        self._db.execute(
            "DELETE FROM stakes WHERE staker=? AND validator=?",
            (staker, validator))
        if commit:
            self._db.commit()

    def get_all_stakes(self) -> list[tuple[str, str, int]]:
        """Return all stakes as (staker, validator, amount) tuples."""
        rows = self._db.execute(
            "SELECT staker, validator, amount FROM stakes").fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def put_unbonding(self, staker: str, validator: str, amount: int,
                      release_block: int, commit: bool = True):
        """Record an unbonding entry."""
        self._db.execute(
            "INSERT INTO unbonding (staker, validator, amount, release_block) "
            "VALUES (?, ?, ?, ?)", (staker, validator, amount, release_block))
        if commit:
            self._db.commit()

    def get_mature_unbondings(self, current_block: int) -> list[dict]:
        """Get unbonding entries that have matured (release_block <= current)."""
        rows = self._db.execute(
            "SELECT id, staker, validator, amount, release_block "
            "FROM unbonding WHERE release_block <= ?",
            (current_block,)).fetchall()
        return [{"id": r[0], "staker": r[1], "validator": r[2],
                 "amount": r[3], "release_block": r[4]} for r in rows]

    def delete_unbonding(self, staker: str, validator: str,
                         amount: int, release_block: int, commit: bool = True):
        """Remove an unbonding entry by matching fields."""
        self._db.execute(
            "DELETE FROM unbonding WHERE staker=? AND validator=? "
            "AND amount=? AND release_block=?",
            (staker, validator, amount, release_block))
        if commit:
            self._db.commit()

    def delete_unbondings_from_block(self, from_block: int, commit: bool = True):
        """Remove unbonding entries created at or after from_block."""
        # release_block = block_index + UNBONDING_PERIOD, so we remove
        # entries whose originating block is >= from_block
        self._db.execute(
            "DELETE FROM unbonding WHERE release_block >= ?",
            (from_block,))
        if commit:
            self._db.commit()

    # ---- Revocation registry ----

    def put_revocation(self, address: str, key_type: str, tx_id: str,
                       timestamp: float, reason: str, commit: bool = True):
        """Record a key revocation."""
        self._db.execute(
            "INSERT OR REPLACE INTO revoked_keys "
            "(address, key_type, tx_id, timestamp, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (address, key_type, tx_id, timestamp, reason))
        if commit:
            self._db.commit()

    def get_revocation(self, address: str, key_type: str) -> dict | None:
        """Get revocation info, or None."""
        row = self._db.execute(
            "SELECT tx_id, timestamp, reason FROM revoked_keys "
            "WHERE address=? AND key_type=?",
            (address, key_type)).fetchone()
        if row:
            return {"tx_id": row[0], "timestamp": row[1], "reason": row[2]}
        return None

    def delete_revocation(self, address: str, key_type: str, commit: bool = True):
        """Remove a revocation (for rollback)."""
        self._db.execute(
            "DELETE FROM revoked_keys WHERE address=? AND key_type=?",
            (address, key_type))
        if commit:
            self._db.commit()

    def get_all_revocations(self) -> list[tuple[str, str, str, float, str]]:
        """Return all revocations as (address, key_type, tx_id, timestamp, reason)."""
        rows = self._db.execute(
            "SELECT address, key_type, tx_id, timestamp, reason "
            "FROM revoked_keys").fetchall()
        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    def block_hash_exists(self, h: str) -> bool:
        return self._db.execute(
            "SELECT 1 FROM blocks WHERE hash=?", (h,)).fetchone() is not None

    def tx_exists(self, tx_id: str) -> bool:
        return self._db.execute(
            "SELECT 1 FROM txs WHERE tx_id=?", (tx_id,)).fetchone() is not None

    def get_blocks_range(self, start: int, end: int) -> list[Block]:
        """Return blocks with start <= idx < end, in order."""
        rows = self._db.execute(
            "SELECT data FROM blocks WHERE idx >= ? AND idx < ? ORDER BY idx",
            (start, end)).fetchall()
        return [Block.from_dict(json.loads(r[0])) for r in rows]

    def get_blocks_count(self) -> int:
        """Return total number of blocks stored."""
        return self._height + 1 if self._height >= 0 else 0

    def latest_block(self) -> Block | None:
        return self.get_block(self._height) if self._height >= 0 else None

    def delete_blocks_from(self, from_index: int):
        """Delete all blocks with idx >= from_index (for rollback). Atomic.

        Handles validator registry cleanup within the same transaction to
        prevent non-atomic commits during reorg.
        """
        c = self._db.cursor()
        try:
            # Collect validator addresses and revocations from rolled-back blocks
            # so we can clean them up atomically within this transaction
            rows = c.execute(
                "SELECT data FROM blocks WHERE idx >= ? ORDER BY idx", (from_index,)
            ).fetchall()
            validator_addrs_to_remove = []
            revocations_to_remove = []  # (address, key_type)
            for row in rows:
                block_data = json.loads(row[0])
                for tx_data in block_data.get("transactions", []):
                    if tx_data.get("type") == "REGISTER_VALIDATOR":
                        vaddr = tx_data.get("payload", {}).get("validator_address", "")
                        if vaddr:
                            validator_addrs_to_remove.append(vaddr)
                    elif tx_data.get("type") == "REVOKE_KEY":
                        payload = tx_data.get("payload", {})
                        sender = tx_data.get("from", "")
                        kt = payload.get("key_type", "")
                        if sender and kt:
                            revocations_to_remove.append((sender, kt))

            # Delete block data and cascading indices
            c.execute("DELETE FROM txs WHERE block_idx >= ?", (from_index,))
            c.execute("DELETE FROM notarizations WHERE tx_id NOT IN (SELECT tx_id FROM txs)")
            c.execute("DELETE FROM key_registry WHERE address || ':' || seq NOT IN "
                       "(SELECT address || ':' || seq FROM key_registry kr "
                       "WHERE EXISTS (SELECT 1 FROM txs t WHERE t.sender = kr.address))")
            c.execute("DELETE FROM blocks WHERE idx >= ?", (from_index,))

            # Remove validators registered in the rolled-back blocks
            for vaddr in validator_addrs_to_remove:
                c.execute("DELETE FROM validator_registry WHERE address=?", (vaddr,))

            # Remove revocations from rolled-back blocks
            for addr, kt in revocations_to_remove:
                c.execute(
                    "DELETE FROM revoked_keys WHERE address=? AND key_type=?",
                    (addr, kt))

            # Rebuild stakes table from remaining chain data
            # (simpler and more correct than incremental rollback)
            c.execute("DELETE FROM stakes")
            c.execute("DELETE FROM unbonding")
            # Re-scan remaining blocks for STAKE/DELEGATE/UNSTAKE
            remaining = c.execute(
                "SELECT idx, data FROM blocks WHERE idx < ? ORDER BY idx",
                (from_index,)).fetchall()
            stake_state: dict[tuple[str, str], int] = {}  # (staker, validator) -> amount
            for row_idx, row_data in remaining:
                bd = json.loads(row_data)
                for tx_data in bd.get("transactions", []):
                    tt = tx_data.get("type", "")
                    payload = tx_data.get("payload", {})
                    sender = tx_data.get("from", "")
                    if tt in ("STAKE", "DELEGATE"):
                        vaddr = payload.get("validator_address", "")
                        amt = payload.get("amount", 0)
                        if vaddr and amt > 0:
                            key = (sender, vaddr)
                            stake_state[key] = stake_state.get(key, 0) + amt
                    elif tt == "UNSTAKE":
                        vaddr = payload.get("validator_address", "")
                        amt = payload.get("amount", 0)
                        if vaddr and amt > 0:
                            key = (sender, vaddr)
                            stake_state[key] = max(0, stake_state.get(key, 0) - amt)
            for (staker, validator), amount in stake_state.items():
                if amount > 0:
                    c.execute(
                        "INSERT INTO stakes (staker, validator, amount) VALUES (?, ?, ?)",
                        (staker, validator, amount))

            self._db.commit()
            row = self._db.execute("SELECT MAX(idx) FROM blocks").fetchone()
            self._height = row[0] if row and row[0] is not None else -1
        except Exception:
            self._db.rollback()
            raise

    def close(self):
        self._db.close()


def migrate_json_to_sqlite(data_dir: str):
    """One-time migration from chain.json to SQLite."""
    chain_file = os.path.join(data_dir, "chain.json")
    db_file = os.path.join(data_dir, "chain.db")

    if not os.path.exists(chain_file):
        return
    if os.path.exists(db_file):
        return  # already migrated

    logger.info("Migrating chain.json → SQLite...")
    try:
        with open(chain_file) as f:
            chain_data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Cannot migrate corrupt chain.json: {e}")
        return

    if not isinstance(chain_data, list) or len(chain_data) == 0:
        return

    store = SQLiteStore(data_dir)
    for bd in chain_data:
        block = Block.from_dict(bd)
        store.append_block(block)
    store.close()

    backup = chain_file + ".migrated"
    os.rename(chain_file, backup)
    logger.info(f"Migration complete: {len(chain_data)} blocks. Backup: {backup}")

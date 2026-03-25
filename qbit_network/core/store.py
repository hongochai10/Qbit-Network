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
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_txs_sender ON txs(sender, nonce);
CREATE INDEX IF NOT EXISTS idx_txs_recipient ON txs(recipient);
CREATE INDEX IF NOT EXISTS idx_notarizations_first ON notarizations(doc_hash, is_first);
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

    def block_hash_exists(self, h: str) -> bool:
        return self._db.execute(
            "SELECT 1 FROM blocks WHERE hash=?", (h,)).fetchone() is not None

    def tx_exists(self, tx_id: str) -> bool:
        return self._db.execute(
            "SELECT 1 FROM txs WHERE tx_id=?", (tx_id,)).fetchone() is not None

    def latest_block(self) -> Block | None:
        return self.get_block(self._height) if self._height >= 0 else None

    def delete_blocks_from(self, from_index: int):
        """Delete all blocks with idx >= from_index (for rollback). Atomic."""
        c = self._db.cursor()
        try:
            # Get tx_ids being deleted for cascading cleanup
            c.execute("DELETE FROM txs WHERE block_idx >= ?", (from_index,))
            c.execute("DELETE FROM notarizations WHERE tx_id NOT IN (SELECT tx_id FROM txs)")
            c.execute("DELETE FROM key_registry WHERE address || ':' || seq NOT IN "
                       "(SELECT address || ':' || seq FROM key_registry kr "
                       "WHERE EXISTS (SELECT 1 FROM txs t WHERE t.sender = kr.address))")
            c.execute("DELETE FROM blocks WHERE idx >= ?", (from_index,))
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

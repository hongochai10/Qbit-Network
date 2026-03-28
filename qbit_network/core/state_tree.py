"""Sorted key-value Merkle trie for state commitments.

Provides a deterministic state root computed from sorted (key, value) pairs.
The root is a Merkle tree over SHA3-256 hashed leaves, where each leaf is
``sha3_256(key_bytes + value_bytes)``.  Ordering is lexicographic on the
string key so that the same logical state always produces the same root
regardless of mutation order.

Proof generation and verification reuse the existing ``merkle_proof`` /
``verify_merkle_proof`` helpers from ``qbit_network.crypto.hashing``.
"""

from ..crypto.hashing import sha3_256, merkle_root, merkle_proof, verify_merkle_proof

# Sentinel: the canonical root when the state is empty.
_EMPTY_STATE_TAG = b"empty_state"


class StateTrie:
    """Simple sorted key-value Merkle trie for state commitments."""

    __slots__ = ('_entries', '_dirty', '_cached_root')

    def __init__(self):
        self._entries: dict[str, bytes] = {}
        self._dirty: bool = True
        self._cached_root: bytes | None = None

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def set(self, key: str, value: bytes):
        """Insert or update a key-value pair."""
        old = self._entries.get(key)
        if old is None or old != value:
            self._entries[key] = value
            self._dirty = True

    def delete(self, key: str):
        """Remove a key. No-op if the key does not exist."""
        if key in self._entries:
            del self._entries[key]
            self._dirty = True

    # ------------------------------------------------------------------
    # Root computation
    # ------------------------------------------------------------------

    def root(self) -> bytes:
        """Compute the Merkle root from sorted entries.

        Returns a 32-byte SHA3-256 hash.  When the trie is empty the root
        is ``sha3_256(b"empty_state")``.

        The result is cached and only recomputed when entries have changed
        (tracked via a dirty flag).  This avoids O(n) recomputation on
        repeated calls when the state has not mutated (R19-PERF-002).
        """
        if not self._dirty and self._cached_root is not None:
            return self._cached_root
        if not self._entries:
            self._cached_root = sha3_256(_EMPTY_STATE_TAG)
        else:
            leaves = self._sorted_leaves()
            self._cached_root = merkle_root(leaves)
        self._dirty = False
        return self._cached_root

    # ------------------------------------------------------------------
    # Proof generation
    # ------------------------------------------------------------------

    def get_proof(self, key: str) -> dict | None:
        """Generate a Merkle inclusion proof for *key*.

        Returns ``None`` if the key is not in the trie.  Otherwise returns::

            {
                "key": "<key>",
                "value": "<hex-encoded value>",
                "proof": [["<hex sibling>", <is_left>], ...],
                "root": "<hex root>",
            }
        """
        if key not in self._entries:
            return None

        sorted_keys = sorted(self._entries.keys())
        leaves = [sha3_256(k.encode() + self._entries[k]) for k in sorted_keys]
        key_index = sorted_keys.index(key)

        proof = merkle_proof(leaves, key_index)
        return {
            "key": key,
            "value": self._entries[key].hex(),
            "proof": [[h.hex(), is_left] for h, is_left in proof],
            "root": self.root().hex(),
        }

    # ------------------------------------------------------------------
    # Static verification
    # ------------------------------------------------------------------

    @staticmethod
    def verify_proof(key: str, value: bytes, proof: list[tuple[bytes, bool]],
                     root: bytes) -> bool:
        """Verify a Merkle inclusion proof against *root*.

        The *proof* list contains ``(sibling_hash, is_left)`` pairs exactly as
        returned by ``merkle_proof``.
        """
        leaf = sha3_256(key.encode() + value)
        return verify_merkle_proof(leaf, proof, root)

    # ------------------------------------------------------------------
    # Snapshot / restore (for rollback support)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, bytes]:
        """Return a shallow copy of the internal entries."""
        return dict(self._entries)

    def restore(self, entries: dict[str, bytes]):
        """Replace internal entries with *entries* (for rollback)."""
        self._entries = dict(entries)
        self._dirty = True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sorted_leaves(self) -> list[bytes]:
        """Hash each (key, value) pair into a leaf, sorted by key."""
        return [
            sha3_256(k.encode() + self._entries[k])
            for k in sorted(self._entries.keys())
        ]

    def __len__(self) -> int:
        return len(self._entries)

"""SHA-3 hashing and Merkle tree."""
import hashlib


def sha3_256(data: bytes) -> bytes:
    """SHA3-256 hash."""
    return hashlib.sha3_256(data).digest()


def shake256(data: bytes, length: int = 32) -> bytes:
    """SHAKE-256 extendable output."""
    return hashlib.shake_256(data).digest(length)


# Domain separation prefixes for Merkle tree (prevents second-preimage attacks)
_LEAF_PREFIX = b'\x00'
_NODE_PREFIX = b'\x01'


def _merkle_node(left: bytes, right: bytes) -> bytes:
    return sha3_256(_NODE_PREFIX + left + right)


def merkle_root(items: list[bytes]) -> bytes:
    """Compute Merkle root with domain-separated hashing."""
    if not items:
        return b'\x00' * 32
    # Hash leaves with leaf prefix
    layer = [sha3_256(_LEAF_PREFIX + item) for item in items]
    while len(layer) > 1:
        next_layer = []
        for i in range(0, len(layer) - 1, 2):
            next_layer.append(_merkle_node(layer[i], layer[i + 1]))
        if len(layer) % 2 == 1:
            # Promote odd element without pairing (no duplicate)
            next_layer.append(layer[-1])
        layer = next_layer
    return layer[0]


def merkle_proof(items: list[bytes], index: int) -> list[tuple[bytes, bool]]:
    """Generate Merkle proof for item at index.

    Returns list of (sibling_hash, is_left) pairs.
    """
    if not items or index >= len(items):
        return []
    layer = [sha3_256(_LEAF_PREFIX + item) for item in items]
    proof = []
    idx = index
    while len(layer) > 1:
        next_layer = []
        for i in range(0, len(layer) - 1, 2):
            if i == idx - (idx % 2):
                if idx % 2 == 0:
                    proof.append((layer[i + 1], False))
                else:
                    proof.append((layer[i], True))
            next_layer.append(_merkle_node(layer[i], layer[i + 1]))
        if len(layer) % 2 == 1:
            if idx == len(layer) - 1:
                pass  # Promoted without sibling — no proof entry
            next_layer.append(layer[-1])
        layer = next_layer
        idx //= 2
    return proof


def verify_merkle_proof(leaf: bytes, proof: list[tuple[bytes, bool]], root: bytes) -> bool:
    """Verify a Merkle proof with domain-separated hashing."""
    current = sha3_256(_LEAF_PREFIX + leaf)
    for sibling, is_left in proof:
        if is_left:
            current = _merkle_node(sibling, current)
        else:
            current = _merkle_node(current, sibling)
    return current == root

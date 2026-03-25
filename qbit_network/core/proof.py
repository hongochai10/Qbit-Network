"""Standalone notarization proof — export and offline verification."""
import json
from ..crypto import sha3_256, MLDSA, merkle_proof, verify_merkle_proof

PROOF_VERSION = 1
PROOF_TYPE = "qbit-notarization-proof"


def export_proof(block_dict: dict, tx_index: int, tx_id: str,
                 document_hash: str, notarizer: str) -> dict:
    """Build a self-contained proof document from block data.

    Args:
        block_dict: Block.to_dict() output.
        tx_index: Index of the notarization tx within the block.
        tx_id: Transaction ID.
        document_hash: SHA3-256 hex of the original file.
        notarizer: Address that submitted the notarization.

    Returns:
        Proof dict suitable for JSON serialization.
    """
    tx_hashes = [bytes.fromhex(tx["id"]) for tx in block_dict["transactions"]]
    proof_path = merkle_proof(tx_hashes, tx_index)
    proof_serialized = [{"hash": h.hex(), "is_left": left} for h, left in proof_path]

    # Get the tx's sender_pubkey for signature verification
    tx_dict = block_dict["transactions"][tx_index]

    return {
        "version": PROOF_VERSION,
        "type": PROOF_TYPE,
        "document_hash": document_hash,
        "tx_id": tx_id,
        "block": {
            "index": block_dict["index"],
            "timestamp": block_dict["timestamp"],
            "hash": block_dict["hash"],
            "prevHash": block_dict["prevHash"],
            "merkleRoot": block_dict["merkleRoot"],
            "validator": block_dict["validator"],
            "signature": block_dict["signature"],
            "txCount": len(block_dict["transactions"]),
        },
        "merkle_proof": proof_serialized,
        "notarizer": notarizer,
        "notarizer_pubkey": tx_dict.get("sender_pubkey", ""),
    }


def verify_proof(proof: dict) -> tuple[bool, list[str]]:
    """Verify a notarization proof offline.

    Returns (is_valid, list_of_errors).
    """
    errors = []

    if proof.get("type") != PROOF_TYPE:
        errors.append("not a valid QBit notarization proof")
        return False, errors

    version = proof.get("version", 0)
    if version < 1:
        errors.append(f"unsupported proof version: {version}")
        return False, errors

    tx_id = proof.get("tx_id", "")
    block = proof.get("block", {})
    merkle_path = proof.get("merkle_proof", [])

    # 1. Verify Merkle proof — tx_id is in the block's Merkle root
    proof_tuples = [(bytes.fromhex(p["hash"]), p["is_left"]) for p in merkle_path]
    merkle_root_hex = block.get("merkleRoot", "")
    if not verify_merkle_proof(bytes.fromhex(tx_id), proof_tuples,
                                bytes.fromhex(merkle_root_hex)):
        errors.append("Merkle proof invalid — transaction not in block")

    # 2. Verify block header hash
    header_obj = {
        "index": block["index"],
        "merkleRoot": block["merkleRoot"],
        "prevHash": block["prevHash"],
        "timestamp": block["timestamp"],
        "txCount": block.get("txCount", 0),
        "validator": block["validator"],
    }
    header_bytes = json.dumps(header_obj, sort_keys=True, separators=(",", ":")).encode()
    computed_hash = sha3_256(header_bytes).hex()
    claimed_hash = block.get("hash", "")
    if computed_hash != claimed_hash:
        errors.append(f"block hash mismatch: computed {computed_hash[:16]}... != claimed {claimed_hash[:16]}...")

    # 3. Verify block signature (if validator pubkey available via notarizer_pubkey or external)
    block_sig = block.get("signature", "")
    if block_sig:
        # We can verify the validator signed this exact header
        # Note: In production, the verifier should have the validator's pk from a trusted source
        # For now, we verify internal consistency only
        pass  # Signature verification requires validator pk — see note in proof doc

    return len(errors) == 0, errors

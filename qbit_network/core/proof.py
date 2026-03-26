"""Standalone notarization proof — export and offline verification."""
import json
from ..crypto import sha3_256, MLDSA, merkle_proof, verify_merkle_proof
from ..config import CHAIN_ID

PROOF_VERSION = 1
PROOF_TYPE = "qbit-notarization-proof"


def export_proof(block_dict: dict, tx_index: int, tx_id: str,
                 document_hash: str, notarizer: str,
                 validator_pubkey: str = "") -> dict:
    """Build a self-contained proof document from block data.

    Args:
        block_dict: Block.to_dict() output.
        tx_index: Index of the notarization tx within the block.
        tx_id: Transaction ID.
        document_hash: SHA3-256 hex of the original file.
        notarizer: Address that submitted the notarization.
        validator_pubkey: Hex-encoded ML-DSA-65 public key of the block
            validator, for offline signature verification.

    Returns:
        Proof dict suitable for JSON serialization.
    """
    tx_hashes = [bytes.fromhex(tx["id"]) for tx in block_dict["transactions"]]
    proof_path = merkle_proof(tx_hashes, tx_index)
    proof_serialized = [{"hash": h.hex(), "is_left": left} for h, left in proof_path]

    # Get the tx's sender_pubkey for signature verification
    tx_dict = block_dict["transactions"][tx_index]

    result = {
        "version": PROOF_VERSION,
        "type": PROOF_TYPE,
        "chain_id": CHAIN_ID,
        "document_hash": document_hash,
        "tx_id": tx_id,
        "block": {
            "index": block_dict["index"],
            "timestamp": block_dict["timestamp"],
            "hash": block_dict["hash"],
            "prevHash": block_dict["prevHash"],
            "merkleRoot": block_dict["merkleRoot"],
            "baseFee": block_dict.get("baseFee", 0),
            "stateRoot": block_dict.get("stateRoot", ""),
            "receiptsRoot": block_dict.get("receiptsRoot", ""),
            "validator": block_dict["validator"],
            "signature": block_dict["signature"],
            "txCount": len(block_dict["transactions"]),
        },
        "merkle_proof": proof_serialized,
        "notarizer": notarizer,
        "notarizer_pubkey": tx_dict.get("sender_pubkey", ""),
    }

    # Include validator pubkey for offline block signature verification
    if validator_pubkey:
        result["validator_pubkey"] = validator_pubkey

    return result


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

    # Verify chain_id if present (R15-009)
    proof_chain_id = proof.get("chain_id")
    if proof_chain_id is not None and proof_chain_id != CHAIN_ID:
        errors.append(f"chain_id mismatch: proof has {proof_chain_id!r}, expected {CHAIN_ID!r}")

    tx_id = proof.get("tx_id", "")
    block = proof.get("block", {})
    merkle_path = proof.get("merkle_proof", [])

    # 1. Verify Merkle proof -- tx_id is in the block's Merkle root
    proof_tuples = [(bytes.fromhex(p["hash"]), p["is_left"]) for p in merkle_path]
    merkle_root_hex = block.get("merkleRoot", "")
    if not verify_merkle_proof(bytes.fromhex(tx_id), proof_tuples,
                                bytes.fromhex(merkle_root_hex)):
        errors.append("Merkle proof invalid — transaction not in block")

    # 2. Verify block header hash
    header_obj = {
        "baseFee": block.get("baseFee", 0),
        "index": block["index"],
        "merkleRoot": block["merkleRoot"],
        "prevHash": block["prevHash"],
        "timestamp": block["timestamp"],
        "txCount": block.get("txCount", 0),
        "validator": block["validator"],
    }
    # Include optional header fields only when present (backward-compatible)
    if block.get("receiptsRoot"):
        header_obj["receiptsRoot"] = block["receiptsRoot"]
    if block.get("stateRoot"):
        header_obj["stateRoot"] = block["stateRoot"]
    header_bytes = json.dumps(header_obj, sort_keys=True, separators=(",", ":")).encode()
    computed_hash = sha3_256(header_bytes).hex()
    claimed_hash = block.get("hash", "")
    if computed_hash != claimed_hash:
        errors.append(f"block hash mismatch: computed {computed_hash[:16]}... != claimed {claimed_hash[:16]}...")

    # 3. Verify block signature if validator_pubkey is available
    validator_pubkey_hex = proof.get("validator_pubkey", "")
    block_sig_hex = block.get("signature", "")
    if validator_pubkey_hex and block_sig_hex:
        try:
            vpk = bytes.fromhex(validator_pubkey_hex)
            block_sig = bytes.fromhex(block_sig_hex)
            if not MLDSA.verify(vpk, header_bytes, block_sig):
                errors.append("block signature invalid: ML-DSA verification failed")
        except (ValueError, TypeError) as e:
            errors.append(f"block signature verification error: {e}")

    return len(errors) == 0, errors

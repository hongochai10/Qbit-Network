"""Proof of Authority consensus."""
import logging
import time
from ..crypto import MLDSA
from ..config import MAX_BLOCK_DRIFT, MAX_BLOCK_SIZE, MAX_TX_PER_BLOCK, MAX_TX_PAYLOAD_SIZE
from .block import Block

logger = logging.getLogger("qbit_network.consensus")

# Upper bound estimate: each tx < header + payload + sig + pubkey
_TX_OVERHEAD = 200  # JSON keys, hex encoding overhead
_MAX_TX_WIRE_SIZE = _TX_OVERHEAD + MAX_TX_PAYLOAD_SIZE + 3309 * 2 + 1952 * 2  # sig+pk in hex


class ProofOfAuthority:
    """PoA consensus - only authorized validators can produce blocks."""

    def __init__(self):
        self.validators: dict[str, bytes] = {}
        self._genesis_hash: str = ""
        self._chain_nonces: dict[str, int] | None = None  # injected by blockchain
        self._chain_tx_ids: set[str] | None = None       # injected by blockchain

    def add_validator(self, address: str, signing_pk: bytes):
        self.validators[address] = signing_pk
        logger.info(f"Validator registered: {address[:16]}...")

    def remove_validator(self, address: str):
        self.validators.pop(address, None)

    def is_validator(self, address: str) -> bool:
        return address in self.validators

    def get_validator_pk(self, address: str) -> bytes | None:
        return self.validators.get(address)

    def set_genesis_hash(self, h: str):
        self._genesis_hash = h

    def select_validator(self, block_index: int) -> str | None:
        if not self.validators:
            return None
        addresses = sorted(self.validators.keys())
        return addresses[block_index % len(addresses)]

    def validate_block(self, block: Block, parent: Block | None) -> tuple[bool, str]:

        # ---- Genesis ----
        if block.index == 0:
            if self._genesis_hash and block.block_hash != self._genesis_hash:
                return False, "genesis hash mismatch"
            return True, ""

        if parent is None:
            return False, "parent block required"

        # ---- Header ----
        if block.index != parent.index + 1:
            return False, f"expected index {parent.index + 1}, got {block.index}"

        if block.prev_hash != parent.block_hash:
            return False, "prev_hash mismatch"

        if block.timestamp <= parent.timestamp:
            return False, "timestamp must be after parent"

        now = int(time.time())
        if block.timestamp > now + MAX_BLOCK_DRIFT:
            return False, (f"timestamp too far in future: {block.timestamp} > "
                           f"{now} + {MAX_BLOCK_DRIFT}")

        # ---- Validator ----
        if not self.is_validator(block.validator):
            return False, f"unknown validator {block.validator[:16]}..."

        expected = self.select_validator(block.index)
        if expected and block.validator != expected:
            return False, (f"wrong validator turn: expected {expected[:16]}..., "
                           f"got {block.validator[:16]}...")

        pk = self.validators[block.validator]
        if not block.verify_signature(pk):
            return False, "invalid block signature"

        # ---- Tx count limit ----
        if len(block.transactions) > MAX_TX_PER_BLOCK:
            return False, f"too many txs: {len(block.transactions)} > {MAX_TX_PER_BLOCK}"

        # ---- Non-genesis must have transactions ----
        if not block.transactions:
            return False, "non-genesis block must contain at least one transaction"

        # ---- Block size (fast estimate without full serialization) ----
        estimated = 500 + len(block.transactions) * _MAX_TX_WIRE_SIZE
        if estimated > MAX_BLOCK_SIZE * 2:
            return False, "block estimated too large"

        # ---- Transactions ----
        seen_ids = set()
        sender_nonces: dict[str, int] = {}

        for tx in block.transactions:
            if not tx.verify():
                return False, f"invalid tx signature: {tx.tx_id[:16]}..."

            if tx.tx_id in seen_ids:
                return False, f"duplicate tx in block: {tx.tx_id[:16]}..."
            seen_ids.add(tx.tx_id)

            if self._chain_tx_ids is not None and tx.tx_id in self._chain_tx_ids:
                return False, f"tx already in chain: {tx.tx_id[:16]}..."

            ok, err = tx.validate_payload()
            if not ok:
                return False, f"invalid tx payload: {err}"

            # Nonce: check sequential within block
            prev_nonce = sender_nonces.get(tx.sender)
            if prev_nonce is not None and tx.nonce != prev_nonce + 1:
                return False, (f"nonce gap for {tx.sender[:16]}...: "
                               f"expected {prev_nonce + 1}, got {tx.nonce}")
            sender_nonces[tx.sender] = tx.nonce

        # Nonce: check first nonce per sender matches chain state
        if self._chain_nonces is not None:
            for sender, last_nonce_in_block in sender_nonces.items():
                first_nonce_in_block = last_nonce_in_block - sum(
                    1 for tx in block.transactions if tx.sender == sender) + 1
                expected_start = self._chain_nonces.get(sender, -1) + 1
                if first_nonce_in_block != expected_start:
                    return False, (
                        f"nonce mismatch for {sender[:16]}...: "
                        f"chain expects {expected_start}, "
                        f"block starts at {first_nonce_in_block}")

        return True, ""

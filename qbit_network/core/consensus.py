"""Proof of Authority consensus with dPoS stake-weighted validator selection."""
import logging
import time
from typing import Callable
from ..crypto import MLDSA, sha3_256
from ..config import (MAX_BLOCK_DRIFT, MAX_BLOCK_SIZE, MAX_TX_PER_BLOCK,
                      MAX_TX_PAYLOAD_SIZE, BLOCK_INTERVAL,
                      DYNAMIC_FEE_ACTIVATION_HEIGHT, INITIAL_BASE_FEE,
                      MAX_BLOCK_WEIGHT, TX_WEIGHTS, MAX_SELF_TX_WEIGHT_RATIO)
from .fees import compute_base_fee, effective_block_weight, tx_weight
from .block import Block

logger = logging.getLogger("qbit_network.consensus")

# Upper bound estimate: each tx < header + payload + sig + pubkey
_TX_OVERHEAD = 200  # JSON keys, hex encoding overhead
_MAX_TX_WIRE_SIZE = _TX_OVERHEAD + MAX_TX_PAYLOAD_SIZE + 3309 * 2 + 1952 * 2  # sig+pk in hex


class ProofOfAuthority:
    """PoA consensus with dPoS stake-weighted validator selection.

    When staked validators exist, uses stake-proportional deterministic
    selection. Falls back to PoA round-robin when no stakes are present.
    """

    def __init__(self):
        self.validators: dict[str, bytes] = {}
        self._genesis_hash: str = ""
        self._chain_nonces: dict[str, int] | None = None  # injected by blockchain
        self._chain_tx_ids: set[str] | None = None       # injected by blockchain
        self._revoked_keys: dict[str, dict] | None = None  # injected by blockchain
        self._get_active_validators: Callable[[], list[tuple[str, int]]] | None = None  # injected by blockchain

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

    def select_validator(self, block_index: int,
                         parent_timestamp: float | None = None,
                         parent_hash: str = "",
                         block_timestamp: float | None = None) -> str | None:
        # Try dPoS stake-weighted selection first
        if self._get_active_validators is not None and parent_hash:
            active = self._get_active_validators()
            if active:
                return self._select_dpos(block_index, parent_hash, active)

        # Fallback to PoA round-robin
        return self._select_poa(block_index, parent_timestamp, block_timestamp)

    def _select_poa(self, block_index: int,
                    parent_timestamp: float | None = None,
                    block_timestamp: float | None = None) -> str | None:
        """Original PoA round-robin selection."""
        if not self.validators:
            return None
        addresses = sorted(self.validators.keys())
        n = len(addresses)
        base = block_index % n
        if parent_timestamp is not None:
            return self._select_with_skip(addresses, base, n,
                                          parent_timestamp, block_timestamp)
        return addresses[base]

    @staticmethod
    def _select_dpos(block_index: int, parent_hash: str,
                     active: list[tuple[str, int]]) -> str:
        """Deterministic stake-weighted validator selection.

        Seed is derived from parent_hash + block_index so validators
        cannot predict future selections.
        """
        seed = sha3_256(f"{parent_hash}:{block_index}".encode())
        seed_int = int.from_bytes(seed[:8], 'big')
        total = sum(s for _, s in active)
        point = seed_int % total
        cumulative = 0
        for addr, stake in active:
            cumulative += stake
            if point < cumulative:
                return addr
        return active[-1][0]  # fallback

    def select_validator_with_skip(self, block_index: int,
                                   parent_timestamp: float,
                                   block_timestamp: float | None = None) -> str | None:
        """Select validator with skip-slot: if the expected validator hasn't
        produced a block within BLOCK_INTERVAL * 3 (15s), allow the next one."""
        if not self.validators:
            return None
        addresses = sorted(self.validators.keys())
        n = len(addresses)
        base = block_index % n
        return self._select_with_skip(addresses, base, n,
                                      parent_timestamp, block_timestamp)

    @staticmethod
    def _select_with_skip(addresses: list[str], base: int, n: int,
                          parent_timestamp: float,
                          block_timestamp: float | None = None) -> str:
        current = block_timestamp if block_timestamp is not None else time.time()
        elapsed = current - parent_timestamp
        skips = max(0, int(elapsed / (BLOCK_INTERVAL * 3)))
        return addresses[(base + skips) % n]

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

        # Revoked signing key cannot produce blocks (SPRINT2-014)
        if self._revoked_keys is not None:
            if f"{block.validator}:signing" in self._revoked_keys:
                return False, "validator signing key revoked"

        expected = self.select_validator(block.index,
                                        parent_timestamp=parent.timestamp,
                                        parent_hash=parent.block_hash,
                                        block_timestamp=block.timestamp)
        if expected and block.validator != expected:
            return False, (f"wrong validator turn: expected {expected[:16]}..., "
                           f"got {block.validator[:16]}...")

        pk = self.validators[block.validator]
        if not block.verify_signature(pk):
            return False, "invalid block signature"

        # ---- EIP-1559 dynamic fee validation ----
        _dynamic_active = block.index >= DYNAMIC_FEE_ACTIVATION_HEIGHT and block.index > 0
        if _dynamic_active:
            # Verify base_fee derivation from parent
            # First dynamic block or parent is pre-activation/genesis: use initial
            _parent_pre = (parent.index == 0
                           or parent.index < DYNAMIC_FEE_ACTIVATION_HEIGHT)
            if block.index == DYNAMIC_FEE_ACTIVATION_HEIGHT or _parent_pre:
                expected_bf = INITIAL_BASE_FEE
            else:
                parent_eff_weight = effective_block_weight(
                    parent.transactions, parent.validator)
                expected_bf = compute_base_fee(parent.base_fee, parent_eff_weight)
            if block.base_fee != expected_bf:
                return False, (f"base_fee mismatch: expected {expected_bf}, "
                               f"got {block.base_fee}")

            # Verify total block weight does not exceed MAX_BLOCK_WEIGHT
            total_weight = sum(TX_WEIGHTS.get(tx.tx_type.value, 0)
                               for tx in block.transactions)
            if total_weight > MAX_BLOCK_WEIGHT:
                return False, (f"block weight {total_weight} > "
                               f"{MAX_BLOCK_WEIGHT}")

            # Verify self-TX weight ratio (anti-spam)
            self_weight = sum(TX_WEIGHTS.get(tx.tx_type.value, 0)
                              for tx in block.transactions
                              if tx.sender == block.validator)
            if total_weight > 0 and self_weight * 100 > total_weight * MAX_SELF_TX_WEIGHT_RATIO:
                return False, "self-tx weight exceeds 25%"

        # ---- Tx count limit ----
        if len(block.transactions) > MAX_TX_PER_BLOCK:
            return False, f"too many txs: {len(block.transactions)} > {MAX_TX_PER_BLOCK}"

        # ---- Non-genesis must have transactions (pre-activation only) ----
        if not block.transactions and not _dynamic_active:
            return False, "non-genesis block must contain at least one transaction"

        # ---- Block size (fast estimate without full serialization) ----
        estimated = 500 + len(block.transactions) * _MAX_TX_WIRE_SIZE
        if estimated > MAX_BLOCK_SIZE * 2:
            return False, "block estimated too large"

        # ---- Transactions ----
        seen_ids = set()
        sender_nonces: dict[str, int] = {}      # sender -> last nonce in block
        sender_first_nonce: dict[str, int] = {}  # sender -> first nonce in block (O(1))

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

            # EIP-1559: TX must offer at least base_fee per weight unit
            if _dynamic_active:
                w = TX_WEIGHTS.get(tx.tx_type.value, 0)
                if w > 0 and tx.max_fee_per_weight < block.base_fee:
                    return False, (f"max_fee_per_weight {tx.max_fee_per_weight} "
                                   f"< base_fee {block.base_fee}")

            # Revoked signing key cannot submit transactions
            if self._revoked_keys is not None:
                if f"{tx.sender}:signing" in self._revoked_keys:
                    return False, (f"tx from revoked signing key: "
                                   f"{tx.sender[:16]}...")

            # Nonce: check sequential within block
            prev_nonce = sender_nonces.get(tx.sender)
            if prev_nonce is not None and tx.nonce != prev_nonce + 1:
                return False, (f"nonce gap for {tx.sender[:16]}...: "
                               f"expected {prev_nonce + 1}, got {tx.nonce}")
            if tx.sender not in sender_first_nonce:
                sender_first_nonce[tx.sender] = tx.nonce
            sender_nonces[tx.sender] = tx.nonce

        # Nonce: check first nonce per sender matches chain state — O(senders) not O(txs)
        if self._chain_nonces is not None:
            for sender in sender_nonces:
                expected_start = self._chain_nonces.get(sender, -1) + 1
                actual_start = sender_first_nonce[sender]
                if actual_start != expected_start:
                    return False, (
                        f"nonce mismatch for {sender[:16]}...: "
                        f"chain expects {expected_start}, "
                        f"block starts at {actual_start}")

        return True, ""

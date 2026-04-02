"""QVault transaction types: NOTARIZE, STORE, SHARE, REGISTER_KEY, REGISTER_VALIDATOR, STAKE, DELEGATE, UNSTAKE, EVIDENCE, TRANSFER, ISSUE_TOKEN, MINT_TOKEN, TRANSFER_TOKEN."""
import json
import re
import time
from enum import Enum
from ..crypto import MLDSA, sha3_256
from ..config import (MAX_TX_PAYLOAD_SIZE, CHAIN_ID, MIN_STAKE, MAX_STAKE,
                      MAX_TOKEN_NAME_LENGTH, MAX_TOKEN_SYMBOL_LENGTH,
                      MIN_TOKEN_SYMBOL_LENGTH, MAX_TOKEN_DECIMALS,
                      MAX_SUPPLY, MAX_TX_AMOUNT)

# Upper bound for EIP-1559-style fee parameters (2^63)
_MAX_FEE_PARAM = 2 ** 63


class TxType(str, Enum):
    REGISTER_KEY = "REGISTER_KEY"
    REGISTER_VALIDATOR = "REGISTER_VALIDATOR"
    REVOKE_KEY = "REVOKE_KEY"
    NOTARIZE = "NOTARIZE"
    STORE = "STORE"
    SHARE = "SHARE"
    STAKE = "STAKE"
    DELEGATE = "DELEGATE"
    UNSTAKE = "UNSTAKE"
    EVIDENCE = "EVIDENCE"
    TRANSFER = "TRANSFER"
    ISSUE_TOKEN = "ISSUE_TOKEN"
    MINT_TOKEN = "MINT_TOKEN"
    TRANSFER_TOKEN = "TRANSFER_TOKEN"


_HEX_RE = re.compile(r'^[0-9a-fA-F]+$')


class Transaction:
    """An immutable, signed transaction on the QVault chain."""

    __slots__ = (
        'tx_type', 'sender', 'recipient', 'timestamp', 'payload',
        'signature', 'sender_pubkey', 'nonce', 'chain_id',
        'max_fee_per_weight', 'max_priority_fee',
        '_cached_signable', '_cached_id',
    )

    def __init__(self, tx_type: TxType, sender: str, payload: dict,
                 recipient: str = "", timestamp: int = None,
                 signature: bytes = b'', sender_pubkey: bytes = b'',
                 nonce: int = 0, chain_id: str = CHAIN_ID,
                 max_fee_per_weight: int = 0, max_priority_fee: int = 0):
        self.tx_type = tx_type
        self.sender = sender
        self.recipient = recipient
        self.timestamp = timestamp if timestamp is not None else int(time.time())
        self.payload = payload
        self.signature = signature
        self.sender_pubkey = sender_pubkey
        self.nonce = nonce
        self.chain_id = chain_id
        self.max_fee_per_weight = max_fee_per_weight
        self.max_priority_fee = max_priority_fee
        self._cached_signable: bytes | None = None
        self._cached_id: str | None = None

    def _signable_bytes(self) -> bytes:
        if self._cached_signable is None:
            obj = {
                "chainId": self.chain_id,
                "from": self.sender,
                "maxFeePerWeight": self.max_fee_per_weight,
                "maxPriorityFee": self.max_priority_fee,
                "nonce": self.nonce,
                "payload": self.payload,
                "timestamp": self.timestamp,
                "to": self.recipient,
                "type": self.tx_type.value,
            }
            self._cached_signable = json.dumps(
                obj, sort_keys=True, separators=(',', ':')).encode()
        return self._cached_signable

    @property
    def tx_id(self) -> str:
        if self._cached_id is None:
            self._cached_id = sha3_256(self._signable_bytes()).hex()
        return self._cached_id

    def sign(self, signing_sk: bytes, signing_pk: bytes):
        self.sender_pubkey = signing_pk
        self.signature = MLDSA.sign(signing_sk, self._signable_bytes())

    def verify(self) -> bool:
        if not self.signature or not self.sender_pubkey:
            return False
        from .wallet import Wallet
        if Wallet.derive_address(self.sender_pubkey) != self.sender:
            return False
        return MLDSA.verify(self.sender_pubkey, self._signable_bytes(), self.signature)

    _ALLOWED_KEYS = {
        TxType.REGISTER_KEY: {"encryption_pk"},
        TxType.REGISTER_VALIDATOR: {"validator_pubkey", "validator_address", "commission"},
        TxType.REVOKE_KEY: {"key_type", "reason"},
        TxType.NOTARIZE: {"documentHash", "metadata"},
        TxType.STORE: {"documentHash", "cid", "metadata"},
        TxType.SHARE: {"cid", "encapsulatedKey", "expires"},
        TxType.STAKE: {"amount", "validator_address"},
        TxType.DELEGATE: {"amount", "validator_address"},
        TxType.UNSTAKE: {"amount", "validator_address"},
        TxType.EVIDENCE: {"evidence_type", "block_a_hash", "block_b_hash",
                          "block_a_sig", "block_b_sig", "block_index",
                          "validator_address",
                          "block_a_header", "block_b_header"},
        TxType.TRANSFER: {"amount", "memo"},
        TxType.ISSUE_TOKEN: {"name", "symbol", "decimals", "max_supply", "transferable"},
        TxType.MINT_TOKEN: {"token_id", "amount"},
        TxType.TRANSFER_TOKEN: {"token_id", "amount", "memo"},
    }

    # EVIDENCE payloads contain two ML-DSA-65 signatures (3309 bytes each = 6618 hex chars)
    # which inherently exceed the standard payload size limit. Use a larger limit.
    _EVIDENCE_MAX_PAYLOAD = 32768  # 32 KB

    def validate_payload(self) -> tuple[bool, str]:
        raw = json.dumps(self.payload, sort_keys=True, separators=(',', ':')).encode()
        max_size = self._EVIDENCE_MAX_PAYLOAD if self.tx_type == TxType.EVIDENCE else MAX_TX_PAYLOAD_SIZE
        if len(raw) > max_size:
            return False, f"payload too large: {len(raw)} > {max_size}"

        # Reject unknown payload keys to prevent dedup bypass
        allowed = self._ALLOWED_KEYS.get(self.tx_type)
        if allowed is not None:
            extra = set(self.payload.keys()) - allowed
            if extra:
                return False, f"unknown payload keys: {extra}"

        # Universal big-integer guard for all amount-bearing TX types (R30-001)
        if self.tx_type in (TxType.TRANSFER, TxType.STAKE, TxType.DELEGATE,
                            TxType.UNSTAKE, TxType.MINT_TOKEN,
                            TxType.TRANSFER_TOKEN):
            amount = self.payload.get("amount")
            if isinstance(amount, int) and amount > MAX_TX_AMOUNT:
                return False, f"amount exceeds MAX_TX_AMOUNT ({MAX_TX_AMOUNT})"
        if self.tx_type == TxType.ISSUE_TOKEN:
            max_supply = self.payload.get("max_supply")
            if isinstance(max_supply, int) and max_supply > MAX_TX_AMOUNT:
                return False, f"max_supply exceeds MAX_TX_AMOUNT ({MAX_TX_AMOUNT})"

        if self.tx_type == TxType.NOTARIZE:
            dh = self.payload.get("documentHash", "")
            if not dh or not _HEX_RE.match(dh):
                return False, "documentHash must be non-empty hex string"
            if len(dh) > 128:
                return False, "documentHash too long (max 128 hex chars)"
            meta = self.payload.get("metadata")
            if meta is not None and not isinstance(meta, str):
                return False, "metadata must be a string"
            if isinstance(meta, str) and len(meta) > 1024:
                return False, "metadata exceeds 1024 characters"

        elif self.tx_type == TxType.STORE:
            dh = self.payload.get("documentHash", "")
            if not dh or not _HEX_RE.match(dh):
                return False, "documentHash must be non-empty hex string"
            if len(dh) > 128:
                return False, "documentHash too long (max 128 hex chars)"
            if not self.payload.get("cid"):
                return False, "cid required for STORE tx"
            meta = self.payload.get("metadata")
            if meta is not None and not isinstance(meta, str):
                return False, "metadata must be a string"
            if isinstance(meta, str) and len(meta) > 1024:
                return False, "metadata exceeds 1024 characters"

        elif self.tx_type == TxType.SHARE:
            if not self.payload.get("cid"):
                return False, "cid required for SHARE tx"
            ek = self.payload.get("encapsulatedKey", "")
            if not ek or not _HEX_RE.match(ek):
                return False, "encapsulatedKey must be non-empty hex"
            exp = self.payload.get("expires", 0)
            if not isinstance(exp, int) or exp < 0:
                return False, "expires must be non-negative integer"

        elif self.tx_type == TxType.REGISTER_KEY:
            epk = self.payload.get("encryption_pk", "")
            if not epk or not _HEX_RE.match(epk):
                return False, "encryption_pk must be non-empty hex"

        elif self.tx_type == TxType.REGISTER_VALIDATOR:
            vpk = self.payload.get("validator_pubkey", "")
            if not vpk or not _HEX_RE.match(vpk):
                return False, "validator_pubkey must be non-empty hex"
            # ML-DSA-65 public key = 1952 bytes = 3904 hex chars
            if len(vpk) != 3904:
                return False, (f"validator_pubkey wrong size: {len(vpk)} hex chars, "
                               f"expected 3904 (1952 bytes)")
            vaddr = self.payload.get("validator_address", "")
            if not vaddr or not isinstance(vaddr, str):
                return False, "validator_address must be non-empty string"
            # Verify the claimed address derives from the claimed pubkey
            from .wallet import Wallet
            expected_addr = Wallet.derive_address(bytes.fromhex(vpk))
            if vaddr != expected_addr:
                return False, "validator_address does not match validator_pubkey"
            # Self-registration only: sender must be the validator (T-01)
            if self.sender != vaddr:
                return False, "REGISTER_VALIDATOR requires sender == validator_address"
            # Optional commission rate (0-100 integer percent)
            commission = self.payload.get("commission")
            if commission is not None:
                if not isinstance(commission, int) or commission < 0 or commission > 100:
                    return False, "commission must be an integer 0-100"

        elif self.tx_type == TxType.REVOKE_KEY:
            _VALID_KEY_TYPES = {"signing", "encryption", "validator"}
            _VALID_REASONS = {"compromised", "rotation", "decommission"}
            kt = self.payload.get("key_type", "")
            if not isinstance(kt, str) or kt not in _VALID_KEY_TYPES:
                return False, (f"key_type must be one of {sorted(_VALID_KEY_TYPES)}, "
                               f"got {kt!r}")
            reason = self.payload.get("reason", "")
            if not isinstance(reason, str) or reason not in _VALID_REASONS:
                return False, (f"reason must be one of {sorted(_VALID_REASONS)}, "
                               f"got {reason!r}")

        elif self.tx_type in (TxType.STAKE, TxType.DELEGATE, TxType.UNSTAKE):
            amount = self.payload.get("amount")
            if not isinstance(amount, int) or amount < MIN_STAKE:
                return False, f"amount must be integer >= {MIN_STAKE}"
            if amount > MAX_STAKE:
                return False, f"amount must be <= {MAX_STAKE}"
            vaddr = self.payload.get("validator_address", "")
            if not isinstance(vaddr, str) or not vaddr:
                return False, "validator_address must be non-empty string"

        elif self.tx_type == TxType.TRANSFER:
            amount = self.payload.get("amount")
            if not isinstance(amount, int) or amount <= 0:
                return False, "amount must be a positive integer"
            if amount > MAX_SUPPLY:
                return False, f"amount exceeds MAX_SUPPLY ({MAX_SUPPLY})"
            memo = self.payload.get("memo")
            if memo is not None:
                if not isinstance(memo, str):
                    return False, "memo must be a string"
                if len(memo) > 256:
                    return False, "memo exceeds 256 characters"
            if not self.recipient:
                return False, "TRANSFER requires a recipient"
            if self.recipient == self.sender:
                return False, "cannot transfer to self"
            # Validate recipient address format (R16-001)
            from ..config import ADDRESS_PREFIX
            if (not self.recipient.startswith(ADDRESS_PREFIX) or
                    len(self.recipient) != 67 or
                    not _HEX_RE.match(self.recipient[3:])):
                return False, "recipient must be a valid qv1 address (67 chars)"

        elif self.tx_type == TxType.EVIDENCE:
            et = self.payload.get("evidence_type", "")
            if et != "double_sign":
                return False, f"evidence_type must be 'double_sign', got {et!r}"
            for field in ("block_a_hash", "block_b_hash", "block_a_sig", "block_b_sig"):
                val = self.payload.get(field, "")
                if not isinstance(val, str) or not val or not _HEX_RE.match(val):
                    return False, f"{field} must be non-empty hex string"
            # block_a_header / block_b_header carry the raw header JSON bytes
            # (hex-encoded) that the validator actually signed.
            for field in ("block_a_header", "block_b_header"):
                val = self.payload.get(field, "")
                if not isinstance(val, str) or not val or not _HEX_RE.match(val):
                    return False, f"{field} must be non-empty hex string"
            # Verify that each header hashes to the claimed block hash
            for hdr_field, hash_field in (("block_a_header", "block_a_hash"),
                                          ("block_b_header", "block_b_hash")):
                hdr_bytes = bytes.fromhex(self.payload[hdr_field])
                computed = sha3_256(hdr_bytes).hex()
                if computed != self.payload[hash_field]:
                    return False, f"{hdr_field} does not hash to {hash_field}"
            bah = self.payload.get("block_a_hash")
            bbh = self.payload.get("block_b_hash")
            if bah == bbh:
                return False, "block_a_hash and block_b_hash must differ (not double-sign)"
            bi = self.payload.get("block_index")
            if not isinstance(bi, int) or bi < 0:
                return False, "block_index must be non-negative integer"
            vaddr = self.payload.get("validator_address", "")
            if not isinstance(vaddr, str) or not vaddr:
                return False, "validator_address must be non-empty string"

        elif self.tx_type == TxType.ISSUE_TOKEN:
            _NAME_RE = re.compile(r'^[a-zA-Z0-9 ]+$')
            _SYMBOL_RE = re.compile(r'^[A-Z][A-Z0-9]+$')
            name = self.payload.get("name", "")
            if not isinstance(name, str) or not name:
                return False, "name must be a non-empty string"
            if len(name) > MAX_TOKEN_NAME_LENGTH:
                return False, f"name exceeds {MAX_TOKEN_NAME_LENGTH} characters"
            if not _NAME_RE.match(name):
                return False, "name must contain only alphanumeric characters and spaces"
            symbol = self.payload.get("symbol", "")
            if not isinstance(symbol, str) or not symbol:
                return False, "symbol must be a non-empty string"
            if len(symbol) < MIN_TOKEN_SYMBOL_LENGTH:
                return False, f"symbol must be at least {MIN_TOKEN_SYMBOL_LENGTH} characters"
            if len(symbol) > MAX_TOKEN_SYMBOL_LENGTH:
                return False, f"symbol exceeds {MAX_TOKEN_SYMBOL_LENGTH} characters"
            if not _SYMBOL_RE.match(symbol):
                return False, "symbol must be uppercase alphanumeric starting with a letter"
            if symbol == "QBIT":
                return False, "symbol QBIT is reserved for the native token"
            decimals = self.payload.get("decimals")
            if not isinstance(decimals, int) or decimals < 0 or decimals > MAX_TOKEN_DECIMALS:
                return False, f"decimals must be an integer 0-{MAX_TOKEN_DECIMALS}"
            max_supply = self.payload.get("max_supply", 0)
            if not isinstance(max_supply, int) or max_supply < 0:
                return False, "max_supply must be a non-negative integer"
            transferable = self.payload.get("transferable", True)
            if not isinstance(transferable, bool):
                return False, "transferable must be a boolean"

        elif self.tx_type == TxType.MINT_TOKEN:
            token_id = self.payload.get("token_id", "")
            if not isinstance(token_id, str) or len(token_id) != 32 or not _HEX_RE.match(token_id):
                return False, "token_id must be a 32-character hex string"
            amount = self.payload.get("amount")
            if not isinstance(amount, int) or amount <= 0:
                return False, "amount must be a positive integer"
            if amount > MAX_SUPPLY:
                return False, f"amount exceeds MAX_SUPPLY ({MAX_SUPPLY})"
            if not self.recipient:
                return False, "MINT_TOKEN requires a recipient"
            from ..config import ADDRESS_PREFIX
            if (not self.recipient.startswith(ADDRESS_PREFIX) or
                    len(self.recipient) != 67 or
                    not _HEX_RE.match(self.recipient[3:])):
                return False, "recipient must be a valid qv1 address (67 chars)"

        elif self.tx_type == TxType.TRANSFER_TOKEN:
            token_id = self.payload.get("token_id", "")
            if not isinstance(token_id, str) or len(token_id) != 32 or not _HEX_RE.match(token_id):
                return False, "token_id must be a 32-character hex string"
            amount = self.payload.get("amount")
            if not isinstance(amount, int) or amount <= 0:
                return False, "amount must be a positive integer"
            if amount > MAX_SUPPLY:
                return False, f"amount exceeds MAX_SUPPLY ({MAX_SUPPLY})"
            if not self.recipient:
                return False, "TRANSFER_TOKEN requires a recipient"
            if self.recipient == self.sender:
                return False, "cannot transfer token to self"
            from ..config import ADDRESS_PREFIX
            if (not self.recipient.startswith(ADDRESS_PREFIX) or
                    len(self.recipient) != 67 or
                    not _HEX_RE.match(self.recipient[3:])):
                return False, "recipient must be a valid qv1 address (67 chars)"
            memo = self.payload.get("memo")
            if memo is not None:
                if not isinstance(memo, str):
                    return False, "memo must be a string"
                if len(memo) > 256:
                    return False, "memo exceeds 256 characters"

        return True, ""

    def to_dict(self) -> dict:
        return {
            "id": self.tx_id,
            "type": self.tx_type.value,
            "from": self.sender,
            "to": self.recipient,
            "timestamp": self.timestamp,
            "payload": self.payload,
            "nonce": self.nonce,
            "chainId": self.chain_id,
            "maxFeePerWeight": self.max_fee_per_weight,
            "maxPriorityFee": self.max_priority_fee,
            "signature": self.signature.hex(),
            "sender_pubkey": self.sender_pubkey.hex(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Transaction':
        if not isinstance(data.get("timestamp"), int):
            raise ValueError("timestamp must be int")
        if not isinstance(data.get("from"), str):
            raise ValueError("from must be string")
        if not isinstance(data.get("nonce", 0), int):
            raise ValueError("nonce must be int")
        if not isinstance(data.get("payload"), dict):
            raise ValueError("payload must be dict")
        try:
            sig = bytes.fromhex(data.get("signature", ""))
            spk = bytes.fromhex(data.get("sender_pubkey", ""))
        except ValueError:
            raise ValueError("signature/sender_pubkey must be valid hex")
        if spk and len(spk) != 1952:  # ML-DSA-65 public key size
            raise ValueError(f"sender_pubkey wrong size: {len(spk)}")
        chain_id = data.get("chainId")
        if chain_id is None:
            raise ValueError("chainId is required")
        if not isinstance(chain_id, str):
            raise ValueError("chainId must be string")
        mfpw = data.get("maxFeePerWeight", 0)
        mpf = data.get("maxPriorityFee", 0)
        if not isinstance(mfpw, int) or mfpw < 0:
            raise ValueError("maxFeePerWeight must be non-negative int")
        if mfpw > _MAX_FEE_PARAM:
            raise ValueError(f"maxFeePerWeight exceeds {_MAX_FEE_PARAM}")
        if not isinstance(mpf, int) or mpf < 0:
            raise ValueError("maxPriorityFee must be non-negative int")
        if mpf > _MAX_FEE_PARAM:
            raise ValueError(f"maxPriorityFee exceeds {_MAX_FEE_PARAM}")
        # Defense-in-depth: validate payload amount fields at deserialization (R30-004)
        tx_type_str = data.get("type", "")
        payload = data["payload"]
        _AMOUNT_TX_TYPES = {"TRANSFER", "STAKE", "DELEGATE", "UNSTAKE",
                            "MINT_TOKEN", "TRANSFER_TOKEN"}
        if tx_type_str in _AMOUNT_TX_TYPES:
            amt = payload.get("amount")
            if amt is not None:
                if not isinstance(amt, int) or isinstance(amt, bool):
                    raise ValueError("payload amount must be an integer")
                if amt <= 0:
                    raise ValueError("payload amount must be positive")
                if amt > MAX_TX_AMOUNT:
                    raise ValueError(
                        f"payload amount exceeds MAX_TX_AMOUNT ({MAX_TX_AMOUNT})")
        if tx_type_str == "ISSUE_TOKEN":
            ms = payload.get("max_supply")
            if ms is not None:
                if not isinstance(ms, int) or isinstance(ms, bool):
                    raise ValueError("payload max_supply must be an integer")
                if ms < 0:
                    raise ValueError("payload max_supply must be non-negative")
                if ms > MAX_TX_AMOUNT:
                    raise ValueError(
                        f"payload max_supply exceeds MAX_TX_AMOUNT ({MAX_TX_AMOUNT})")
        return cls(
            tx_type=TxType(data["type"]),
            sender=data["from"],
            recipient=data.get("to", ""),
            timestamp=data["timestamp"],
            payload=data["payload"],
            nonce=data.get("nonce", 0),
            chain_id=chain_id,
            signature=sig,
            sender_pubkey=spk,
            max_fee_per_weight=mfpw,
            max_priority_fee=mpf,
        )

    # ---- Factory methods ----

    @classmethod
    def register_key(cls, sender: str, encryption_pk: bytes,
                     nonce: int = 0,
                     max_fee_per_weight: int = 0,
                     max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.REGISTER_KEY, sender=sender, nonce=nonce,
            payload={"encryption_pk": encryption_pk.hex()},
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def register_validator(cls, sender: str, validator_pubkey: bytes,
                           validator_address: str,
                           nonce: int = 0,
                           max_fee_per_weight: int = 0,
                           max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.REGISTER_VALIDATOR, sender=sender, nonce=nonce,
            payload={
                "validator_pubkey": validator_pubkey.hex(),
                "validator_address": validator_address,
            },
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def notarize(cls, sender: str, document_hash: str,
                 metadata: str = "", nonce: int = 0,
                 max_fee_per_weight: int = 0,
                 max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.NOTARIZE, sender=sender, nonce=nonce,
            payload={"documentHash": document_hash, "metadata": metadata},
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def store(cls, sender: str, document_hash: str,
              cid: str, metadata: str = "", nonce: int = 0,
              max_fee_per_weight: int = 0,
              max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.STORE, sender=sender, nonce=nonce,
            payload={"documentHash": document_hash, "cid": cid, "metadata": metadata},
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def revoke_key(cls, sender: str, key_type: str, reason: str,
                   nonce: int = 0,
                   max_fee_per_weight: int = 0,
                   max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.REVOKE_KEY, sender=sender, nonce=nonce,
            payload={"key_type": key_type, "reason": reason},
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def share(cls, sender: str, recipient: str, cid: str,
              encapsulated_key: bytes, expires: int = 0,
              nonce: int = 0,
              max_fee_per_weight: int = 0,
              max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.SHARE, sender=sender, recipient=recipient, nonce=nonce,
            payload={
                "cid": cid,
                "encapsulatedKey": encapsulated_key.hex(),
                "expires": expires,
            },
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def stake(cls, sender: str, validator_address: str,
              amount: int, nonce: int = 0,
              max_fee_per_weight: int = 0,
              max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.STAKE, sender=sender, nonce=nonce,
            payload={"amount": amount, "validator_address": validator_address},
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def delegate(cls, sender: str, validator_address: str,
                 amount: int, nonce: int = 0,
                 max_fee_per_weight: int = 0,
                 max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.DELEGATE, sender=sender, nonce=nonce,
            payload={"amount": amount, "validator_address": validator_address},
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def unstake(cls, sender: str, validator_address: str,
                amount: int, nonce: int = 0,
                max_fee_per_weight: int = 0,
                max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.UNSTAKE, sender=sender, nonce=nonce,
            payload={"amount": amount, "validator_address": validator_address},
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def transfer(cls, sender: str, recipient: str, amount: int,
                 memo: str = "", nonce: int = 0,
                 max_fee_per_weight: int = 0,
                 max_priority_fee: int = 0) -> 'Transaction':
        payload = {"amount": amount}
        if memo:
            payload["memo"] = memo
        return cls(
            tx_type=TxType.TRANSFER, sender=sender,
            recipient=recipient, nonce=nonce, payload=payload,
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def evidence(cls, sender: str, validator_address: str,
                 block_index: int, block_a_hash: str, block_b_hash: str,
                 block_a_sig: str, block_b_sig: str,
                 block_a_header: str = "", block_b_header: str = "",
                 nonce: int = 0,
                 max_fee_per_weight: int = 0,
                 max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.EVIDENCE, sender=sender, nonce=nonce,
            payload={
                "evidence_type": "double_sign",
                "block_a_hash": block_a_hash,
                "block_b_hash": block_b_hash,
                "block_a_sig": block_a_sig,
                "block_b_sig": block_b_sig,
                "block_a_header": block_a_header,
                "block_b_header": block_b_header,
                "block_index": block_index,
                "validator_address": validator_address,
            },
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def issue_token(cls, sender: str, name: str, symbol: str,
                    decimals: int, max_supply: int = 0,
                    transferable: bool = True, nonce: int = 0,
                    max_fee_per_weight: int = 0,
                    max_priority_fee: int = 0) -> 'Transaction':
        payload = {
            "name": name,
            "symbol": symbol,
            "decimals": decimals,
            "max_supply": max_supply,
            "transferable": transferable,
        }
        return cls(
            tx_type=TxType.ISSUE_TOKEN, sender=sender, nonce=nonce,
            payload=payload,
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def mint_token(cls, sender: str, recipient: str, token_id: str,
                   amount: int, nonce: int = 0,
                   max_fee_per_weight: int = 0,
                   max_priority_fee: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.MINT_TOKEN, sender=sender,
            recipient=recipient, nonce=nonce,
            payload={"token_id": token_id, "amount": amount},
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

    @classmethod
    def transfer_token(cls, sender: str, recipient: str, token_id: str,
                       amount: int, memo: str = "", nonce: int = 0,
                       max_fee_per_weight: int = 0,
                       max_priority_fee: int = 0) -> 'Transaction':
        payload = {"token_id": token_id, "amount": amount}
        if memo:
            payload["memo"] = memo
        return cls(
            tx_type=TxType.TRANSFER_TOKEN, sender=sender,
            recipient=recipient, nonce=nonce, payload=payload,
            max_fee_per_weight=max_fee_per_weight,
            max_priority_fee=max_priority_fee,
        )

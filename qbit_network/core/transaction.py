"""QVault transaction types: NOTARIZE, STORE, SHARE, REGISTER_KEY, REGISTER_VALIDATOR."""
import json
import re
import time
from enum import Enum
from ..crypto import MLDSA, sha3_256
from ..config import MAX_TX_PAYLOAD_SIZE, CHAIN_ID


class TxType(str, Enum):
    REGISTER_KEY = "REGISTER_KEY"
    REGISTER_VALIDATOR = "REGISTER_VALIDATOR"
    NOTARIZE = "NOTARIZE"
    STORE = "STORE"
    SHARE = "SHARE"


_HEX_RE = re.compile(r'^[0-9a-fA-F]+$')


class Transaction:
    """An immutable, signed transaction on the QVault chain."""

    __slots__ = (
        'tx_type', 'sender', 'recipient', 'timestamp', 'payload',
        'signature', 'sender_pubkey', 'nonce', 'chain_id',
        '_cached_signable', '_cached_id',
    )

    def __init__(self, tx_type: TxType, sender: str, payload: dict,
                 recipient: str = "", timestamp: int = None,
                 signature: bytes = b'', sender_pubkey: bytes = b'',
                 nonce: int = 0, chain_id: str = CHAIN_ID):
        self.tx_type = tx_type
        self.sender = sender
        self.recipient = recipient
        self.timestamp = timestamp if timestamp is not None else int(time.time())
        self.payload = payload
        self.signature = signature
        self.sender_pubkey = sender_pubkey
        self.nonce = nonce
        self.chain_id = chain_id
        self._cached_signable: bytes | None = None
        self._cached_id: str | None = None

    def _signable_bytes(self) -> bytes:
        if self._cached_signable is None:
            obj = {
                "chainId": self.chain_id,
                "from": self.sender,
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
        TxType.REGISTER_VALIDATOR: {"validator_pubkey", "validator_address"},
        TxType.NOTARIZE: {"documentHash", "metadata"},
        TxType.STORE: {"documentHash", "cid", "metadata"},
        TxType.SHARE: {"cid", "encapsulatedKey", "expires"},
    }

    def validate_payload(self) -> tuple[bool, str]:
        raw = json.dumps(self.payload, sort_keys=True, separators=(',', ':')).encode()
        if len(raw) > MAX_TX_PAYLOAD_SIZE:
            return False, f"payload too large: {len(raw)} > {MAX_TX_PAYLOAD_SIZE}"

        # Reject unknown payload keys to prevent dedup bypass
        allowed = self._ALLOWED_KEYS.get(self.tx_type)
        if allowed is not None:
            extra = set(self.payload.keys()) - allowed
            if extra:
                return False, f"unknown payload keys: {extra}"

        if self.tx_type == TxType.NOTARIZE:
            dh = self.payload.get("documentHash", "")
            if not dh or not _HEX_RE.match(dh):
                return False, "documentHash must be non-empty hex string"

        elif self.tx_type == TxType.STORE:
            dh = self.payload.get("documentHash", "")
            if not dh or not _HEX_RE.match(dh):
                return False, "documentHash must be non-empty hex string"
            if not self.payload.get("cid"):
                return False, "cid required for STORE tx"

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
        return cls(
            tx_type=TxType(data["type"]),
            sender=data["from"],
            recipient=data.get("to", ""),
            timestamp=data["timestamp"],
            payload=data["payload"],
            nonce=data.get("nonce", 0),
            chain_id=data.get("chainId", CHAIN_ID),
            signature=sig,
            sender_pubkey=spk,
        )

    # ---- Factory methods ----

    @classmethod
    def register_key(cls, sender: str, encryption_pk: bytes,
                     nonce: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.REGISTER_KEY, sender=sender, nonce=nonce,
            payload={"encryption_pk": encryption_pk.hex()},
        )

    @classmethod
    def register_validator(cls, sender: str, validator_pubkey: bytes,
                           validator_address: str,
                           nonce: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.REGISTER_VALIDATOR, sender=sender, nonce=nonce,
            payload={
                "validator_pubkey": validator_pubkey.hex(),
                "validator_address": validator_address,
            },
        )

    @classmethod
    def notarize(cls, sender: str, document_hash: str,
                 metadata: str = "", nonce: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.NOTARIZE, sender=sender, nonce=nonce,
            payload={"documentHash": document_hash, "metadata": metadata},
        )

    @classmethod
    def store(cls, sender: str, document_hash: str,
              cid: str, metadata: str = "", nonce: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.STORE, sender=sender, nonce=nonce,
            payload={"documentHash": document_hash, "cid": cid, "metadata": metadata},
        )

    @classmethod
    def share(cls, sender: str, recipient: str, cid: str,
              encapsulated_key: bytes, expires: int = 0,
              nonce: int = 0) -> 'Transaction':
        return cls(
            tx_type=TxType.SHARE, sender=sender, recipient=recipient, nonce=nonce,
            payload={
                "cid": cid,
                "encapsulatedKey": encapsulated_key.hex(),
                "expires": expires,
            },
        )

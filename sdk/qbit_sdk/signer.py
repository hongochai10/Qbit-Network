"""Client-side ML-DSA-65 transaction signing for QBit Network.

Requires ``liboqs-python>=0.15.0`` (optional dependency).
Install with: ``pip install qbit-sdk[pqc]``

Usage::

    from qbit_sdk.signer import KeyPair, TransactionBuilder

    kp = KeyPair.generate()
    tx = (TransactionBuilder()
          .transfer(to="qv1...", amount=1000)
          .set_sender(kp.address)
          .set_nonce(0)
          .build())
    signed = kp.sign_tx(tx)

    from qbit_sdk.client import QBitClient
    client = QBitClient("http://localhost:8545/api/v1")
    result = client.submit_signed(signed)
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any

# SHA3-256 from stdlib
from hashlib import sha3_256 as _sha3_256

# liboqs is an optional dependency — fail fast with a clear message
try:
    import oqs
except ImportError:
    oqs = None  # type: ignore[assignment]

_MLDSA_ALGORITHM = "ML-DSA-65"
_MLDSA_SK_SIZE = 4032
_MLDSA_PK_SIZE = 1952
_MLDSA_SIG_SIZE = 3309

_ADDRESS_PREFIX = "qv1"
_DEFAULT_CHAIN_ID = "qbit-mainnet"

# scrypt KDF parameters (match node wallet format)
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SALT_LEN = 32

# TX types that require a recipient
_RECIPIENT_REQUIRED = {"TRANSFER", "SHARE", "MINT_TOKEN", "TRANSFER_TOKEN"}


def _require_oqs() -> None:
    if oqs is None:
        raise ImportError(
            "liboqs-python is required for key generation and signing. "
            "Install with: pip install liboqs-python>=0.15.0"
        )


def _derive_address(pk: bytes) -> str:
    """Derive qv1... address from ML-DSA-65 public key."""
    h = _sha3_256(pk).digest()
    return _ADDRESS_PREFIX + h.hex()


def _zero_bytearray(ba: bytearray) -> None:
    for i in range(len(ba)):
        ba[i] = 0


@dataclass(frozen=True)
class UnsignedTransaction:
    """An unsigned transaction ready to be signed."""
    tx_type: str
    sender: str
    recipient: str
    timestamp: int
    nonce: int
    chain_id: str
    max_fee_per_weight: int
    max_priority_fee: int
    payload: dict[str, Any]

    def signable_bytes(self) -> bytes:
        obj = {
            "chainId": self.chain_id,
            "from": self.sender,
            "maxFeePerWeight": self.max_fee_per_weight,
            "maxPriorityFee": self.max_priority_fee,
            "nonce": self.nonce,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "to": self.recipient,
            "type": self.tx_type,
        }
        return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()

    @property
    def tx_id(self) -> str:
        return _sha3_256(self.signable_bytes()).hexdigest()


@dataclass(frozen=True)
class SignedTransaction:
    """A signed transaction ready for submission."""
    tx_type: str
    sender: str
    recipient: str
    timestamp: int
    nonce: int
    chain_id: str
    max_fee_per_weight: int
    max_priority_fee: int
    payload: dict[str, Any]
    signature: bytes
    sender_pubkey: bytes

    @property
    def tx_id(self) -> str:
        sb = self._signable_bytes()
        return _sha3_256(sb).hexdigest()

    def _signable_bytes(self) -> bytes:
        obj = {
            "chainId": self.chain_id,
            "from": self.sender,
            "maxFeePerWeight": self.max_fee_per_weight,
            "maxPriorityFee": self.max_priority_fee,
            "nonce": self.nonce,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "to": self.recipient,
            "type": self.tx_type,
        }
        return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the JSON format expected by POST /api/v1/txs."""
        return {
            "type": self.tx_type,
            "from": self.sender,
            "to": self.recipient,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "chainId": self.chain_id,
            "maxFeePerWeight": self.max_fee_per_weight,
            "maxPriorityFee": self.max_priority_fee,
            "payload": self.payload,
            "signature": self.signature.hex(),
            "sender_pubkey": self.sender_pubkey.hex(),
        }

    def verify(self) -> bool:
        """Verify this transaction's signature locally (requires liboqs)."""
        _require_oqs()
        if _derive_address(self.sender_pubkey) != self.sender:
            return False
        with oqs.Signature(_MLDSA_ALGORITHM) as verifier:
            return verifier.verify(
                self._signable_bytes(), self.signature, self.sender_pubkey
            )


class KeyPair:
    """ML-DSA-65 keypair for client-side transaction signing.

    Secret key material is stored in a bytearray and zeroed on close/GC.
    """

    def __init__(self, sk: bytes, pk: bytes):
        if len(pk) != _MLDSA_PK_SIZE:
            raise ValueError(f"public key must be {_MLDSA_PK_SIZE} bytes, got {len(pk)}")
        if len(sk) != _MLDSA_SK_SIZE:
            raise ValueError(f"secret key must be {_MLDSA_SK_SIZE} bytes, got {len(sk)}")
        self._sk = bytearray(sk)
        self._pk = bytes(pk)
        self._address = _derive_address(pk)

    @property
    def address(self) -> str:
        return self._address

    @property
    def public_key(self) -> bytes:
        return self._pk

    @classmethod
    def generate(cls) -> KeyPair:
        """Generate a new ML-DSA-65 keypair. No network call needed."""
        _require_oqs()
        with oqs.Signature(_MLDSA_ALGORITHM) as signer:
            pk = signer.generate_keypair()
            sk = signer.export_secret_key()
        return cls(sk=bytes(sk), pk=bytes(pk))

    @classmethod
    def from_secret_key(cls, sk: bytes, pk: bytes) -> KeyPair:
        """Reconstruct keypair from a raw secret key and public key.

        Both SK and PK must be provided because ML-DSA-65 does not embed
        the public key inside the secret key.  The SK format is
        ``(rho || K || tr || s1 || s2 || t0)`` while PK is ``(rho || t1)``
        — PK cannot be derived from SK with current liboqs.
        """
        if len(sk) != _MLDSA_SK_SIZE:
            raise ValueError(f"secret key must be {_MLDSA_SK_SIZE} bytes")
        if len(pk) != _MLDSA_PK_SIZE:
            raise ValueError(f"public key must be {_MLDSA_PK_SIZE} bytes")
        return cls(sk=sk, pk=pk)

    def sign_tx(self, tx: UnsignedTransaction) -> SignedTransaction:
        """Sign an unsigned transaction. Returns a new SignedTransaction."""
        if tx.sender != self._address:
            raise ValueError(
                f"transaction sender {tx.sender} does not match "
                f"keypair address {self._address}"
            )
        _require_oqs()
        signable = tx.signable_bytes()
        with oqs.Signature(_MLDSA_ALGORITHM, secret_key=bytes(self._sk)) as signer:
            signature = signer.sign(signable)
        return SignedTransaction(
            tx_type=tx.tx_type,
            sender=tx.sender,
            recipient=tx.recipient,
            timestamp=tx.timestamp,
            nonce=tx.nonce,
            chain_id=tx.chain_id,
            max_fee_per_weight=tx.max_fee_per_weight,
            max_priority_fee=tx.max_priority_fee,
            payload=tx.payload,
            signature=bytes(signature),
            sender_pubkey=self._pk,
        )

    # ---- Persistence (encrypted file, compatible with node wallet format) ----

    def save(self, filepath: str, password: str) -> None:
        """Save encrypted keypair to file. Password is required."""
        if not password:
            raise ValueError("password is required for saving keypairs")

        salt = os.urandom(_SCRYPT_SALT_LEN)
        dk = bytearray(hashlib.scrypt(
            password.encode(), salt=salt,
            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
        ))

        # AES-256-GCM encryption
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        aes = AESGCM(bytes(dk))
        plaintext = bytearray(
            len(self._sk).to_bytes(4, 'big') + bytes(self._sk)
        )
        ciphertext = aes.encrypt(nonce, bytes(plaintext), self._address.encode())

        _zero_bytearray(dk)
        _zero_bytearray(plaintext)

        data = {
            "version": 1,
            "address": self._address,
            "signing_pk": self._pk.hex(),
            "crypto": {
                "cipher": "aes-256-gcm",
                "kdf": "scrypt",
                "kdfparams": {
                    "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P,
                    "dklen": _SCRYPT_DKLEN, "salt": salt.hex(),
                },
                "ciphertext": ciphertext.hex(),
                "nonce": nonce.hex(),
            },
        }

        dirpath = os.path.dirname(filepath)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)

        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(filepath) or '.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, filepath)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, filepath: str, password: str) -> KeyPair:
        """Load encrypted keypair from file."""
        if not password:
            raise ValueError("password is required")

        with open(filepath, 'r') as f:
            data = json.load(f)

        crypto = data.get("crypto")
        if crypto is None:
            # Legacy unencrypted format (node wallet compat)
            sk = bytes.fromhex(data["signing_sk"])
            pk = bytes.fromhex(data["signing_pk"])
            return cls(sk=sk, pk=pk)

        kdfparams = crypto["kdfparams"]
        salt = bytes.fromhex(kdfparams["salt"])
        n = min(max(kdfparams.get("n", _SCRYPT_N), _SCRYPT_N), 2**20)
        r = min(max(kdfparams.get("r", _SCRYPT_R), _SCRYPT_R), 16)
        p = min(max(kdfparams.get("p", _SCRYPT_P), _SCRYPT_P), 4)

        dk = bytearray(hashlib.scrypt(
            password.encode(), salt=salt,
            n=n, r=r, p=p, dklen=_SCRYPT_DKLEN,
        ))

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = bytes.fromhex(crypto["nonce"])
        ciphertext = bytes.fromhex(crypto["ciphertext"])
        address = data["address"]

        try:
            plaintext = bytearray(
                AESGCM(bytes(dk)).decrypt(nonce, ciphertext, address.encode())
            )
        except Exception:
            _zero_bytearray(dk)
            raise ValueError("decryption failed: wrong password or tampered file")

        _zero_bytearray(dk)

        # Parse length-prefixed secret key
        if len(plaintext) < 4:
            _zero_bytearray(plaintext)
            raise ValueError("truncated ciphertext")
        sk_len = int.from_bytes(plaintext[:4], 'big')
        if sk_len != _MLDSA_SK_SIZE:
            _zero_bytearray(plaintext)
            raise ValueError(f"unexpected secret key length: {sk_len}")
        if len(plaintext) < 4 + sk_len:
            _zero_bytearray(plaintext)
            raise ValueError("truncated secret key data")
        sk = bytes(plaintext[4:4 + sk_len])
        _zero_bytearray(plaintext)

        pk = bytes.fromhex(data["signing_pk"])
        kp = cls(sk=sk, pk=pk)
        if kp.address != address:
            raise ValueError("address mismatch after decryption")
        return kp

    def close(self) -> None:
        """Zero secret key material."""
        _zero_bytearray(self._sk)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> KeyPair:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class TransactionBuilder:
    """Fluent builder for constructing unsigned transactions."""

    def __init__(self, chain_id: str = _DEFAULT_CHAIN_ID):
        self._chain_id = chain_id
        self._tx_type: str | None = None
        self._sender: str = ""
        self._recipient: str = ""
        self._nonce: int | None = None
        self._timestamp: int | None = None
        self._max_fee_per_weight: int = 0
        self._max_priority_fee: int = 0
        self._payload: dict[str, Any] = {}

    # ---- TX type factory methods ----

    def transfer(self, to: str, amount: int, memo: str = "") -> TransactionBuilder:
        self._tx_type = "TRANSFER"
        self._recipient = to
        self._payload = {"amount": amount}
        if memo:
            self._payload["memo"] = memo
        return self

    def notarize(self, document_hash: str, metadata: str = "") -> TransactionBuilder:
        self._tx_type = "NOTARIZE"
        self._payload = {"documentHash": document_hash}
        if metadata:
            self._payload["metadata"] = metadata
        return self

    def store(self, document_hash: str, cid: str, metadata: str = "") -> TransactionBuilder:
        self._tx_type = "STORE"
        self._payload = {"documentHash": document_hash, "cid": cid}
        if metadata:
            self._payload["metadata"] = metadata
        return self

    def share(self, to: str, cid: str, encapsulated_key: str,
              expires: int = 0) -> TransactionBuilder:
        self._tx_type = "SHARE"
        self._recipient = to
        self._payload = {"cid": cid, "encapsulatedKey": encapsulated_key}
        if expires:
            self._payload["expires"] = expires
        return self

    def stake(self, validator: str, amount: int) -> TransactionBuilder:
        self._tx_type = "STAKE"
        self._payload = {"amount": amount, "validator_address": validator}
        return self

    def delegate(self, validator: str, amount: int) -> TransactionBuilder:
        self._tx_type = "DELEGATE"
        self._payload = {"amount": amount, "validator_address": validator}
        return self

    def unstake(self, validator: str, amount: int) -> TransactionBuilder:
        self._tx_type = "UNSTAKE"
        self._payload = {"amount": amount, "validator_address": validator}
        return self

    def register_key(self, encryption_pk: str) -> TransactionBuilder:
        self._tx_type = "REGISTER_KEY"
        self._payload = {"encryption_pk": encryption_pk}
        return self

    def register_validator(self, validator_pubkey: str, validator_address: str,
                           commission: int = 0) -> TransactionBuilder:
        self._tx_type = "REGISTER_VALIDATOR"
        self._payload = {
            "validator_pubkey": validator_pubkey,
            "validator_address": validator_address,
            "commission": commission,
        }
        return self

    def revoke_key(self, key_type: str, reason: str) -> TransactionBuilder:
        self._tx_type = "REVOKE_KEY"
        self._payload = {"key_type": key_type, "reason": reason}
        return self

    def issue_token(self, name: str, symbol: str, decimals: int,
                    max_supply: int = 0,
                    transferable: bool = True) -> TransactionBuilder:
        self._tx_type = "ISSUE_TOKEN"
        self._payload = {
            "name": name, "symbol": symbol,
            "decimals": decimals, "max_supply": max_supply,
            "transferable": transferable,
        }
        return self

    def mint_token(self, to: str, token_id: str, amount: int) -> TransactionBuilder:
        self._tx_type = "MINT_TOKEN"
        self._recipient = to
        self._payload = {"token_id": token_id, "amount": amount}
        return self

    def transfer_token(self, to: str, token_id: str, amount: int,
                       memo: str = "") -> TransactionBuilder:
        self._tx_type = "TRANSFER_TOKEN"
        self._recipient = to
        self._payload = {"token_id": token_id, "amount": amount}
        if memo:
            self._payload["memo"] = memo
        return self

    # ---- Common setters ----

    def set_sender(self, address: str) -> TransactionBuilder:
        self._sender = address
        return self

    def set_nonce(self, nonce: int) -> TransactionBuilder:
        self._nonce = nonce
        return self

    def set_fee(self, max_fee_per_weight: int,
                max_priority_fee: int = 0) -> TransactionBuilder:
        self._max_fee_per_weight = max_fee_per_weight
        self._max_priority_fee = max_priority_fee
        return self

    def set_timestamp(self, ts: int) -> TransactionBuilder:
        self._timestamp = ts
        return self

    # ---- Build ----

    def build(self) -> UnsignedTransaction:
        """Validate and return an UnsignedTransaction."""
        if not self._tx_type:
            raise ValueError("transaction type not set — call a type method first")
        if not self._sender:
            raise ValueError("sender address not set — call set_sender()")
        if not self._sender.startswith(_ADDRESS_PREFIX) or len(self._sender) != 67:
            raise ValueError(f"invalid sender address format: {self._sender}")
        if self._nonce is None:
            raise ValueError("nonce not set — call set_nonce()")
        if self._nonce < 0:
            raise ValueError("nonce must be non-negative")
        if self._tx_type in _RECIPIENT_REQUIRED and not self._recipient:
            raise ValueError(f"{self._tx_type} requires a recipient — set 'to'")
        if self._recipient and (
            not self._recipient.startswith(_ADDRESS_PREFIX)
            or len(self._recipient) != 67
        ):
            raise ValueError(f"invalid recipient address format: {self._recipient}")
        if self._max_fee_per_weight < 0 or self._max_priority_fee < 0:
            raise ValueError("fee parameters must be non-negative")

        ts = self._timestamp if self._timestamp is not None else int(time.time())

        return UnsignedTransaction(
            tx_type=self._tx_type,
            sender=self._sender,
            recipient=self._recipient,
            timestamp=ts,
            nonce=self._nonce,
            chain_id=self._chain_id,
            max_fee_per_weight=self._max_fee_per_weight,
            max_priority_fee=self._max_priority_fee,
            payload=self._payload,
        )

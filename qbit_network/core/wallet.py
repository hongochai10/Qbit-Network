"""QVault Wallet - dual PQC keypair identity."""
import hashlib
import json
import os
import tempfile
from ..crypto import MLDSA, MLKEM, sha3_256, aes_encrypt, aes_decrypt
from ..config import ADDRESS_PREFIX

# scrypt parameters (OWASP recommended minimum)
_SCRYPT_N = 2**14   # CPU/memory cost (16384)
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SALT_LEN = 32


class Wallet:
    """A QVault wallet holding ML-DSA (signing) and ML-KEM (encryption) keypairs."""

    def __init__(self, signing_sk: bytes, signing_pk: bytes,
                 encryption_sk: bytes, encryption_pk: bytes):
        self.signing_sk = signing_sk
        self.signing_pk = signing_pk
        self.encryption_sk = encryption_sk
        self.encryption_pk = encryption_pk
        self.address = self.derive_address(signing_pk)

    @staticmethod
    def derive_address(signing_pk: bytes) -> str:
        """Derive qv1... address from ML-DSA public key."""
        h = sha3_256(signing_pk)
        return ADDRESS_PREFIX + h.hex()

    @classmethod
    def generate(cls) -> 'Wallet':
        """Generate a new wallet with fresh keypairs."""
        signing_sk, signing_pk = MLDSA.keygen()
        encryption_sk, encryption_pk = MLKEM.keygen()
        return cls(signing_sk, signing_pk, encryption_sk, encryption_pk)

    def sign(self, message: bytes) -> bytes:
        """Sign data with ML-DSA."""
        return MLDSA.sign(self.signing_sk, message)

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "signing_sk": self.signing_sk.hex(),
            "signing_pk": self.signing_pk.hex(),
            "encryption_sk": self.encryption_sk.hex(),
            "encryption_pk": self.encryption_pk.hex(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Wallet':
        return cls(
            signing_sk=bytes.fromhex(data["signing_sk"]),
            signing_pk=bytes.fromhex(data["signing_pk"]),
            encryption_sk=bytes.fromhex(data["encryption_sk"]),
            encryption_pk=bytes.fromhex(data["encryption_pk"]),
        )

    def save(self, filepath: str, password: str = ""):
        """Save wallet to JSON file. If password provided, encrypt with AES-256-GCM."""
        dirpath = os.path.dirname(filepath)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)

        if password:
            payload = self._encrypt(password)
        else:
            payload = {"encrypted": False, **self.to_dict()}

        # Atomic write — prevent corruption on crash
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(filepath) or '.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(payload, f)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, filepath)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, filepath: str, password: str = "") -> 'Wallet':
        """Load wallet from JSON file."""
        with open(filepath, 'r') as f:
            data = json.load(f)
        if data.get("encrypted"):
            if not password:
                raise ValueError("wallet is encrypted, password required")
            return cls._decrypt(data, password)
        return cls.from_dict(data)

    def _encrypt(self, password: str) -> dict:
        """Encrypt all secret keys with AES-256-GCM, key derived via scrypt."""
        salt = os.urandom(_SCRYPT_SALT_LEN)
        dk = hashlib.scrypt(
            password.encode(), salt=salt,
            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)

        # Concatenate secrets with length prefixes for authenticated decryption
        signing_sk_bytes = self.signing_sk
        encryption_sk_bytes = self.encryption_sk
        plaintext = (
            len(signing_sk_bytes).to_bytes(4, 'big') + signing_sk_bytes +
            len(encryption_sk_bytes).to_bytes(4, 'big') + encryption_sk_bytes
        )

        ciphertext = aes_encrypt(dk, plaintext, aad=self.address.encode())

        return {
            "encrypted": True,
            "version": 1,
            "address": self.address,
            "signing_pk": self.signing_pk.hex(),
            "encryption_pk": self.encryption_pk.hex(),
            "salt": salt.hex(),
            "ciphertext": ciphertext.hex(),
            "scrypt": {"n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P},
        }

    @classmethod
    def _decrypt(cls, data: dict, password: str) -> 'Wallet':
        """Decrypt wallet. Raises ValueError on wrong password or tampered data."""
        salt = bytes.fromhex(data["salt"])
        params = data.get("scrypt", {})
        # Enforce min/max scrypt params to prevent downgrade and DoS
        n = min(max(params.get("n", _SCRYPT_N), _SCRYPT_N), 2**20)
        r = min(max(params.get("r", _SCRYPT_R), _SCRYPT_R), 16)
        p = min(max(params.get("p", _SCRYPT_P), _SCRYPT_P), 4)
        dk = hashlib.scrypt(
            password.encode(), salt=salt,
            n=n, r=r, p=p, dklen=_SCRYPT_DKLEN)

        address = data["address"]
        ciphertext = bytes.fromhex(data["ciphertext"])

        try:
            plaintext = aes_decrypt(dk, ciphertext, aad=address.encode())
        except Exception:
            raise ValueError("decryption failed: wrong password or tampered file")

        # Parse length-prefixed secrets with bounds validation
        _MLDSA_SK_SIZE = 4032
        _MLKEM_SK_SIZE = 2400

        offset = 0
        if offset + 4 > len(plaintext):
            raise ValueError("truncated plaintext: missing signing_sk length")
        sk_len = int.from_bytes(plaintext[offset:offset + 4], 'big')
        if sk_len != _MLDSA_SK_SIZE:
            raise ValueError(f"unexpected signing_sk length: {sk_len}")
        offset += 4
        if offset + sk_len > len(plaintext):
            raise ValueError("truncated plaintext: signing_sk incomplete")
        signing_sk = plaintext[offset:offset + sk_len]
        offset += sk_len

        if offset + 4 > len(plaintext):
            raise ValueError("truncated plaintext: missing encryption_sk length")
        ek_len = int.from_bytes(plaintext[offset:offset + 4], 'big')
        if ek_len != _MLKEM_SK_SIZE:
            raise ValueError(f"unexpected encryption_sk length: {ek_len}")
        offset += 4
        if offset + ek_len > len(plaintext):
            raise ValueError("truncated plaintext: encryption_sk incomplete")
        encryption_sk = plaintext[offset:offset + ek_len]
        offset += ek_len

        if offset != len(plaintext):
            raise ValueError(f"trailing data after keys: {len(plaintext) - offset} bytes")

        signing_pk = bytes.fromhex(data["signing_pk"])
        encryption_pk = bytes.fromhex(data["encryption_pk"])

        wallet = cls(signing_sk, signing_pk, encryption_sk, encryption_pk)

        # Verify derived address matches
        if wallet.address != address:
            raise ValueError("address mismatch after decryption (tampered file?)")

        return wallet

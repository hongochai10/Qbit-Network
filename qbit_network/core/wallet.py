"""QVault Wallet - dual PQC keypair identity."""
import hashlib
import json
import os
import tempfile
from ..crypto import MLDSA, MLKEM, sha3_256, aes_encrypt, aes_decrypt
from ..crypto.secure_bytes import SecureBytes
from ..config import ADDRESS_PREFIX

# scrypt parameters (OWASP recommended minimum)
_SCRYPT_N = 2**14   # CPU/memory cost (16384)
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SALT_LEN = 32


def _zero_bytearray(ba: bytearray) -> None:
    """Best-effort zeroing of a bytearray."""
    for i in range(len(ba)):
        ba[i] = 0


class Wallet:
    """A QVault wallet holding ML-DSA (signing) and ML-KEM (encryption) keypairs.

    Secret keys are wrapped in ``SecureBytes`` for secure zeroing.  Use
    ``wallet.close()`` or the context manager protocol to scrub key material
    from memory when done.
    """

    def __init__(self, signing_sk, signing_pk: bytes,
                 encryption_sk, encryption_pk: bytes):
        # Wrap secret keys in SecureBytes if they aren't already
        if isinstance(signing_sk, SecureBytes):
            self.signing_sk = signing_sk
        else:
            self.signing_sk = SecureBytes(signing_sk)
        self.signing_pk = signing_pk

        if isinstance(encryption_sk, SecureBytes):
            self.encryption_sk = encryption_sk
        else:
            self.encryption_sk = SecureBytes(encryption_sk)
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
        dk_bytes = hashlib.scrypt(
            password.encode(), salt=salt,
            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
        # Convert to bytearray so we can zero it after use
        dk = bytearray(dk_bytes)

        # Concatenate secrets with length prefixes for authenticated decryption
        signing_sk_bytes = bytes(self.signing_sk)
        encryption_sk_bytes = bytes(self.encryption_sk)
        plaintext = bytearray(
            len(signing_sk_bytes).to_bytes(4, 'big') + signing_sk_bytes +
            len(encryption_sk_bytes).to_bytes(4, 'big') + encryption_sk_bytes
        )

        ciphertext = aes_encrypt(bytes(dk), bytes(plaintext), aad=self.address.encode())

        # Zero derived key and plaintext
        _zero_bytearray(dk)
        _zero_bytearray(plaintext)

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
        dk_bytes = hashlib.scrypt(
            password.encode(), salt=salt,
            n=n, r=r, p=p, dklen=_SCRYPT_DKLEN)
        dk = bytearray(dk_bytes)

        address = data["address"]
        ciphertext = bytes.fromhex(data["ciphertext"])

        try:
            plaintext = bytearray(aes_decrypt(bytes(dk), ciphertext, aad=address.encode()))
        except Exception:
            _zero_bytearray(dk)
            raise ValueError("decryption failed: wrong password or tampered file")

        # Zero derived key now that decryption is done
        _zero_bytearray(dk)

        # Parse length-prefixed secrets with bounds validation
        _MLDSA_SK_SIZE = 4032
        _MLKEM_SK_SIZE = 2400

        offset = 0
        if offset + 4 > len(plaintext):
            _zero_bytearray(plaintext)
            raise ValueError("truncated plaintext: missing signing_sk length")
        sk_len = int.from_bytes(plaintext[offset:offset + 4], 'big')
        if sk_len != _MLDSA_SK_SIZE:
            _zero_bytearray(plaintext)
            raise ValueError(f"unexpected signing_sk length: {sk_len}")
        offset += 4
        if offset + sk_len > len(plaintext):
            _zero_bytearray(plaintext)
            raise ValueError("truncated plaintext: signing_sk incomplete")
        signing_sk = bytes(plaintext[offset:offset + sk_len])
        offset += sk_len

        if offset + 4 > len(plaintext):
            _zero_bytearray(plaintext)
            raise ValueError("truncated plaintext: missing encryption_sk length")
        ek_len = int.from_bytes(plaintext[offset:offset + 4], 'big')
        if ek_len != _MLKEM_SK_SIZE:
            _zero_bytearray(plaintext)
            raise ValueError(f"unexpected encryption_sk length: {ek_len}")
        offset += 4
        if offset + ek_len > len(plaintext):
            _zero_bytearray(plaintext)
            raise ValueError("truncated plaintext: encryption_sk incomplete")
        encryption_sk = bytes(plaintext[offset:offset + ek_len])
        offset += ek_len

        if offset != len(plaintext):
            _zero_bytearray(plaintext)
            raise ValueError(f"trailing data after keys: {len(plaintext) - offset} bytes")

        # Zero the plaintext buffer now that keys are extracted
        _zero_bytearray(plaintext)

        signing_pk = bytes.fromhex(data["signing_pk"])
        encryption_pk = bytes.fromhex(data["encryption_pk"])

        wallet = cls(signing_sk, signing_pk, encryption_sk, encryption_pk)

        # Verify derived address matches
        if wallet.address != address:
            raise ValueError("address mismatch after decryption (tampered file?)")

        return wallet

    def close(self) -> None:
        """Zero all secret key material.  Safe to call multiple times."""
        if isinstance(self.signing_sk, SecureBytes):
            self.signing_sk.zero()
        if isinstance(self.encryption_sk, SecureBytes):
            self.encryption_sk.zero()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> 'Wallet':
        return self

    def __exit__(self, *args) -> None:
        self.close()

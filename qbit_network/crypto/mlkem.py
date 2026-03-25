"""ML-KEM (CRYSTALS-Kyber) key encapsulation via liboqs."""
import oqs
from ..config import MLKEM_ALGORITHM

# Expected sizes for ML-KEM-768
MLKEM_PK_SIZE = 1184
MLKEM_SK_SIZE = 2400
MLKEM_CT_SIZE = 1088


class MLKEM:
    """ML-KEM-768 key encapsulation mechanism."""

    @staticmethod
    def keygen() -> tuple[bytes, bytes]:
        """Generate ML-KEM keypair.

        Returns (secret_key, public_key).
        """
        with oqs.KeyEncapsulation(MLKEM_ALGORITHM) as kem:
            public_key = kem.generate_keypair()
            secret_key = kem.export_secret_key()
        return secret_key, public_key

    @staticmethod
    def encapsulate(public_key: bytes) -> tuple[bytes, bytes]:
        """Encapsulate a shared secret using recipient's public key.

        Returns (ciphertext, shared_secret).
        Raises ValueError on invalid public key.
        """
        if len(public_key) != MLKEM_PK_SIZE:
            raise ValueError(
                f"invalid ML-KEM public key length: {len(public_key)}, "
                f"expected {MLKEM_PK_SIZE}")
        try:
            with oqs.KeyEncapsulation(MLKEM_ALGORITHM) as kem:
                ciphertext, shared_secret = kem.encap_secret(public_key)
            return ciphertext, shared_secret
        except Exception as e:
            raise ValueError(f"ML-KEM encapsulation failed: {e}") from e

    @staticmethod
    def decapsulate(secret_key, ciphertext: bytes) -> bytes:
        """Decapsulate to recover shared secret.

        ``secret_key`` may be ``bytes`` or ``SecureBytes``.
        Raises ValueError on invalid inputs.
        """
        # Support SecureBytes transparently
        sk_bytes = bytes(secret_key) if not isinstance(secret_key, bytes) else secret_key
        if len(sk_bytes) != MLKEM_SK_SIZE:
            raise ValueError(
                f"invalid ML-KEM secret key length: {len(sk_bytes)}, "
                f"expected {MLKEM_SK_SIZE}")
        if len(ciphertext) != MLKEM_CT_SIZE:
            raise ValueError(
                f"invalid ML-KEM ciphertext length: {len(ciphertext)}, "
                f"expected {MLKEM_CT_SIZE}")
        try:
            with oqs.KeyEncapsulation(MLKEM_ALGORITHM, secret_key=sk_bytes) as kem:
                return kem.decap_secret(ciphertext)
        except Exception as e:
            raise ValueError(f"ML-KEM decapsulation failed: {e}") from e

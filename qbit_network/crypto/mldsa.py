"""ML-DSA (CRYSTALS-Dilithium) digital signatures via liboqs."""
import oqs
from ..config import MLDSA_ALGORITHM


class MLDSA:
    """ML-DSA-65 signing and verification."""

    @staticmethod
    def keygen() -> tuple[bytes, bytes]:
        """Generate ML-DSA keypair.

        Returns (secret_key, public_key).
        """
        with oqs.Signature(MLDSA_ALGORITHM) as signer:
            public_key = signer.generate_keypair()
            secret_key = signer.export_secret_key()
        return secret_key, public_key

    @staticmethod
    def sign(secret_key, message: bytes) -> bytes:
        """Sign message with ML-DSA secret key.

        ``secret_key`` may be ``bytes`` or ``SecureBytes``.
        Raises RuntimeError on failure (e.g. corrupted key).
        """
        # Support SecureBytes transparently
        sk_bytes = bytes(secret_key) if not isinstance(secret_key, bytes) else secret_key
        try:
            with oqs.Signature(MLDSA_ALGORITHM, secret_key=sk_bytes) as signer:
                return signer.sign(message)
        except Exception as e:
            raise RuntimeError(f"ML-DSA signing failed: {e}") from e

    @staticmethod
    def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
        """Verify ML-DSA signature. Returns False on any error."""
        try:
            if len(public_key) == 0 or len(signature) == 0:
                return False
            with oqs.Signature(MLDSA_ALGORITHM) as verifier:
                return verifier.verify(message, signature, public_key)
        except Exception:
            return False

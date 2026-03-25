"""AES-256-GCM authenticated encryption."""
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def aes_encrypt(key: bytes, plaintext: bytes, aad: bytes = b'') -> bytes:
    """Encrypt with AES-256-GCM.

    Args:
        key: 32-byte key (e.g. from ML-KEM shared secret).
        plaintext: data to encrypt.
        aad: additional authenticated data (optional).

    Returns:
        nonce (12 bytes) || ciphertext || tag (16 bytes).
    """
    nonce = os.urandom(12)
    aes = AESGCM(key)
    ciphertext = aes.encrypt(nonce, plaintext, aad)
    return nonce + ciphertext


def aes_decrypt(key: bytes, data: bytes, aad: bytes = b'') -> bytes:
    """Decrypt AES-256-GCM.

    Args:
        key: 32-byte key.
        data: nonce (12) || ciphertext || tag (16).
        aad: additional authenticated data (must match encryption).

    Returns:
        Decrypted plaintext.
    """
    if len(data) < 28:  # 12 nonce + 16 tag minimum
        raise ValueError(f"ciphertext too short: {len(data)} < 28")
    nonce = data[:12]
    ciphertext = data[12:]
    aes = AESGCM(key)
    return aes.decrypt(nonce, ciphertext, aad)

"""Tests for SecureBytes wrapper and its integration with wallet / crypto."""
import gc
import os
import pytest
from qbit_network.crypto.secure_bytes import SecureBytes, wrap_secret
from qbit_network.crypto import MLDSA, MLKEM
from qbit_network.core.wallet import Wallet


# =============================================================================
# SecureBytes unit tests
# =============================================================================

class TestSecureBytesBasic:
    """Core SecureBytes behaviour."""

    def test_create_from_bytes(self):
        sb = SecureBytes(b"secret")
        assert bytes(sb) == b"secret"

    def test_create_from_bytearray(self):
        ba = bytearray(b"secret")
        sb = SecureBytes(ba)
        assert bytes(sb) == b"secret"
        # Source bytearray should be zeroed (best-effort)
        assert ba == bytearray(6)

    def test_len(self):
        sb = SecureBytes(b"hello")
        assert len(sb) == 5

    def test_hex(self):
        sb = SecureBytes(b"\xde\xad")
        assert sb.hex() == "dead"

    def test_repr_live(self):
        sb = SecureBytes(b"x" * 10)
        assert "10 bytes" in repr(sb)

    def test_repr_zeroed(self):
        sb = SecureBytes(b"x" * 10)
        sb.zero()
        assert "zeroed" in repr(sb)

    def test_bool_nonzero(self):
        sb = SecureBytes(b"data")
        assert bool(sb) is True

    def test_bool_empty(self):
        sb = SecureBytes(b"")
        assert bool(sb) is False

    def test_bool_after_zero(self):
        sb = SecureBytes(b"data")
        sb.zero()
        assert bool(sb) is False

    def test_type_error_on_invalid_input(self):
        with pytest.raises(TypeError):
            SecureBytes(42)

    def test_type_error_on_string(self):
        with pytest.raises(TypeError):
            SecureBytes("not bytes")


class TestSecureBytesZeroing:
    """Zeroing and use-after-zero behaviour."""

    def test_zero_sets_flag(self):
        sb = SecureBytes(b"secret")
        assert sb.is_zeroed is False
        sb.zero()
        assert sb.is_zeroed is True

    def test_bytes_after_zero_raises(self):
        sb = SecureBytes(b"secret")
        sb.zero()
        with pytest.raises(ValueError, match="zeroed"):
            bytes(sb)

    def test_hex_after_zero_raises(self):
        sb = SecureBytes(b"secret")
        sb.zero()
        with pytest.raises(ValueError, match="zeroed"):
            sb.hex()

    def test_double_zero_is_safe(self):
        sb = SecureBytes(b"secret")
        sb.zero()
        sb.zero()  # should not raise
        assert sb.is_zeroed is True

    def test_zero_actually_clears_memory(self):
        """After zeroing, the underlying buffer should contain all zeros."""
        data = os.urandom(64)
        sb = SecureBytes(data)
        sb.zero()
        # Access the raw buffer to verify zeros
        raw = bytes(sb._buf)
        assert raw == b"\x00" * 64


class TestSecureBytesContextManager:
    """Context manager protocol."""

    def test_context_manager_zeros_on_exit(self):
        sb = SecureBytes(b"secret")
        with sb:
            assert bytes(sb) == b"secret"
        assert sb.is_zeroed is True

    def test_context_manager_zeros_on_exception(self):
        sb = SecureBytes(b"secret")
        with pytest.raises(RuntimeError):
            with sb:
                raise RuntimeError("boom")
        assert sb.is_zeroed is True


class TestSecureBytesEquality:
    """Equality and hashing."""

    def test_eq_bytes(self):
        sb = SecureBytes(b"hello")
        assert sb == b"hello"
        assert not (sb == b"world")

    def test_eq_secure_bytes(self):
        sb1 = SecureBytes(b"hello")
        sb2 = SecureBytes(b"hello")
        assert sb1 == sb2

    def test_eq_zeroed_returns_false(self):
        sb = SecureBytes(b"hello")
        sb.zero()
        assert sb != b"hello"

    def test_eq_other_zeroed_returns_false(self):
        sb1 = SecureBytes(b"hello")
        sb2 = SecureBytes(b"hello")
        sb2.zero()
        assert sb1 != sb2

    def test_eq_different_type_returns_not_implemented(self):
        sb = SecureBytes(b"hello")
        assert sb != 42

    def test_hash_matches_bytes(self):
        data = b"key material"
        sb = SecureBytes(data)
        assert hash(sb) == hash(data)

    def test_hash_zeroed_raises(self):
        sb = SecureBytes(b"key")
        sb.zero()
        with pytest.raises(ValueError, match="zeroed"):
            hash(sb)


class TestSecureBytesGC:
    """Destructor zeroing via garbage collection."""

    def test_del_zeros_buffer(self):
        sb = SecureBytes(os.urandom(32))
        buf = sb._buf  # keep a reference to the underlying buffer
        del sb
        gc.collect()
        # The buffer should now be all zeros
        assert bytes(buf) == b"\x00" * 32


class TestWrapSecret:
    """wrap_secret convenience factory."""

    def test_wrap_secret_returns_secure_bytes(self):
        sb = wrap_secret(b"data")
        assert isinstance(sb, SecureBytes)
        assert bytes(sb) == b"data"


# =============================================================================
# Integration: MLDSA with SecureBytes
# =============================================================================

class TestMLDSAWithSecureBytes:
    """ML-DSA sign/verify using SecureBytes secret keys."""

    def test_sign_with_secure_bytes(self):
        sk_raw, pk = MLDSA.keygen()
        sk = SecureBytes(sk_raw)
        msg = b"test message"
        sig = MLDSA.sign(sk, msg)
        assert MLDSA.verify(pk, msg, sig)

    def test_sign_still_works_with_plain_bytes(self):
        sk, pk = MLDSA.keygen()
        msg = b"test message"
        sig = MLDSA.sign(sk, msg)
        assert MLDSA.verify(pk, msg, sig)


# =============================================================================
# Integration: MLKEM with SecureBytes
# =============================================================================

class TestMLKEMWithSecureBytes:
    """ML-KEM decapsulate using SecureBytes secret keys."""

    def test_decapsulate_with_secure_bytes(self):
        sk_raw, pk = MLKEM.keygen()
        sk = SecureBytes(sk_raw)
        ct, shared_secret_enc = MLKEM.encapsulate(pk)
        shared_secret_dec = MLKEM.decapsulate(sk, ct)
        assert shared_secret_enc == shared_secret_dec

    def test_decapsulate_still_works_with_plain_bytes(self):
        sk, pk = MLKEM.keygen()
        ct, shared_secret_enc = MLKEM.encapsulate(pk)
        shared_secret_dec = MLKEM.decapsulate(sk, ct)
        assert shared_secret_enc == shared_secret_dec


# =============================================================================
# Integration: Wallet with SecureBytes
# =============================================================================

class TestWalletSecureBytes:
    """Wallet stores keys as SecureBytes and supports close()."""

    def test_generate_wraps_keys_in_secure_bytes(self):
        w = Wallet.generate()
        assert isinstance(w.signing_sk, SecureBytes)
        assert isinstance(w.encryption_sk, SecureBytes)

    def test_wallet_sign_works_with_secure_bytes(self):
        w = Wallet.generate()
        msg = b"blockchain transaction"
        sig = w.sign(msg)
        assert MLDSA.verify(w.signing_pk, msg, sig)

    def test_wallet_close_zeros_keys(self):
        w = Wallet.generate()
        w.close()
        assert w.signing_sk.is_zeroed
        assert w.encryption_sk.is_zeroed

    def test_wallet_close_idempotent(self):
        w = Wallet.generate()
        w.close()
        w.close()  # should not raise

    def test_wallet_context_manager(self):
        with Wallet.generate() as w:
            msg = b"hello"
            sig = w.sign(msg)
            assert MLDSA.verify(w.signing_pk, msg, sig)
        assert w.signing_sk.is_zeroed
        assert w.encryption_sk.is_zeroed

    def test_wallet_to_dict_works(self):
        w = Wallet.generate()
        d = w.to_dict()
        assert len(d["signing_sk"]) == 4032 * 2  # hex
        assert len(d["encryption_sk"]) == 2400 * 2

    def test_wallet_from_dict_wraps_in_secure_bytes(self):
        w = Wallet.generate()
        d = w.to_dict()
        restored = Wallet.from_dict(d)
        assert isinstance(restored.signing_sk, SecureBytes)
        assert isinstance(restored.encryption_sk, SecureBytes)
        assert restored.signing_sk == w.signing_sk

    def test_wallet_key_sizes_via_len(self):
        w = Wallet.generate()
        assert len(w.signing_sk) == 4032
        assert len(w.encryption_sk) == 2400

    def test_wallet_save_load_plaintext(self, tmp_path):
        path = str(tmp_path / "wallet.json")
        w = Wallet.generate()
        w.save(path)
        loaded = Wallet.load(path)
        assert isinstance(loaded.signing_sk, SecureBytes)
        assert loaded.signing_sk == w.signing_sk

    def test_wallet_save_load_encrypted(self, tmp_path):
        path = str(tmp_path / "wallet.json")
        w = Wallet.generate()
        w.save(path, password="Str0ng!Pass#2024")
        loaded = Wallet.load(path, password="Str0ng!Pass#2024")
        assert isinstance(loaded.signing_sk, SecureBytes)
        assert loaded.signing_sk == w.signing_sk
        assert loaded.encryption_sk == w.encryption_sk

    def test_wallet_del_zeros_keys(self):
        w = Wallet.generate()
        sk_ref = w.signing_sk
        ek_ref = w.encryption_sk
        del w
        gc.collect()
        assert sk_ref.is_zeroed
        assert ek_ref.is_zeroed

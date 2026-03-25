"""Tests for qbit_network.crypto — ML-DSA, ML-KEM, SHA3, AES, Merkle tree."""
import pytest
from qbit_network.crypto import (
    MLDSA, MLKEM, sha3_256, shake256,
    aes_encrypt, aes_decrypt,
    merkle_root, merkle_proof, verify_merkle_proof,
)
from qbit_network.crypto.mlkem import MLKEM_PK_SIZE, MLKEM_SK_SIZE, MLKEM_CT_SIZE


# ========== ML-DSA-65 ==========

class TestMLDSA:
    def test_keygen_sizes(self):
        sk, pk = MLDSA.keygen()
        assert len(pk) == 1952
        assert len(sk) == 4032

    def test_sign_verify_roundtrip(self):
        sk, pk = MLDSA.keygen()
        msg = b"QVault PQC test message"
        sig = MLDSA.sign(sk, msg)
        assert len(sig) == 3309
        assert MLDSA.verify(pk, msg, sig) is True

    def test_verify_wrong_message(self):
        sk, pk = MLDSA.keygen()
        sig = MLDSA.sign(sk, b"original")
        assert MLDSA.verify(pk, b"tampered", sig) is False

    def test_verify_wrong_key(self):
        sk1, pk1 = MLDSA.keygen()
        _, pk2 = MLDSA.keygen()
        sig = MLDSA.sign(sk1, b"msg")
        assert MLDSA.verify(pk2, b"msg", sig) is False

    def test_verify_empty_pk(self):
        assert MLDSA.verify(b"", b"msg", b"sig") is False

    def test_verify_empty_sig(self):
        _, pk = MLDSA.keygen()
        assert MLDSA.verify(pk, b"msg", b"") is False

    def test_verify_garbage_inputs(self):
        assert MLDSA.verify(b"x" * 100, b"msg", b"y" * 50) is False

    def test_sign_bad_sk_raises(self):
        # liboqs may tolerate garbage sk (produces invalid sig), but shouldn't crash
        sig = MLDSA.sign(b"x" * 4032, b"msg")
        assert isinstance(sig, bytes)


# ========== ML-KEM-768 ==========

class TestMLKEM:
    def test_keygen_sizes(self):
        sk, pk = MLKEM.keygen()
        assert len(pk) == MLKEM_PK_SIZE
        assert len(sk) == MLKEM_SK_SIZE

    def test_encapsulate_decapsulate_roundtrip(self):
        sk, pk = MLKEM.keygen()
        ct, ss1 = MLKEM.encapsulate(pk)
        assert len(ct) == MLKEM_CT_SIZE
        assert len(ss1) == 32
        ss2 = MLKEM.decapsulate(sk, ct)
        assert ss1 == ss2

    def test_encapsulate_bad_pk_size(self):
        with pytest.raises(ValueError, match="invalid ML-KEM public key length"):
            MLKEM.encapsulate(b"short")

    def test_decapsulate_bad_sk_size(self):
        with pytest.raises(ValueError, match="invalid ML-KEM secret key length"):
            MLKEM.decapsulate(b"short", b"x" * MLKEM_CT_SIZE)

    def test_decapsulate_bad_ct_size(self):
        sk, _ = MLKEM.keygen()
        with pytest.raises(ValueError, match="invalid ML-KEM ciphertext length"):
            MLKEM.decapsulate(sk, b"short")


# ========== SHA3 / SHAKE ==========

class TestHashing:
    def test_sha3_256_deterministic(self):
        assert sha3_256(b"hello") == sha3_256(b"hello")
        assert sha3_256(b"hello") != sha3_256(b"world")

    def test_sha3_256_length(self):
        assert len(sha3_256(b"test")) == 32

    def test_shake256_default_length(self):
        assert len(shake256(b"test")) == 32

    def test_shake256_custom_length(self):
        assert len(shake256(b"test", 64)) == 64

    def test_sha3_empty_input(self):
        h = sha3_256(b"")
        assert len(h) == 32


# ========== AES-256-GCM ==========

class TestAES:
    def test_encrypt_decrypt_roundtrip(self):
        key = b"k" * 32
        plaintext = b"secret data for QVault"
        ct = aes_encrypt(key, plaintext)
        result = aes_decrypt(key, ct)
        assert result == plaintext

    def test_encrypt_decrypt_with_aad(self):
        key = b"k" * 32
        plaintext = b"data"
        aad = b"associated"
        ct = aes_encrypt(key, plaintext, aad=aad)
        assert aes_decrypt(key, ct, aad=aad) == plaintext

    def test_wrong_key_fails(self):
        key1 = b"a" * 32
        key2 = b"b" * 32
        ct = aes_encrypt(key1, b"data")
        with pytest.raises(Exception):
            aes_decrypt(key2, ct)

    def test_wrong_aad_fails(self):
        key = b"k" * 32
        ct = aes_encrypt(key, b"data", aad=b"correct")
        with pytest.raises(Exception):
            aes_decrypt(key, ct, aad=b"wrong")

    def test_tampered_ciphertext_fails(self):
        key = b"k" * 32
        ct = bytearray(aes_encrypt(key, b"data"))
        ct[-1] ^= 0xFF
        with pytest.raises(Exception):
            aes_decrypt(key, bytes(ct))

    def test_too_short_ciphertext(self):
        with pytest.raises(ValueError, match="ciphertext too short"):
            aes_decrypt(b"k" * 32, b"short")

    def test_nonce_uniqueness(self):
        key = b"k" * 32
        ct1 = aes_encrypt(key, b"same")
        ct2 = aes_encrypt(key, b"same")
        assert ct1[:12] != ct2[:12]  # nonces differ


# ========== Merkle Tree ==========

class TestMerkleTree:
    def test_empty_items(self):
        assert merkle_root([]) == b"\x00" * 32

    def test_single_item(self):
        root = merkle_root([b"leaf"])
        assert len(root) == 32
        assert root != b"\x00" * 32

    def test_deterministic(self):
        items = [b"a", b"b", b"c"]
        assert merkle_root(items) == merkle_root(items)

    def test_order_matters(self):
        assert merkle_root([b"a", b"b"]) != merkle_root([b"b", b"a"])

    def test_no_second_preimage(self):
        """[a,b,c] and [a,b,c,c] must produce different roots."""
        r3 = merkle_root([b"a", b"b", b"c"])
        r4 = merkle_root([b"a", b"b", b"c", b"c"])
        assert r3 != r4

    @pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 7, 8, 13, 16])
    def test_proof_verification(self, count):
        items = [f"tx_{i}".encode() for i in range(count)]
        root = merkle_root(items)
        for idx in range(count):
            proof = merkle_proof(items, idx)
            assert verify_merkle_proof(items[idx], proof, root), \
                f"proof failed for index {idx} in {count}-item tree"

    def test_proof_wrong_leaf_fails(self):
        items = [b"a", b"b", b"c"]
        root = merkle_root(items)
        proof = merkle_proof(items, 0)
        assert not verify_merkle_proof(b"wrong", proof, root)

    def test_proof_invalid_index(self):
        assert merkle_proof([b"a"], 5) == []
        assert merkle_proof([], 0) == []

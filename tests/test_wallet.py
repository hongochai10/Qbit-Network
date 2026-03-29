"""Tests for qbit_network.core.wallet — keypair, address, persistence, encryption."""
import json
import os
import pytest
from qbit_network.core.wallet import Wallet
from qbit_network.crypto import MLDSA, MLKEM


class TestWalletGeneration:
    def test_generate_address_prefix(self, wallet):
        assert wallet.address.startswith("qv1")

    def test_generate_address_length(self, wallet):
        assert len(wallet.address) == 67  # "qv1" + 64 hex

    def test_generate_key_sizes(self, wallet):
        assert len(wallet.signing_pk) == 1952
        assert len(wallet.signing_sk) == 4032
        assert len(wallet.encryption_pk) == 1184
        assert len(wallet.encryption_sk) == 2400

    def test_generate_unique_addresses(self):
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        assert w1.address != w2.address

    def test_derive_address_deterministic(self, wallet):
        assert Wallet.derive_address(wallet.signing_pk) == wallet.address

    def test_sign_and_verify(self, wallet):
        msg = b"test message"
        sig = wallet.sign(msg)
        assert MLDSA.verify(wallet.signing_pk, msg, sig)


class TestWalletSerialization:
    def test_to_dict_from_dict_roundtrip(self, wallet):
        d = wallet.to_dict()
        restored = Wallet.from_dict(d)
        assert restored.address == wallet.address
        assert restored.signing_sk == wallet.signing_sk
        assert restored.encryption_sk == wallet.encryption_sk

    def test_to_dict_contains_all_keys(self, wallet):
        d = wallet.to_dict()
        assert set(d.keys()) == {"address", "signing_sk", "signing_pk",
                                  "encryption_sk", "encryption_pk"}


class TestWalletPersistence:
    def test_save_load_plaintext(self, wallet, tmp_dir):
        path = os.path.join(tmp_dir, "wallet.json")
        wallet.save(path)
        loaded = Wallet.load(path)
        assert loaded.address == wallet.address
        assert loaded.signing_sk == wallet.signing_sk

    def test_save_load_encrypted(self, wallet, tmp_dir):
        path = os.path.join(tmp_dir, "wallet.json")
        wallet.save(path, password="Str0ng!Pass#2024")
        loaded = Wallet.load(path, password="Str0ng!Pass#2024")
        assert loaded.address == wallet.address
        assert loaded.signing_sk == wallet.signing_sk
        assert loaded.encryption_sk == wallet.encryption_sk

    def test_wrong_password_raises(self, wallet, tmp_dir):
        path = os.path.join(tmp_dir, "wallet.json")
        wallet.save(path, password="correct")
        with pytest.raises(ValueError, match="wrong password"):
            Wallet.load(path, password="wrong")

    def test_encrypted_missing_password_raises(self, wallet, tmp_dir):
        path = os.path.join(tmp_dir, "wallet.json")
        wallet.save(path, password="secret")
        with pytest.raises(ValueError, match="password required"):
            Wallet.load(path)

    def test_file_permissions(self, wallet, tmp_dir):
        path = os.path.join(tmp_dir, "wallet.json")
        wallet.save(path)
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600

    def test_encrypted_file_is_not_plaintext(self, wallet, tmp_dir):
        path = os.path.join(tmp_dir, "wallet.json")
        wallet.save(path, password="secret")
        with open(path) as f:
            data = json.load(f)
        assert data["encrypted"] is True
        assert "signing_sk" not in data  # sk not stored as plaintext field
        assert "ciphertext" in data

    def test_scrypt_param_downgrade_blocked(self, wallet, tmp_dir):
        """Attacker sets n=1 in file — code enforces minimum, same key derived, loads OK."""
        path = os.path.join(tmp_dir, "wallet.json")
        wallet.save(path, password="test")
        with open(path) as f:
            data = json.load(f)
        data["scrypt"] = {"n": 1, "r": 1, "p": 1}
        with open(path, "w") as f:
            json.dump(data, f)
        loaded = Wallet.load(path, password="test")
        assert loaded.address == wallet.address

    def test_tampered_ciphertext_raises(self, wallet, tmp_dir):
        path = os.path.join(tmp_dir, "wallet.json")
        wallet.save(path, password="test")
        with open(path) as f:
            data = json.load(f)
        ct = bytearray.fromhex(data["ciphertext"])
        ct[-1] ^= 0xFF
        data["ciphertext"] = ct.hex()
        with open(path, "w") as f:
            json.dump(data, f)
        with pytest.raises(ValueError, match="wrong password"):
            Wallet.load(path, password="test")

    def test_tampered_address_raises(self, wallet, tmp_dir):
        path = os.path.join(tmp_dir, "wallet.json")
        wallet.save(path, password="test")
        with open(path) as f:
            data = json.load(f)
        data["address"] = "qv1" + "ff" * 32
        with open(path, "w") as f:
            json.dump(data, f)
        with pytest.raises(ValueError):
            Wallet.load(path, password="test")

    def test_plaintext_save_emits_warning(self, wallet, tmp_dir, caplog):
        """Saving without password logs a security warning."""
        import logging
        path = os.path.join(tmp_dir, "wallet.json")
        with caplog.at_level(logging.WARNING, logger="qbit_network.wallet"):
            wallet.save(path)
        assert any("WITHOUT encryption" in r.message for r in caplog.records)

    def test_encrypted_save_no_warning(self, wallet, tmp_dir, caplog):
        """Saving with password does NOT log the plaintext warning."""
        import logging
        path = os.path.join(tmp_dir, "wallet.json")
        with caplog.at_level(logging.WARNING, logger="qbit_network.wallet"):
            wallet.save(path, password="test")
        assert not any("WITHOUT encryption" in r.message for r in caplog.records)

    def test_wallet_directory_permissions(self, wallet, tmp_dir):
        """Wallet directory should be set to 0o700."""
        subdir = os.path.join(tmp_dir, "wallets")
        path = os.path.join(subdir, "wallet.json")
        wallet.save(path)
        mode = os.stat(subdir).st_mode & 0o777
        assert mode == 0o700

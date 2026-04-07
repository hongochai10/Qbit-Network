"""Tests for client-side ML-DSA-65 transaction signing (sdk/qbit_sdk/signer.py).

Covers:
- KeyPair generation, save/load, address derivation
- TransactionBuilder validation and all 14 TX types
- SignedTransaction serialization and verification
- UnsignedTransaction canonical signable bytes (matches node format)
- Integration: sign → serialize → node Transaction.from_dict → verify
"""
import json
import os
import tempfile
import time

import pytest

# SDK signer module
from sdk.qbit_sdk.signer import (
    KeyPair,
    TransactionBuilder,
    UnsignedTransaction,
    SignedTransaction,
    _derive_address,
    _MLDSA_PK_SIZE,
    _MLDSA_SK_SIZE,
)

# Node-side Transaction for integration verification
from qbit_network.core.transaction import Transaction as NodeTransaction


# ---------------------------------------------------------------------------
# KeyPair
# ---------------------------------------------------------------------------

class TestKeyPair:
    def test_generate(self):
        kp = KeyPair.generate()
        assert kp.address.startswith("qv1")
        assert len(kp.address) == 67
        assert len(kp.public_key) == _MLDSA_PK_SIZE

    def test_address_deterministic(self):
        kp = KeyPair.generate()
        assert _derive_address(kp.public_key) == kp.address

    def test_from_secret_key(self):
        kp1 = KeyPair.generate()
        # Extract raw SK and PK bytes before close
        sk = bytes(kp1._sk)
        pk = bytes(kp1.public_key)
        kp2 = KeyPair.from_secret_key(sk, pk)
        assert kp2.address == kp1.address
        assert kp2.public_key == kp1.public_key

    def test_from_secret_key_wrong_size(self):
        with pytest.raises(ValueError, match="secret key must be"):
            KeyPair.from_secret_key(b"\x00" * 100, b"\x00" * _MLDSA_PK_SIZE)

    def test_save_load_roundtrip(self):
        kp = KeyPair.generate()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            kp.save(path, password="test-password-123")
            # Check file permissions
            mode = os.stat(path).st_mode & 0o777
            assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

            loaded = KeyPair.load(path, password="test-password-123")
            assert loaded.address == kp.address
            assert loaded.public_key == kp.public_key
        finally:
            os.unlink(path)

    def test_load_wrong_password(self):
        kp = KeyPair.generate()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            kp.save(path, password="correct")
            with pytest.raises(ValueError, match="decryption failed"):
                KeyPair.load(path, password="wrong")
        finally:
            os.unlink(path)

    def test_save_requires_password(self):
        kp = KeyPair.generate()
        with pytest.raises(ValueError, match="password is required"):
            kp.save("/tmp/test.json", password="")

    def test_context_manager_zeros_key(self):
        with KeyPair.generate() as kp:
            addr = kp.address
            assert any(b != 0 for b in kp._sk)
        # After exit, SK should be zeroed
        assert all(b == 0 for b in kp._sk)

    def test_close_idempotent(self):
        kp = KeyPair.generate()
        kp.close()
        kp.close()  # Should not raise


# ---------------------------------------------------------------------------
# TransactionBuilder
# ---------------------------------------------------------------------------

class TestTransactionBuilder:
    @pytest.fixture
    def kp(self):
        return KeyPair.generate()

    def test_build_transfer(self, kp):
        kp2 = KeyPair.generate()
        tx = (TransactionBuilder()
              .transfer(to=kp2.address, amount=1000, memo="test")
              .set_sender(kp.address)
              .set_nonce(0)
              .set_timestamp(1700000000)
              .build())
        assert isinstance(tx, UnsignedTransaction)
        assert tx.tx_type == "TRANSFER"
        assert tx.sender == kp.address
        assert tx.recipient == kp2.address
        assert tx.payload == {"amount": 1000, "memo": "test"}
        assert tx.nonce == 0
        assert tx.timestamp == 1700000000
        assert tx.chain_id == "qbit-mainnet"

    def test_build_notarize(self, kp):
        doc_hash = "a" * 64
        tx = (TransactionBuilder()
              .notarize(doc_hash, metadata="test doc")
              .set_sender(kp.address)
              .set_nonce(1)
              .build())
        assert tx.tx_type == "NOTARIZE"
        assert tx.payload["documentHash"] == doc_hash

    def test_build_store(self, kp):
        tx = (TransactionBuilder()
              .store("b" * 64, "QmTest123")
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "STORE"

    def test_build_share(self, kp):
        kp2 = KeyPair.generate()
        tx = (TransactionBuilder()
              .share(to=kp2.address, cid="QmTest", encapsulated_key="ff" * 100)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "SHARE"

    def test_build_stake(self, kp):
        validator = "qv1" + "a" * 64
        tx = (TransactionBuilder()
              .stake(validator, 10000)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "STAKE"
        assert tx.payload["validator_address"] == validator

    def test_build_delegate(self, kp):
        tx = (TransactionBuilder()
              .delegate("qv1" + "b" * 64, 5000)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "DELEGATE"

    def test_build_unstake(self, kp):
        tx = (TransactionBuilder()
              .unstake("qv1" + "c" * 64, 3000)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "UNSTAKE"

    def test_build_register_key(self, kp):
        tx = (TransactionBuilder()
              .register_key("dd" * 592)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "REGISTER_KEY"

    def test_build_register_validator(self, kp):
        tx = (TransactionBuilder()
              .register_validator(
                  validator_pubkey="ee" * 976,
                  validator_address="qv1" + "f" * 64,
                  commission=5)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "REGISTER_VALIDATOR"

    def test_build_revoke_key(self, kp):
        tx = (TransactionBuilder()
              .revoke_key("signing", "compromised")
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "REVOKE_KEY"

    def test_build_issue_token(self, kp):
        tx = (TransactionBuilder()
              .issue_token("TestCoin", "TST", 18, max_supply=1000000)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "ISSUE_TOKEN"

    def test_build_mint_token(self, kp):
        kp2 = KeyPair.generate()
        tx = (TransactionBuilder()
              .mint_token(to=kp2.address, token_id="aa" * 32, amount=500)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "MINT_TOKEN"

    def test_build_transfer_token(self, kp):
        kp2 = KeyPair.generate()
        tx = (TransactionBuilder()
              .transfer_token(to=kp2.address, token_id="bb" * 32, amount=100)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.tx_type == "TRANSFER_TOKEN"

    def test_missing_type_raises(self, kp):
        with pytest.raises(ValueError, match="transaction type not set"):
            TransactionBuilder().set_sender(kp.address).set_nonce(0).build()

    def test_missing_sender_raises(self):
        with pytest.raises(ValueError, match="sender address not set"):
            TransactionBuilder().transfer(to="qv1" + "a" * 64, amount=1).set_nonce(0).build()

    def test_missing_nonce_raises(self, kp):
        with pytest.raises(ValueError, match="nonce not set"):
            TransactionBuilder().transfer(to="qv1" + "a" * 64, amount=1).set_sender(kp.address).build()

    def test_invalid_sender_address(self):
        with pytest.raises(ValueError, match="invalid sender address"):
            (TransactionBuilder()
             .transfer(to="qv1" + "a" * 64, amount=1)
             .set_sender("bad_address")
             .set_nonce(0)
             .build())

    def test_missing_recipient_for_transfer(self, kp):
        builder = TransactionBuilder()
        builder._tx_type = "TRANSFER"
        builder._sender = kp.address
        builder._nonce = 0
        with pytest.raises(ValueError, match="requires a recipient"):
            builder.build()

    def test_negative_nonce_raises(self, kp):
        with pytest.raises(ValueError, match="nonce must be non-negative"):
            (TransactionBuilder()
             .transfer(to="qv1" + "a" * 64, amount=1)
             .set_sender(kp.address)
             .set_nonce(-1)
             .build())

    def test_negative_fee_raises(self, kp):
        with pytest.raises(ValueError, match="fee parameters must be non-negative"):
            (TransactionBuilder()
             .transfer(to="qv1" + "a" * 64, amount=1)
             .set_sender(kp.address)
             .set_nonce(0)
             .set_fee(-1)
             .build())

    def test_custom_chain_id(self, kp):
        tx = (TransactionBuilder(chain_id="qbit-testnet")
              .notarize("cc" * 32)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        assert tx.chain_id == "qbit-testnet"

    def test_auto_timestamp(self, kp):
        before = int(time.time())
        tx = (TransactionBuilder()
              .notarize("dd" * 32)
              .set_sender(kp.address)
              .set_nonce(0)
              .build())
        after = int(time.time())
        assert before <= tx.timestamp <= after


# ---------------------------------------------------------------------------
# Signing and Verification
# ---------------------------------------------------------------------------

class TestSigning:
    def test_sign_and_verify(self):
        kp = KeyPair.generate()
        kp2 = KeyPair.generate()
        tx = (TransactionBuilder()
              .transfer(to=kp2.address, amount=500)
              .set_sender(kp.address)
              .set_nonce(0)
              .set_timestamp(1700000000)
              .build())
        signed = kp.sign_tx(tx)
        assert isinstance(signed, SignedTransaction)
        assert signed.tx_id == tx.tx_id
        assert signed.sender_pubkey == kp.public_key
        assert len(signed.signature) > 0
        assert signed.verify()

    def test_sign_wrong_sender_raises(self):
        kp = KeyPair.generate()
        kp2 = KeyPair.generate()
        tx = (TransactionBuilder()
              .transfer(to="qv1" + "a" * 64, amount=1)
              .set_sender(kp2.address)  # Wrong sender
              .set_nonce(0)
              .build())
        with pytest.raises(ValueError, match="does not match"):
            kp.sign_tx(tx)

    def test_tampered_payload_fails_verify(self):
        kp = KeyPair.generate()
        kp2 = KeyPair.generate()
        tx = (TransactionBuilder()
              .transfer(to=kp2.address, amount=500)
              .set_sender(kp.address)
              .set_nonce(0)
              .set_timestamp(1700000000)
              .build())
        signed = kp.sign_tx(tx)
        # Tamper with the payload
        tampered = SignedTransaction(
            tx_type=signed.tx_type,
            sender=signed.sender,
            recipient=signed.recipient,
            timestamp=signed.timestamp,
            nonce=signed.nonce,
            chain_id=signed.chain_id,
            max_fee_per_weight=signed.max_fee_per_weight,
            max_priority_fee=signed.max_priority_fee,
            payload={"amount": 999999},  # tampered
            signature=signed.signature,
            sender_pubkey=signed.sender_pubkey,
        )
        assert not tampered.verify()


# ---------------------------------------------------------------------------
# Serialization & Node Compatibility
# ---------------------------------------------------------------------------

class TestNodeCompatibility:
    def test_signable_bytes_match_node(self):
        """Ensure SDK signable bytes match node Transaction._signable_bytes()."""
        kp = KeyPair.generate()
        kp2 = KeyPair.generate()
        tx = (TransactionBuilder()
              .transfer(to=kp2.address, amount=1000, memo="compat test")
              .set_sender(kp.address)
              .set_nonce(5)
              .set_timestamp(1700000000)
              .build())

        # Build the same TX on node side
        node_tx = NodeTransaction(
            tx_type=NodeTransaction.__class__.__mro__[0].__module__
            and __import__('qbit_network.core.transaction',
                           fromlist=['TxType']).TxType.TRANSFER,
            sender=kp.address,
            recipient=kp2.address,
            timestamp=1700000000,
            payload={"amount": 1000, "memo": "compat test"},
            nonce=5,
            chain_id="qbit-mainnet",
        )
        assert tx.signable_bytes() == node_tx._signable_bytes()
        assert tx.tx_id == node_tx.tx_id

    def test_signed_tx_to_dict_accepted_by_node(self):
        """SignedTransaction.to_dict() can be parsed by node Transaction.from_dict()."""
        kp = KeyPair.generate()
        kp2 = KeyPair.generate()
        tx = (TransactionBuilder()
              .transfer(to=kp2.address, amount=1000)
              .set_sender(kp.address)
              .set_nonce(0)
              .set_timestamp(1700000000)
              .build())
        signed = kp.sign_tx(tx)
        d = signed.to_dict()

        # Node should accept this dict
        node_tx = NodeTransaction.from_dict(d)
        assert node_tx.verify()
        assert node_tx.tx_id == signed.tx_id
        assert node_tx.sender == kp.address

    def test_all_tx_types_node_compatible(self):
        """All 14 TX types serialize to valid node-parseable dicts."""
        kp = KeyPair.generate()
        kp2 = KeyPair.generate()
        validator = "qv1" + "a" * 64

        builders = [
            TransactionBuilder().transfer(to=kp2.address, amount=100),
            TransactionBuilder().notarize("aa" * 32),
            TransactionBuilder().store("bb" * 32, "QmTest"),
            TransactionBuilder().share(to=kp2.address, cid="QmTest", encapsulated_key="cc" * 100),
            TransactionBuilder().stake(validator, 10000),
            TransactionBuilder().delegate(validator, 5000),
            TransactionBuilder().unstake(validator, 3000),
            TransactionBuilder().register_key("dd" * 592),
            TransactionBuilder().register_validator("ee" * 976, validator, 5),
            TransactionBuilder().revoke_key("signing", "test"),
            TransactionBuilder().issue_token("Test", "TST", 18),
            TransactionBuilder().mint_token(to=kp2.address, token_id="ff" * 32, amount=100),
            TransactionBuilder().transfer_token(to=kp2.address, token_id="ff" * 32, amount=50),
        ]

        for i, builder in enumerate(builders):
            tx = (builder
                  .set_sender(kp.address)
                  .set_nonce(i)
                  .set_timestamp(1700000000)
                  .build())
            signed = kp.sign_tx(tx)
            d = signed.to_dict()

            # Must be valid JSON-serializable
            json.dumps(d)

            # Must be parseable by node
            node_tx = NodeTransaction.from_dict(d)
            assert node_tx.verify(), f"TX type {tx.tx_type} failed node verification"


# ---------------------------------------------------------------------------
# UnsignedTransaction
# ---------------------------------------------------------------------------

class TestUnsignedTransaction:
    def test_tx_id_deterministic(self):
        tx = UnsignedTransaction(
            tx_type="TRANSFER",
            sender="qv1" + "a" * 64,
            recipient="qv1" + "b" * 64,
            timestamp=1700000000,
            nonce=0,
            chain_id="qbit-mainnet",
            max_fee_per_weight=0,
            max_priority_fee=0,
            payload={"amount": 100},
        )
        id1 = tx.tx_id
        id2 = tx.tx_id
        assert id1 == id2
        assert len(id1) == 64  # SHA3-256 hex

    def test_signable_bytes_canonical_json(self):
        tx = UnsignedTransaction(
            tx_type="TRANSFER",
            sender="qv1" + "a" * 64,
            recipient="qv1" + "b" * 64,
            timestamp=1700000000,
            nonce=0,
            chain_id="qbit-mainnet",
            max_fee_per_weight=0,
            max_priority_fee=0,
            payload={"amount": 100},
        )
        sb = tx.signable_bytes()
        parsed = json.loads(sb)
        # Must have sorted keys
        keys = list(parsed.keys())
        assert keys == sorted(keys)
        # No whitespace
        assert b' ' not in sb
        assert b'\n' not in sb

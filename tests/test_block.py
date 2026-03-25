"""Tests for qbit_network.core.block — structure, signing, merkle, serialization."""
import pytest
from qbit_network.core.block import Block
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.wallet import Wallet
from qbit_network.crypto import MLDSA


class TestBlockCreation:
    def test_genesis(self, wallet):
        g = Block.genesis(wallet.address)
        assert g.index == 0
        assert g.prev_hash == "0" * 64
        assert len(g.transactions) == 0
        assert g.validator == wallet.address

    def test_block_hash_deterministic(self, wallet):
        g = Block.genesis(wallet.address)
        assert g.block_hash == g.block_hash  # cached
        assert len(g.block_hash) == 64

    def test_merkle_root_empty(self, wallet):
        g = Block.genesis(wallet.address)
        assert g.merkle_root == "0" * 64  # empty tx list → zero hash


class TestBlockSigning:
    def test_sign_and_verify(self, wallet):
        g = Block.genesis(wallet.address)
        g.sign(wallet.signing_sk)
        assert g.verify_signature(wallet.signing_pk) is True

    def test_verify_wrong_key(self, wallet_pair):
        alice, bob = wallet_pair
        g = Block.genesis(alice.address)
        g.sign(alice.signing_sk)
        assert g.verify_signature(bob.signing_pk) is False

    def test_verify_unsigned(self, wallet):
        g = Block.genesis(wallet.address)
        assert g.verify_signature(wallet.signing_pk) is False


class TestBlockSerialization:
    def test_roundtrip_genesis(self, wallet):
        g = Block.genesis(wallet.address)
        g.sign(wallet.signing_sk)
        d = g.to_dict()
        restored = Block.from_dict(d)
        assert restored.block_hash == g.block_hash
        assert restored.index == 0

    def test_roundtrip_with_transactions(self, wallet):
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block = Block(index=1, prev_hash="ff" * 32,
                      transactions=[tx], validator=wallet.address)
        block.sign(wallet.signing_sk)
        d = block.to_dict()
        restored = Block.from_dict(d)
        assert restored.block_hash == block.block_hash
        assert len(restored.transactions) == 1
        assert restored.transactions[0].tx_id == tx.tx_id

    def test_from_dict_hash_mismatch_raises(self, wallet):
        g = Block.genesis(wallet.address)
        d = g.to_dict()
        d["hash"] = "ff" * 32
        with pytest.raises(ValueError, match="block hash mismatch"):
            Block.from_dict(d)

    def test_from_dict_index_must_be_int(self):
        with pytest.raises(ValueError, match="block index must be int"):
            Block.from_dict({"index": "0", "timestamp": 1, "prevHash": "0" * 64,
                             "validator": "", "transactions": [], "signature": ""})

    def test_from_dict_timestamp_must_be_int(self):
        with pytest.raises(ValueError, match="block timestamp must be int"):
            Block.from_dict({"index": 0, "timestamp": "not_int", "prevHash": "0" * 64,
                             "validator": "", "transactions": [], "signature": ""})


class TestMerkleProof:
    def test_proof_for_single_tx(self, wallet):
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block = Block(index=1, prev_hash="ff" * 32,
                      transactions=[tx], validator=wallet.address)
        proof = block.get_tx_proof(0)
        assert block.verify_tx_proof(tx.tx_id, proof) is True

    def test_proof_for_multiple_txs(self, wallet):
        txs = []
        for i in range(5):
            tx = Transaction.notarize(wallet.address, f"{i:02x}" * 16, nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            txs.append(tx)
        block = Block(index=1, prev_hash="ff" * 32,
                      transactions=txs, validator=wallet.address)
        for i, tx in enumerate(txs):
            proof = block.get_tx_proof(i)
            assert block.verify_tx_proof(tx.tx_id, proof), f"proof failed for tx {i}"

    def test_proof_wrong_leaf_fails(self, wallet):
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block = Block(index=1, prev_hash="ff" * 32,
                      transactions=[tx], validator=wallet.address)
        proof = block.get_tx_proof(0)
        assert not block.verify_tx_proof("ff" * 32, proof)

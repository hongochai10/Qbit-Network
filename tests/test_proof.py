"""Tests for qbit_network.core.proof — export and offline verification."""
import json
import pytest
from qbit_network.core.proof import export_proof, verify_proof, PROOF_VERSION, PROOF_TYPE
from qbit_network.core.wallet import Wallet
from qbit_network.core.transaction import Transaction
from qbit_network.core.block import Block
from qbit_network.core.blockchain import Blockchain
from qbit_network.crypto import sha3_256, merkle_root


@pytest.fixture
def wallet():
    return Wallet.generate()


@pytest.fixture
def blockchain(wallet):
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk)
    return bc


def _mine(bc, wallet, doc_hash, nonce):
    """Submit a notarize tx and mine a block, return (block, tx)."""
    tx = Transaction.notarize(wallet.address, doc_hash, nonce=nonce)
    tx.sign(wallet.signing_sk, wallet.signing_pk)
    bc.submit_tx(tx)
    block = bc.produce_block(wallet.address, wallet.signing_sk)
    return block, tx


# =========================================================================
# export_proof
# =========================================================================

class TestExportProof:

    def test_proof_has_correct_version(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "aa" * 32, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "aa" * 32, wallet.address)
        assert proof["version"] == PROOF_VERSION

    def test_proof_has_correct_type(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "bb" * 32, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "bb" * 32, wallet.address)
        assert proof["type"] == PROOF_TYPE

    def test_proof_contains_document_hash(self, blockchain, wallet):
        doc = "cc" * 32
        block, tx = _mine(blockchain, wallet, doc, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, doc, wallet.address)
        assert proof["document_hash"] == doc

    def test_proof_contains_tx_id(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "dd" * 32, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "dd" * 32, wallet.address)
        assert proof["tx_id"] == tx.tx_id

    def test_proof_contains_notarizer_address(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "ee" * 32, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "ee" * 32, wallet.address)
        assert proof["notarizer"] == wallet.address

    def test_proof_contains_block_info(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "ff" * 32, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "ff" * 32, wallet.address)
        b = proof["block"]
        assert b["index"] == block.index
        assert b["hash"] == block.block_hash
        assert b["merkleRoot"] == block.merkle_root
        assert b["validator"] == wallet.address
        assert b["txCount"] == 1

    def test_proof_contains_merkle_proof(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "1122" * 16, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "1122" * 16, wallet.address)
        # Single-tx block: merkle_proof should have one or zero entries
        assert isinstance(proof["merkle_proof"], list)

    def test_proof_contains_notarizer_pubkey(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "3344" * 16, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "3344" * 16, wallet.address)
        assert proof["notarizer_pubkey"] == wallet.signing_pk.hex()

    def test_proof_is_json_serializable(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "5566" * 16, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "5566" * 16, wallet.address)
        serialized = json.dumps(proof)
        restored = json.loads(serialized)
        assert restored["type"] == PROOF_TYPE

    def test_multi_tx_block_proof_correct_index(self, blockchain, wallet):
        """Proof for tx at index 1 in a 2-tx block is valid."""
        tx0 = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        tx0.sign(wallet.signing_sk, wallet.signing_pk)
        tx1 = Transaction.notarize(wallet.address, "bb" * 32, nonce=1)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx0)
        blockchain.submit_tx(tx1)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        block_dict = block.to_dict()
        proof = export_proof(block_dict, 1, tx1.tx_id, "bb" * 32, wallet.address)
        valid, errors = verify_proof(proof)
        assert valid, errors


# =========================================================================
# verify_proof
# =========================================================================

class TestVerifyProof:

    def test_valid_proof_verifies(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "aabb" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "aabb" * 8, wallet.address)
        valid, errors = verify_proof(proof)
        assert valid is True
        assert errors == []

    def test_wrong_type_fails(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "ccdd" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "ccdd" * 8, wallet.address)
        proof["type"] = "wrong-type"
        valid, errors = verify_proof(proof)
        assert valid is False
        assert any("not a valid" in e for e in errors)

    def test_zero_version_fails(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "eeff" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "eeff" * 8, wallet.address)
        proof["version"] = 0
        valid, errors = verify_proof(proof)
        assert valid is False
        assert any("version" in e for e in errors)

    def test_tampered_tx_id_fails(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "1234" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "1234" * 8, wallet.address)
        proof["tx_id"] = "ff" * 32
        valid, errors = verify_proof(proof)
        assert valid is False
        assert any("Merkle" in e for e in errors)

    def test_tampered_block_hash_fails(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "5678" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "5678" * 8, wallet.address)
        proof["block"]["hash"] = "00" * 32
        valid, errors = verify_proof(proof)
        assert valid is False
        assert any("hash mismatch" in e for e in errors)

    def test_tampered_merkle_root_fails(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "abcd" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "abcd" * 8, wallet.address)
        proof["block"]["merkleRoot"] = "ff" * 32
        valid, errors = verify_proof(proof)
        # Both Merkle check AND block hash check should fail
        assert valid is False
        assert len(errors) >= 1

    def test_tampered_block_index_fails(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "cafe" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "cafe" * 8, wallet.address)
        proof["block"]["index"] = 999
        valid, errors = verify_proof(proof)
        assert valid is False
        assert any("hash mismatch" in e for e in errors)

    def test_roundtrip_json_valid(self, blockchain, wallet):
        """Export -> JSON serialize -> deserialize -> verify is still valid."""
        block, tx = _mine(blockchain, wallet, "feed" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "feed" * 8, wallet.address)
        restored = json.loads(json.dumps(proof))
        valid, errors = verify_proof(restored)
        assert valid is True

    def test_empty_merkle_proof_single_tx(self, blockchain, wallet):
        """Single-tx block: verify still works with potentially empty merkle path."""
        block, tx = _mine(blockchain, wallet, "dead" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "dead" * 8, wallet.address)
        valid, errors = verify_proof(proof)
        assert valid is True

    def test_missing_type_field_fails(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "beef" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "beef" * 8, wallet.address)
        del proof["type"]
        valid, errors = verify_proof(proof)
        assert valid is False

    def test_missing_version_field_fails(self, blockchain, wallet):
        block, tx = _mine(blockchain, wallet, "babe" * 8, 0)
        proof = export_proof(block.to_dict(), 0, tx.tx_id, "babe" * 8, wallet.address)
        del proof["version"]
        valid, errors = verify_proof(proof)
        assert valid is False

    def test_multiple_txs_first_proof_valid(self, blockchain, wallet):
        tx0 = Transaction.notarize(wallet.address, "1111" * 16, nonce=0)
        tx0.sign(wallet.signing_sk, wallet.signing_pk)
        tx1 = Transaction.notarize(wallet.address, "2222" * 16, nonce=1)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx0)
        blockchain.submit_tx(tx1)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        proof = export_proof(block.to_dict(), 0, tx0.tx_id, "1111" * 16, wallet.address)
        valid, errors = verify_proof(proof)
        assert valid is True, errors

    def test_multiple_txs_second_proof_valid(self, blockchain, wallet):
        tx0 = Transaction.notarize(wallet.address, "3333" * 16, nonce=0)
        tx0.sign(wallet.signing_sk, wallet.signing_pk)
        tx1 = Transaction.notarize(wallet.address, "4444" * 16, nonce=1)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx0)
        blockchain.submit_tx(tx1)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        proof = export_proof(block.to_dict(), 1, tx1.tx_id, "4444" * 16, wallet.address)
        valid, errors = verify_proof(proof)
        assert valid is True, errors

    def test_cross_proof_wrong_index_fails(self, blockchain, wallet):
        """Using proof built for tx0 but claiming it's for tx1 fails Merkle check."""
        tx0 = Transaction.notarize(wallet.address, "5555" * 16, nonce=0)
        tx0.sign(wallet.signing_sk, wallet.signing_pk)
        tx1 = Transaction.notarize(wallet.address, "6666" * 16, nonce=1)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx0)
        blockchain.submit_tx(tx1)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        # Build proof for tx0 but claim tx1
        proof = export_proof(block.to_dict(), 0, tx0.tx_id, "5555" * 16, wallet.address)
        proof["tx_id"] = tx1.tx_id
        valid, errors = verify_proof(proof)
        assert valid is False

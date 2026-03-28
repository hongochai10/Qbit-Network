"""Tests for QBit Network light client protocol.

Covers block headers, receipt proofs, state proofs at specific blocks,
static verification, and edge cases. ~60 tests total.
"""
import pytest

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.wallet import Wallet
from qbit_network.core.receipt import (
    TransactionReceipt, receipt_proof, verify_receipt_proof, receipts_root,
)
from qbit_network.core.state_tree import StateTrie
from qbit_network.crypto import sha3_256


# ---- Helpers ----

_doc_counter = 0


def _unique_doc_hash():
    """Return a unique document hash for each call."""
    global _doc_counter
    _doc_counter += 1
    return sha3_256(f"test-document-{_doc_counter}".encode()).hex()


@pytest.fixture
def wallet():
    return Wallet.generate()


@pytest.fixture
def wallet2():
    return Wallet.generate()


@pytest.fixture
def sender():
    """A separate wallet used to submit TXs (not the validator)."""
    return Wallet.generate()


@pytest.fixture
def funded_chain(wallet, sender):
    """Blockchain with financial layer active, genesis validator funded,
    and a separate sender funded for submitting TXs."""
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
    bc.activate_financial_layer(wallet.address)
    # Fund the separate sender so it can submit TXs
    _fund_sender(bc, wallet, sender)
    return bc


def _fund_sender(bc, validator_wallet, sender_wallet):
    """Transfer native QBIT from validator to sender using pre-activation rules."""
    nonce = bc.get_nonce(validator_wallet.address)
    tx = Transaction.transfer(
        validator_wallet.address, sender_wallet.address, 1_000_000_000_000,
        nonce=nonce,
    )
    tx.sign(validator_wallet.signing_sk, validator_wallet.signing_pk)
    ok, tx_id = bc.submit_tx(tx)
    assert ok, f"fund_sender submit failed: {tx_id}"
    block = bc.produce_block(validator_wallet.address, validator_wallet.signing_sk)
    assert block is not None, "fund_sender produce_block returned None"


def submit_and_mine(bc, validator_wallet, tx):
    """Submit tx and produce block. Returns (block, tx_id)."""
    ok, tx_id = bc.submit_tx(tx)
    assert ok, f"submit_tx failed: {tx_id}"
    block = bc.produce_block(validator_wallet.address, validator_wallet.signing_sk)
    assert block is not None, "produce_block returned None"
    return block, tx_id


def make_notarize_tx(sender_wallet, bc, doc_hash=None):
    """Create a signed NOTARIZE transaction from the sender (not the validator)."""
    if doc_hash is None:
        doc_hash = _unique_doc_hash()
    nonce = bc.get_nonce(sender_wallet.address) + bc._pool_sender_count.get(sender_wallet.address, 0)
    tx = Transaction.notarize(
        sender_wallet.address, doc_hash, metadata="test", nonce=nonce,
    )
    tx.sign(sender_wallet.signing_sk, sender_wallet.signing_pk)
    return tx


def mine_block(bc, validator_wallet, sender_wallet):
    """Mine a block containing one notarize TX. Returns the block."""
    tx = make_notarize_tx(sender_wallet, bc)
    block, _ = submit_and_mine(bc, validator_wallet, tx)
    return block


# ========================================================================
# 1. BLOCK HEADERS -- to_header_dict
# ========================================================================

class TestBlockToHeaderDict:

    def test_header_has_all_expected_keys(self, funded_chain, wallet, sender):
        """to_header_dict returns dict with all required header fields."""
        block = mine_block(funded_chain, wallet, sender)
        header = block.to_header_dict()
        expected_keys = {
            "hash", "index", "timestamp", "prevHash", "merkleRoot",
            "baseFee", "stateRoot", "receiptsRoot", "validator",
            "txCount", "signature",
        }
        assert set(header.keys()) == expected_keys

    def test_header_has_no_transactions_key(self, funded_chain, wallet, sender):
        """Header dict must not contain a transactions array."""
        tx = make_notarize_tx(sender, funded_chain)
        block, _ = submit_and_mine(funded_chain, wallet, tx)
        header = block.to_header_dict()
        assert "transactions" not in header

    def test_header_hash_matches_block_hash(self, funded_chain, wallet, sender):
        block = mine_block(funded_chain, wallet, sender)
        header = block.to_header_dict()
        assert isinstance(header["hash"], str)
        assert len(header["hash"]) == 64
        assert header["hash"] == block.block_hash

    def test_header_index_matches(self, funded_chain, wallet, sender):
        block = mine_block(funded_chain, wallet, sender)
        assert block.to_header_dict()["index"] == block.index

    def test_header_timestamp_is_int(self, funded_chain, wallet, sender):
        header = mine_block(funded_chain, wallet, sender).to_header_dict()
        assert isinstance(header["timestamp"], int)

    def test_header_prev_hash_matches(self, funded_chain, wallet, sender):
        block = mine_block(funded_chain, wallet, sender)
        assert block.to_header_dict()["prevHash"] == block.prev_hash

    def test_header_merkle_root_matches(self, funded_chain, wallet, sender):
        tx = make_notarize_tx(sender, funded_chain)
        block, _ = submit_and_mine(funded_chain, wallet, tx)
        assert block.to_header_dict()["merkleRoot"] == block.merkle_root

    def test_header_validator_matches(self, funded_chain, wallet, sender):
        block = mine_block(funded_chain, wallet, sender)
        assert block.to_header_dict()["validator"] == wallet.address

    def test_header_tx_count_with_transactions(self, funded_chain, wallet, sender):
        tx = make_notarize_tx(sender, funded_chain)
        block, _ = submit_and_mine(funded_chain, wallet, tx)
        assert block.to_header_dict()["txCount"] == len(block.transactions)
        assert block.to_header_dict()["txCount"] >= 1

    def test_header_signature_is_hex_string(self, funded_chain, wallet, sender):
        header = mine_block(funded_chain, wallet, sender).to_header_dict()
        assert isinstance(header["signature"], str)
        # Should be valid hex
        bytes.fromhex(header["signature"])

    def test_header_state_root_present(self, funded_chain, wallet, sender):
        """After financial layer activation, state root should be non-empty."""
        header = mine_block(funded_chain, wallet, sender).to_header_dict()
        assert isinstance(header["stateRoot"], str)
        assert len(header["stateRoot"]) == 64

    def test_header_receipts_root_present(self, funded_chain, wallet, sender):
        header = mine_block(funded_chain, wallet, sender).to_header_dict()
        assert isinstance(header["receiptsRoot"], str)
        assert len(header["receiptsRoot"]) > 0

    def test_header_base_fee_is_int(self, funded_chain, wallet, sender):
        header = mine_block(funded_chain, wallet, sender).to_header_dict()
        assert isinstance(header["baseFee"], int)

    def test_genesis_header_has_correct_prev_hash(self, funded_chain, wallet):
        """Genesis block should have prev_hash of all zeros."""
        headers = funded_chain.get_block_headers(0, 1)
        assert headers[0]["prevHash"] == "0" * 64


# ========================================================================
# 2. BLOCK HEADERS -- get_block_headers
# ========================================================================

class TestGetBlockHeaders:

    def test_returns_genesis_header(self, funded_chain, wallet):
        headers = funded_chain.get_block_headers(0, 1)
        assert len(headers) == 1
        assert headers[0]["index"] == 0

    def test_returns_correct_range(self, funded_chain, wallet, sender):
        """Mine several blocks and fetch a range."""
        for _ in range(5):
            mine_block(funded_chain, wallet, sender)
        headers = funded_chain.get_block_headers(1, 3)
        assert len(headers) == 3
        assert [h["index"] for h in headers] == [1, 2, 3]

    def test_headers_capped_at_100(self, funded_chain, wallet):
        """Requesting more than 100 headers should be capped."""
        headers = funded_chain.get_block_headers(0, 200)
        # Chain is small, so result count <= chain length, but the cap is applied
        assert len(headers) <= 100

    def test_prev_hash_chain_linkage(self, funded_chain, wallet, sender):
        """Each header's prevHash should match the previous header's hash."""
        for _ in range(4):
            mine_block(funded_chain, wallet, sender)
        headers = funded_chain.get_block_headers(0, 10)
        for i in range(1, len(headers)):
            assert headers[i]["prevHash"] == headers[i - 1]["hash"], \
                f"prevHash linkage broken at index {headers[i]['index']}"

    def test_sequential_indices(self, funded_chain, wallet, sender):
        for _ in range(3):
            mine_block(funded_chain, wallet, sender)
        headers = funded_chain.get_block_headers(0, 10)
        indices = [h["index"] for h in headers]
        assert indices == list(range(len(indices)))

    def test_start_beyond_chain_returns_empty(self, funded_chain, wallet):
        headers = funded_chain.get_block_headers(9999, 10)
        assert headers == []

    def test_partial_range_at_end(self, funded_chain, wallet, sender):
        """Requesting beyond chain tip should return only available headers."""
        mine_block(funded_chain, wallet, sender)
        total = funded_chain._height + 1
        headers = funded_chain.get_block_headers(0, 50)
        assert len(headers) == total

    def test_count_zero_returns_empty(self, funded_chain, wallet):
        headers = funded_chain.get_block_headers(0, 0)
        assert headers == []

    def test_headers_have_no_transactions(self, funded_chain, wallet, sender):
        tx = make_notarize_tx(sender, funded_chain)
        submit_and_mine(funded_chain, wallet, tx)
        headers = funded_chain.get_block_headers(0, 10)
        for h in headers:
            assert "transactions" not in h

    def test_single_header_start_equals_end(self, funded_chain, wallet, sender):
        mine_block(funded_chain, wallet, sender)
        idx = funded_chain._height
        headers = funded_chain.get_block_headers(idx, 1)
        assert len(headers) == 1
        assert headers[0]["index"] == idx


# ========================================================================
# 3. RECEIPT PROOFS -- receipt_proof / verify_receipt_proof (standalone)
# ========================================================================

class TestReceiptProofStandalone:

    def _make_receipts(self, count=3):
        receipts = []
        for i in range(count):
            r = TransactionReceipt(
                tx_id=sha3_256(f"tx-{i}".encode()).hex(),
                status="success",
                fee_paid=100 * i,
                block_index=1,
                tx_index=i,
                events=[],
            )
            receipts.append(r)
        return receipts

    def test_generate_proof_for_each_receipt(self):
        receipts = self._make_receipts(4)
        root = receipts_root(receipts)
        for i, r in enumerate(receipts):
            proof = receipt_proof(receipts, i)
            assert len(proof) > 0
            assert verify_receipt_proof(r.receipt_hash, proof, root)

    def test_proof_fails_with_wrong_root(self):
        receipts = self._make_receipts(3)
        proof = receipt_proof(receipts, 0)
        wrong_root = "ff" * 32
        assert not verify_receipt_proof(receipts[0].receipt_hash, proof, wrong_root)

    def test_proof_fails_with_wrong_receipt_hash(self):
        receipts = self._make_receipts(3)
        root = receipts_root(receipts)
        proof = receipt_proof(receipts, 0)
        wrong_hash = "aa" * 32
        assert not verify_receipt_proof(wrong_hash, proof, root)

    def test_proof_for_single_receipt(self):
        """Single receipt should produce valid (possibly empty) proof."""
        receipts = self._make_receipts(1)
        root = receipts_root(receipts)
        proof = receipt_proof(receipts, 0)
        assert verify_receipt_proof(receipts[0].receipt_hash, proof, root)

    def test_empty_receipts_return_empty_proof(self):
        proof = receipt_proof([], 0)
        assert proof == []

    def test_negative_index_returns_empty_proof(self):
        receipts = self._make_receipts(2)
        proof = receipt_proof(receipts, -1)
        assert proof == []

    def test_out_of_range_index_returns_empty_proof(self):
        receipts = self._make_receipts(2)
        proof = receipt_proof(receipts, 5)
        assert proof == []

    def test_two_receipt_proof_distinctness(self):
        """Proofs for different indices in the same tree should differ."""
        receipts = self._make_receipts(4)
        proof_0 = receipt_proof(receipts, 0)
        proof_1 = receipt_proof(receipts, 1)
        # The leaf is different, so the proofs should not be identical
        assert proof_0 != proof_1


# ========================================================================
# 4. RECEIPT PROOFS -- blockchain.get_receipt_proof
# ========================================================================

class TestBlockchainReceiptProof:

    def test_receipt_proof_for_confirmed_tx(self, funded_chain, wallet, sender):
        tx = make_notarize_tx(sender, funded_chain)
        block, tx_id = submit_and_mine(funded_chain, wallet, tx)
        result = funded_chain.get_receipt_proof(tx_id)
        assert result is not None
        assert result["tx_id"] == tx_id
        assert "receipt" in result
        assert "proof" in result
        assert "receiptsRoot" in result
        assert "block_index" in result

    def test_receipt_proof_verifiable(self, funded_chain, wallet, sender):
        """The returned proof should verify against the receiptsRoot."""
        tx = make_notarize_tx(sender, funded_chain)
        block, tx_id = submit_and_mine(funded_chain, wallet, tx)
        result = funded_chain.get_receipt_proof(tx_id)
        receipt_hash = result["receipt"]["receipt_hash"]
        root = result["receiptsRoot"]
        # Convert proof back to binary tuples
        proof = [(bytes.fromhex(h), is_right) for h, is_right in result["proof"]]
        assert verify_receipt_proof(receipt_hash, proof, root)

    def test_receipt_proof_root_matches_block(self, funded_chain, wallet, sender):
        tx = make_notarize_tx(sender, funded_chain)
        block, tx_id = submit_and_mine(funded_chain, wallet, tx)
        result = funded_chain.get_receipt_proof(tx_id)
        assert result["receiptsRoot"] == block.receipts_root

    def test_receipt_proof_nonexistent_tx(self, funded_chain, wallet):
        result = funded_chain.get_receipt_proof("deadbeef" * 8)
        assert result is None

    def test_receipt_proof_block_index_matches(self, funded_chain, wallet, sender):
        tx = make_notarize_tx(sender, funded_chain)
        block, tx_id = submit_and_mine(funded_chain, wallet, tx)
        result = funded_chain.get_receipt_proof(tx_id)
        assert result["block_index"] == block.index

    def test_multiple_txs_different_proofs(self, funded_chain, wallet, sender):
        """Two TXs in the same block should produce different but valid proofs."""
        tx1 = make_notarize_tx(sender, funded_chain)
        ok1, tx_id1 = funded_chain.submit_tx(tx1)
        assert ok1

        tx2 = make_notarize_tx(sender, funded_chain, doc_hash=sha3_256(b"doc-2").hex())
        ok2, tx_id2 = funded_chain.submit_tx(tx2)
        assert ok2

        block = funded_chain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        proof1 = funded_chain.get_receipt_proof(tx_id1)
        proof2 = funded_chain.get_receipt_proof(tx_id2)
        assert proof1 is not None
        assert proof2 is not None
        # Both proofs should verify
        root = block.receipts_root
        p1 = [(bytes.fromhex(h), r) for h, r in proof1["proof"]]
        p2 = [(bytes.fromhex(h), r) for h, r in proof2["proof"]]
        assert verify_receipt_proof(proof1["receipt"]["receipt_hash"], p1, root)
        assert verify_receipt_proof(proof2["receipt"]["receipt_hash"], p2, root)

    def test_receipt_has_correct_status(self, funded_chain, wallet, sender):
        """Successful TX receipt should have status 'success'."""
        tx = make_notarize_tx(sender, funded_chain)
        _, tx_id = submit_and_mine(funded_chain, wallet, tx)
        result = funded_chain.get_receipt_proof(tx_id)
        assert result["receipt"]["status"] == "success"


# ========================================================================
# 5. STATE PROOFS AT BLOCK -- get_state_proof_at_block
# ========================================================================

class TestStateProofAtBlock:

    def test_proof_at_current_state(self, funded_chain, wallet):
        """Proof at block_index=None uses current state."""
        key = f"balance:{wallet.address}"
        proof = funded_chain.get_state_proof_at_block(key, None)
        assert proof is not None
        assert proof["key"] == key
        assert "value" in proof
        assert "proof" in proof
        assert "root" in proof
        assert "block_index" in proof

    def test_proof_at_specific_block(self, funded_chain, wallet, sender):
        """Proof at a recent block index using state snapshot."""
        mine_block(funded_chain, wallet, sender)
        block_idx = funded_chain._height
        key = f"balance:{wallet.address}"
        proof = funded_chain.get_state_proof_at_block(key, block_idx)
        assert proof is not None
        assert proof["block_index"] == block_idx

    def test_proof_at_genesis(self, funded_chain, wallet):
        """Genesis block snapshot should be available."""
        key = f"balance:{wallet.address}"
        proof = funded_chain.get_state_proof_at_block(key, 0)
        assert proof is not None
        assert proof["block_index"] == 0

    def test_proof_shows_balance_change(self, funded_chain, wallet, wallet2, sender):
        """Balance proof should differ before and after a transfer."""
        # Transfer from sender to wallet2
        nonce = funded_chain.get_nonce(sender.address)
        tx = Transaction.transfer(
            sender.address, wallet2.address, 1000, nonce=nonce,
        )
        tx.sign(sender.signing_sk, sender.signing_pk)
        submit_and_mine(funded_chain, wallet, tx)
        transfer_block_idx = funded_chain._height

        key = f"balance:{wallet2.address}"
        # At genesis, wallet2 had no balance
        proof_genesis = funded_chain.get_state_proof_at_block(key, 0)
        # At transfer block, wallet2 should have balance
        proof_after = funded_chain.get_state_proof_at_block(key, transfer_block_idx)
        assert proof_after is not None
        # Genesis should not have wallet2 balance
        assert proof_genesis is None

    def test_proof_unavailable_for_pruned_block(self, funded_chain, wallet):
        """Pruned snapshots should return None."""
        # Manually remove a snapshot to simulate pruning
        funded_chain._state_snapshots.pop(0, None)
        result = funded_chain.get_state_proof_at_block(f"balance:{wallet.address}", 0)
        assert result is None

    def test_proof_at_nonexistent_block_index(self, funded_chain, wallet):
        result = funded_chain.get_state_proof_at_block("balance:noone", 9999)
        assert result is None

    def test_proof_for_nonexistent_key(self, funded_chain, wallet):
        """Key not in state trie should return None."""
        result = funded_chain.get_state_proof_at_block("balance:nonexistent_address", None)
        assert result is None

    def test_proof_at_block_has_correct_root(self, funded_chain, wallet, sender):
        mine_block(funded_chain, wallet, sender)
        block_idx = funded_chain._height
        key = f"balance:{wallet.address}"
        proof = funded_chain.get_state_proof_at_block(key, block_idx)
        assert proof is not None
        # Root should be a 64-char hex string
        assert isinstance(proof["root"], str)
        assert len(proof["root"]) == 64

    def test_current_proof_block_index_matches_height(self, funded_chain, wallet, sender):
        mine_block(funded_chain, wallet, sender)
        mine_block(funded_chain, wallet, sender)
        key = f"balance:{wallet.address}"
        proof = funded_chain.get_state_proof_at_block(key, None)
        assert proof is not None
        assert proof["block_index"] == funded_chain._height

    def test_nonce_key_proof(self, funded_chain, wallet, sender):
        """State proof should work for nonce keys too."""
        tx = make_notarize_tx(sender, funded_chain)
        submit_and_mine(funded_chain, wallet, tx)
        key = f"nonce:{sender.address}"
        proof = funded_chain.get_state_proof_at_block(key, None)
        assert proof is not None
        assert proof["key"] == key

    def test_proof_at_block_for_nonexistent_key_in_snapshot(self, funded_chain, wallet, sender):
        """A key absent from a historical snapshot should return None."""
        mine_block(funded_chain, wallet, sender)
        block_idx = funded_chain._height
        result = funded_chain.get_state_proof_at_block("balance:nobody", block_idx)
        assert result is None


# ========================================================================
# 6. STATIC VERIFICATION -- StateTrie.verify_proof
# ========================================================================

class TestStateTrieVerifyProof:

    def test_verify_proof_roundtrip(self):
        """Generate a proof and verify it statically."""
        trie = StateTrie()
        trie.set("balance:alice", (500).to_bytes(8, 'big'))
        trie.set("balance:bob", (300).to_bytes(8, 'big'))
        trie.set("nonce:alice", (1).to_bytes(8, 'big'))

        root = trie.root()
        proof_dict = trie.get_proof("balance:alice")
        assert proof_dict is not None

        # Convert proof from serialized form to binary tuples
        proof_tuples = [(bytes.fromhex(h), is_left) for h, is_left in proof_dict["proof"]]
        value = bytes.fromhex(proof_dict["value"])

        assert StateTrie.verify_proof("balance:alice", value, proof_tuples, root)

    def test_verify_proof_fails_wrong_key(self):
        trie = StateTrie()
        trie.set("balance:alice", (500).to_bytes(8, 'big'))
        trie.set("balance:bob", (300).to_bytes(8, 'big'))

        root = trie.root()
        proof_dict = trie.get_proof("balance:alice")
        proof_tuples = [(bytes.fromhex(h), is_left) for h, is_left in proof_dict["proof"]]
        value = bytes.fromhex(proof_dict["value"])

        # Wrong key should fail
        assert not StateTrie.verify_proof("balance:bob", value, proof_tuples, root)

    def test_verify_proof_fails_wrong_value(self):
        trie = StateTrie()
        trie.set("balance:alice", (500).to_bytes(8, 'big'))
        trie.set("balance:bob", (300).to_bytes(8, 'big'))

        root = trie.root()
        proof_dict = trie.get_proof("balance:alice")
        proof_tuples = [(bytes.fromhex(h), is_left) for h, is_left in proof_dict["proof"]]

        wrong_value = (999).to_bytes(8, 'big')
        assert not StateTrie.verify_proof("balance:alice", wrong_value, proof_tuples, root)

    def test_verify_proof_fails_wrong_root(self):
        trie = StateTrie()
        trie.set("balance:alice", (500).to_bytes(8, 'big'))

        proof_dict = trie.get_proof("balance:alice")
        proof_tuples = [(bytes.fromhex(h), is_left) for h, is_left in proof_dict["proof"]]
        value = bytes.fromhex(proof_dict["value"])

        wrong_root = b'\xff' * 32
        assert not StateTrie.verify_proof("balance:alice", value, proof_tuples, wrong_root)

    def test_verify_proof_single_entry(self):
        """Trie with one entry should produce a verifiable proof."""
        trie = StateTrie()
        trie.set("key:only", b"value")
        root = trie.root()
        proof_dict = trie.get_proof("key:only")
        assert proof_dict is not None
        proof_tuples = [(bytes.fromhex(h), is_left) for h, is_left in proof_dict["proof"]]
        value = bytes.fromhex(proof_dict["value"])
        assert StateTrie.verify_proof("key:only", value, proof_tuples, root)

    def test_get_proof_nonexistent_key_returns_none(self):
        trie = StateTrie()
        trie.set("balance:alice", (100).to_bytes(8, 'big'))
        assert trie.get_proof("balance:bob") is None

    def test_empty_trie_proof_returns_none(self):
        trie = StateTrie()
        assert trie.get_proof("anything") is None

    def test_verify_proof_large_trie(self):
        """Proof should work in a trie with many entries."""
        trie = StateTrie()
        for i in range(50):
            trie.set(f"key:{i}", i.to_bytes(8, 'big'))
        root = trie.root()
        proof_dict = trie.get_proof("key:25")
        assert proof_dict is not None
        proof_tuples = [(bytes.fromhex(h), is_left) for h, is_left in proof_dict["proof"]]
        value = bytes.fromhex(proof_dict["value"])
        assert StateTrie.verify_proof("key:25", value, proof_tuples, root)

    def test_proof_root_matches_trie_root(self):
        trie = StateTrie()
        trie.set("a", b"1")
        trie.set("b", b"2")
        proof_dict = trie.get_proof("a")
        assert proof_dict is not None
        assert proof_dict["root"] == trie.root().hex()


# ========================================================================
# 7. STATIC VERIFICATION -- verify_receipt_proof (standalone)
# ========================================================================

class TestVerifyReceiptProofStandalone:

    def test_verify_roundtrip(self):
        r = TransactionReceipt(
            tx_id=sha3_256(b"tx-standalone").hex(),
            status="success", fee_paid=50, block_index=1, tx_index=0, events=[],
        )
        receipts = [r]
        root = receipts_root(receipts)
        proof = receipt_proof(receipts, 0)
        assert verify_receipt_proof(r.receipt_hash, proof, root)

    def test_verify_fails_tampered_receipt(self):
        r1 = TransactionReceipt(
            tx_id=sha3_256(b"tx-1").hex(),
            status="success", fee_paid=50, block_index=1, tx_index=0, events=[],
        )
        r2 = TransactionReceipt(
            tx_id=sha3_256(b"tx-2").hex(),
            status="success", fee_paid=100, block_index=1, tx_index=1, events=[],
        )
        receipts = [r1, r2]
        root = receipts_root(receipts)
        proof = receipt_proof(receipts, 0)
        # Use r2's hash with r1's proof -- should fail
        assert not verify_receipt_proof(r2.receipt_hash, proof, root)

    def test_receipts_root_empty_is_zero_hash(self):
        """Empty receipts list should produce a zero hash root."""
        root = receipts_root([])
        assert root == "0" * 64

    def test_receipts_root_deterministic(self):
        """Same receipts should always produce the same root."""
        r = TransactionReceipt(
            tx_id=sha3_256(b"deterministic").hex(),
            status="success", fee_paid=0, block_index=0, tx_index=0, events=[],
        )
        assert receipts_root([r]) == receipts_root([r])


# ========================================================================
# 8. EDGE CASES
# ========================================================================

class TestEdgeCases:

    def test_genesis_header(self, funded_chain, wallet):
        """Genesis block should be accessible as a header."""
        headers = funded_chain.get_block_headers(0, 1)
        assert len(headers) == 1
        assert headers[0]["index"] == 0
        assert headers[0]["prevHash"] == "0" * 64

    def test_out_of_range_start_negative_small_count(self, funded_chain, wallet):
        """Negative start with small count yields empty result (end < 0)."""
        headers = funded_chain.get_block_headers(-5, 3)
        # start=-5, count=3 => end = min(-2, height+1) = -2 => range(0, -2) is empty
        assert headers == []

    def test_out_of_range_start_negative_large_count(self, funded_chain, wallet):
        """Negative start with count larger than abs(start) returns headers from 0."""
        headers = funded_chain.get_block_headers(-5, 100)
        # start=-5, count=100 => end = min(95, height+1) => range(0, height+1)
        assert len(headers) >= 1
        assert headers[0]["index"] == 0

    def test_receipt_proof_pending_tx(self, funded_chain, wallet, sender):
        """A TX still in the pool (not mined) should have no receipt proof."""
        tx = make_notarize_tx(sender, funded_chain)
        ok, tx_id = funded_chain.submit_tx(tx)
        assert ok
        # Don't mine -- TX is still pending
        result = funded_chain.get_receipt_proof(tx_id)
        assert result is None

    def test_state_proof_after_multiple_blocks(self, funded_chain, wallet, sender):
        """State proofs should be available for multiple recent blocks."""
        for _ in range(5):
            mine_block(funded_chain, wallet, sender)
        key = f"balance:{wallet.address}"
        for idx in range(funded_chain._height - 4, funded_chain._height + 1):
            proof = funded_chain.get_state_proof_at_block(key, idx)
            assert proof is not None, f"No proof at block {idx}"
            assert proof["block_index"] == idx

    def test_header_dict_vs_full_dict(self, funded_chain, wallet, sender):
        """Header and full dict should share common fields (hash, index, etc.)."""
        tx = make_notarize_tx(sender, funded_chain)
        block, _ = submit_and_mine(funded_chain, wallet, tx)
        header = block.to_header_dict()
        full = block.to_dict()
        # These keys appear in both header and full dict with identical values
        shared_keys = {"hash", "index", "timestamp", "prevHash", "merkleRoot",
                       "baseFee", "stateRoot", "receiptsRoot", "validator", "signature"}
        for key in shared_keys:
            assert key in header, f"Missing '{key}' in header"
            assert key in full, f"Missing '{key}' in full dict"
            assert header[key] == full[key], f"Mismatch on shared key '{key}'"
        # Header has txCount instead of transactions
        assert "txCount" in header
        assert "transactions" not in header
        assert "transactions" in full
        assert header["txCount"] == len(full["transactions"])

    def test_large_header_range_cap(self, funded_chain, wallet):
        """Even with a large count, result is capped at 100."""
        headers = funded_chain.get_block_headers(0, 500)
        assert len(headers) <= 100

    def test_state_proof_at_block_preserves_snapshot_values(self, funded_chain, wallet, wallet2, sender):
        """Proof at old block should reflect the old state, not current."""
        key = f"balance:{wallet.address}"
        # Record state at genesis
        proof_at_0 = funded_chain.get_state_proof_at_block(key, 0)
        assert proof_at_0 is not None
        value_at_0 = proof_at_0["value"]

        # Transfer some balance away to change state
        nonce = funded_chain.get_nonce(sender.address)
        tx = Transaction.transfer(
            sender.address, wallet2.address, 5000, nonce=nonce,
        )
        tx.sign(sender.signing_sk, sender.signing_pk)
        submit_and_mine(funded_chain, wallet, tx)

        # Proof at genesis should still return the old value
        proof_at_0_after = funded_chain.get_state_proof_at_block(key, 0)
        assert proof_at_0_after is not None
        assert proof_at_0_after["value"] == value_at_0

    def test_receipt_proof_structure_has_hex_proof_pairs(self, funded_chain, wallet, sender):
        """Proof pairs should be (hex_string, bool) tuples."""
        tx = make_notarize_tx(sender, funded_chain)
        _, tx_id = submit_and_mine(funded_chain, wallet, tx)
        result = funded_chain.get_receipt_proof(tx_id)
        assert result is not None
        for item in result["proof"]:
            assert isinstance(item, (list, tuple))
            assert len(item) == 2
            hex_str, is_right = item
            assert isinstance(hex_str, str)
            assert isinstance(is_right, bool)
            bytes.fromhex(hex_str)  # Must be valid hex

    def test_state_proof_value_decodable(self, funded_chain, wallet):
        """State proof value should be decodable back to an integer balance."""
        key = f"balance:{wallet.address}"
        proof = funded_chain.get_state_proof_at_block(key, None)
        assert proof is not None
        value_bytes = bytes.fromhex(proof["value"])
        balance = int.from_bytes(value_bytes, 'big')
        assert balance > 0  # Genesis validator has funds

    def test_verify_state_proof_from_blockchain(self, funded_chain, wallet, sender):
        """End-to-end: get state proof from blockchain and verify statically."""
        mine_block(funded_chain, wallet, sender)
        key = f"balance:{wallet.address}"
        proof_dict = funded_chain.get_state_proof_at_block(key, None)
        assert proof_dict is not None

        proof_tuples = [(bytes.fromhex(h), is_left) for h, is_left in proof_dict["proof"]]
        value = bytes.fromhex(proof_dict["value"])
        root = bytes.fromhex(proof_dict["root"])

        assert StateTrie.verify_proof(key, value, proof_tuples, root)

    def test_verify_receipt_proof_from_blockchain(self, funded_chain, wallet, sender):
        """End-to-end: get receipt proof from blockchain and verify statically."""
        tx = make_notarize_tx(sender, funded_chain)
        block, tx_id = submit_and_mine(funded_chain, wallet, tx)
        result = funded_chain.get_receipt_proof(tx_id)
        assert result is not None

        receipt_hash = result["receipt"]["receipt_hash"]
        root = result["receiptsRoot"]
        proof = [(bytes.fromhex(h), is_right) for h, is_right in result["proof"]]
        assert verify_receipt_proof(receipt_hash, proof, root)

    def test_headers_immutable_across_calls(self, funded_chain, wallet, sender):
        """Two calls to get_block_headers should return identical data."""
        mine_block(funded_chain, wallet, sender)
        h1 = funded_chain.get_block_headers(0, 10)
        h2 = funded_chain.get_block_headers(0, 10)
        assert h1 == h2

    def test_receipt_proof_tx_id_not_hex(self, funded_chain, wallet):
        """Invalid tx_id (not in receipts) returns None, does not crash."""
        result = funded_chain.get_receipt_proof("not_a_valid_tx_id")
        assert result is None

    def test_state_proof_at_negative_block_index(self, funded_chain, wallet):
        """Negative block index should return None (no snapshot)."""
        result = funded_chain.get_state_proof_at_block(f"balance:{wallet.address}", -1)
        assert result is None

    def test_header_from_each_block_is_consistent(self, funded_chain, wallet, sender):
        """Headers fetched individually should match batch fetch."""
        for _ in range(3):
            mine_block(funded_chain, wallet, sender)
        batch = funded_chain.get_block_headers(0, 10)
        for h in batch:
            single = funded_chain.get_block_headers(h["index"], 1)
            assert len(single) == 1
            assert single[0] == h

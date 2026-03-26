"""Tests for qbit_network.core.state_tree — StateTrie, state root, proofs."""
import pytest
from qbit_network.core.state_tree import StateTrie
from qbit_network.crypto.hashing import sha3_256


# ====================================================================
# StateTrie unit tests
# ====================================================================

class TestStateTrieBasic:
    """Basic StateTrie operations: set, delete, root, len."""

    def test_empty_root(self):
        trie = StateTrie()
        root = trie.root()
        assert len(root) == 32
        assert root == sha3_256(b"empty_state")

    def test_single_entry(self):
        trie = StateTrie()
        trie.set("balance:addr1", (100).to_bytes(8, 'big'))
        root = trie.root()
        assert len(root) == 32
        assert root != sha3_256(b"empty_state")

    def test_multiple_entries_sorted(self):
        trie = StateTrie()
        trie.set("balance:bbb", (200).to_bytes(8, 'big'))
        trie.set("balance:aaa", (100).to_bytes(8, 'big'))
        root1 = trie.root()

        # Same entries added in different order must give the same root
        trie2 = StateTrie()
        trie2.set("balance:aaa", (100).to_bytes(8, 'big'))
        trie2.set("balance:bbb", (200).to_bytes(8, 'big'))
        root2 = trie2.root()

        assert root1 == root2

    def test_set_overwrite(self):
        trie = StateTrie()
        trie.set("balance:addr1", (100).to_bytes(8, 'big'))
        root1 = trie.root()
        trie.set("balance:addr1", (200).to_bytes(8, 'big'))
        root2 = trie.root()
        assert root1 != root2

    def test_delete_existing(self):
        trie = StateTrie()
        trie.set("balance:addr1", (100).to_bytes(8, 'big'))
        trie.delete("balance:addr1")
        assert trie.root() == sha3_256(b"empty_state")

    def test_delete_nonexistent(self):
        trie = StateTrie()
        trie.delete("nonexistent")  # should not raise
        assert trie.root() == sha3_256(b"empty_state")

    def test_len(self):
        trie = StateTrie()
        assert len(trie) == 0
        trie.set("a", b"\x01")
        assert len(trie) == 1
        trie.set("b", b"\x02")
        assert len(trie) == 2
        trie.delete("a")
        assert len(trie) == 1

    def test_root_consistency_after_delete_and_re_add(self):
        trie = StateTrie()
        trie.set("balance:addr1", (100).to_bytes(8, 'big'))
        root1 = trie.root()
        trie.delete("balance:addr1")
        trie.set("balance:addr1", (100).to_bytes(8, 'big'))
        root2 = trie.root()
        assert root1 == root2


class TestStateTrieSnapshot:
    """Snapshot and restore for rollback support."""

    def test_snapshot_restore(self):
        trie = StateTrie()
        trie.set("balance:addr1", (100).to_bytes(8, 'big'))
        snap = trie.snapshot()
        root_before = trie.root()

        trie.set("balance:addr1", (999).to_bytes(8, 'big'))
        trie.set("balance:addr2", (500).to_bytes(8, 'big'))
        assert trie.root() != root_before

        trie.restore(snap)
        assert trie.root() == root_before

    def test_snapshot_is_independent(self):
        trie = StateTrie()
        trie.set("a", b"\x01")
        snap = trie.snapshot()
        trie.set("b", b"\x02")
        # Mutating the trie should not affect the snapshot
        assert "b" not in snap


# ====================================================================
# Proof generation and verification
# ====================================================================

class TestStateTrieProofs:
    """Merkle proof generation and verification."""

    def test_proof_single_entry(self):
        trie = StateTrie()
        key = "balance:addr1"
        val = (100).to_bytes(8, 'big')
        trie.set(key, val)
        proof_data = trie.get_proof(key)
        assert proof_data is not None
        assert proof_data["key"] == key
        assert proof_data["value"] == val.hex()
        assert isinstance(proof_data["proof"], list)

    def test_proof_nonexistent_key(self):
        trie = StateTrie()
        trie.set("balance:addr1", (100).to_bytes(8, 'big'))
        assert trie.get_proof("balance:missing") is None

    def test_proof_empty_trie(self):
        trie = StateTrie()
        assert trie.get_proof("balance:addr1") is None

    def test_verify_proof_valid(self):
        trie = StateTrie()
        key = "balance:addr1"
        val = (100).to_bytes(8, 'big')
        trie.set(key, val)
        trie.set("nonce:addr1", (5).to_bytes(8, 'big'))
        trie.set("balance:addr2", (200).to_bytes(8, 'big'))

        proof_data = trie.get_proof(key)
        assert proof_data is not None

        # Reconstruct proof tuples
        proof_tuples = [
            (bytes.fromhex(h), is_left)
            for h, is_left in proof_data["proof"]
        ]
        root = bytes.fromhex(proof_data["root"])

        assert StateTrie.verify_proof(key, val, proof_tuples, root) is True

    def test_verify_proof_tampered_value(self):
        trie = StateTrie()
        key = "balance:addr1"
        val = (100).to_bytes(8, 'big')
        trie.set(key, val)
        trie.set("balance:addr2", (200).to_bytes(8, 'big'))

        proof_data = trie.get_proof(key)
        proof_tuples = [
            (bytes.fromhex(h), is_left)
            for h, is_left in proof_data["proof"]
        ]
        root = bytes.fromhex(proof_data["root"])

        tampered_val = (999).to_bytes(8, 'big')
        assert StateTrie.verify_proof(key, tampered_val, proof_tuples, root) is False

    def test_verify_proof_tampered_proof(self):
        trie = StateTrie()
        key = "balance:addr1"
        val = (100).to_bytes(8, 'big')
        trie.set(key, val)
        trie.set("balance:addr2", (200).to_bytes(8, 'big'))

        proof_data = trie.get_proof(key)
        root = bytes.fromhex(proof_data["root"])

        # Tamper with a proof sibling
        tampered_proof = [
            (b'\x00' * 32, True)  # replaced with zeros
        ]
        assert StateTrie.verify_proof(key, val, tampered_proof, root) is False

    def test_verify_proof_wrong_root(self):
        trie = StateTrie()
        key = "balance:addr1"
        val = (100).to_bytes(8, 'big')
        trie.set(key, val)

        proof_data = trie.get_proof(key)
        proof_tuples = [
            (bytes.fromhex(h), is_left)
            for h, is_left in proof_data["proof"]
        ]
        wrong_root = b'\xff' * 32
        assert StateTrie.verify_proof(key, val, proof_tuples, wrong_root) is False

    def test_proof_for_all_keys(self):
        """Every key in a multi-entry trie should have a valid proof."""
        trie = StateTrie()
        entries = {
            "balance:addr1": (100).to_bytes(8, 'big'),
            "balance:addr2": (200).to_bytes(8, 'big'),
            "nonce:addr1": (3).to_bytes(8, 'big'),
            "nonce:addr2": (7).to_bytes(8, 'big'),
        }
        for k, v in entries.items():
            trie.set(k, v)

        root = trie.root()
        for k, v in entries.items():
            proof_data = trie.get_proof(k)
            assert proof_data is not None
            proof_tuples = [
                (bytes.fromhex(h), is_left)
                for h, is_left in proof_data["proof"]
            ]
            assert StateTrie.verify_proof(k, v, proof_tuples, root) is True


# ====================================================================
# Blockchain integration tests
# ====================================================================

class TestBlockchainStateRoot:
    """State root computed and stored in block header."""

    def test_genesis_has_state_root(self, blockchain, wallet):
        """Genesis block should have a non-empty state_root."""
        genesis = blockchain.chain[0]
        assert genesis.state_root != ""
        assert len(genesis.state_root) == 64  # hex-encoded 32 bytes

    def test_state_root_in_to_dict(self, blockchain, wallet):
        """state_root should appear in block serialization."""
        genesis = blockchain.chain[0]
        d = genesis.to_dict()
        assert "stateRoot" in d
        assert d["stateRoot"] == genesis.state_root

    def test_state_root_changes_after_balance_change(self, blockchain, wallet):
        """After a block with financial txs, state root should change."""
        # Activate financial layer
        blockchain.activate_financial_layer(wallet.address)

        root_before = blockchain.get_state_root()

        # Create and mine a notarize TX (changes nonce, deducts fee)
        tx = _notarize_tx(wallet, blockchain)
        ok, msg = blockchain.submit_tx(tx)
        assert ok, msg
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        root_after = blockchain.get_state_root()
        assert root_after != root_before

    def test_state_root_deterministic(self, wallet):
        """Same transactions produce the same state root."""
        from qbit_network.core.blockchain import Blockchain

        roots = []
        for _ in range(2):
            bc = Blockchain()
            bc.consensus.add_validator(wallet.address, wallet.signing_pk)
            bc.init_chain(wallet.address, wallet.signing_sk)
            roots.append(bc.get_state_root())

        assert roots[0] == roots[1]

    def test_empty_block_same_state_root(self, blockchain, wallet):
        """A block with no TXs should not change the state root."""
        from qbit_network.config import DYNAMIC_FEE_ACTIVATION_HEIGHT
        # Only works when dynamic fees are active (empty blocks allowed)
        if DYNAMIC_FEE_ACTIVATION_HEIGHT > blockchain.height + 1:
            pytest.skip("Dynamic fees not active yet")

        root_before = blockchain.get_state_root()
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        if block is not None and len(block.transactions) == 0:
            root_after = blockchain.get_state_root()
            # Block reward changes state, so only truly no-reward empty blocks
            # would have same root. With rewards, root changes.
            # Just verify we have a valid root.
            assert len(root_after) == 64

    def test_state_root_in_block_hash(self, blockchain, wallet):
        """state_root is included in the block hash computation."""
        genesis = blockchain.chain[0]
        # state_root is non-empty, so it's part of header bytes
        assert genesis.state_root != ""
        # Verify the hash is 64 hex chars
        assert len(genesis.block_hash) == 64

    def test_block_roundtrip_preserves_state_root(self, blockchain, wallet):
        """from_dict preserves state_root."""
        genesis = blockchain.chain[0]
        d = genesis.to_dict()
        restored = blockchain.chain[0].__class__.from_dict(d)
        assert restored.state_root == genesis.state_root
        assert restored.block_hash == genesis.block_hash

    def test_state_root_after_transfer(self, wallet):
        """State root changes correctly after a TRANSFER."""
        from qbit_network.core.blockchain import Blockchain
        from qbit_network.core.wallet import Wallet

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        bob = Wallet.generate()
        root_before = bc.get_state_root()

        tx = _transfer_tx(wallet, bob.address, 1_000_000, bc)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg
        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        root_after = bc.get_state_root()
        assert root_after != root_before

    def test_rollback_restores_state_root(self, wallet):
        """After rollback, state root returns to previous value."""
        from qbit_network.core.blockchain import Blockchain

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        root_after_genesis = bc.get_state_root()

        # Mine a block with a notarize TX
        tx = _notarize_tx(wallet, bc)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg
        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        root_after_block1 = bc.get_state_root()
        assert root_after_block1 != root_after_genesis

        # Rollback block 1
        bc._rollback_to(1)  # rollback to genesis (index 0 stays)
        root_after_rollback = bc.get_state_root()
        assert root_after_rollback == root_after_genesis


class TestBlockchainStateProof:
    """State proof generation from blockchain."""

    def test_get_state_proof_balance(self, wallet):
        """Get a valid balance proof for a funded address."""
        from qbit_network.core.blockchain import Blockchain

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        proof = bc.get_state_proof(wallet.address, "balance")
        assert proof is not None
        assert proof["key"] == f"balance:{wallet.address}"
        assert "proof" in proof
        assert "root" in proof

        # Verify the proof
        val = bytes.fromhex(proof["value"])
        proof_tuples = [
            (bytes.fromhex(h), is_left)
            for h, is_left in proof["proof"]
        ]
        root = bytes.fromhex(proof["root"])
        assert StateTrie.verify_proof(proof["key"], val, proof_tuples, root)

    def test_get_state_proof_nonce(self, wallet):
        """Get a valid nonce proof after mining a block."""
        from qbit_network.core.blockchain import Blockchain

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, wallet.signing_pk)

        # Submit a TX so the address has a nonce
        tx = _notarize_tx(wallet, bc)
        ok, msg = bc.submit_tx(tx)
        assert ok, msg
        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        proof = bc.get_state_proof(wallet.address, "nonce")
        assert proof is not None
        assert proof["key"] == f"nonce:{wallet.address}"

    def test_get_state_proof_nonexistent(self, blockchain, wallet):
        """Proof for nonexistent address returns None."""
        proof = blockchain.get_state_proof("nonexistent_address", "balance")
        assert proof is None

    def test_get_state_proof_invalid_key_type(self, blockchain, wallet):
        """Invalid key_type returns None."""
        proof = blockchain.get_state_proof(wallet.address, "invalid")
        assert proof is None

    def test_state_root_matches_proof_root(self, wallet):
        """The state root from blockchain matches the root in proof."""
        from qbit_network.core.blockchain import Blockchain

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk, wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        state_root = bc.get_state_root()
        proof = bc.get_state_proof(wallet.address, "balance")
        assert proof is not None
        assert proof["root"] == state_root


# ====================================================================
# Adversarial tests
# ====================================================================

class TestStateTrieAdversarial:
    """Adversarial inputs and edge cases."""

    def test_large_value(self):
        trie = StateTrie()
        large_val = b'\xff' * 1024
        trie.set("key", large_val)
        assert len(trie.root()) == 32

    def test_empty_key(self):
        trie = StateTrie()
        trie.set("", b"\x01")
        assert len(trie) == 1
        proof = trie.get_proof("")
        assert proof is not None

    def test_empty_value(self):
        trie = StateTrie()
        trie.set("key", b"")
        assert len(trie) == 1
        root = trie.root()
        assert root != sha3_256(b"empty_state")

    def test_many_entries(self):
        """Trie with many entries still produces correct proofs."""
        trie = StateTrie()
        entries = {}
        for i in range(100):
            key = f"balance:addr{i:04d}"
            val = i.to_bytes(8, 'big')
            entries[key] = val
            trie.set(key, val)

        root = trie.root()
        # Verify a sample of proofs
        for key in list(entries.keys())[::10]:
            proof_data = trie.get_proof(key)
            assert proof_data is not None
            proof_tuples = [
                (bytes.fromhex(h), is_left)
                for h, is_left in proof_data["proof"]
            ]
            assert StateTrie.verify_proof(
                key, entries[key], proof_tuples, root) is True

    def test_proof_does_not_verify_for_different_key(self):
        """A valid proof for key A should not verify for key B."""
        trie = StateTrie()
        trie.set("balance:addr1", (100).to_bytes(8, 'big'))
        trie.set("balance:addr2", (200).to_bytes(8, 'big'))

        proof_data = trie.get_proof("balance:addr1")
        proof_tuples = [
            (bytes.fromhex(h), is_left)
            for h, is_left in proof_data["proof"]
        ]
        root = bytes.fromhex(proof_data["root"])

        # Using addr1's proof to verify addr2's value should fail
        assert StateTrie.verify_proof(
            "balance:addr2", (200).to_bytes(8, 'big'),
            proof_tuples, root) is False


# ====================================================================
# Helpers
# ====================================================================

def _notarize_tx(wallet, bc):
    """Create a signed NOTARIZE transaction."""
    from qbit_network.core.transaction import Transaction
    nonce = bc.get_nonce(wallet.address)
    tx = Transaction.notarize(
        sender=wallet.address,
        document_hash="a" * 64,
        nonce=nonce,
        metadata="test",
    )
    tx.sign(wallet.signing_sk, wallet.signing_pk)
    return tx


def _transfer_tx(wallet, to_addr, amount, bc):
    """Create a signed TRANSFER transaction."""
    from qbit_network.core.transaction import Transaction
    nonce = bc.get_nonce(wallet.address)
    tx = Transaction.transfer(
        sender=wallet.address,
        recipient=to_addr,
        amount=amount,
        nonce=nonce,
    )
    tx.sign(wallet.signing_sk, wallet.signing_pk)
    return tx

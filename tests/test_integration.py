"""Integration tests -- end-to-end workflow tests for QBit Network blockchain."""
import asyncio
import json
import os
import shutil
import tempfile
import time
import pytest
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.wallet import Wallet
from qbit_network.core.store import SQLiteStore, migrate_json_to_sqlite
from qbit_network.core.proof import export_proof, verify_proof
from qbit_network.crypto import MLDSA, MLKEM, sha3_256, aes_encrypt, aes_decrypt
from qbit_network.config import CHAIN_ID, MAX_REORG_DEPTH
from tests.conftest import submit_and_mine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_chain(wallet, num_blocks):
    """Create a blockchain with the given number of blocks beyond genesis.

    Each block contains one NOTARIZE tx.
    Returns (blockchain, list_of_tx_ids).
    """
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk)
    tx_ids = []
    for i in range(num_blocks):
        tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        tx_ids.append(tx.tx_id)
    return bc, tx_ids


def _two_validator_chain():
    """Set up a blockchain with two validators and genesis."""
    v1 = Wallet.generate()
    v2 = Wallet.generate()
    bc = Blockchain()
    bc.consensus.add_validator(v1.address, v1.signing_pk)
    bc.consensus.add_validator(v2.address, v2.signing_pk)
    bc.init_chain(v1.address, v1.signing_sk)
    return v1, v2, bc


# ===========================================================================
# Full lifecycle: wallet -> register key -> notarize -> verify -> proof
# ===========================================================================

class TestFullLifecycle:
    """Complete end-to-end lifecycle tests."""

    def test_wallet_to_notarize_to_proof(self):
        """Full lifecycle: generate wallet, register key, notarize a document,
        verify it on chain, export proof, and verify proof offline."""
        # Step 1: Generate wallet
        wallet = Wallet.generate()
        assert wallet.address.startswith("qv1")

        # Step 2: Create blockchain
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        # Step 3: Register encryption key
        reg_tx = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        reg_tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, reg_tx)
        assert bc.get_encryption_pk(wallet.address) == wallet.encryption_pk.hex()

        # Step 4: Notarize a document
        doc_hash = sha3_256(b"My important document content").hex()
        note_tx = Transaction.notarize(wallet.address, doc_hash, nonce=1)
        note_tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, note_tx)

        # Step 5: Verify the document on chain
        result = bc.verify_document(doc_hash)
        assert result is not None
        assert result["sender"] == wallet.address
        assert result["block_index"] == 2

        # Step 6: Export proof
        block = bc.chain[result["block_index"]]
        tx_index = 0  # only tx in this block
        proof = export_proof(block.to_dict(), tx_index, note_tx.tx_id,
                             doc_hash, wallet.address)
        assert proof["version"] == 1
        assert proof["document_hash"] == doc_hash

        # Step 7: Verify proof offline
        valid, errors = verify_proof(proof)
        assert valid, f"Proof verification failed: {errors}"

    def test_wallet_save_load_and_continue(self, tmp_dir):
        """Generate wallet, save to disk, load it back, and use it to notarize."""
        wallet = Wallet.generate()
        path = os.path.join(tmp_dir, "test_wallet.json")
        wallet.save(path, password="SecurePass#123")

        loaded = Wallet.load(path, password="SecurePass#123")
        assert loaded.address == wallet.address
        assert loaded.signing_sk == wallet.signing_sk

        # Use loaded wallet to produce blockchain activity
        bc = Blockchain()
        bc.consensus.add_validator(loaded.address, loaded.signing_pk)
        bc.init_chain(loaded.address, loaded.signing_sk)

        tx = Transaction.notarize(loaded.address, "aabbccdd" * 8, nonce=0)
        tx.sign(loaded.signing_sk, loaded.signing_pk)
        submit_and_mine(bc, loaded, tx)
        assert bc.verify_document("aabbccdd" * 8) is not None

    def test_store_and_retrieve_document(self):
        """Notarize + STORE flow: hash a document, notarize, store with CID."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        doc_hash = sha3_256(b"store-me").hex()

        # Notarize
        tx1 = Transaction.notarize(wallet.address, doc_hash, nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx1)

        # Store with CID
        tx2 = Transaction.store(wallet.address, doc_hash, "QmFakeCID123", nonce=1)
        tx2.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx2)

        # Verify both transactions exist
        assert bc.get_tx(tx1.tx_id) is not None
        assert bc.get_tx(tx2.tx_id) is not None
        assert bc.verify_document(doc_hash) is not None


# ===========================================================================
# Multi-wallet interactions
# ===========================================================================

class TestMultiWallet:
    """Multiple wallets interacting on the same chain."""

    def test_share_encrypted_data(self):
        """Alice shares encrypted data with Bob using ML-KEM key encapsulation."""
        alice = Wallet.generate()
        bob = Wallet.generate()

        bc = Blockchain()
        bc.consensus.add_validator(alice.address, alice.signing_pk)
        bc.init_chain(alice.address, alice.signing_sk)

        # Register Bob's encryption key
        reg_tx = Transaction.register_key(bob.address, bob.encryption_pk, nonce=0)
        reg_tx.sign(bob.signing_sk, bob.signing_pk)
        submit_and_mine(bc, alice, reg_tx)

        # Alice encapsulates a shared secret for Bob
        ct, shared_secret = MLKEM.encapsulate(bob.encryption_pk)
        assert len(shared_secret) == 32

        # Alice encrypts data with the shared secret
        plaintext = b"Confidential document content"
        encrypted = aes_encrypt(shared_secret, plaintext)

        # Alice shares the encapsulated key on chain
        future_expiry = int(time.time()) + 86400
        share_tx = Transaction.share(
            alice.address, bob.address, "QmEncryptedCID",
            ct, expires=future_expiry, nonce=0)
        share_tx.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, alice, share_tx)

        # Bob retrieves shares
        shares = bc.get_shared_with(bob.address)
        assert len(shares) == 1
        assert shares[0]["from"] == alice.address

        # Bob decapsulates the shared secret
        ct_from_chain = bytes.fromhex(shares[0]["payload"]["encapsulatedKey"])
        bob_shared_secret = MLKEM.decapsulate(bob.encryption_sk, ct_from_chain)
        assert bob_shared_secret == shared_secret

        # Bob decrypts the data
        decrypted = aes_decrypt(bob_shared_secret, encrypted)
        assert decrypted == plaintext

    def test_multiple_wallets_notarize_same_document(self):
        """Multiple wallets notarize the same document -- first notarizer is preserved."""
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        w3 = Wallet.generate()

        bc = Blockchain()
        bc.consensus.add_validator(w1.address, w1.signing_pk)
        bc.init_chain(w1.address, w1.signing_sk)

        doc_hash = "abcdef" * 10 + "abcd"

        for i, w in enumerate([w1, w2, w3]):
            tx = Transaction.notarize(w.address, doc_hash, nonce=0)
            tx.sign(w.signing_sk, w.signing_pk)
            submit_and_mine(bc, w1, tx)

        # First notarizer wins
        result = bc.verify_document(doc_hash)
        assert result["sender"] == w1.address

        # But all notarizations are tracked
        all_n = bc.get_all_notarizations(doc_hash)
        assert len(all_n) == 3

    def test_multi_sender_transactions_in_one_block(self):
        """Multiple senders' txs are mined in a single block with correct nonces."""
        validator = Wallet.generate()
        sender1 = Wallet.generate()
        sender2 = Wallet.generate()

        bc = Blockchain()
        bc.consensus.add_validator(validator.address, validator.signing_pk)
        bc.init_chain(validator.address, validator.signing_sk)

        tx1 = Transaction.notarize(sender1.address, "1111" * 16, nonce=0)
        tx1.sign(sender1.signing_sk, sender1.signing_pk)
        bc.submit_tx(tx1)

        tx2 = Transaction.notarize(sender2.address, "2222" * 16, nonce=0)
        tx2.sign(sender2.signing_sk, sender2.signing_pk)
        bc.submit_tx(tx2)

        tx3 = Transaction.notarize(validator.address, "3333" * 16, nonce=0)
        tx3.sign(validator.signing_sk, validator.signing_pk)
        bc.submit_tx(tx3)

        block = bc.produce_block(validator.address, validator.signing_sk)
        assert block is not None
        assert len(block.transactions) == 3
        assert len(bc.tx_pool) == 0

    def test_key_rotation(self):
        """Wallet registers a new encryption key, old one is still in history."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        # Register first key
        tx1 = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx1)

        # Generate and register new key
        new_sk, new_pk = MLKEM.keygen()
        tx2 = Transaction.register_key(wallet.address, new_pk, nonce=1)
        tx2.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx2)

        # Current key is the new one
        assert bc.get_encryption_pk(wallet.address) == new_pk.hex()
        # History has both
        assert len(bc._key_history[wallet.address]) == 2


# ===========================================================================
# Chain persistence and reload
# ===========================================================================

class TestChainPersistence:
    """Tests for chain data persistence and reload integrity."""

    def test_sqlite_persist_and_reload(self, tmp_dir):
        """Write blocks to SQLite, create new Blockchain, load and verify state."""
        wallet = Wallet.generate()
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        # Mine 5 blocks
        for i in range(5):
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            bc.submit_tx(tx)
            bc.produce_block(wallet.address, wallet.signing_sk)

        assert bc.height == 5
        doc_hash_3 = f"{3:064x}"
        assert bc.verify_document(doc_hash_3) is not None

        # Reload from SQLite in a fresh Blockchain
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load()
        assert loaded is True
        assert bc2.height == 5

        # Verify all documents
        for i in range(5):
            assert bc2.verify_document(f"{i:064x}") is not None

        # Nonces should be correct after reload
        assert bc2.get_nonce(wallet.address) == 5

    def test_json_migrate_and_reload(self, tmp_dir):
        """Write chain.json, migrate to SQLite, reload and verify data."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        tx = Transaction.notarize(wallet.address, "ab" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        # Write chain.json (legacy format)
        os.makedirs(tmp_dir, exist_ok=True)
        chain_path = os.path.join(tmp_dir, "chain.json")
        with open(chain_path, "w") as f:
            json.dump([b.to_dict() for b in bc.chain], f)

        # Migrate to SQLite first (before Blockchain creates empty chain.db)
        migrate_json_to_sqlite(tmp_dir)
        assert os.path.exists(os.path.join(tmp_dir, "chain.db"))

        # Reload via SQLite
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load()
        assert loaded is True
        assert bc2.height == 1
        assert bc2.verify_document("ab" * 32) is not None

    def test_sqlite_migration_from_json(self, tmp_dir):
        """Migrate from chain.json to SQLite and verify data integrity."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        for i in range(3):
            tx = Transaction.notarize(wallet.address, f"a{i:063x}", nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            submit_and_mine(bc, wallet, tx)

        # Write legacy chain.json
        os.makedirs(tmp_dir, exist_ok=True)
        with open(os.path.join(tmp_dir, "chain.json"), "w") as f:
            json.dump([b.to_dict() for b in bc.chain], f)

        # Trigger migration
        migrate_json_to_sqlite(tmp_dir)
        assert os.path.exists(os.path.join(tmp_dir, "chain.db"))
        assert os.path.exists(os.path.join(tmp_dir, "chain.json.migrated"))

        # Load via SQLite and verify
        store = SQLiteStore(tmp_dir)
        assert store.height() == 3
        for i in range(3):
            assert store.get_notarization(f"a{i:063x}") is not None
        store.close()

    def test_indices_rebuilt_after_reload(self, tmp_dir):
        """After reloading from SQLite, all in-memory indices are correct."""
        alice = Wallet.generate()
        bob = Wallet.generate()
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(alice.address, alice.signing_pk)
        bc.init_chain(alice.address, alice.signing_sk)

        # Register key
        reg = Transaction.register_key(alice.address, alice.encryption_pk, nonce=0)
        reg.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, alice, reg)

        # Notarize
        note = Transaction.notarize(alice.address, "ff" * 32, nonce=1)
        note.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, alice, note)

        # Share
        ct, _ = MLKEM.encapsulate(alice.encryption_pk)  # self-share for testing
        share = Transaction.share(alice.address, bob.address, "QmTest", ct,
                                  expires=int(time.time()) + 86400, nonce=2)
        share.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, alice, share)

        # Reload
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(alice.address, alice.signing_pk)
        assert bc2.load() is True

        # Check indices
        assert bc2.get_nonce(alice.address) == 3
        assert bc2.get_encryption_pk(alice.address) == alice.encryption_pk.hex()
        assert bc2.verify_document("ff" * 32) is not None
        assert len(bc2.get_txs_by_sender(alice.address)) == 3
        assert len(bc2.get_txs_by_recipient(bob.address)) == 1

    def test_corrupt_db_block_detected(self, tmp_dir):
        """Corrupted block data in SQLite is detected during load."""
        import sqlite3
        wallet = Wallet.generate()
        bc = Blockchain(data_dir=tmp_dir)
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        tx = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(bc, wallet, tx)

        # Tamper with SQLite DB
        db_path = os.path.join(tmp_dir, "chain.db")
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT data FROM blocks WHERE idx=1").fetchone()
        block_data = json.loads(row[0])
        block_data["transactions"][0]["signature"] = "ff" * 3309
        conn.execute("UPDATE blocks SET data=? WHERE idx=1",
                     (json.dumps(block_data, separators=(",", ":")),))
        conn.commit()
        conn.close()

        # Reload -- loads structurally but tampered tx fails verify
        bc2 = Blockchain(data_dir=tmp_dir)
        bc2.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = bc2.load()
        assert loaded is True
        tampered_tx = bc2.chain[1].transactions[0]
        assert tampered_tx.verify() is False


# ===========================================================================
# Fork resolution integration
# ===========================================================================

class TestForkResolutionIntegration:
    """End-to-end fork resolution scenarios."""

    def test_diverge_and_converge(self):
        """Two chains diverge from same genesis, then converge via reorg."""
        v1 = Wallet.generate()
        v2 = Wallet.generate()

        # Chain A: 2 blocks
        chain_a = Blockchain()
        chain_a.consensus.add_validator(v1.address, v1.signing_pk)
        chain_a.consensus.add_validator(v2.address, v2.signing_pk)
        chain_a.init_chain(v1.address, v1.signing_sk)

        nonces_a = {}
        for _ in range(2):
            expected = chain_a.consensus.select_validator(len(chain_a.chain))
            w = v1 if expected == v1.address else v2
            n = nonces_a.get(w.address, 0)
            tx = Transaction.notarize(w.address, f"a{n:063x}", nonce=n)
            tx.sign(w.signing_sk, w.signing_pk)
            chain_a.submit_tx(tx)
            chain_a.produce_block(expected, w.signing_sk)
            nonces_a[w.address] = n + 1

        # Chain B: 3 blocks (longer) from same genesis
        chain_b_blocks = []
        fork_parent = chain_a.chain[0].block_hash
        fork_nonces = {}
        for i in range(1, 4):
            expected = chain_a.consensus.select_validator(i)
            w = v1 if expected == v1.address else v2
            n = fork_nonces.get(w.address, 0)
            tx = Transaction.notarize(w.address, f"b{n:063x}", nonce=n)
            tx.sign(w.signing_sk, w.signing_pk)
            fb = Block(index=i, prev_hash=fork_parent, transactions=[tx],
                       validator=expected,
                       timestamp=chain_a.chain[0].timestamp + i)
            fb.sign(w.signing_sk)
            fork_parent = fb.block_hash
            chain_b_blocks.append(fb)
            fork_nonces[w.address] = n + 1

        # Chain A adopts Chain B
        ok, msg = chain_a.try_reorg(chain_b_blocks)
        assert ok, msg
        assert chain_a.height == 3

        # Verify Chain B's documents are now visible
        assert chain_a.verify_document(f"b{0:063x}") is not None

    def test_displaced_txs_return_to_pool_after_reorg(self):
        """Transactions displaced by a reorg that are not in the fork return to pool."""
        v1 = Wallet.generate()
        v2 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.consensus.add_validator(v2.address, v2.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        # Build 2-block main chain
        nonces = {}
        for _ in range(2):
            expected = bc.consensus.select_validator(len(bc.chain))
            w = v1 if expected == v1.address else v2
            n = nonces.get(w.address, 0)
            tx = Transaction.notarize(w.address, f"aa{n:062x}", nonce=n)
            tx.sign(w.signing_sk, w.signing_pk)
            bc.submit_tx(tx)
            bc.produce_block(expected, w.signing_sk)
            nonces[w.address] = n + 1

        pool_before = len(bc.tx_pool)

        # Build 3-block fork with different txs
        fork_blocks = []
        fork_parent = bc.chain[0].block_hash
        fork_nonces = {}
        for i in range(1, 4):
            expected = bc.consensus.select_validator(i)
            w = v1 if expected == v1.address else v2
            n = fork_nonces.get(w.address, 0)
            tx = Transaction.notarize(w.address, f"bb{n:062x}", nonce=n)
            tx.sign(w.signing_sk, w.signing_pk)
            fb = Block(index=i, prev_hash=fork_parent, transactions=[tx],
                       validator=expected,
                       timestamp=bc.chain[0].timestamp + i)
            fb.sign(w.signing_sk)
            fork_parent = fb.block_hash
            fork_blocks.append(fb)
            fork_nonces[w.address] = n + 1

        bc.try_reorg(fork_blocks)
        # Displaced txs that are not in the fork should return to pool
        assert len(bc.tx_pool) >= pool_before


# ===========================================================================
# Concurrent submissions (simulated)
# ===========================================================================

class TestConcurrentSubmissions:
    """Simulate concurrent transaction submissions."""

    def test_sequential_batch_submission(self):
        """Submit many transactions with sequential nonces and mine them all."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        num_txs = 20
        for i in range(num_txs):
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            ok, _ = bc.submit_tx(tx)
            assert ok, f"Failed to submit tx with nonce {i}"

        assert len(bc.tx_pool) == num_txs

        # Mine all in one block
        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        assert len(block.transactions) == num_txs
        assert len(bc.tx_pool) == 0
        assert bc.get_nonce(wallet.address) == num_txs

    def test_interleaved_multi_sender_submission(self):
        """Multiple senders submit interleaved txs, all processed correctly."""
        validator = Wallet.generate()
        senders = [Wallet.generate() for _ in range(5)]

        bc = Blockchain()
        bc.consensus.add_validator(validator.address, validator.signing_pk)
        bc.init_chain(validator.address, validator.signing_sk)

        # Each sender submits 3 txs
        for nonce in range(3):
            for sender in senders:
                tx = Transaction.notarize(
                    sender.address, sha3_256(f"{sender.address}{nonce}".encode()).hex(),
                    nonce=nonce)
                tx.sign(sender.signing_sk, sender.signing_pk)
                ok, _ = bc.submit_tx(tx)
                assert ok

        assert len(bc.tx_pool) == 15

        # Mine all
        block = bc.produce_block(validator.address, validator.signing_sk)
        assert block is not None
        assert len(block.transactions) == 15
        assert len(bc.tx_pool) == 0

        # Verify nonces
        for sender in senders:
            assert bc.get_nonce(sender.address) == 3

    def test_multiple_blocks_sequential(self):
        """Submit txs in batches, mine each batch, verify all state is correct."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        total_txs = 0
        for batch in range(5):
            batch_size = 3
            for j in range(batch_size):
                nonce = total_txs
                tx = Transaction.notarize(wallet.address, f"cc{batch:02x}{j:060x}",
                                          nonce=nonce)
                tx.sign(wallet.signing_sk, wallet.signing_pk)
                ok, _ = bc.submit_tx(tx)
                assert ok
                total_txs += 1

            block = bc.produce_block(wallet.address, wallet.signing_sk)
            assert block is not None
            assert len(block.transactions) == batch_size

        assert bc.height == 5
        assert bc.get_nonce(wallet.address) == total_txs


# ===========================================================================
# Proof export/verify integration
# ===========================================================================

class TestProofIntegration:
    """End-to-end proof export and verification."""

    def test_proof_for_each_tx_in_multi_tx_block(self):
        """Export and verify proof for each transaction in a multi-tx block."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        # Submit 5 txs
        txs = []
        for i in range(5):
            doc_hash = f"dd{i:062x}"
            tx = Transaction.notarize(wallet.address, doc_hash, nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            bc.submit_tx(tx)
            txs.append((tx, doc_hash))

        block = bc.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        assert len(block.transactions) == 5

        # Export and verify proof for each
        block_dict = block.to_dict()
        for i, (tx, doc_hash) in enumerate(txs):
            proof = export_proof(block_dict, i, tx.tx_id, doc_hash, wallet.address)
            valid, errors = verify_proof(proof)
            assert valid, f"Proof failed for tx {i}: {errors}"

    def test_proof_json_roundtrip(self):
        """Proof survives JSON serialization/deserialization."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        doc_hash = "ee" * 32
        tx = Transaction.notarize(wallet.address, doc_hash, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(wallet.address, wallet.signing_sk)

        proof = export_proof(block.to_dict(), 0, tx.tx_id, doc_hash, wallet.address)

        # Round-trip through JSON
        serialized = json.dumps(proof)
        restored = json.loads(serialized)
        valid, errors = verify_proof(restored)
        assert valid, errors

    def test_tampered_proof_detected(self):
        """Tampering with any field in the proof is detected."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        doc_hash = "cd" * 32
        tx = Transaction.notarize(wallet.address, doc_hash, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(wallet.address, wallet.signing_sk)

        proof = export_proof(block.to_dict(), 0, tx.tx_id, doc_hash, wallet.address)

        # Tamper tx_id
        bad_proof = json.loads(json.dumps(proof))
        bad_proof["tx_id"] = "ff" * 32
        valid, _ = verify_proof(bad_proof)
        assert not valid

        # Tamper block hash
        bad_proof2 = json.loads(json.dumps(proof))
        bad_proof2["block"]["hash"] = "ee" * 32
        valid2, _ = verify_proof(bad_proof2)
        assert not valid2


# ===========================================================================
# Query API integration
# ===========================================================================

class TestQueryIntegration:
    """Integration tests for blockchain query APIs."""

    def test_get_block_by_hash_and_index(self):
        """Query blocks by both index and hash."""
        wallet = Wallet.generate()
        bc, tx_ids = _build_chain(wallet, 3)

        for i in range(4):  # genesis + 3 blocks
            block_by_idx = bc.get_block(i)
            assert block_by_idx is not None
            block_by_hash = bc.get_block(block_by_idx.block_hash)
            assert block_by_hash is not None
            assert block_by_hash.block_hash == block_by_idx.block_hash

    def test_get_tx_and_tx_block(self):
        """Query transactions and their containing block."""
        wallet = Wallet.generate()
        bc, tx_ids = _build_chain(wallet, 3)

        for i, tx_id in enumerate(tx_ids):
            tx = bc.get_tx(tx_id)
            assert tx is not None
            block_idx = bc.get_tx_block(tx_id)
            assert block_idx == i + 1  # genesis is block 0

    def test_txs_by_sender(self):
        """Query transactions by sender address."""
        wallet = Wallet.generate()
        bc, tx_ids = _build_chain(wallet, 5)
        sender_txs = bc.get_txs_by_sender(wallet.address)
        assert len(sender_txs) == 5

    def test_nonexistent_queries_return_none(self):
        """Queries for nonexistent data return None/empty."""
        wallet = Wallet.generate()
        bc, _ = _build_chain(wallet, 1)

        assert bc.get_block(999) is None
        assert bc.get_block("dead" * 16) is None
        assert bc.get_tx("dead" * 16) is None
        assert bc.get_tx_block("dead" * 16) is None
        assert bc.verify_document("dead" * 16) is None
        assert bc.get_encryption_pk("qv1nonexistent" + "0" * 53) is None
        assert bc.get_txs_by_sender("nobody") == []
        assert bc.get_shared_with("nobody") == []


# ===========================================================================
# Edge cases and boundary conditions
# ===========================================================================

class TestEdgeCases:
    """Boundary conditions and edge case tests."""

    def test_chain_height_after_genesis_only(self):
        """Height is 0 after only genesis."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        assert bc.height == 0
        assert len(bc.chain) == 1

    def test_produce_block_empty_pool_returns_none(self):
        """Producing a block with empty pool returns None."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        assert bc.produce_block(wallet.address, wallet.signing_sk) is None

    def test_double_init_is_noop(self):
        """Calling init_chain twice does not create a second genesis."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        assert bc.height == 0
        assert len(bc.chain) == 1

    def test_merkle_proof_single_tx_block(self):
        """Merkle proof works for a single-tx block."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        tx = Transaction.notarize(wallet.address, "ab" * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(wallet.address, wallet.signing_sk)

        proof = block.get_tx_proof(0)
        assert block.verify_tx_proof(tx.tx_id, proof) is True

    def test_pool_sender_count_accuracy(self):
        """_pool_sender_count is accurate through submit and mine cycles."""
        wallet = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)

        for i in range(3):
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            bc.submit_tx(tx)
            assert bc._pool_sender_count[wallet.address] == i + 1

        bc.produce_block(wallet.address, wallet.signing_sk)
        assert bc._pool_sender_count.get(wallet.address, 0) == 0

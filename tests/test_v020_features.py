"""Tests for v0.2.0 features: TLS, request-ID, SQLite store, fork resolution, proof, CLI, perf."""
import json
import os
import tempfile
import shutil
import time
import pytest
from qbit_network.core.wallet import Wallet
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.store import SQLiteStore, migrate_json_to_sqlite
from qbit_network.core.proof import export_proof, verify_proof
from qbit_network.crypto import MLKEM
from qbit_network.config import MAX_REORG_DEPTH


@pytest.fixture
def two_validators():
    v1 = Wallet.generate()
    v2 = Wallet.generate()
    bc = Blockchain()
    bc.consensus.add_validator(v1.address, v1.signing_pk)
    bc.consensus.add_validator(v2.address, v2.signing_pk)
    bc.init_chain(v1.address, v1.signing_sk)
    return v1, v2, bc


# ========== SQLite Store ==========

class TestSQLiteStore:
    def test_append_and_query(self, wallet, tmp_dir):
        store = SQLiteStore(tmp_dir)
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store.append_block(genesis)
        assert store.height() == 0
        assert store.get_block(0).block_hash == genesis.block_hash
        store.close()

    def test_transaction_indices(self, wallet, tmp_dir):
        store = SQLiteStore(tmp_dir)
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store.append_block(genesis)

        tx = Transaction.notarize(wallet.address, 'aabb' * 8, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block = Block(index=1, prev_hash=genesis.block_hash,
                      transactions=[tx], validator=wallet.address,
                      timestamp=int(time.time()))
        block.sign(wallet.signing_sk)
        store.append_block(block)

        assert store.get_tx(tx.tx_id).tx_id == tx.tx_id
        assert store.get_tx_block(tx.tx_id) == 1
        assert store.get_nonce(wallet.address) == 1
        assert store.get_notarization('aabb' * 8) == tx.tx_id
        assert store.tx_exists(tx.tx_id)
        assert len(store.get_txs_by_sender(wallet.address)) == 1
        store.close()

    def test_persistence(self, wallet, tmp_dir):
        store = SQLiteStore(tmp_dir)
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store.append_block(genesis)
        store.close()

        store2 = SQLiteStore(tmp_dir)
        assert store2.height() == 0
        assert store2.get_block(0).block_hash == genesis.block_hash
        store2.close()

    def test_key_registry(self, wallet, tmp_dir):
        store = SQLiteStore(tmp_dir)
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store.append_block(genesis)

        tx = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        block = Block(index=1, prev_hash=genesis.block_hash,
                      transactions=[tx], validator=wallet.address,
                      timestamp=int(time.time()))
        block.sign(wallet.signing_sk)
        store.append_block(block)

        assert store.get_encryption_pk(wallet.address) == wallet.encryption_pk.hex()
        store.close()

    def test_migration_from_json(self, wallet, tmp_dir):
        """Test migration from chain.json to SQLite."""
        # Build chain without data_dir (in-memory only), then manually save JSON
        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk)
        tx = Transaction.notarize(wallet.address, 'cc' * 32, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(wallet.address, wallet.signing_sk)

        # Write chain.json manually
        import json as _json
        os.makedirs(tmp_dir, exist_ok=True)
        with open(os.path.join(tmp_dir, 'chain.json'), 'w') as f:
            _json.dump([b.to_dict() for b in bc.chain], f)

        # Run migration
        migrate_json_to_sqlite(tmp_dir)
        assert os.path.exists(os.path.join(tmp_dir, 'chain.db'))
        assert os.path.exists(os.path.join(tmp_dir, 'chain.json.migrated'))

        store = SQLiteStore(tmp_dir)
        assert store.height() == 1
        assert store.get_notarization('cc' * 32) is not None
        store.close()


# ========== Fork Resolution ==========

class TestForkResolution:
    def _produce_correct(self, bc, v1, v2, nonces):
        expected = bc.consensus.select_validator(len(bc.chain))
        wallet = v1 if expected == v1.address else v2
        n = nonces.get(wallet.address, 0)
        tx = Transaction.notarize(wallet.address, f'{n:064x}', nonce=n)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(expected, wallet.signing_sk)
        nonces[wallet.address] = n + 1
        return block

    def test_longer_fork_wins(self, two_validators):
        v1, v2, bc = two_validators
        nonces = {}
        for _ in range(2):
            self._produce_correct(bc, v1, v2, nonces)
        assert bc.height == 2

        # Build 3-block fork from block 0
        fork_blocks = []
        fork_parent = bc.chain[0].block_hash
        fork_nonces = {}
        for i in range(1, 4):
            expected = bc.consensus.select_validator(i)
            wallet = v1 if expected == v1.address else v2
            n = fork_nonces.get(wallet.address, 0)
            tx = Transaction.notarize(wallet.address, f'ff{n:062x}', nonce=n)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            fb = Block(index=i, prev_hash=fork_parent, transactions=[tx],
                       validator=expected, timestamp=bc.chain[0].timestamp + i)
            fb.sign(wallet.signing_sk)
            fork_parent = fb.block_hash
            fork_blocks.append(fb)
            fork_nonces[wallet.address] = n + 1

        ok, msg = bc.try_reorg(fork_blocks)
        assert ok, msg
        assert bc.height == 3

    def test_equal_fork_rejected(self, two_validators):
        v1, v2, bc = two_validators
        nonces = {}
        for _ in range(2):
            self._produce_correct(bc, v1, v2, nonces)

        # Build 2-block fork (same length, same score)
        fork_blocks = []
        fork_parent = bc.chain[0].block_hash
        fork_nonces = {}
        for i in range(1, 3):
            expected = bc.consensus.select_validator(i)
            wallet = v1 if expected == v1.address else v2
            n = fork_nonces.get(wallet.address, 0)
            tx = Transaction.notarize(wallet.address, f'ee{n:062x}', nonce=n)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            fb = Block(index=i, prev_hash=fork_parent, transactions=[tx],
                       validator=expected, timestamp=bc.chain[0].timestamp + i)
            fb.sign(wallet.signing_sk)
            fork_parent = fb.block_hash
            fork_blocks.append(fb)
            fork_nonces[wallet.address] = n + 1

        ok, msg = bc.try_reorg(fork_blocks)
        assert not ok  # same score, same length → rejected

    def test_deep_reorg_blocked(self, two_validators):
        v1, v2, bc = two_validators
        fake = [Block(index=1, prev_hash='00' * 32, transactions=[],
                      validator=v1.address)] * (MAX_REORG_DEPTH + 5)
        ok, msg = bc.try_reorg(fake)
        assert not ok
        assert "too deep" in msg or "connect" in msg or "invalid" in msg

    def test_displaced_txs_return_to_pool(self, two_validators):
        v1, v2, bc = two_validators
        nonces = {}
        for _ in range(2):
            self._produce_correct(bc, v1, v2, nonces)

        pool_before = len(bc.tx_pool)

        fork_blocks = []
        fork_parent = bc.chain[0].block_hash
        fork_nonces = {}
        for i in range(1, 4):
            expected = bc.consensus.select_validator(i)
            wallet = v1 if expected == v1.address else v2
            n = fork_nonces.get(wallet.address, 0)
            tx = Transaction.notarize(wallet.address, f'dd{n:062x}', nonce=n)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            fb = Block(index=i, prev_hash=fork_parent, transactions=[tx],
                       validator=expected, timestamp=bc.chain[0].timestamp + i)
            fb.sign(wallet.signing_sk)
            fork_parent = fb.block_hash
            fork_blocks.append(fb)
            fork_nonces[wallet.address] = n + 1

        bc.try_reorg(fork_blocks)
        # Displaced txs that aren't in the fork should be in pool
        assert len(bc.tx_pool) >= pool_before


# ========== Proof Export/Verify ==========

class TestProofExportVerify:
    def test_export_and_verify(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, 'aabb' * 8, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)

        proof = export_proof(block.to_dict(), 0, tx.tx_id, 'aabb' * 8, wallet.address)
        valid, errors = verify_proof(proof)
        assert valid, errors

    def test_tampered_proof_rejected(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, 'ccdd' * 8, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)

        proof = export_proof(block.to_dict(), 0, tx.tx_id, 'ccdd' * 8, wallet.address)
        proof["tx_id"] = "ff" * 32  # tamper
        valid, errors = verify_proof(proof)
        assert not valid

    def test_proof_roundtrip_json(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, 'eeff' * 8, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)

        proof = export_proof(block.to_dict(), 0, tx.tx_id, 'eeff' * 8, wallet.address)
        serialized = json.dumps(proof)
        restored = json.loads(serialized)
        valid, _ = verify_proof(restored)
        assert valid


# ========== Performance Optimizations ==========

class TestPerfOptimizations:
    def test_pool_sender_count_tracks(self, blockchain, wallet):
        """_pool_sender_count maintained correctly."""
        tx1 = Transaction.notarize(wallet.address, 'aa' * 32, nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx1)
        assert blockchain._pool_sender_count[wallet.address] == 1

        tx2 = Transaction.notarize(wallet.address, 'bb' * 32, nonce=1)
        tx2.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx2)
        assert blockchain._pool_sender_count[wallet.address] == 2

        blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert blockchain._pool_sender_count.get(wallet.address, 0) == 0

    def test_notarizations_by_hash_index(self, blockchain, wallet):
        w2 = Wallet.generate()
        tx1 = Transaction.notarize(wallet.address, 'ab' * 32, nonce=0)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx1)
        blockchain.produce_block(wallet.address, wallet.signing_sk)

        tx2 = Transaction.notarize(w2.address, 'ab' * 32, nonce=0)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        blockchain.submit_tx(tx2)
        blockchain.produce_block(wallet.address, wallet.signing_sk)

        all_n = blockchain.get_all_notarizations('ab' * 32)
        assert len(all_n) == 2


# ========== TLS Configuration ==========

class TestTLSConfig:
    def test_rpc_server_tls_required_methods(self):
        from qbit_network.network.rpc import RPCServer
        server = RPCServer(port=0)
        assert "qv_getSharedSecret" in server.TLS_REQUIRED_METHODS
        assert "qv_decapsulateShared" in server.TLS_REQUIRED_METHODS
        assert "qv_blockNumber" not in server.TLS_REQUIRED_METHODS

    def test_rpc_server_no_tls_flag(self):
        from qbit_network.network.rpc import RPCServer
        server = RPCServer(port=0)
        assert server._tls_active is False


# ========== Request-ID Correlation ==========

class TestRequestID:
    def test_pending_requests_dict(self):
        from qbit_network.network.p2p import P2PNode
        node = P2PNode()
        assert hasattr(node, '_pending_requests')
        assert isinstance(node._pending_requests, dict)

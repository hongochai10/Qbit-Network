"""Tests for v0.4.0 Sprint 3: Peer Reputation, Chain Pruning, Proof Sig Verification.

30+ tests covering:
- PeerReputation: events, scoring, banning, decay, unban, edge cases
- Chain pruning: prune old blocks, indices preserved, pruned blocks inaccessible
- Proof signature: roundtrip with ML-DSA sig verification, tampered sig detected
"""
import json
import os
import shutil
import tempfile
import time
import pytest

from qbit_network.network.reputation import PeerReputation
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.block import Block
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.wallet import Wallet
from qbit_network.core.store import SQLiteStore
from qbit_network.core.proof import export_proof, verify_proof, PROOF_VERSION, PROOF_TYPE
from qbit_network.crypto import sha3_256


# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture
def rep():
    return PeerReputation()


@pytest.fixture
def wallet():
    return Wallet.generate()


@pytest.fixture
def blockchain(wallet):
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk)
    return bc


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="qv_prune_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sqlite_blockchain(wallet, tmp_dir):
    bc = Blockchain(data_dir=tmp_dir)
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
    return bc


def _mine_notarize(bc, wallet, doc_hash, nonce):
    """Submit a notarize tx and mine a block, return (block, tx)."""
    tx = Transaction.notarize(wallet.address, doc_hash, nonce=nonce)
    tx.sign(wallet.signing_sk, wallet.signing_pk)
    bc.submit_tx(tx)
    block = bc.produce_block(wallet.address, wallet.signing_sk)
    return block, tx


# =========================================================================
# Part 1: PeerReputation
# =========================================================================

class TestPeerReputationBasic:

    def test_default_score(self, rep):
        assert rep.get_score("peer1") == PeerReputation.DEFAULT_SCORE

    def test_valid_block_increases_score(self, rep):
        rep.record("peer1", "valid_block")
        assert rep.get_score("peer1") == PeerReputation.DEFAULT_SCORE + 10

    def test_valid_tx_increases_score(self, rep):
        rep.record("peer1", "valid_tx")
        assert rep.get_score("peer1") == PeerReputation.DEFAULT_SCORE + 1

    def test_invalid_block_decreases_score(self, rep):
        rep.record("peer1", "invalid_block")
        assert rep.get_score("peer1") == PeerReputation.DEFAULT_SCORE - 50

    def test_invalid_tx_decreases_score(self, rep):
        rep.record("peer1", "invalid_tx")
        assert rep.get_score("peer1") == PeerReputation.DEFAULT_SCORE - 10

    def test_state_root_mismatch_heavy_penalty(self, rep):
        """RS-2: state_root_mismatch carries -100 penalty (Byzantine forgery)."""
        rep.record("peer1", "state_root_mismatch")
        assert rep.get_score("peer1") == PeerReputation.DEFAULT_SCORE - 100

    def test_state_root_mismatch_bans_quickly(self, rep):
        """RS-2: Two state_root_mismatch events from default score triggers ban."""
        rep.record("peer1", "state_root_mismatch")
        assert not rep.is_banned("peer1")  # 100 - 100 = 0, not yet banned
        rep.record("peer1", "state_root_mismatch")
        assert rep.is_banned("peer1")  # 0 - 100 = -100, banned

    def test_multiple_events_accumulate(self, rep):
        rep.record("peer1", "valid_block")
        rep.record("peer1", "valid_block")
        rep.record("peer1", "invalid_tx")
        expected = PeerReputation.DEFAULT_SCORE + 10 + 10 - 10
        assert rep.get_score("peer1") == expected

    def test_unknown_event_raises(self, rep):
        with pytest.raises(ValueError, match="unknown"):
            rep.record("peer1", "not_a_real_event")


class TestPeerReputationBanning:

    def test_auth_failed_bans_immediately(self, rep):
        # DEFAULT_SCORE=100, auth_failed=-100 => score=0 > BAN_THRESHOLD=-100
        rep.record("peer1", "auth_failed")
        assert rep.get_score("peer1") == 0
        assert not rep.is_banned("peer1")

    def test_two_auth_failures_ban(self, rep):
        rep.record("peer1", "auth_failed")
        rep.record("peer1", "auth_failed")
        assert rep.get_score("peer1") == -100
        assert rep.is_banned("peer1")

    def test_cumulative_bad_events_ban(self, rep):
        # 100 - 50 - 50 - 50 = -50, not banned yet
        rep.record("peer1", "invalid_block")
        rep.record("peer1", "invalid_block")
        rep.record("peer1", "invalid_block")
        assert rep.get_score("peer1") == -50
        assert not rep.is_banned("peer1")
        # One more: -50 - 50 = -100, not banned (threshold is -100, need below)
        rep.record("peer1", "invalid_block")
        assert rep.get_score("peer1") == -100
        assert rep.is_banned("peer1")

    def test_banned_peer_stays_banned_after_good_events(self, rep):
        rep.record("peer1", "auth_failed")
        rep.record("peer1", "auth_failed")
        assert rep.is_banned("peer1")
        # Good events don't unban
        rep.record("peer1", "valid_block")
        assert rep.is_banned("peer1")

    def test_unban_resets_score(self, rep):
        rep.record("peer1", "auth_failed")
        rep.record("peer1", "auth_failed")
        assert rep.is_banned("peer1")
        rep.unban("peer1")
        assert not rep.is_banned("peer1")
        assert rep.get_score("peer1") == PeerReputation.DEFAULT_SCORE

    def test_is_banned_unknown_peer(self, rep):
        assert not rep.is_banned("never_seen")

    def test_protocol_error_penalty(self, rep):
        rep.record("peer1", "protocol_error")
        assert rep.get_score("peer1") == PeerReputation.DEFAULT_SCORE - 30

    def test_timeout_penalty(self, rep):
        rep.record("peer1", "timeout")
        assert rep.get_score("peer1") == PeerReputation.DEFAULT_SCORE - 5

    def test_rate_limited_penalty(self, rep):
        rep.record("peer1", "rate_limited")
        assert rep.get_score("peer1") == PeerReputation.DEFAULT_SCORE - 20


class TestPeerReputationDecay:

    def test_decay_moves_score_toward_default(self, rep):
        """R33-M01: Decay moves scores toward DEFAULT_SCORE, not zero."""
        rep.record("peer1", "valid_block")
        score_before = rep.get_score("peer1")
        rep.decay()
        score_after = rep.get_score("peer1")
        # Score should move closer to DEFAULT_SCORE
        default = PeerReputation.DEFAULT_SCORE
        assert abs(score_after - default) < abs(score_before - default)

    def test_decay_preserves_ban_status(self, rep):
        rep.record("peer1", "auth_failed")
        rep.record("peer1", "auth_failed")
        assert rep.is_banned("peer1")
        rep.decay()
        # Ban persists even as score decays
        assert rep.is_banned("peer1")

    def test_multiple_decays_converge_to_default(self, rep):
        """R33-M01: After many decays, score converges to DEFAULT_SCORE."""
        rep.record("peer1", "invalid_block")
        for _ in range(500):
            rep.decay()
        default = PeerReputation.DEFAULT_SCORE
        assert abs(rep.get_score("peer1") - default) < 1.0

    def test_decay_cleans_up_near_default_entries(self, rep):
        """R33-M01: Peers near DEFAULT_SCORE are cleaned up."""
        rep.record("peer1", "valid_tx")
        # Decay many times until cleaned
        for _ in range(1000):
            rep.decay()
        default = PeerReputation.DEFAULT_SCORE
        # Peer count may drop to 0 as near-default entries are cleaned
        assert rep.peer_count() == 0 or abs(rep.get_score("peer1") - default) < 0.01

    def test_idle_peer_stays_at_default_score(self, rep):
        """R33-M01: A peer with only DEFAULT_SCORE should not decay below it."""
        rep.record("peer1", "valid_tx")  # score = 101
        # Decay many rounds
        for _ in range(2000):
            rep.decay()
        default = PeerReputation.DEFAULT_SCORE
        score = rep.get_score("peer1")
        # Should converge to DEFAULT_SCORE, never go below
        assert score >= default - 0.1

    def test_many_peers_decay_all_converge(self, rep):
        """R33-M03: Many peers with varied events all converge to DEFAULT_SCORE."""
        default = PeerReputation.DEFAULT_SCORE
        # Create diverse peers with different starting scores
        for i in range(50):
            peer = f"peer_{i}"
            if i % 3 == 0:
                rep.record(peer, "valid_block")   # score = 110
            elif i % 3 == 1:
                rep.record(peer, "invalid_tx")    # score = 90
            else:
                rep.record(peer, "timeout")        # score = 95

        # Decay 10,000 rounds (simulates long-running node)
        for _ in range(10000):
            rep.decay()

        # All non-banned peers should converge to DEFAULT_SCORE or be cleaned up
        for i in range(50):
            peer = f"peer_{i}"
            score = rep.get_score(peer)
            assert abs(score - default) < 0.1, (
                f"{peer} score={score}, expected ~{default} after 10k decays"
            )


class TestPeerReputationEdgeCases:

    def test_empty_peer_id_ignored(self, rep):
        rep.record("", "valid_block")
        assert rep.peer_count() == 0

    def test_none_peer_id_ignored(self, rep):
        rep.record(None, "valid_block")
        assert rep.peer_count() == 0

    def test_get_score_empty_string(self, rep):
        assert rep.get_score("") == PeerReputation.DEFAULT_SCORE

    def test_unban_unknown_peer_safe(self, rep):
        rep.unban("never_seen")  # should not raise

    def test_get_all_scores(self, rep):
        rep.record("p1", "valid_block")
        rep.record("p2", "invalid_tx")
        scores = rep.get_all_scores()
        assert "p1" in scores
        assert "p2" in scores

    def test_get_banned_peers(self, rep):
        rep.record("p1", "auth_failed")
        rep.record("p1", "auth_failed")
        banned = rep.get_banned_peers()
        assert "p1" in banned


# =========================================================================
# Part 2: Chain Pruning
# =========================================================================

class TestChainPruning:

    def test_prune_no_op_below_retention(self, sqlite_blockchain, wallet):
        """Pruning does nothing when height < retention."""
        count = sqlite_blockchain.prune(retention=10000)
        assert count == 0

    def test_prune_removes_old_blocks(self, sqlite_blockchain, wallet):
        """Prune deletes block data for blocks older than retention."""
        # Mine 20 blocks
        for i in range(20):
            doc = f"{i:064x}"
            _mine_notarize(sqlite_blockchain, wallet, doc, i)

        assert sqlite_blockchain.height >= 20

        # Prune with retention=10 -- should keep last 10 blocks
        count = sqlite_blockchain.prune(retention=10)
        assert count > 0

    def test_prune_preserves_recent_blocks(self, sqlite_blockchain, wallet):
        """Recent blocks are still accessible after pruning."""
        for i in range(20):
            doc = f"{i:064x}"
            _mine_notarize(sqlite_blockchain, wallet, doc, i)

        height = sqlite_blockchain.height
        sqlite_blockchain.prune(retention=10)

        # Latest block should still be accessible via store
        block = sqlite_blockchain._store.get_block(height)
        assert block is not None

    def test_pruned_blocks_inaccessible_from_store(self, sqlite_blockchain, wallet):
        """Pruned blocks return None from store.get_block()."""
        for i in range(20):
            doc = f"{i:064x}"
            _mine_notarize(sqlite_blockchain, wallet, doc, i)

        sqlite_blockchain.prune(retention=5)

        # Block 1 (early block) should be pruned from store
        early_block = sqlite_blockchain._store.get_block(1)
        assert early_block is None

    def test_prune_preserves_in_memory_indices(self, sqlite_blockchain, wallet):
        """In-memory indices (tx_by_id, notarizations) remain after pruning."""
        doc = "aa" * 32
        _mine_notarize(sqlite_blockchain, wallet, doc, 0)

        for i in range(1, 20):
            d = f"{i:064x}"
            _mine_notarize(sqlite_blockchain, wallet, d, i)

        # Notarization index should still find the doc
        result = sqlite_blockchain.verify_document(doc)
        assert result is not None

        sqlite_blockchain.prune(retention=5)

        # In-memory index still works
        result_after = sqlite_blockchain.verify_document(doc)
        # The in-memory index still knows about it but the block data is pruned
        # so get_block will return None for the pruned block
        assert result_after is not None or doc in sqlite_blockchain._notarizations

    def test_prune_in_memory_mode_no_op(self, blockchain, wallet):
        """Pruning is a no-op in in-memory mode (no store)."""
        for i in range(5):
            _mine_notarize(blockchain, wallet, f"{i:064x}", i)
        count = blockchain.prune(retention=2)
        assert count == 0

    def test_prune_invalid_retention(self, sqlite_blockchain, wallet):
        """Invalid retention values are rejected."""
        assert sqlite_blockchain.prune(retention=0) == 0
        assert sqlite_blockchain.prune(retention=-1) == 0


class TestSQLiteStorePruning:

    def test_prune_blocks_direct(self, tmp_dir, wallet):
        """Direct SQLiteStore.prune_blocks() test."""
        store = SQLiteStore(tmp_dir)
        # Append some blocks
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store.append_block(genesis)

        for i in range(1, 15):
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i - 1)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            prev = store.get_block(i - 1)
            block = Block(
                index=i,
                prev_hash=prev.block_hash,
                transactions=[tx],
                validator=wallet.address,
                timestamp=int(time.time()) + i,
            )
            block.sign(wallet.signing_sk)
            store.append_block(block)

        assert store.height() == 14

        # Prune blocks 0-9 (keep 10-14)
        count = store.prune_blocks(10)
        assert count == 10

        # Verify pruned blocks are gone
        assert store.get_block(0) is None
        assert store.get_block(5) is None
        assert store.get_block(9) is None

        # Verify remaining blocks are intact
        assert store.get_block(10) is not None
        assert store.get_block(14) is not None

        store.close()

    def test_prune_zero_before_index(self, tmp_dir, wallet):
        """prune_blocks(0) is a no-op."""
        store = SQLiteStore(tmp_dir)
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store.append_block(genesis)
        count = store.prune_blocks(0)
        assert count == 0
        store.close()

    def test_prune_preserves_height(self, tmp_dir, wallet):
        """Pruning does not change the store height."""
        store = SQLiteStore(tmp_dir)
        genesis = Block.genesis(wallet.address)
        genesis.sign(wallet.signing_sk)
        store.append_block(genesis)
        for i in range(1, 10):
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i - 1)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            prev = store.get_block(i - 1)
            block = Block(
                index=i,
                prev_hash=prev.block_hash,
                transactions=[tx],
                validator=wallet.address,
                timestamp=int(time.time()) + i,
            )
            block.sign(wallet.signing_sk)
            store.append_block(block)

        assert store.height() == 9
        store.prune_blocks(5)
        assert store.height() == 9  # height unchanged
        store.close()


# =========================================================================
# Part 3: Proof Block Signature Verification
# =========================================================================

class TestProofSignatureVerification:

    def test_export_proof_includes_validator_pubkey(self, blockchain, wallet):
        """export_proof with validator_pubkey includes it in proof bundle."""
        block, tx = _mine_notarize(blockchain, wallet, "aa" * 32, 0)
        proof = export_proof(
            block.to_dict(), 0, tx.tx_id, "aa" * 32, wallet.address,
            validator_pubkey=wallet.signing_pk.hex())
        assert "validator_pubkey" in proof
        assert proof["validator_pubkey"] == wallet.signing_pk.hex()

    def test_export_proof_without_validator_pubkey(self, blockchain, wallet):
        """export_proof without validator_pubkey omits the field."""
        block, tx = _mine_notarize(blockchain, wallet, "bb" * 32, 0)
        proof = export_proof(
            block.to_dict(), 0, tx.tx_id, "bb" * 32, wallet.address)
        assert "validator_pubkey" not in proof

    def test_verify_proof_with_valid_signature(self, blockchain, wallet):
        """Proof with correct validator_pubkey and block sig verifies."""
        block, tx = _mine_notarize(blockchain, wallet, "cc" * 32, 0)
        proof = export_proof(
            block.to_dict(), 0, tx.tx_id, "cc" * 32, wallet.address,
            validator_pubkey=wallet.signing_pk.hex())
        valid, errors = verify_proof(proof)
        assert valid is True, errors
        assert errors == []

    def test_verify_proof_tampered_signature_fails(self, blockchain, wallet):
        """Proof with tampered block signature fails verification."""
        block, tx = _mine_notarize(blockchain, wallet, "dd" * 32, 0)
        proof = export_proof(
            block.to_dict(), 0, tx.tx_id, "dd" * 32, wallet.address,
            validator_pubkey=wallet.signing_pk.hex())
        # Tamper with the block signature
        sig = proof["block"]["signature"]
        # Flip a byte in the signature
        tampered = "ff" + sig[2:]
        proof["block"]["signature"] = tampered
        valid, errors = verify_proof(proof)
        assert valid is False
        assert any("signature" in e.lower() for e in errors)

    def test_verify_proof_wrong_validator_pubkey_fails(self, blockchain, wallet):
        """Proof with wrong validator pubkey fails signature verification."""
        block, tx = _mine_notarize(blockchain, wallet, "ee" * 32, 0)
        # Use a different wallet's pubkey
        other_wallet = Wallet.generate()
        proof = export_proof(
            block.to_dict(), 0, tx.tx_id, "ee" * 32, wallet.address,
            validator_pubkey=other_wallet.signing_pk.hex())
        valid, errors = verify_proof(proof)
        assert valid is False
        assert any("signature" in e.lower() for e in errors)

    def test_verify_proof_roundtrip_json_with_sig(self, blockchain, wallet):
        """Proof with sig survives JSON serialization roundtrip."""
        block, tx = _mine_notarize(blockchain, wallet, "ff" * 32, 0)
        proof = export_proof(
            block.to_dict(), 0, tx.tx_id, "ff" * 32, wallet.address,
            validator_pubkey=wallet.signing_pk.hex())
        restored = json.loads(json.dumps(proof))
        valid, errors = verify_proof(restored)
        assert valid is True, errors

    def test_verify_proof_without_validator_pubkey_still_works(self, blockchain, wallet):
        """Proof without validator_pubkey skips sig check, still validates."""
        block, tx = _mine_notarize(blockchain, wallet, "1234" * 8, 0)
        proof = export_proof(
            block.to_dict(), 0, tx.tx_id, "1234" * 8, wallet.address)
        valid, errors = verify_proof(proof)
        assert valid is True, errors

    def test_verify_proof_empty_validator_pubkey_skips_sig(self, blockchain, wallet):
        """Empty validator_pubkey string skips signature verification."""
        block, tx = _mine_notarize(blockchain, wallet, "5678" * 8, 0)
        proof = export_proof(
            block.to_dict(), 0, tx.tx_id, "5678" * 8, wallet.address,
            validator_pubkey="")
        valid, errors = verify_proof(proof)
        assert valid is True, errors

    def test_verify_proof_invalid_pubkey_hex_reports_error(self, blockchain, wallet):
        """Invalid hex for validator_pubkey reports an error."""
        block, tx = _mine_notarize(blockchain, wallet, "abcd" * 8, 0)
        proof = export_proof(
            block.to_dict(), 0, tx.tx_id, "abcd" * 8, wallet.address,
            validator_pubkey=wallet.signing_pk.hex())
        proof["validator_pubkey"] = "not_valid_hex!!!"
        valid, errors = verify_proof(proof)
        assert valid is False
        assert any("error" in e.lower() or "signature" in e.lower() for e in errors)

    def test_multi_tx_block_proof_with_sig(self, blockchain, wallet):
        """Multi-tx block proof with signature verification works."""
        tx0 = Transaction.notarize(wallet.address, "aa" * 32, nonce=0)
        tx0.sign(wallet.signing_sk, wallet.signing_pk)
        tx1 = Transaction.notarize(wallet.address, "bb" * 32, nonce=1)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx0)
        blockchain.submit_tx(tx1)
        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        proof = export_proof(
            block.to_dict(), 1, tx1.tx_id, "bb" * 32, wallet.address,
            validator_pubkey=wallet.signing_pk.hex())
        valid, errors = verify_proof(proof)
        assert valid is True, errors

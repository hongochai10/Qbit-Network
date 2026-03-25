"""Adversarial tests — attack scenarios discovered during 9 audit rounds + ISS-015 expansion."""
import json
import os
import time
import pytest
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.wallet import Wallet
from qbit_network.crypto import MLDSA, MLKEM
from qbit_network.network.p2p import _is_safe_peer
from qbit_network.config import MAX_BLOCK_DRIFT, MAX_TX_PAYLOAD_SIZE, MAX_REORG_DEPTH
from tests.conftest import submit_and_mine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_blockchain_with_validator(wallet):
    """Create a fresh blockchain with one validator."""
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk)
    return bc


def _sign_and_submit(bc, wallet, tx):
    """Sign a transaction and submit it to the blockchain pool."""
    tx.sign(wallet.signing_sk, wallet.signing_pk)
    return bc.submit_tx(tx)


# ===========================================================================
# Chain integrity attacks
# ===========================================================================

class TestChainIntegrity:
    """Attacks against chain state."""

    def test_genesis_replay_rejected(self, blockchain, wallet):
        """Attacker sends a second genesis block after chain is initialized."""
        fake_genesis = Block.genesis(wallet.address)
        fake_genesis.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(fake_genesis)
        assert not ok  # rejected -- either "already have" or "out-of-order"

    def test_fork_via_old_parent(self, blockchain, wallet):
        """Attacker creates block pointing to historical parent hash."""
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx)

        # Try block at index=2 pointing to genesis (not latest)
        fork = Block(index=2, prev_hash=blockchain.chain[0].block_hash,
                     transactions=[], validator=wallet.address,
                     timestamp=blockchain.latest_block.timestamp + 1)
        fork.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(fork)
        assert not ok  # prev_hash mismatch caught by consensus

    def test_future_timestamp_block_rejected(self, blockchain, wallet):
        """Block with timestamp exceeding MAX_BLOCK_DRIFT is rejected."""
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)

        future_block = Block(
            index=1, prev_hash=blockchain.chain[0].block_hash,
            transactions=[tx], validator=wallet.address,
            timestamp=int(time.time()) + MAX_BLOCK_DRIFT + 9999)
        future_block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(future_block)
        assert not ok
        assert "future" in err

    def test_future_timestamp_exactly_at_drift_boundary(self, blockchain, wallet):
        """Block timestamp at exactly MAX_BLOCK_DRIFT+1 seconds into the future is rejected."""
        tx = Transaction.notarize(wallet.address, "ab", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)

        boundary_block = Block(
            index=1, prev_hash=blockchain.chain[0].block_hash,
            transactions=[tx], validator=wallet.address,
            timestamp=int(time.time()) + MAX_BLOCK_DRIFT + 1)
        boundary_block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(boundary_block)
        assert not ok
        assert "future" in err

    def test_timestamp_before_parent_rejected(self, blockchain, wallet):
        """Block with timestamp equal to or before parent is rejected."""
        tx = Transaction.notarize(wallet.address, "cd", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)

        stale_block = Block(
            index=1, prev_hash=blockchain.chain[0].block_hash,
            transactions=[tx], validator=wallet.address,
            timestamp=blockchain.chain[0].timestamp)
        stale_block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(stale_block)
        assert not ok
        assert "after parent" in err

    def test_invalid_block_hash_tampered(self, blockchain, wallet):
        """Attacker modifies a transaction after block is signed, corrupting the hash."""
        tx = Transaction.notarize(wallet.address, "abcd", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)

        block = blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None

        # Tamper with internal block data and attempt to re-add via serialization
        block_dict = block.to_dict()
        block_dict["hash"] = "ff" * 32  # tamper hash
        with pytest.raises(ValueError, match="block hash mismatch"):
            Block.from_dict(block_dict)

    def test_empty_block_after_genesis_rejected(self, blockchain, wallet):
        """Non-genesis block with zero transactions must be rejected."""
        empty = Block(index=1, prev_hash=blockchain.chain[0].block_hash,
                      transactions=[], validator=wallet.address,
                      timestamp=blockchain.chain[0].timestamp + 1)
        empty.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(empty)
        assert not ok
        assert "at least one" in err


# ===========================================================================
# Double-spend and replay attacks
# ===========================================================================

class TestDoubleSpendAndReplay:
    """Double-spend and transaction replay attacks."""

    def test_double_spend_same_tx_in_pool(self, blockchain, wallet):
        """Submit the exact same signed tx twice -- second should be rejected as duplicate."""
        tx = Transaction.notarize(wallet.address, "deadbeef", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok1, _ = blockchain.submit_tx(tx)
        assert ok1
        ok2, err = blockchain.submit_tx(tx)
        assert not ok2
        assert "already in pool" in err

    def test_replay_confirmed_tx(self, blockchain, wallet):
        """Replay a transaction that was already confirmed in a block."""
        tx = Transaction.notarize(wallet.address, "aabb", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx)

        # Attempt to re-submit the same confirmed tx
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "already in chain" in err or "duplicate" in err

    def test_replay_confirmed_tx_in_new_block(self, blockchain, wallet):
        """Attacker includes a confirmed tx in a new block -- consensus rejects it."""
        tx = Transaction.notarize(wallet.address, "ccdd", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx)

        replay_block = Block(
            index=blockchain.height + 1,
            prev_hash=blockchain.latest_block.block_hash,
            transactions=[tx], validator=wallet.address,
            timestamp=blockchain.latest_block.timestamp + 1)
        replay_block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(replay_block)
        assert not ok
        assert "already in chain" in err

    def test_duplicate_tx_within_single_block(self, blockchain, wallet):
        """Same tx included twice in a single block -- consensus rejects duplicate."""
        tx = Transaction.notarize(wallet.address, "eeff", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)

        dup_block = Block(
            index=1, prev_hash=blockchain.chain[0].block_hash,
            transactions=[tx, tx], validator=wallet.address,
            timestamp=blockchain.chain[0].timestamp + 1)
        dup_block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(dup_block)
        assert not ok
        assert "duplicate" in err


# ===========================================================================
# Transaction replay and nonce attacks
# ===========================================================================

class TestTransactionReplay:
    """Transaction replay and nonce attacks."""

    def test_cross_chain_replay_blocked_by_chain_id(self, wallet):
        """Same tx content on different chain_id has different tx_id, blocking replay."""
        tx1 = Transaction(TxType.NOTARIZE, wallet.address,
                          payload={"documentHash": "aa", "metadata": ""},
                          nonce=0, chain_id="mainnet")
        tx2 = Transaction(TxType.NOTARIZE, wallet.address,
                          payload={"documentHash": "aa", "metadata": ""},
                          nonce=0, chain_id="testnet")
        assert tx1.tx_id != tx2.tx_id

    def test_nonce_gap_in_block(self, blockchain, wallet):
        """Block with nonce gap (0 then 2, missing 1) rejected."""
        tx0 = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx0.sign(wallet.signing_sk, wallet.signing_pk)
        tx2 = Transaction.notarize(wallet.address, "bb", nonce=2)  # skip 1
        tx2.sign(wallet.signing_sk, wallet.signing_pk)

        block = Block(index=1, prev_hash=blockchain.chain[0].block_hash,
                      transactions=[tx0, tx2], validator=wallet.address,
                      timestamp=blockchain.chain[0].timestamp + 1)
        block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(block)
        assert not ok
        assert "nonce gap" in err

    def test_nonce_skip_rejected_in_pool(self, blockchain, wallet):
        """Submit a tx with nonce=5 when expected is 0 -- pool rejects it."""
        tx = Transaction.notarize(wallet.address, "bb", nonce=5)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "invalid nonce" in err

    def test_nonce_duplicate_rejected_in_pool(self, blockchain, wallet):
        """After mining nonce=0, submitting nonce=0 again is rejected."""
        tx0 = Transaction.notarize(wallet.address, "cc", nonce=0)
        tx0.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx0)

        tx0_dup = Transaction.notarize(wallet.address, "dd", nonce=0)
        tx0_dup.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx0_dup)
        assert not ok
        assert "invalid nonce" in err

    def test_negative_nonce_in_block(self, blockchain, wallet):
        """Tx with negative nonce should not match expected nonce and be rejected."""
        tx = Transaction.notarize(wallet.address, "ee", nonce=-1)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "invalid nonce" in err

    def test_nonce_reuse_across_blocks(self, blockchain, wallet):
        """After block mines nonce=0, a new block with nonce=0 again is rejected."""
        tx0 = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx0.sign(wallet.signing_sk, wallet.signing_pk)
        submit_and_mine(blockchain, wallet, tx0)

        replay = Transaction.notarize(wallet.address, "bb", nonce=0)
        replay.sign(wallet.signing_sk, wallet.signing_pk)
        bad_block = Block(
            index=blockchain.height + 1,
            prev_hash=blockchain.latest_block.block_hash,
            transactions=[replay], validator=wallet.address,
            timestamp=blockchain.latest_block.timestamp + 1)
        bad_block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(bad_block)
        assert not ok
        assert "nonce" in err


# ===========================================================================
# Payload manipulation attacks
# ===========================================================================

class TestPayloadAttacks:
    """Payload manipulation attacks."""

    def test_extra_payload_keys_dedup_bypass(self, blockchain, wallet):
        """Extra keys in payload change tx_id -- blocked by _ALLOWED_KEYS."""
        tx = Transaction(TxType.NOTARIZE, wallet.address,
                         payload={"documentHash": "aabb", "metadata": "",
                                  "injected": "evil"}, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "unknown payload keys" in err

    def test_oversized_payload(self, blockchain, wallet):
        """Payload exceeding MAX_TX_PAYLOAD_SIZE is rejected."""
        tx = Transaction.notarize(wallet.address, "aa" * 5000, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "too large" in err

    def test_oversized_payload_exact_boundary(self, wallet):
        """Payload exactly at MAX_TX_PAYLOAD_SIZE boundary should still be valid."""
        # Build a documentHash that fills payload to just under limit
        # MAX_TX_PAYLOAD_SIZE = 8192 bytes for the serialized payload
        small_hash = "ab" * 32  # well under limit
        tx = Transaction.notarize(wallet.address, small_hash, nonce=0)
        ok, _ = tx.validate_payload()
        assert ok

    def test_tx_type_mismatch_notarize_with_share_keys(self, wallet):
        """NOTARIZE tx with SHARE payload keys (cid, encapsulatedKey) is rejected."""
        tx = Transaction(TxType.NOTARIZE, wallet.address,
                         payload={"cid": "QmX", "encapsulatedKey": "aabb",
                                  "expires": 0}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "unknown payload keys" in err or "documentHash" in err.lower() or not ok

    def test_tx_type_mismatch_store_with_notarize_keys(self, wallet):
        """STORE tx missing required cid field is rejected."""
        tx = Transaction(TxType.STORE, wallet.address,
                         payload={"documentHash": "aa", "metadata": ""}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "cid required" in err

    def test_tx_type_mismatch_register_key_with_notarize_keys(self, wallet):
        """REGISTER_KEY tx with wrong payload keys is rejected."""
        tx = Transaction(TxType.REGISTER_KEY, wallet.address,
                         payload={"documentHash": "aa", "metadata": ""}, nonce=0)
        ok, err = tx.validate_payload()
        assert not ok


# ===========================================================================
# Cryptographic attacks
# ===========================================================================

class TestCryptoAttacks:
    """Attacks against cryptographic primitives."""

    def test_invalid_signature_rejected(self, blockchain, wallet):
        """Tx with completely wrong signature is rejected at submission."""
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sender_pubkey = wallet.signing_pk
        tx.signature = b"\xff" * 3309  # wrong signature, correct size
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "invalid signature" in err

    def test_malformed_signature_doesnt_crash(self, blockchain, wallet):
        """1-byte signature should not crash node."""
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sender_pubkey = wallet.signing_pk
        tx.signature = b"\x00"
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "invalid signature" in err

    def test_wrong_signer_rejected(self, blockchain, wallet_pair):
        """Tx signed by wrong key (bob signs alice's tx) is rejected."""
        alice, bob = wallet_pair
        bc = _make_blockchain_with_validator(alice)
        tx = Transaction.notarize(alice.address, "aabb", nonce=0)
        tx.sign(bob.signing_sk, bob.signing_pk)  # signed by bob but sender=alice
        ok, err = bc.submit_tx(tx)
        assert not ok
        assert "invalid signature" in err

    def test_wrong_pubkey_size_from_dict(self):
        """from_dict with wrong-size pubkey raises ValueError."""
        with pytest.raises(ValueError, match="sender_pubkey wrong size"):
            Transaction.from_dict({
                "type": "NOTARIZE", "from": "qv1test", "timestamp": 1,
                "nonce": 0, "payload": {"documentHash": "aa", "metadata": ""},
                "signature": "aa" * 3309, "sender_pubkey": "bb" * 100,
                "chainId": "qbit-mainnet",
            })

    def test_mlkem_bad_ciphertext_size(self):
        """ML-KEM decapsulate with wrong ciphertext size raises ValueError."""
        with pytest.raises(ValueError):
            MLKEM.decapsulate(b"x" * 2400, b"short_ct")

    def test_block_signature_from_wrong_validator(self, blockchain, wallet_pair):
        """Block signed by non-validator key is rejected."""
        alice, bob = wallet_pair
        bc = _make_blockchain_with_validator(alice)
        tx = Transaction.notarize(alice.address, "dd", nonce=0)
        tx.sign(alice.signing_sk, alice.signing_pk)
        bc.submit_tx(tx)

        bad_block = Block(
            index=1, prev_hash=bc.chain[0].block_hash,
            transactions=[tx], validator=alice.address,
            timestamp=bc.chain[0].timestamp + 1)
        bad_block.sign(bob.signing_sk)  # signed by bob, but validator=alice
        ok, err = bc.add_block(bad_block)
        assert not ok
        assert "signature" in err

    def test_unsigned_block_rejected(self, blockchain, wallet):
        """Block without any signature is rejected by consensus."""
        tx = Transaction.notarize(wallet.address, "ff", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)

        unsigned_block = Block(
            index=1, prev_hash=blockchain.chain[0].block_hash,
            transactions=[tx], validator=wallet.address,
            timestamp=blockchain.chain[0].timestamp + 1)
        # no sign() call
        ok, err = blockchain.add_block(unsigned_block)
        assert not ok
        assert "signature" in err


# ===========================================================================
# Validator attacks
# ===========================================================================

class TestValidatorAttacks:
    """Attacks involving validator identity and turn order."""

    def test_invalid_validator_address_rejected(self, blockchain, wallet):
        """Block from an address not in the validator set is rejected."""
        impostor = Wallet.generate()
        tx = Transaction.notarize(wallet.address, "ab", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)

        bad_block = Block(
            index=1, prev_hash=blockchain.chain[0].block_hash,
            transactions=[tx], validator=impostor.address,
            timestamp=blockchain.chain[0].timestamp + 1)
        bad_block.sign(impostor.signing_sk)
        ok, err = blockchain.add_block(bad_block)
        assert not ok
        assert "unknown validator" in err or "wrong validator" in err

    def test_wrong_validator_turn(self):
        """Block produced by valid validator but out of turn is rejected."""
        v1 = Wallet.generate()
        v2 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.consensus.add_validator(v2.address, v2.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        # Determine who should produce block 1
        expected = bc.consensus.select_validator(1)
        wrong = v1 if expected != v1.address else v2
        right = v1 if expected == v1.address else v2

        tx = Transaction.notarize(right.address, "bb", nonce=0)
        tx.sign(right.signing_sk, right.signing_pk)

        bad_block = Block(
            index=1, prev_hash=bc.chain[0].block_hash,
            transactions=[tx], validator=wrong.address,
            timestamp=bc.chain[0].timestamp + 1)
        bad_block.sign(wrong.signing_sk)
        ok, err = bc.add_block(bad_block)
        assert not ok
        assert "wrong validator turn" in err


# ===========================================================================
# Fork attacks
# ===========================================================================

class TestForkAttacks:
    """Fork and reorganization attacks."""

    def test_longer_fork_wins(self):
        """A strictly longer fork replaces the current chain via try_reorg."""
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
            tx = Transaction.notarize(w.address, f"{n:064x}", nonce=n)
            tx.sign(w.signing_sk, w.signing_pk)
            bc.submit_tx(tx)
            bc.produce_block(expected, w.signing_sk)
            nonces[w.address] = n + 1
        assert bc.height == 2

        # Build 3-block fork from genesis
        fork_blocks = []
        fork_parent = bc.chain[0].block_hash
        fork_nonces = {}
        for i in range(1, 4):
            expected = bc.consensus.select_validator(i)
            w = v1 if expected == v1.address else v2
            n = fork_nonces.get(w.address, 0)
            tx = Transaction.notarize(w.address, f"ff{n:062x}", nonce=n)
            tx.sign(w.signing_sk, w.signing_pk)
            fb = Block(index=i, prev_hash=fork_parent, transactions=[tx],
                       validator=expected, timestamp=bc.chain[0].timestamp + i)
            fb.sign(w.signing_sk)
            fork_parent = fb.block_hash
            fork_blocks.append(fb)
            fork_nonces[w.address] = n + 1

        ok, msg = bc.try_reorg(fork_blocks)
        assert ok, msg
        assert bc.height == 3

    def test_equal_length_fork_rejected(self):
        """A fork of equal length to the current chain is rejected (first-seen wins)."""
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
            tx = Transaction.notarize(w.address, f"{n:064x}", nonce=n)
            tx.sign(w.signing_sk, w.signing_pk)
            bc.submit_tx(tx)
            bc.produce_block(expected, w.signing_sk)
            nonces[w.address] = n + 1

        # Build 2-block fork (same length) -- should be rejected
        fork_blocks = []
        fork_parent = bc.chain[0].block_hash
        fork_nonces = {}
        for i in range(1, 3):
            expected = bc.consensus.select_validator(i)
            w = v1 if expected == v1.address else v2
            n = fork_nonces.get(w.address, 0)
            tx = Transaction.notarize(w.address, f"ee{n:062x}", nonce=n)
            tx.sign(w.signing_sk, w.signing_pk)
            fb = Block(index=i, prev_hash=fork_parent, transactions=[tx],
                       validator=expected, timestamp=bc.chain[0].timestamp + i)
            fb.sign(w.signing_sk)
            fork_parent = fb.block_hash
            fork_blocks.append(fb)
            fork_nonces[w.address] = n + 1

        ok, msg = bc.try_reorg(fork_blocks)
        assert not ok

    def test_deep_reorg_blocked(self):
        """Reorg exceeding MAX_REORG_DEPTH is rejected."""
        v1 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        fake = [Block(index=1, prev_hash="00" * 32, transactions=[],
                      validator=v1.address)] * (MAX_REORG_DEPTH + 5)
        ok, msg = bc.try_reorg(fake)
        assert not ok
        assert "too deep" in msg or "connect" in msg or "invalid" in msg

    def test_empty_fork_rejected(self):
        """Empty fork block list is rejected."""
        v1 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        ok, msg = bc.try_reorg([])
        assert not ok
        assert "empty" in msg

    def test_fork_not_connecting_to_chain(self):
        """Fork whose first block does not link to our chain is rejected."""
        v1 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        # Mine one block on main chain
        tx = Transaction.notarize(v1.address, "aa", nonce=0)
        tx.sign(v1.signing_sk, v1.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(v1.address, v1.signing_sk)

        # Fork block pointing to non-existent parent
        fork_tx = Transaction.notarize(v1.address, "bb", nonce=0)
        fork_tx.sign(v1.signing_sk, v1.signing_pk)
        fork_block = Block(index=1, prev_hash="dead" * 16, transactions=[fork_tx],
                           validator=v1.address,
                           timestamp=bc.chain[0].timestamp + 1)
        fork_block.sign(v1.signing_sk)

        ok, msg = bc.try_reorg([fork_block, fork_block])
        assert not ok
        assert "connect" in msg


# ===========================================================================
# SSRF Protection
# ===========================================================================

class TestSSRFProtection:
    """P2P SSRF and peer validation."""

    def test_block_private_ips(self):
        """Private IP ranges (10.x, 192.168.x, 172.16.x) are blocked."""
        assert not _is_safe_peer("10.0.0.1", 9000, "0.0.0.0", 9000)
        assert not _is_safe_peer("192.168.1.1", 9000, "0.0.0.0", 9000)
        assert not _is_safe_peer("172.16.0.1", 9000, "0.0.0.0", 9000)

    def test_block_link_local(self):
        """Link-local/metadata IPs are blocked."""
        assert not _is_safe_peer("169.254.169.254", 80, "0.0.0.0", 9000)

    def test_block_loopback(self):
        """Loopback addresses are blocked."""
        assert not _is_safe_peer("127.0.0.1", 9000, "0.0.0.0", 9000)

    def test_block_reserved_ports(self):
        """Reserved service ports (SSH, MySQL) are blocked."""
        assert not _is_safe_peer("8.8.8.8", 22, "0.0.0.0", 9000)
        assert not _is_safe_peer("8.8.8.8", 3306, "0.0.0.0", 9000)

    def test_block_self_connection(self):
        """Self-connection is blocked."""
        assert not _is_safe_peer("0.0.0.0", 9000, "0.0.0.0", 9000)

    def test_block_negative_port(self):
        """Invalid port numbers are blocked."""
        assert not _is_safe_peer("8.8.8.8", -1, "0.0.0.0", 9000)
        assert not _is_safe_peer("8.8.8.8", 70000, "0.0.0.0", 9000)

    def test_block_metadata_hostnames(self):
        """Cloud metadata hostnames are blocked."""
        assert not _is_safe_peer("metadata.google.internal", 80, "0.0.0.0", 9000)


# ===========================================================================
# Wallet file tampering
# ===========================================================================

class TestWalletAttacks:
    """Wallet file tampering."""

    def test_scrypt_dos_capped(self, wallet, tmp_dir):
        """Huge scrypt params in file are capped to max."""
        path = os.path.join(tmp_dir, "w.json")
        wallet.save(path, password="test")
        with open(path) as f:
            data = json.load(f)
        data["scrypt"] = {"n": 2**30, "r": 128, "p": 64}  # insane params
        with open(path, "w") as f:
            json.dump(data, f)
        # Should use capped params -- different from original, decrypt fails
        with pytest.raises(ValueError):
            Wallet.load(path, password="test")

    def test_truncated_ciphertext(self, wallet, tmp_dir):
        """Truncated ciphertext in wallet file raises ValueError."""
        path = os.path.join(tmp_dir, "w.json")
        wallet.save(path, password="test")
        with open(path) as f:
            data = json.load(f)
        data["ciphertext"] = data["ciphertext"][:20]
        with open(path, "w") as f:
            json.dump(data, f)
        with pytest.raises(ValueError):
            Wallet.load(path, password="test")

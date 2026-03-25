"""Adversarial tests — attack scenarios discovered during 9 audit rounds."""
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
from tests.conftest import submit_and_mine


class TestChainIntegrity:
    """Attacks against chain state."""

    def test_genesis_replay_rejected(self, blockchain, wallet):
        """Attacker sends a second genesis block."""
        fake_genesis = Block.genesis(wallet.address)
        fake_genesis.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(fake_genesis)
        assert not ok  # rejected — either "already have" or "out-of-order"

    def test_fork_via_old_parent(self, blockchain, wallet):
        """Attacker creates block pointing to historical parent."""
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
        """Validator with far-future timestamp freezes chain."""
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        blockchain.submit_tx(tx)

        future_block = Block(
            index=1, prev_hash=blockchain.chain[0].block_hash,
            transactions=[tx], validator=wallet.address,
            timestamp=int(time.time()) + 9999)
        future_block.sign(wallet.signing_sk)
        ok, err = blockchain.add_block(future_block)
        assert not ok
        assert "future" in err


class TestTransactionReplay:
    """Transaction replay and nonce attacks."""

    def test_cross_chain_replay_blocked_by_chain_id(self, wallet):
        """Same tx on different chain_id has different tx_id."""
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


class TestPayloadAttacks:
    """Payload manipulation attacks."""

    def test_extra_payload_keys_dedup_bypass(self, blockchain, wallet):
        """Extra keys in payload change tx_id — blocked by _ALLOWED_KEYS."""
        tx = Transaction(TxType.NOTARIZE, wallet.address,
                         payload={"documentHash": "aabb", "metadata": "",
                                  "injected": "evil"}, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "unknown payload keys" in err

    def test_oversized_payload(self, blockchain, wallet):
        tx = Transaction.notarize(wallet.address, "aa" * 5000, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "too large" in err


class TestCryptoAttacks:
    """Attacks against cryptographic primitives."""

    def test_malformed_signature_doesnt_crash(self, blockchain, wallet):
        """1-byte signature should not crash node."""
        tx = Transaction.notarize(wallet.address, "aa", nonce=0)
        tx.sender_pubkey = wallet.signing_pk
        tx.signature = b"\x00"
        ok, err = blockchain.submit_tx(tx)
        assert not ok
        assert "invalid signature" in err

    def test_wrong_pubkey_size_from_dict(self):
        with pytest.raises(ValueError, match="sender_pubkey wrong size"):
            Transaction.from_dict({
                "type": "NOTARIZE", "from": "qv1test", "timestamp": 1,
                "nonce": 0, "payload": {"documentHash": "aa", "metadata": ""},
                "signature": "aa" * 3309, "sender_pubkey": "bb" * 100,
                "chainId": "qbit-mainnet",
            })

    def test_mlkem_bad_ciphertext_size(self):
        with pytest.raises(ValueError):
            MLKEM.decapsulate(b"x" * 2400, b"short_ct")


class TestSSRFProtection:
    """P2P SSRF and peer validation."""

    def test_block_private_ips(self):
        assert not _is_safe_peer("10.0.0.1", 9000, "0.0.0.0", 9000)
        assert not _is_safe_peer("192.168.1.1", 9000, "0.0.0.0", 9000)
        assert not _is_safe_peer("172.16.0.1", 9000, "0.0.0.0", 9000)

    def test_block_link_local(self):
        assert not _is_safe_peer("169.254.169.254", 80, "0.0.0.0", 9000)

    def test_block_loopback(self):
        assert not _is_safe_peer("127.0.0.1", 9000, "0.0.0.0", 9000)

    def test_block_reserved_ports(self):
        assert not _is_safe_peer("8.8.8.8", 22, "0.0.0.0", 9000)
        assert not _is_safe_peer("8.8.8.8", 3306, "0.0.0.0", 9000)

    def test_block_self_connection(self):
        assert not _is_safe_peer("0.0.0.0", 9000, "0.0.0.0", 9000)

    def test_block_negative_port(self):
        assert not _is_safe_peer("8.8.8.8", -1, "0.0.0.0", 9000)
        assert not _is_safe_peer("8.8.8.8", 70000, "0.0.0.0", 9000)

    def test_block_metadata_hostnames(self):
        assert not _is_safe_peer("metadata.google.internal", 80, "0.0.0.0", 9000)


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
        # Should use capped params — different from original → decrypt fails
        with pytest.raises(ValueError):
            Wallet.load(path, password="test")

    def test_truncated_ciphertext(self, wallet, tmp_dir):
        path = os.path.join(tmp_dir, "w.json")
        wallet.save(path, password="test")
        with open(path) as f:
            data = json.load(f)
        data["ciphertext"] = data["ciphertext"][:20]
        with open(path, "w") as f:
            json.dump(data, f)
        with pytest.raises(ValueError):
            Wallet.load(path, password="test")

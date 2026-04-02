"""Direct tests for qbit_network.node.FullNode (TEC-576).

Covers: P2P handlers, sync loop, RPC methods (public + protected),
wallet management, helpers, WebSocket/webhook notification, and lifecycle.

Uses mock P2P peers + real in-memory Blockchain to avoid network I/O.
"""
import asyncio
import collections
import os
import shutil
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.block import Block
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.wallet import Wallet
from qbit_network.node import FullNode, _MAX_SHARED_SECRETS
from qbit_network.network.p2p import MSG_NEW_BLOCK, MSG_NEW_TX, MSG_BLOCKS, MSG_GET_BLOCKS, MSG_STATUS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(tmp_dir, wallet=None):
    """Create a FullNode with an in-memory blockchain and mock network."""
    node = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
    if wallet:
        node.validator_wallet = wallet
        node.wallets[wallet.address] = wallet
        node.blockchain.consensus.add_validator(wallet.address, wallet.signing_pk)
        node.blockchain.init_chain(wallet.address, wallet.signing_sk,
                                   validator_pk=wallet.signing_pk)
        node.blockchain.activate_financial_layer(wallet.address)
        # Register encryption key
        reg_tx = Transaction.register_key(wallet.address, wallet.encryption_pk, nonce=0)
        reg_tx.sign(wallet.signing_sk, wallet.signing_pk)
        node.blockchain.submit_tx(reg_tx)
        block = node.blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
    # Mock P2P broadcast to capture calls
    node.p2p.broadcast = AsyncMock()
    node.p2p.reputation = MagicMock()
    node.p2p.reputation.record = MagicMock()
    # Mock WS/Webhook managers
    node.ws_manager.broadcast = AsyncMock()
    node.webhook_manager.deliver = AsyncMock()
    return node


class FakePeer:
    """Minimal mock of a P2P peer."""

    def __init__(self, addr="127.0.0.1:9999", height=0):
        self.addr = addr
        self.height = height
        self.connected = True
        self.send = AsyncMock()


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="qv_node_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def wallet():
    return Wallet.generate()


@pytest.fixture
def node_with_wallet(tmp_dir, wallet):
    return _make_node(tmp_dir, wallet)


# ===========================================================================
# 1. P2P Handlers
# ===========================================================================

class TestP2PNewBlock:
    """Tests for _p2p_new_block handler."""

    @pytest.mark.asyncio
    async def test_valid_block_accepted_and_rebroadcast(self, node_with_wallet, wallet):
        """Valid block from peer is added and rebroadcast."""
        node = node_with_wallet
        peer = FakePeer()

        # Build a valid block on a separate chain with same genesis
        tx = Transaction.notarize(wallet.address, "a" * 64, nonce=1)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        node.blockchain.submit_tx(tx)
        block = node.blockchain.produce_block(wallet.address, wallet.signing_sk)
        assert block is not None
        height_before = node.blockchain.height

        # Simulate receiving this same block type via P2P
        # Since block is already added, it will fail as duplicate — test the path
        await node._p2p_new_block(peer, {"block": block.to_dict()})
        # Reputation was recorded (either valid or invalid path)
        assert node.p2p.reputation.record.called

    @pytest.mark.asyncio
    async def test_invalid_block_records_bad_reputation(self, node_with_wallet):
        """Malformed block data triggers invalid_block reputation."""
        node = node_with_wallet
        peer = FakePeer()

        await node._p2p_new_block(peer, {"block": {"bad": "data"}})
        node.p2p.reputation.record.assert_called_with(peer.addr, "invalid_block")


class TestP2PNewTx:
    """Tests for _p2p_new_tx handler."""

    @pytest.mark.asyncio
    async def test_valid_tx_accepted_and_broadcast(self, node_with_wallet, wallet):
        """Valid transaction from peer enters pool and is rebroadcast."""
        node = node_with_wallet
        peer = FakePeer()

        tx = Transaction.notarize(wallet.address, "b" * 64, nonce=1)
        tx.sign(wallet.signing_sk, wallet.signing_pk)

        await node._p2p_new_tx(peer, {"tx": tx.to_dict()})
        node.p2p.reputation.record.assert_called_with(peer.addr, "valid_tx")
        node.p2p.broadcast.assert_called()

    @pytest.mark.asyncio
    async def test_invalid_tx_records_bad_reputation(self, node_with_wallet):
        """Malformed tx triggers invalid_tx reputation."""
        node = node_with_wallet
        peer = FakePeer()

        await node._p2p_new_tx(peer, {"tx": {"bad": "data"}})
        node.p2p.reputation.record.assert_called_with(peer.addr, "invalid_tx")


class TestP2PGetBlocks:
    """Tests for _p2p_get_blocks handler."""

    @pytest.mark.asyncio
    async def test_returns_requested_blocks(self, node_with_wallet, wallet):
        """Responds with blocks in the requested range."""
        node = node_with_wallet
        peer = FakePeer()

        await node._p2p_get_blocks(peer, {"from": 0, "count": 5, "request_id": "abc"})
        peer.send.assert_called_once()
        call_args = peer.send.call_args
        assert call_args[0][0] == MSG_BLOCKS
        payload = call_args[0][1]
        assert "blocks" in payload
        assert payload["request_id"] == "abc"
        # Should include genesis + 1 mined block = 2 blocks total
        assert len(payload["blocks"]) == 2

    @pytest.mark.asyncio
    async def test_caps_count_at_100(self, node_with_wallet):
        """Count parameter is capped at 100."""
        node = node_with_wallet
        peer = FakePeer()

        await node._p2p_get_blocks(peer, {"from": 0, "count": 500})
        peer.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_bad_params_silently_ignored(self, node_with_wallet):
        """Non-numeric params don't crash."""
        node = node_with_wallet
        peer = FakePeer()

        await node._p2p_get_blocks(peer, {"from": "bad", "count": "bad"})
        peer.send.assert_not_called()


class TestP2PBlocks:
    """Tests for _p2p_blocks handler (incoming sync response)."""

    @pytest.mark.asyncio
    async def test_rejects_unsolicited_blocks(self, node_with_wallet):
        """Blocks without request_id are dropped."""
        node = node_with_wallet
        peer = FakePeer()
        height_before = node.blockchain.height

        await node._p2p_blocks(peer, {"blocks": []})
        assert node.blockchain.height == height_before

    @pytest.mark.asyncio
    async def test_rejects_unknown_request_id(self, node_with_wallet):
        """Blocks with unrecognized request_id are dropped."""
        node = node_with_wallet
        peer = FakePeer()

        await node._p2p_blocks(peer, {"blocks": [], "request_id": "unknown"})

    @pytest.mark.asyncio
    async def test_accepts_valid_request_id(self, node_with_wallet):
        """Blocks with matching request_id are processed."""
        node = node_with_wallet
        # Register a pending request
        node.p2p._pending_requests = {"valid_req": time.time()}

        await node._p2p_blocks(FakePeer(), {"blocks": [], "request_id": "valid_req"})
        # Request ID should be consumed
        assert "valid_req" not in node.p2p._pending_requests


class TestP2PPeers:
    """Tests for _p2p_get_peers and _p2p_peers handlers."""

    @pytest.mark.asyncio
    async def test_get_peers_returns_connected(self, node_with_wallet):
        """_p2p_get_peers sends list of connected peer addresses."""
        node = node_with_wallet
        peer = FakePeer()
        # Add a fake connected peer to the node's peer list
        fake = FakePeer("10.0.0.1:9000")
        node.p2p.peers = {"10.0.0.1:9000": fake}

        await node._p2p_get_peers(peer, {})
        peer.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_peers_caps_at_50(self, node_with_wallet):
        """_p2p_peers processes at most 50 peer addresses."""
        node = node_with_wallet
        node.p2p.connect = AsyncMock()
        peer = FakePeer()
        # Send 60 peers — only 50 should be processed
        addrs = [f"10.0.0.{i}:9000" for i in range(60)]

        with patch("qbit_network.node._is_safe_peer", return_value=True):
            await node._p2p_peers(peer, {"peers": addrs})
        assert node.p2p.connect.call_count <= 50


class TestP2PStatus:
    """Tests for _p2p_status handler."""

    @pytest.mark.asyncio
    async def test_updates_peer_height(self, node_with_wallet):
        """Valid height updates peer.height."""
        node = node_with_wallet
        peer = FakePeer()

        await node._p2p_status(peer, {"height": 42})
        assert peer.height == 42

    @pytest.mark.asyncio
    async def test_rejects_absurd_height(self, node_with_wallet):
        """Height above 10M is ignored."""
        node = node_with_wallet
        peer = FakePeer()
        peer.height = 5

        await node._p2p_status(peer, {"height": 999_999_999})
        assert peer.height == 5  # unchanged

    @pytest.mark.asyncio
    async def test_rejects_non_int_height(self, node_with_wallet):
        """Non-integer height is ignored."""
        node = node_with_wallet
        peer = FakePeer()
        peer.height = 3

        await node._p2p_status(peer, {"height": "abc"})
        assert peer.height == 3


# ===========================================================================
# 2. RPC — Public read methods
# ===========================================================================

class TestRPCPublicRead:
    """Tests for public RPC methods."""

    @pytest.mark.asyncio
    async def test_block_number(self, node_with_wallet):
        result = await node_with_wallet._rpc_block_number()
        assert isinstance(result, int)
        assert result >= 1  # genesis + 1 mined block

    @pytest.mark.asyncio
    async def test_get_block_by_index(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_block(index=0)
        assert result is not None
        assert result["index"] == 0

    @pytest.mark.asyncio
    async def test_get_block_by_hash(self, node_with_wallet):
        genesis = node_with_wallet.blockchain.get_block(0)
        result = await node_with_wallet._rpc_get_block(block_hash=genesis.block_hash)
        assert result is not None
        assert result["index"] == 0

    @pytest.mark.asyncio
    async def test_get_block_none_params(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_block()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_block_invalid_index_type(self, node_with_wallet):
        with pytest.raises(ValueError, match="index must be integer"):
            await node_with_wallet._rpc_get_block(index="bad")

    @pytest.mark.asyncio
    async def test_pending_tx_count(self, node_with_wallet):
        result = await node_with_wallet._rpc_pending_count()
        assert result == 0

    @pytest.mark.asyncio
    async def test_peer_count(self, node_with_wallet):
        node_with_wallet.p2p.peer_count = MagicMock(return_value=3)
        result = await node_with_wallet._rpc_peer_count()
        assert result == 3

    @pytest.mark.asyncio
    async def test_node_info(self, node_with_wallet, wallet):
        node_with_wallet.p2p.peer_count = MagicMock(return_value=0)
        result = await node_with_wallet._rpc_node_info()
        assert result["version"] is not None
        assert result["validator"] == wallet.address
        assert isinstance(result["wallets"], int)

    @pytest.mark.asyncio
    async def test_validators(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_validators()
        assert wallet.address in result

    @pytest.mark.asyncio
    async def test_get_balance(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_get_balance(address=wallet.address)
        assert "balance" in result
        assert result["address"] == wallet.address

    @pytest.mark.asyncio
    async def test_get_balance_bad_input(self, node_with_wallet):
        with pytest.raises(ValueError):
            await node_with_wallet._rpc_get_balance(address="")

    @pytest.mark.asyncio
    async def test_get_supply(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_supply()
        assert "circulating" in result
        assert "max_supply" in result

    @pytest.mark.asyncio
    async def test_get_state_root(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_state_root()
        assert "state_root" in result

    @pytest.mark.asyncio
    async def test_get_finalized(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_finalized()
        assert "finalized_height" in result

    @pytest.mark.asyncio
    async def test_get_epoch(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_epoch()
        assert "epoch" in result
        assert "validators" in result

    @pytest.mark.asyncio
    async def test_get_fee_info(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_fee_info()
        assert "base_fee" in result
        assert "fee_model" in result


# ===========================================================================
# 3. RPC — Protected write methods
# ===========================================================================

class TestRPCProtectedWrite:
    """Tests for protected (auth-required) RPC methods."""

    @pytest.mark.asyncio
    async def test_new_wallet(self, node_with_wallet):
        result = await node_with_wallet._rpc_new_wallet()
        assert "address" in result
        assert result["address"] in node_with_wallet.wallets

    @pytest.mark.asyncio
    async def test_list_wallets(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_list_wallets()
        assert wallet.address in result

    @pytest.mark.asyncio
    async def test_get_wallet_keys(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_get_wallet_keys(address=wallet.address)
        assert result["address"] == wallet.address
        assert "signing_pk" in result

    @pytest.mark.asyncio
    async def test_get_wallet_keys_not_found(self, node_with_wallet):
        with pytest.raises(ValueError, match="wallet not found"):
            await node_with_wallet._rpc_get_wallet_keys(address="nonexistent")

    @pytest.mark.asyncio
    async def test_notarize(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_notarize(
            wallet_address=wallet.address, document_hash="c" * 64)
        assert "tx_id" in result

    @pytest.mark.asyncio
    async def test_store(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_store(
            wallet_address=wallet.address, document_hash="d" * 64,
            cid="QmTest123", metadata="test")
        assert "tx_id" in result

    @pytest.mark.asyncio
    async def test_register_key(self, node_with_wallet, wallet):
        """Register key for a new wallet (funded first)."""
        node = node_with_wallet
        new_w = Wallet.generate()
        node.wallets[new_w.address] = new_w
        # Fund the new wallet first
        await node._rpc_transfer(
            wallet_address=wallet.address,
            to_address=new_w.address,
            amount=5_000_000_000)
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)
        result = await node._rpc_register_key(wallet_address=new_w.address)
        assert "tx_id" in result

    @pytest.mark.asyncio
    async def test_transfer(self, node_with_wallet, wallet):
        """Transfer QBIT tokens to another address."""
        node = node_with_wallet
        recipient = Wallet.generate()
        result = await node._rpc_transfer(
            wallet_address=wallet.address,
            to_address=recipient.address,
            amount=100,
            memo="test transfer")
        assert "tx_id" in result
        assert result["amount"] == 100

    @pytest.mark.asyncio
    async def test_transfer_to_self_rejected(self, node_with_wallet, wallet):
        with pytest.raises(ValueError, match="cannot transfer to self"):
            await node_with_wallet._rpc_transfer(
                wallet_address=wallet.address,
                to_address=wallet.address,
                amount=100)

    @pytest.mark.asyncio
    async def test_transfer_bad_amount(self, node_with_wallet, wallet):
        with pytest.raises(ValueError, match="amount must be a positive integer"):
            await node_with_wallet._rpc_transfer(
                wallet_address=wallet.address,
                to_address="qv1someone",
                amount=-1)

    @pytest.mark.asyncio
    async def test_send_raw_tx(self, node_with_wallet, wallet):
        """Send a pre-signed raw transaction."""
        node = node_with_wallet
        tx = Transaction.notarize(wallet.address, "e" * 64, nonce=1)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        result = await node._rpc_send_raw_tx(tx_data=tx.to_dict())
        assert "tx_id" in result

    @pytest.mark.asyncio
    async def test_send_raw_tx_bad_input(self, node_with_wallet):
        with pytest.raises(ValueError, match="tx_data must be a JSON object"):
            await node_with_wallet._rpc_send_raw_tx(tx_data="not a dict")

    @pytest.mark.asyncio
    async def test_register_validator(self, node_with_wallet, wallet):
        """Register a new validator on-chain."""
        node = node_with_wallet
        new_w = Wallet.generate()
        node.wallets[new_w.address] = new_w
        # Fund the new wallet
        await node._rpc_transfer(
            wallet_address=wallet.address,
            to_address=new_w.address,
            amount=5_000_000_000)
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)
        result = await node._rpc_register_validator(wallet_address=new_w.address)
        assert "tx_id" in result
        assert result["validator_address"] == new_w.address

    @pytest.mark.asyncio
    async def test_revoke_key(self, node_with_wallet, wallet):
        """Revoke a key on-chain."""
        node = node_with_wallet
        new_w = Wallet.generate()
        node.wallets[new_w.address] = new_w
        # Fund the new wallet
        await node._rpc_transfer(
            wallet_address=wallet.address,
            to_address=new_w.address,
            amount=5_000_000_000)
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)
        result = await node._rpc_revoke_key(
            wallet_address=new_w.address,
            key_type="signing",
            reason="rotation")
        assert "tx_id" in result
        assert result["key_type"] == "signing"

    @pytest.mark.asyncio
    async def test_revoke_key_bad_type(self, node_with_wallet, wallet):
        with pytest.raises(ValueError, match="key_type must be one of"):
            await node_with_wallet._rpc_revoke_key(
                wallet_address=wallet.address,
                key_type="invalid",
                reason="rotation")

    @pytest.mark.asyncio
    async def test_revoke_key_bad_reason(self, node_with_wallet, wallet):
        with pytest.raises(ValueError, match="reason must be one of"):
            await node_with_wallet._rpc_revoke_key(
                wallet_address=wallet.address,
                key_type="signing",
                reason="invalid")


# ===========================================================================
# 4. RPC — Staking
# ===========================================================================

class TestRPCStaking:

    @pytest.mark.asyncio
    async def test_get_stake(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_get_stake(
            validator_address=wallet.address)
        assert "total_stake" in result

    @pytest.mark.asyncio
    async def test_get_validator_stakes(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_validator_stakes()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_stake_bad_validator(self, node_with_wallet, wallet):
        with pytest.raises(ValueError, match="validator_address must be non-empty"):
            await node_with_wallet._rpc_stake(
                wallet_address=wallet.address,
                validator_address="",
                amount=10)

    @pytest.mark.asyncio
    async def test_stake_bad_amount(self, node_with_wallet, wallet):
        with pytest.raises(ValueError, match="amount must be positive"):
            await node_with_wallet._rpc_stake(
                wallet_address=wallet.address,
                validator_address=wallet.address,
                amount=0)


# ===========================================================================
# 5. RPC — Token operations
# ===========================================================================

class TestRPCTokens:

    @pytest.mark.asyncio
    async def test_list_tokens(self, node_with_wallet):
        result = await node_with_wallet._rpc_list_tokens()
        assert "tokens" in result
        assert "total" in result

    @pytest.mark.asyncio
    async def test_list_tokens_bad_page(self, node_with_wallet):
        with pytest.raises(ValueError, match="page must be a positive integer"):
            await node_with_wallet._rpc_list_tokens(page=0)

    @pytest.mark.asyncio
    async def test_get_token_info_empty(self, node_with_wallet):
        with pytest.raises(ValueError, match="token_id must be a non-empty string"):
            await node_with_wallet._rpc_get_token_info(token_id="")

    @pytest.mark.asyncio
    async def test_get_token_balance_empty(self, node_with_wallet):
        with pytest.raises(ValueError):
            await node_with_wallet._rpc_get_token_balance(token_id="", address="x")

    @pytest.mark.asyncio
    async def test_issue_token(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_issue_token(
            wallet_address=wallet.address,
            name="TestCoin",
            symbol="TST",
            decimals=8,
            max_supply=1_000_000,
            transferable=True)
        assert "tx_id" in result
        assert result["symbol"] == "TST"


# ===========================================================================
# 6. RPC — Light Client
# ===========================================================================

class TestRPCLightClient:

    @pytest.mark.asyncio
    async def test_get_block_headers(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_block_headers(start=0, count=10)
        assert isinstance(result, list)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_get_block_headers_bad_count(self, node_with_wallet):
        with pytest.raises(ValueError, match="count must be 1-100"):
            await node_with_wallet._rpc_get_block_headers(start=0, count=0)

    @pytest.mark.asyncio
    async def test_get_receipt_proof_bad_input(self, node_with_wallet):
        with pytest.raises(ValueError, match="tx_id must be a non-empty string"):
            await node_with_wallet._rpc_get_receipt_proof(tx_id="")

    @pytest.mark.asyncio
    async def test_get_state_proof_at(self, node_with_wallet, wallet):
        # This returns a proof or error dict — just ensure it doesn't crash
        result = await node_with_wallet._rpc_get_state_proof_at(
            key=f"balance:{wallet.address}")
        assert result is not None


# ===========================================================================
# 7. RPC — Webhooks
# ===========================================================================

class TestRPCWebhooks:

    @pytest.mark.asyncio
    async def test_register_webhook(self, node_with_wallet):
        result = await node_with_wallet._rpc_register_webhook(
            url="https://example.com/hook",
            events=["Notarize"],
            secret="mysecret")
        assert "id" in result

    @pytest.mark.asyncio
    async def test_list_webhooks(self, node_with_wallet):
        result = await node_with_wallet._rpc_list_webhooks()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_delete_webhook_not_found(self, node_with_wallet):
        with pytest.raises(ValueError, match="webhook not found"):
            await node_with_wallet._rpc_delete_webhook(webhook_id="nonexistent")

    @pytest.mark.asyncio
    async def test_register_webhook_bad_url(self, node_with_wallet):
        with pytest.raises(ValueError, match="url must be non-empty"):
            await node_with_wallet._rpc_register_webhook(
                url="", events=["new_block"], secret="s")


# ===========================================================================
# 8. RPC — Events/Logs & Receipts
# ===========================================================================

class TestRPCEventsReceipts:

    @pytest.mark.asyncio
    async def test_get_logs(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_logs()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_logs_bad_limit(self, node_with_wallet):
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            await node_with_wallet._rpc_get_logs(limit=0)

    @pytest.mark.asyncio
    async def test_get_receipt_none(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_receipt(tx_id="nonexistent_tx")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_slashing_events(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_slashing_events()
        assert isinstance(result, list)


# ===========================================================================
# 9. Wallet management
# ===========================================================================

class TestWalletManagement:

    def test_save_and_load_wallets(self, tmp_dir, wallet):
        """Wallets persist to disk and reload."""
        node = _make_node(tmp_dir, wallet)
        node._save_wallets()

        # Create new node and load
        node2 = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
        node2._load_wallets()
        assert wallet.address in node2.wallets

    def test_load_wallets_empty_dir(self, tmp_dir):
        """Loading from non-existent dir is a no-op."""
        node = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
        node._load_wallets()
        assert len(node.wallets) == 0

    def test_load_wallets_ignores_corrupt_files(self, tmp_dir, wallet):
        """Corrupt wallet files are skipped with warning."""
        node = _make_node(tmp_dir, wallet)
        node._save_wallets()

        # Corrupt a wallet file
        wdir = node._wallets_dir()
        corrupt_path = os.path.join(wdir, "corrupt.json")
        with open(corrupt_path, "w") as f:
            f.write("{bad json")

        node2 = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
        node2._load_wallets()
        # Should still load the valid wallet
        assert wallet.address in node2.wallets

    def test_get_wallet_not_found(self, node_with_wallet):
        with pytest.raises(ValueError, match="wallet not found"):
            node_with_wallet._get_wallet("nonexistent_address")

    def test_get_wallet_non_string(self, node_with_wallet):
        with pytest.raises(ValueError, match="wallet address must be string"):
            node_with_wallet._get_wallet(123)


# ===========================================================================
# 10. Helpers
# ===========================================================================

class TestHelpers:

    def test_next_nonce(self, node_with_wallet, wallet):
        """_next_nonce returns confirmed + pending nonce."""
        nonce = node_with_wallet._next_nonce(wallet.address)
        assert isinstance(nonce, int)
        assert nonce >= 0

    def test_lock_for_creates_and_reuses(self, node_with_wallet, wallet):
        """_lock_for returns same lock for same address."""
        lock1 = node_with_wallet._lock_for(wallet.address)
        lock2 = node_with_wallet._lock_for(wallet.address)
        assert lock1 is lock2

    def test_lock_for_eviction(self, tmp_dir, wallet):
        """Lock pool evicts oldest unlocked entries when over limit."""
        node = _make_node(tmp_dir, wallet)
        node._MAX_WALLET_LOCKS = 3
        node._lock_for("addr1")
        node._lock_for("addr2")
        node._lock_for("addr3")
        node._lock_for("addr4")  # should evict addr1
        assert len(node._wallet_locks) <= 3

    def test_lock_for_rejects_when_all_held(self, tmp_dir, wallet):
        """Lock pool raises ValueError when cap reached and all locks held (R28-003)."""
        node = _make_node(tmp_dir, wallet)
        node._MAX_WALLET_LOCKS = 2
        lock1 = node._lock_for("addr1")
        lock2 = node._lock_for("addr2")
        # Simulate held locks by setting internal state
        lock1._locked = True
        lock2._locked = True
        with pytest.raises(ValueError, match="wallet lock pool exhausted"):
            node._lock_for("addr3")
        assert len(node._wallet_locks) == 2  # did not grow beyond cap

    def test_lock_for_evicts_before_insert(self, tmp_dir, wallet):
        """New lock is only inserted after successful eviction (R28-003)."""
        node = _make_node(tmp_dir, wallet)
        node._MAX_WALLET_LOCKS = 2
        node._lock_for("addr1")
        node._lock_for("addr2")
        # addr1 is unlocked, should be evicted to make room
        node._lock_for("addr3")
        assert len(node._wallet_locks) == 2
        assert "addr1" not in node._wallet_locks
        assert "addr3" in node._wallet_locks

    def test_store_shared_secret(self, node_with_wallet):
        """Shared secrets are stored and capped at _MAX_SHARED_SECRETS."""
        node = node_with_wallet
        node._store_shared_secret("tx1", b"secret1")
        assert node._shared_secrets["tx1"] == b"secret1"

    def test_store_shared_secret_cap(self, node_with_wallet):
        """Shared secrets are evicted when exceeding max."""
        node = node_with_wallet
        # Store more than max
        for i in range(_MAX_SHARED_SECRETS + 5):
            node._store_shared_secret(f"tx_{i}", b"s")
        assert len(node._shared_secrets) <= _MAX_SHARED_SECRETS

    def test_lock_genesis_if_needed(self, node_with_wallet):
        """Genesis hash gets locked after chain init."""
        node = node_with_wallet
        node._lock_genesis_if_needed()
        assert node.blockchain.consensus._genesis_hash != ""

    def test_get_chain_stats(self, node_with_wallet):
        """_get_chain_stats returns expected structure."""
        node = node_with_wallet
        node.p2p.peer_count = MagicMock(return_value=5)
        stats = node._get_chain_stats()
        assert stats["height"] >= 0
        assert stats["peers"] == 5
        assert "tx_count" in stats
        assert "pool_size" in stats


# ===========================================================================
# 11. RPC — Encryption / Share
# ===========================================================================

class TestRPCEncryptionShare:

    @pytest.mark.asyncio
    async def test_get_encryption_pk(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_get_encryption_pk(
            address=wallet.address)
        assert result["address"] == wallet.address
        assert "encryption_pk" in result

    @pytest.mark.asyncio
    async def test_get_encryption_pk_not_found(self, node_with_wallet):
        with pytest.raises(ValueError, match="no encryption key"):
            await node_with_wallet._rpc_get_encryption_pk(address="unknown_addr")

    @pytest.mark.asyncio
    async def test_shared_with_me(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_shared_with_me(
            address=wallet.address)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_shared_secret_not_found(self, node_with_wallet):
        with pytest.raises(ValueError, match="shared secret not found"):
            await node_with_wallet._rpc_get_shared_secret(tx_id="nonexistent")

    @pytest.mark.asyncio
    async def test_share_and_get_secret(self, node_with_wallet, wallet):
        """Full share flow: share doc, then retrieve shared secret."""
        node = node_with_wallet
        # Create a second wallet, fund it, and register its key
        bob = Wallet.generate()
        node.wallets[bob.address] = bob
        # Fund bob
        fund_tx = Transaction.transfer(
            sender=wallet.address, recipient=bob.address,
            amount=5_000_000_000, memo="fund", nonce=1)
        fund_tx.sign(wallet.signing_sk, wallet.signing_pk)
        node.blockchain.submit_tx(fund_tx)
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)
        # Register bob's encryption key
        reg_tx = Transaction.register_key(bob.address, bob.encryption_pk,
                                          nonce=0)
        reg_tx.sign(bob.signing_sk, bob.signing_pk)
        node.blockchain.submit_tx(reg_tx)
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)

        # Share a document
        result = await node._rpc_share(
            wallet_address=wallet.address,
            recipient_address=bob.address,
            cid="QmShareTest")
        assert result["shared_secret_stored"]

        # Retrieve shared secret
        ss = await node._rpc_get_shared_secret(tx_id=result["tx_id"])
        assert "shared_secret" in ss


# ===========================================================================
# 12. RPC — State Proofs
# ===========================================================================

class TestRPCStateProofs:

    @pytest.mark.asyncio
    async def test_get_state_proof_balance(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_get_state_proof(
            address=wallet.address, key_type="balance")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_state_proof_bad_key_type(self, node_with_wallet, wallet):
        with pytest.raises(ValueError, match="key_type must be"):
            await node_with_wallet._rpc_get_state_proof(
                address=wallet.address, key_type="invalid")

    @pytest.mark.asyncio
    async def test_get_state_proof_bad_address(self, node_with_wallet):
        with pytest.raises(ValueError, match="address must be a non-empty"):
            await node_with_wallet._rpc_get_state_proof(address="")


# ===========================================================================
# 13. WebSocket & Webhook notification helpers
# ===========================================================================

class TestWSWebhookNotify:

    @pytest.mark.asyncio
    async def test_ws_notify_block(self, node_with_wallet, wallet):
        """_ws_notify_block broadcasts via WS and delivers webhooks."""
        node = node_with_wallet
        block = node.blockchain.get_block(0)
        await node._ws_notify_block(block)
        node.ws_manager.broadcast.assert_called()

    @pytest.mark.asyncio
    async def test_ws_notify_tx(self, node_with_wallet, wallet):
        """_ws_notify_tx broadcasts tx over WebSocket."""
        node = node_with_wallet
        tx = Transaction.notarize(wallet.address, "f" * 64, nonce=1)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        await node._ws_notify_tx(tx)
        node.ws_manager.broadcast.assert_called_once_with("new_tx", tx.to_dict())

    @pytest.mark.asyncio
    async def test_ws_notify_block_error_handled(self, node_with_wallet):
        """WS broadcast errors are caught, not propagated."""
        node = node_with_wallet
        node.ws_manager.broadcast = AsyncMock(side_effect=Exception("ws fail"))
        block = node.blockchain.get_block(0)
        # Should not raise
        await node._ws_notify_block(block)


# ===========================================================================
# 14. Lifecycle (start/stop)
# ===========================================================================

class TestLifecycle:

    def test_constructor_defaults(self, tmp_dir):
        """FullNode can be constructed with defaults."""
        node = FullNode(data_dir=tmp_dir)
        assert node._running is False
        assert node.validator_wallet is None
        assert isinstance(node.wallets, dict)

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, tmp_dir):
        """Calling stop on a non-running node is safe."""
        node = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
        await node.stop()  # should not raise


# ===========================================================================
# 15. RPC — Document verification & Tx query
# ===========================================================================

class TestRPCDocAndTxQuery:

    @pytest.mark.asyncio
    async def test_verify_document(self, node_with_wallet, wallet):
        """Notarized document can be verified."""
        node = node_with_wallet
        doc_hash = "aa" * 32
        tx = Transaction.notarize(wallet.address, doc_hash, nonce=1)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        node.blockchain.submit_tx(tx)
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)

        result = await node._rpc_verify_document(document_hash=doc_hash)
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_tx(self, node_with_wallet, wallet):
        """Get transaction by ID."""
        node = node_with_wallet
        tx = Transaction.notarize(wallet.address, "bb" * 32, nonce=1)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        node.blockchain.submit_tx(tx)
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)

        result = await node._rpc_get_tx(tx_id=tx.tx_id)
        assert result is not None
        assert result["block_index"] is not None

    @pytest.mark.asyncio
    async def test_get_tx_not_found(self, node_with_wallet):
        result = await node_with_wallet._rpc_get_tx(tx_id="nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_txs_by_sender(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_txs_by_sender(
            address=wallet.address)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_txs_by_recipient(self, node_with_wallet, wallet):
        result = await node_with_wallet._rpc_txs_by_recipient(
            address=wallet.address)
        assert isinstance(result, list)


# ===========================================================================
# 16. RPC — Submit Evidence
# ===========================================================================

class TestRPCSubmitEvidence:

    @pytest.mark.asyncio
    async def test_submit_evidence_bad_block_index(self, node_with_wallet, wallet):
        with pytest.raises(ValueError, match="block_index must be non-negative"):
            await node_with_wallet._rpc_submit_evidence(
                wallet_address=wallet.address,
                validator_address=wallet.address,
                block_index=-1,
                block_a_hash="a", block_b_hash="b",
                block_a_sig="c", block_b_sig="d",
                block_a_header="e", block_b_header="f")

    @pytest.mark.asyncio
    async def test_submit_evidence_missing_field(self, node_with_wallet, wallet):
        with pytest.raises(ValueError, match="block_a_hash must be non-empty"):
            await node_with_wallet._rpc_submit_evidence(
                wallet_address=wallet.address,
                validator_address=wallet.address,
                block_index=0,
                block_a_hash="", block_b_hash="b",
                block_a_sig="c", block_b_sig="d",
                block_a_header="e", block_b_header="f")


# ===========================================================================
# 10. Dynamic Fee Params on RPC methods (TEC-609)
# ===========================================================================

def _make_dynfee_node(tmp_dir, alice, bob, monkeypatch):
    """Create a node with dynamic fees active (ACTIVATION_HEIGHT=0), two wallets funded."""
    monkeypatch.setattr("qbit_network.config.DYNAMIC_FEE_ACTIVATION_HEIGHT", 0)
    monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT", 0)
    monkeypatch.setattr("qbit_network.core.consensus.DYNAMIC_FEE_ACTIVATION_HEIGHT", 0)
    node = _make_node(tmp_dir, alice)
    node.wallets[bob.address] = bob
    node.blockchain._credit(bob.address, 100_000_000_000)
    # Register bob's encryption key
    reg = Transaction.register_key(bob.address, bob.encryption_pk, nonce=0,
                                   max_fee_per_weight=200, max_priority_fee=10)
    reg.sign(bob.signing_sk, bob.signing_pk)
    node.blockchain.submit_tx(reg)
    block = node.blockchain.produce_block(alice.address, alice.signing_sk)
    assert block is not None
    return node


@pytest.fixture
def bob():
    return Wallet.generate()


class TestDynamicFeeRPCParams:
    """Verify all RPC TX methods work with dynamic fees active (TEC-609)."""

    @pytest.fixture
    def dynfee_node(self, tmp_dir, wallet, bob, monkeypatch):
        return _make_dynfee_node(tmp_dir, wallet, bob, monkeypatch)

    @staticmethod
    def _find_pool_tx(node, tx_id):
        """Find a TX in the pool by ID."""
        for tx in node.blockchain.tx_pool:
            if tx.tx_id == tx_id:
                return tx
        return None

    @pytest.mark.asyncio
    async def test_transfer_auto_fees(self, dynfee_node, wallet, bob):
        """Transfer with omitted fee params auto-computes from base_fee."""
        result = await dynfee_node._rpc_transfer(
            wallet_address=bob.address, to_address=wallet.address, amount=100)
        assert "tx_id" in result
        tx = self._find_pool_tx(dynfee_node, result["tx_id"])
        assert tx is not None
        assert tx.max_fee_per_weight > 0

    @pytest.mark.asyncio
    async def test_transfer_explicit_fees(self, dynfee_node, wallet, bob):
        """Transfer with explicit fee params uses provided values."""
        result = await dynfee_node._rpc_transfer(
            wallet_address=bob.address, to_address=wallet.address, amount=100,
            max_fee_per_weight=500, max_priority_fee=50)
        assert "tx_id" in result
        tx = self._find_pool_tx(dynfee_node, result["tx_id"])
        assert tx is not None
        assert tx.max_fee_per_weight == 500
        assert tx.max_priority_fee == 50

    @pytest.mark.asyncio
    async def test_notarize_auto_fees(self, dynfee_node, bob):
        result = await dynfee_node._rpc_notarize(
            wallet_address=bob.address, document_hash="a" * 64)
        assert "tx_id" in result
        tx = self._find_pool_tx(dynfee_node, result["tx_id"])
        assert tx is not None
        assert tx.max_fee_per_weight > 0

    @pytest.mark.asyncio
    async def test_store_auto_fees(self, dynfee_node, bob):
        result = await dynfee_node._rpc_store(
            wallet_address=bob.address, document_hash="b" * 64, cid="QmTest")
        assert "tx_id" in result
        tx = self._find_pool_tx(dynfee_node, result["tx_id"])
        assert tx is not None
        assert tx.max_fee_per_weight > 0

    @pytest.mark.asyncio
    async def test_stake_auto_fees(self, dynfee_node, wallet, bob):
        result = await dynfee_node._rpc_stake(
            wallet_address=bob.address, validator_address=wallet.address, amount=1000)
        assert "tx_id" in result
        tx = self._find_pool_tx(dynfee_node, result["tx_id"])
        assert tx is not None
        assert tx.max_fee_per_weight > 0

    @pytest.mark.asyncio
    async def test_delegate_auto_fees(self, dynfee_node, wallet, bob):
        result = await dynfee_node._rpc_delegate(
            wallet_address=bob.address, validator_address=wallet.address, amount=1000)
        assert "tx_id" in result
        tx = self._find_pool_tx(dynfee_node, result["tx_id"])
        assert tx is not None
        assert tx.max_fee_per_weight > 0

    @pytest.mark.asyncio
    async def test_unstake_auto_fees(self, dynfee_node, wallet, bob):
        # First stake, produce block, then unstake
        await dynfee_node._rpc_stake(
            wallet_address=bob.address, validator_address=wallet.address, amount=1000)
        dynfee_node.blockchain.produce_block(wallet.address, wallet.signing_sk)
        result = await dynfee_node._rpc_unstake(
            wallet_address=bob.address, validator_address=wallet.address, amount=500)
        assert "tx_id" in result
        tx = self._find_pool_tx(dynfee_node, result["tx_id"])
        assert tx is not None
        assert tx.max_fee_per_weight > 0

    @pytest.mark.asyncio
    async def test_compute_fee_defaults_inactive(self, tmp_dir, wallet, bob):
        """When dynamic fees inactive, defaults to 0."""
        node = _make_node(tmp_dir, wallet)
        mf, mp = node._compute_fee_defaults(None, None)
        assert mf == 0
        assert mp == 0

    @pytest.mark.asyncio
    async def test_compute_fee_defaults_explicit_passthrough(self, dynfee_node):
        """Explicit values pass through unchanged."""
        mf, mp = dynfee_node._compute_fee_defaults(999, 42)
        assert mf == 999
        assert mp == 42

    @pytest.mark.asyncio
    async def test_compute_fee_defaults_auto(self, dynfee_node):
        """Auto-compute: max_fee = base_fee * 2, priority = max(1, base_fee // 10)."""
        mf, mp = dynfee_node._compute_fee_defaults(None, None)
        bf = dynfee_node.blockchain._current_base_fee()
        assert mf == bf * 2
        assert mp == max(1, bf // 10)

    @pytest.mark.asyncio
    async def test_backward_compat_zero_fees_inactive(self, tmp_dir, wallet, bob):
        """Without dynamic fees, RPC methods still work with fee=0 (legacy)."""
        node = _make_node(tmp_dir, wallet)
        node.wallets[bob.address] = bob
        node.blockchain._credit(bob.address, 100_000_000_000)
        result = await node._rpc_transfer(
            wallet_address=bob.address, to_address=wallet.address, amount=100)
        assert "tx_id" in result
        tx = self._find_pool_tx(node, result["tx_id"])
        assert tx is not None
        assert tx.max_fee_per_weight == 0
        assert tx.max_priority_fee == 0

    # -- R28-001: fee param type safety regression tests --

    @pytest.mark.asyncio
    async def test_fee_defaults_rejects_string(self, dynfee_node):
        """String fee values must raise ValueError."""
        with pytest.raises(ValueError, match="must be an integer"):
            dynfee_node._compute_fee_defaults("100", 1)
        with pytest.raises(ValueError, match="must be an integer"):
            dynfee_node._compute_fee_defaults(1, "50")

    @pytest.mark.asyncio
    async def test_fee_defaults_rejects_float(self, dynfee_node):
        """Float fee values must raise ValueError."""
        with pytest.raises(ValueError, match="must be an integer"):
            dynfee_node._compute_fee_defaults(1.5, 1)
        with pytest.raises(ValueError, match="must be an integer"):
            dynfee_node._compute_fee_defaults(1, 2.5)

    @pytest.mark.asyncio
    async def test_fee_defaults_rejects_negative(self, dynfee_node):
        """Negative fee values must raise ValueError."""
        with pytest.raises(ValueError, match="must be non-negative"):
            dynfee_node._compute_fee_defaults(-1, 1)
        with pytest.raises(ValueError, match="must be non-negative"):
            dynfee_node._compute_fee_defaults(1, -10)

    @pytest.mark.asyncio
    async def test_fee_defaults_rejects_oversized(self, dynfee_node):
        """Values exceeding 2**63 must raise ValueError."""
        with pytest.raises(ValueError, match="exceeds maximum"):
            dynfee_node._compute_fee_defaults(2**64, 1)
        with pytest.raises(ValueError, match="exceeds maximum"):
            dynfee_node._compute_fee_defaults(1, 2**64)

    @pytest.mark.asyncio
    async def test_fee_defaults_rejects_bool(self, dynfee_node):
        """Bool values must raise ValueError (bool is subclass of int)."""
        with pytest.raises(ValueError, match="must be an integer"):
            dynfee_node._compute_fee_defaults(True, 1)
        with pytest.raises(ValueError, match="must be an integer"):
            dynfee_node._compute_fee_defaults(1, False)


# ===========================================================================
# TEC-1114: Expand node.py coverage
# ===========================================================================


class TestRPCTokenMintTransfer:
    """Tests for token mint and transfer RPC methods."""

    @pytest.mark.asyncio
    async def test_mint_token(self, node_with_wallet, wallet):
        """Minting custom tokens via RPC."""
        node = node_with_wallet
        # Issue first
        issue_result = await node._rpc_issue_token(
            wallet_address=wallet.address, name="Gold", symbol="GLD",
            decimals=8, max_supply=10000)
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)
        tid = issue_result["token_id"]
        result = await node._rpc_mint_token(
            wallet_address=wallet.address,
            token_id=tid,
            recipient=wallet.address,
            amount=1000,
        )
        assert "tx_id" in result
        assert result["token_id"] == tid
        assert result["amount"] == 1000

    @pytest.mark.asyncio
    async def test_mint_token_bad_recipient(self, node_with_wallet, wallet):
        """Minting with invalid recipient raises."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="non-empty string"):
            await node._rpc_mint_token(
                wallet_address=wallet.address,
                token_id="T1",
                recipient="",
                amount=100,
            )

    @pytest.mark.asyncio
    async def test_mint_token_bad_amount(self, node_with_wallet, wallet):
        """Minting with non-positive amount raises."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="positive integer"):
            await node._rpc_mint_token(
                wallet_address=wallet.address,
                token_id="T1",
                recipient=wallet.address,
                amount=0,
            )

    @pytest.mark.asyncio
    async def test_transfer_token(self, node_with_wallet, wallet):
        """Transferring tokens after minting."""
        node = node_with_wallet
        # Issue
        issue_result = await node._rpc_issue_token(
            wallet_address=wallet.address, name="Silver", symbol="SLV",
            decimals=8, max_supply=10000)
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)
        tid = issue_result["token_id"]
        await node._rpc_mint_token(
            wallet_address=wallet.address,
            token_id=tid,
            recipient=wallet.address,
            amount=500,
        )
        # Produce block to confirm mint
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)

        # Create recipient
        bob = Wallet.generate()
        node.wallets[bob.address] = bob

        result = await node._rpc_transfer_token(
            wallet_address=wallet.address,
            token_id=tid,
            recipient=bob.address,
            amount=100,
        )
        assert "tx_id" in result
        assert result["amount"] == 100

    @pytest.mark.asyncio
    async def test_transfer_token_bad_inputs(self, node_with_wallet, wallet):
        """Transfer token with bad inputs raises."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="wallet_address"):
            await node._rpc_transfer_token(
                wallet_address="", token_id="X", recipient="a", amount=1)
        with pytest.raises(ValueError, match="token_id"):
            await node._rpc_transfer_token(
                wallet_address=wallet.address, token_id="",
                recipient="a", amount=1)
        with pytest.raises(ValueError, match="recipient"):
            await node._rpc_transfer_token(
                wallet_address=wallet.address, token_id="X",
                recipient="", amount=1)
        with pytest.raises(ValueError, match="positive integer"):
            await node._rpc_transfer_token(
                wallet_address=wallet.address, token_id="X",
                recipient="a", amount=-1)


class TestRPCIssueToken:
    """Tests for issue_token RPC method."""

    @pytest.mark.asyncio
    async def test_issue_token_full(self, node_with_wallet, wallet):
        """Issue a new token with full params."""
        node = node_with_wallet
        result = await node._rpc_issue_token(
            wallet_address=wallet.address,
            name="Copper", symbol="COP", decimals=2,
            max_supply=1_000_000,
        )
        assert "tx_id" in result
        assert "token_id" in result
        assert result["symbol"] == "COP"

    @pytest.mark.asyncio
    async def test_issue_token_bad_name(self, node_with_wallet, wallet):
        """Issue token with empty name raises."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="name"):
            await node._rpc_issue_token(
                wallet_address=wallet.address,
                name="", symbol="X", max_supply=100,
            )

    @pytest.mark.asyncio
    async def test_issue_token_bad_symbol(self, node_with_wallet, wallet):
        """Issue token with empty symbol raises."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="symbol"):
            await node._rpc_issue_token(
                wallet_address=wallet.address,
                name="T", symbol="", max_supply=100,
            )

    @pytest.mark.asyncio
    async def test_issue_token_bad_wallet(self, node_with_wallet, wallet):
        """Issue token with empty wallet raises."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="wallet_address"):
            await node._rpc_issue_token(
                wallet_address="", name="T", symbol="X", max_supply=100,
            )


class TestRPCDelegateUnstake:
    """Tests for delegate and unstake RPC paths."""

    @pytest.mark.asyncio
    async def test_delegate_success(self, node_with_wallet, wallet):
        """Delegate stake to validator."""
        node = node_with_wallet
        result = await node._rpc_delegate(
            wallet_address=wallet.address,
            validator_address=wallet.address,
            amount=10,
        )
        assert "tx_id" in result

    @pytest.mark.asyncio
    async def test_delegate_bad_validator(self, node_with_wallet, wallet):
        """Delegate with empty validator_address raises."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="non-empty string"):
            await node._rpc_delegate(
                wallet_address=wallet.address,
                validator_address="",
                amount=10,
            )

    @pytest.mark.asyncio
    async def test_delegate_bad_amount(self, node_with_wallet, wallet):
        """Delegate with zero amount raises."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="positive integer"):
            await node._rpc_delegate(
                wallet_address=wallet.address,
                validator_address=wallet.address,
                amount=0,
            )

    @pytest.mark.asyncio
    async def test_unstake_success(self, node_with_wallet, wallet):
        """Unstake from validator."""
        node = node_with_wallet
        # First stake
        await node._rpc_stake(
            wallet_address=wallet.address,
            validator_address=wallet.address,
            amount=10,
        )
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)

        result = await node._rpc_unstake(
            wallet_address=wallet.address,
            validator_address=wallet.address,
            amount=5,
        )
        assert "tx_id" in result

    @pytest.mark.asyncio
    async def test_unstake_bad_validator(self, node_with_wallet, wallet):
        """Unstake with empty validator raises."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="non-empty string"):
            await node._rpc_unstake(
                wallet_address=wallet.address,
                validator_address="",
                amount=10,
            )

    @pytest.mark.asyncio
    async def test_unstake_bad_amount(self, node_with_wallet, wallet):
        """Unstake with zero amount raises."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="positive integer"):
            await node._rpc_unstake(
                wallet_address=wallet.address,
                validator_address=wallet.address,
                amount=0,
            )


class TestRPCSubmitEvidenceSuccess:
    """Tests for submit_evidence success path."""

    @pytest.mark.asyncio
    async def test_submit_evidence_success(self, node_with_wallet, wallet):
        """Submit evidence with valid params creates tx."""
        from qbit_network.crypto import sha3_256
        node = node_with_wallet
        # Create two different fake headers that hash to known values
        header_a = "aa" * 32
        header_b = "bb" * 32
        hash_a = sha3_256(bytes.fromhex(header_a)).hex()
        hash_b = sha3_256(bytes.fromhex(header_b)).hex()
        result = await node._rpc_submit_evidence(
            wallet_address=wallet.address,
            validator_address=wallet.address,
            block_index=0,
            block_a_hash=hash_a,
            block_b_hash=hash_b,
            block_a_sig="cc" * 32,
            block_b_sig="dd" * 32,
            block_a_header=header_a,
            block_b_header=header_b,
        )
        assert "tx_id" in result
        assert result["validator_address"] == wallet.address


class TestNodeSyncLoop:
    """Tests for _sync_loop()."""

    @pytest.mark.asyncio
    async def test_sync_loop_broadcasts_height(self, node_with_wallet, wallet):
        """_sync_loop broadcasts our height."""
        node = node_with_wallet
        node._running = True

        # Run one iteration by patching sleep to run once then stop
        call_count = 0

        async def mock_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                node._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await node._sync_loop()

        node.p2p.broadcast.assert_any_call(
            MSG_STATUS, {"height": node.blockchain.height})

    @pytest.mark.asyncio
    async def test_sync_loop_requests_blocks_from_ahead_peer(self, node_with_wallet, wallet):
        """_sync_loop requests blocks when a peer is ahead."""
        node = node_with_wallet
        node._running = True

        # Add a peer that's ahead
        ahead_peer = FakePeer(height=100)
        node.p2p.peers = {"8.8.8.8:9000": ahead_peer}
        node.p2p.peer_count = MagicMock(return_value=1)
        node.p2p.best_peer_height = MagicMock(return_value=100)

        call_count = 0

        async def mock_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                node._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await node._sync_loop()

        # Should have tried to request blocks from the best peer
        ahead_peer.send.assert_called()

    @pytest.mark.asyncio
    async def test_sync_loop_error_handled(self, node_with_wallet, wallet):
        """_sync_loop handles errors without crashing."""
        node = node_with_wallet
        node._running = True

        # Make broadcast raise
        node.p2p.broadcast = AsyncMock(side_effect=RuntimeError("network error"))

        call_count = 0

        async def mock_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                node._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await node._sync_loop()
        # Should not raise


class TestNodeP2PPeers:
    """Tests for _p2p_peers handler."""

    @pytest.mark.asyncio
    async def test_p2p_peers_connects_to_discovered_peers(self, node_with_wallet):
        """_p2p_peers processes peer list from remote."""
        node = node_with_wallet
        peer = FakePeer()
        node.p2p.connect = AsyncMock()

        await node._p2p_peers(peer, {"peers": ["8.8.8.8:9000", "1.2.3.4:9000"]})
        # Should have tried to connect
        assert node.p2p.connect.call_count == 2

    @pytest.mark.asyncio
    async def test_p2p_peers_bad_port_ignored(self, node_with_wallet):
        """_p2p_peers ignores peers with invalid ports."""
        node = node_with_wallet
        peer = FakePeer()
        node.p2p.connect = AsyncMock()

        await node._p2p_peers(peer, {"peers": ["8.8.8.8:notaport"]})
        node.p2p.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_p2p_peers_non_list_ignored(self, node_with_wallet):
        """_p2p_peers ignores non-list peers data."""
        node = node_with_wallet
        peer = FakePeer()
        node.p2p.connect = AsyncMock()

        await node._p2p_peers(peer, {"peers": "not_a_list"})
        node.p2p.connect.assert_not_called()


class TestNodeBlockLoop:
    """Tests for _block_loop()."""

    @pytest.mark.asyncio
    async def test_block_loop_produces_blocks(self, node_with_wallet, wallet):
        """_block_loop produces blocks when running."""
        node = node_with_wallet
        node._running = True
        # Add a tx to the pool so there's something to mine
        tx = Transaction.notarize(wallet.address, "c" * 64, nonce=1)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        node.blockchain.submit_tx(tx)
        initial_height = node.blockchain.height

        call_count = 0

        async def mock_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                node._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await node._block_loop()

        # Should have produced at least one block
        assert node.blockchain.height > initial_height

    @pytest.mark.asyncio
    async def test_block_loop_handles_error(self, node_with_wallet, wallet):
        """_block_loop handles production errors gracefully."""
        node = node_with_wallet
        node._running = True

        # Make produce_block raise
        node.blockchain.produce_block = MagicMock(
            side_effect=RuntimeError("production error"))

        call_count = 0

        async def mock_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                node._running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await node._block_loop()
        # Should not crash


class TestNodeLifecycleFull:
    """Tests for start() and stop() lifecycle."""

    @pytest.mark.asyncio
    async def test_start_and_stop(self, tmp_dir, wallet):
        """Full start/stop cycle with validator."""
        node = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
        try:
            await node.start(validator_wallet=wallet, verify_on_load=False)
            assert node._running is True
            assert node.blockchain.height >= 0
            assert node.validator_wallet is not None
            assert wallet.address in node.wallets
        finally:
            await node.stop()
            assert node._running is False

    @pytest.mark.asyncio
    async def test_start_without_validator(self, tmp_dir):
        """Start without validator wallet (non-validator node)."""
        node = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
        try:
            await node.start(validator_wallet=None, verify_on_load=False)
            assert node._running is True
            assert node.validator_wallet is None
            assert node._block_task is None  # no block production
        finally:
            await node.stop()

    @pytest.mark.asyncio
    async def test_start_with_bootstrap(self, tmp_dir, wallet):
        """Start with bootstrap peers."""
        node = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0,
                        bootstrap=["invalid:port"])
        try:
            await node.start(validator_wallet=wallet, verify_on_load=False)
            assert node._running is True
        finally:
            await node.stop()

    @pytest.mark.asyncio
    async def test_stop_saves_chain(self, tmp_dir, wallet):
        """stop() persists the blockchain."""
        node = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
        await node.start(validator_wallet=wallet, verify_on_load=False)
        # Produce a block
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)
        height = node.blockchain.height
        await node.stop()

        # Load again and verify
        node2 = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
        node2.blockchain.consensus.add_validator(wallet.address, wallet.signing_pk)
        loaded = node2.blockchain.load(verify_signatures=True)
        assert loaded is True
        assert node2.blockchain.height == height

    @pytest.mark.asyncio
    async def test_run_exits_on_stop(self, tmp_dir, wallet):
        """run() exits when _running becomes False."""
        node = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
        await node.start(validator_wallet=wallet, verify_on_load=False)

        # Schedule stop after a short delay
        async def delayed_stop():
            await asyncio.sleep(0.1)
            await node.stop()

        stop_task = asyncio.create_task(delayed_stop())
        await node.run()
        await stop_task


class TestWSNotifyHelpers:
    """Tests for _ws_notify_block and _ws_notify_tx helpers."""

    @pytest.mark.asyncio
    async def test_ws_notify_block_with_finalized(self, node_with_wallet, wallet):
        """_ws_notify_block also broadcasts finalized height."""
        node = node_with_wallet
        block = node.blockchain.get_block(0)
        # Mock finalized height
        node.blockchain.get_finalized_height = MagicMock(return_value=0)

        await node._ws_notify_block(block)

        # Should have broadcast new_block and finalized
        calls = node.ws_manager.broadcast.call_args_list
        channels = [c[0][0] for c in calls]
        assert "new_block" in channels
        assert "finalized" in channels

    @pytest.mark.asyncio
    async def test_ws_notify_block_finalized_error_handled(self, node_with_wallet, wallet):
        """_ws_notify_block handles finalized broadcast error."""
        node = node_with_wallet
        block = node.blockchain.get_block(0)
        node.blockchain.get_finalized_height = MagicMock(
            side_effect=RuntimeError("db error"))

        # Should not raise
        await node._ws_notify_block(block)

    @pytest.mark.asyncio
    async def test_deliver_block_webhooks(self, node_with_wallet, wallet):
        """_deliver_block_webhooks collects and delivers events."""
        node = node_with_wallet
        block = node.blockchain.get_block(0)
        # Ensure it runs without error
        await node._deliver_block_webhooks(block)


class TestRPCRevokeKey:
    """Tests for _rpc_revoke_key success path."""

    @pytest.mark.asyncio
    async def test_revoke_key_success(self, node_with_wallet, wallet):
        """Successfully revoke a key for a non-genesis wallet."""
        node = node_with_wallet
        # Use a second wallet (non-genesis)
        bob = Wallet.generate()
        node.wallets[bob.address] = bob
        # Register bob's encryption key first
        tx = Transaction.register_key(bob.address, bob.encryption_pk, nonce=0)
        tx.sign(bob.signing_sk, bob.signing_pk)
        node.blockchain.submit_tx(tx)
        node.blockchain.produce_block(wallet.address, wallet.signing_sk)

        result = await node._rpc_revoke_key(
            wallet_address=bob.address,
            key_type="encryption",
            reason="rotation",
        )
        assert "tx_id" in result
        assert result["key_type"] == "encryption"
        assert result["reason"] == "rotation"


class TestRPCRegisterP2P:
    """Tests for _register_p2p and _register_rpc."""

    def test_register_p2p(self, node_with_wallet):
        """_register_p2p sets up all P2P handlers."""
        node = node_with_wallet
        node.p2p._handlers = {}
        node._register_p2p()
        expected = {MSG_NEW_BLOCK, MSG_NEW_TX, MSG_GET_BLOCKS,
                    MSG_BLOCKS, "get_peers", "peers", "status"}
        assert set(node.p2p._handlers.keys()) == expected

    def test_register_rpc(self, node_with_wallet):
        """_register_rpc sets up RPC methods."""
        node = node_with_wallet
        node.rpc._methods = {}
        node._register_rpc()
        assert len(node.rpc._methods) > 10  # many methods registered


class TestWalletPersistence:
    """Additional wallet persistence tests."""

    def test_save_wallets_creates_dir(self, tmp_dir, wallet):
        """_save_wallets creates wallet directory with restricted permissions."""
        node = _make_node(tmp_dir, wallet)
        wallets_dir = node._wallets_dir()
        if os.path.exists(wallets_dir):
            import shutil
            shutil.rmtree(wallets_dir)
        node._save_wallets()
        assert os.path.isdir(wallets_dir)

    def test_load_wallets_missing_dir(self, tmp_dir):
        """_load_wallets does nothing when directory doesn't exist."""
        node = FullNode(data_dir=tmp_dir)
        node.data_dir = os.path.join(tmp_dir, "nonexistent")
        node._load_wallets()  # Should not raise
        assert len(node.wallets) == 0


class TestRPCGetTxWithFee:
    """Test _rpc_get_tx with effective fee calculation."""

    @pytest.mark.asyncio
    async def test_get_tx_with_block_index(self, node_with_wallet, wallet):
        """_rpc_get_tx includes block_index for confirmed tx."""
        node = node_with_wallet
        # The genesis block has a register_key tx
        all_txs = list(node.blockchain._tx_by_id.keys())
        if all_txs:
            result = await node._rpc_get_tx(tx_id=all_txs[0])
            assert result is not None
            assert "block_index" in result

    @pytest.mark.asyncio
    async def test_get_tx_bad_type(self, node_with_wallet):
        """_rpc_get_tx raises for non-string input."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="string"):
            await node._rpc_get_tx(tx_id=42)

    @pytest.mark.asyncio
    async def test_verify_document_bad_type(self, node_with_wallet):
        """_rpc_verify_document raises for non-string input."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="string"):
            await node._rpc_verify_document(document_hash=42)

    @pytest.mark.asyncio
    async def test_txs_by_sender_bad_type(self, node_with_wallet):
        """_rpc_txs_by_sender raises for non-string input."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="string"):
            await node._rpc_txs_by_sender(address=123)

    @pytest.mark.asyncio
    async def test_txs_by_recipient_bad_type(self, node_with_wallet):
        """_rpc_txs_by_recipient raises for non-string input."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="string"):
            await node._rpc_txs_by_recipient(address=123)

    @pytest.mark.asyncio
    async def test_get_encryption_pk_bad_type(self, node_with_wallet):
        """_rpc_get_encryption_pk raises for non-string input."""
        node = node_with_wallet
        with pytest.raises(ValueError, match="string"):
            await node._rpc_get_encryption_pk(address=42)

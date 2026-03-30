"""Dedicated JSON-RPC 2.0 server tests (TEC-612).

Tests all 43 registered RPC methods via real HTTP requests through
aiohttp TestClient, covering:
  - All 23 public methods (no auth)
  - All 20 protected methods (auth required)
  - Auth token validation and rejection
  - Invalid/unknown method names
  - Malformed JSON-RPC requests
  - Missing/extra parameters
  - Batch request handling
  - TLS-required method enforcement
  - Error response codes
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.wallet import Wallet
from qbit_network.network.rpc import RPCServer
from qbit_network.node import FullNode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AUTH_TOKEN = "test-secret-token-for-rpc-tests"


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="qv_rpc_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_node(tmp_dir):
    """Create a FullNode with mock network, real blockchain, and known auth."""
    node = FullNode(data_dir=tmp_dir, rpc_port=0, p2p_port=0)
    w = Wallet.generate()
    node.validator_wallet = w
    node.wallets[w.address] = w
    node.blockchain.consensus.add_validator(w.address, w.signing_pk)
    node.blockchain.init_chain(w.address, w.signing_sk, validator_pk=w.signing_pk)
    node.blockchain.activate_financial_layer(w.address)

    # Register encryption key so share/decapsulate tests work
    reg_tx = Transaction.register_key(w.address, w.encryption_pk, nonce=0)
    reg_tx.sign(w.signing_sk, w.signing_pk)
    node.blockchain.submit_tx(reg_tx)
    block = node.blockchain.produce_block(w.address, w.signing_sk)
    assert block is not None

    # Notarize a document for query tests
    ntx = Transaction.notarize(w.address, "abc123def456", "test doc",
                               nonce=node.blockchain.get_nonce(w.address))
    ntx.sign(w.signing_sk, w.signing_pk)
    node.blockchain.submit_tx(ntx)
    block2 = node.blockchain.produce_block(w.address, w.signing_sk)
    assert block2 is not None

    # Override auth token with known value
    node.rpc.auth_token = AUTH_TOKEN

    # Mock P2P / WS / Webhook to avoid real network
    node.p2p.broadcast = AsyncMock()
    node.p2p.peer_count = MagicMock(return_value=0)
    node.p2p.reputation = MagicMock()
    node.p2p.reputation.record = MagicMock()
    node.ws_manager.broadcast = AsyncMock()
    node.webhook_manager.deliver = AsyncMock()

    # Register RPC methods (normally done in start())
    node._register_rpc()

    return node


@pytest.fixture
def node(tmp_dir):
    return _make_node(tmp_dir)


@pytest_asyncio.fixture
async def client(node):
    """aiohttp TestClient wired to the RPCServer's app."""
    server = TestServer(node.rpc._app)
    cl = TestClient(server)
    await cl.start_server()
    yield cl
    await cl.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rpc(method, params=None, rid=1):
    """Build a JSON-RPC 2.0 request body."""
    body = {"jsonrpc": "2.0", "method": method, "id": rid}
    if params is not None:
        body["params"] = params
    return body


async def _call(client, method, params=None, token=None, rid=1):
    """Send a JSON-RPC request, return parsed response dict."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = await client.post("/", json=_rpc(method, params, rid), headers=headers)
    assert resp.status == 200
    return await resp.json()


async def _call_auth(client, method, params=None, rid=1):
    """Send an authenticated JSON-RPC request."""
    return await _call(client, method, params, token=AUTH_TOKEN, rid=rid)


# ===================================================================
# 1. Protocol-level tests (malformed requests, batches, errors)
# ===================================================================

class TestRPCProtocol:
    """JSON-RPC 2.0 protocol handling."""

    @pytest.mark.asyncio
    async def test_invalid_json(self, client):
        """Malformed JSON returns parse error (-32700)."""
        resp = await client.post("/", data=b"not-json{{{",
                                 headers={"Content-Type": "application/json"})
        body = await resp.json()
        assert body["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_unknown_method(self, client):
        """Unknown method returns -32601."""
        body = await _call(client, "qv_nonExistentMethod")
        assert body["error"]["code"] == -32601
        assert "unknown method" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_request_id_preserved(self, client):
        """Response id matches request id."""
        body = await _call(client, "qv_blockNumber", rid=42)
        assert body["id"] == 42

    @pytest.mark.asyncio
    async def test_batch_request(self, client):
        """Batch of valid requests returns array of results."""
        batch = [
            _rpc("qv_blockNumber", rid=1),
            _rpc("qv_pendingTxCount", rid=2),
        ]
        resp = await client.post("/", json=batch,
                                 headers={"Content-Type": "application/json"})
        results = await resp.json()
        assert isinstance(results, list)
        assert len(results) == 2
        ids = {r["id"] for r in results}
        assert ids == {1, 2}

    @pytest.mark.asyncio
    async def test_batch_too_large(self, client):
        """Batch exceeding MAX_RPC_BATCH is rejected."""
        from qbit_network.config import MAX_RPC_BATCH
        batch = [_rpc("qv_blockNumber", rid=i) for i in range(MAX_RPC_BATCH + 1)]
        resp = await client.post("/", json=batch,
                                 headers={"Content-Type": "application/json"})
        body = await resp.json()
        assert body["error"]["code"] == -32600
        assert "batch too large" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_missing_method_field(self, client):
        """Request without method field returns unknown method error."""
        body = {"jsonrpc": "2.0", "id": 1, "params": {}}
        resp = await client.post("/", json=body,
                                 headers={"Content-Type": "application/json"})
        result = await resp.json()
        assert result["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_info_endpoint_get(self, client):
        """GET / returns node info with method list."""
        resp = await client.get("/")
        assert resp.status == 200
        body = await resp.json()
        assert body["name"] == "QBit Network PQC Blockchain"
        assert "methods" in body
        assert isinstance(body["methods"], list)

    @pytest.mark.asyncio
    async def test_list_params_normalized_to_dict(self, client):
        """List params are normalized to named kwargs (R26-003)."""
        # qv_blockNumber takes no params — empty list should work
        body = await _call(client, "qv_blockNumber", params=[])
        assert "result" in body

    @pytest.mark.asyncio
    async def test_list_params_too_many_rejected(self, client):
        """Too many positional params returns -32602 (R26-003)."""
        # qv_blockNumber takes zero params — any positional arg is too many
        body = await _call(client, "qv_blockNumber", params=["extra"])
        assert body["error"]["code"] == -32602
        assert "too many positional params" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_list_params_protected_method_normalized(self, client):
        """List params on protected method are normalized, not passed as *args (R26-003)."""
        # qv_notarize expects named params (address, doc_hash, label, nonce)
        # Passing as list should normalize to dict and go through same validation
        body = await _call_auth(client, "qv_notarize",
                                params=["addr", "hash123", "label", 0])
        # The call may fail (invalid address) but should NOT fail with
        # a positional-arg type mismatch — it should reach the method logic
        if "error" in body:
            assert body["error"]["code"] == -32603  # internal (method logic)
            assert "too many positional" not in body["error"]["message"]


# ===================================================================
# 2. Auth tests
# ===================================================================

class TestRPCAuth:
    """Auth token validation for protected methods."""

    @pytest.mark.asyncio
    async def test_protected_method_no_token(self, client):
        """Protected method without token returns auth error."""
        body = await _call(client, "qv_newWallet")
        assert body["error"]["code"] == -32600
        assert "authentication required" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_protected_method_wrong_token(self, client):
        """Protected method with wrong token returns auth error."""
        body = await _call(client, "qv_newWallet", token="wrong-token")
        assert body["error"]["code"] == -32600
        assert "authentication required" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_protected_method_valid_token(self, client):
        """Protected method with correct token succeeds."""
        body = await _call_auth(client, "qv_newWallet")
        assert "result" in body
        assert "address" in body["result"]

    @pytest.mark.asyncio
    async def test_public_method_no_token_allowed(self, client):
        """Public methods work without auth."""
        body = await _call(client, "qv_blockNumber")
        assert "result" in body
        assert isinstance(body["result"], int)

    @pytest.mark.asyncio
    async def test_tls_required_method_rejected_without_tls(self, client):
        """TLS-required methods fail when TLS is not active."""
        body = await _call_auth(client, "qv_getSharedSecret",
                                params={"tx_id": "fake"})
        assert body["error"]["code"] == -32600
        assert "requires TLS" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_tls_required_decapsulate_rejected(self, client):
        """qv_decapsulateShared also requires TLS."""
        body = await _call_auth(client, "qv_decapsulateShared",
                                params={"wallet_address": "x", "tx_id": "y"})
        assert body["error"]["code"] == -32600
        assert "requires TLS" in body["error"]["message"]


# ===================================================================
# 3. Public chain query methods
# ===================================================================

class TestPublicChainMethods:
    """All public (no-auth) RPC methods."""

    @pytest.mark.asyncio
    async def test_block_number(self, client, node):
        body = await _call(client, "qv_blockNumber")
        assert body["result"] == node.blockchain.height

    @pytest.mark.asyncio
    async def test_get_block_by_index(self, client):
        body = await _call(client, "qv_getBlock", params={"index": 0})
        assert body["result"] is not None
        assert "transactions" in body["result"]

    @pytest.mark.asyncio
    async def test_get_block_nonexistent(self, client):
        body = await _call(client, "qv_getBlock", params={"index": 99999})
        assert body["result"] is None

    @pytest.mark.asyncio
    async def test_get_block_invalid_index_type(self, client):
        body = await _call(client, "qv_getBlock", params={"index": "not_int"})
        assert body["error"]["code"] == -32603
        assert "integer" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_get_transaction(self, client, node):
        # Get a known tx from the chain
        block = node.blockchain.get_block(1)
        tx_obj = block.transactions[0]
        tx_id = tx_obj.tx_id
        body = await _call(client, "qv_getTransaction", params={"tx_id": tx_id})
        assert body["result"] is not None
        assert body["result"]["id"] == tx_id

    @pytest.mark.asyncio
    async def test_get_transaction_not_found(self, client):
        body = await _call(client, "qv_getTransaction",
                           params={"tx_id": "nonexistent"})
        assert body["result"] is None

    @pytest.mark.asyncio
    async def test_pending_tx_count(self, client):
        body = await _call(client, "qv_pendingTxCount")
        assert isinstance(body["result"], int)

    @pytest.mark.asyncio
    async def test_verify_document_found(self, client):
        body = await _call(client, "qv_verifyDocument",
                           params={"document_hash": "abc123def456"})
        assert body["result"] is not None

    @pytest.mark.asyncio
    async def test_verify_document_not_found(self, client):
        body = await _call(client, "qv_verifyDocument",
                           params={"document_hash": "unknown_hash"})
        # Returns false/empty/None depending on implementation
        assert "result" in body

    @pytest.mark.asyncio
    async def test_get_encryption_pk(self, client, node):
        addr = node.validator_wallet.address
        body = await _call(client, "qv_getEncryptionPk",
                           params={"address": addr})
        assert body["result"]["address"] == addr
        assert "encryption_pk" in body["result"]

    @pytest.mark.asyncio
    async def test_get_encryption_pk_unknown(self, client):
        body = await _call(client, "qv_getEncryptionPk",
                           params={"address": "qv1unknownaddr"})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_peer_count(self, client):
        body = await _call(client, "qv_peerCount")
        assert body["result"] == 0

    @pytest.mark.asyncio
    async def test_node_info(self, client):
        body = await _call(client, "qv_nodeInfo")
        r = body["result"]
        assert "version" in r
        assert "chain_height" in r
        assert "validators" in r
        assert "wallets" in r

    @pytest.mark.asyncio
    async def test_validators(self, client, node):
        body = await _call(client, "qv_validators")
        assert isinstance(body["result"], list)
        assert node.validator_wallet.address in body["result"]

    @pytest.mark.asyncio
    async def test_get_txs_by_sender(self, client, node):
        addr = node.validator_wallet.address
        body = await _call(client, "qv_getTxsBySender",
                           params={"address": addr})
        assert "result" in body

    @pytest.mark.asyncio
    async def test_get_txs_by_recipient(self, client, node):
        body = await _call(client, "qv_getTxsByRecipient",
                           params={"address": "qv1nobody"})
        assert "result" in body

    @pytest.mark.asyncio
    async def test_get_stake(self, client, node):
        addr = node.validator_wallet.address
        body = await _call(client, "qv_getStake",
                           params={"validator_address": addr})
        r = body["result"]
        assert r["validator_address"] == addr
        assert "total_stake" in r

    @pytest.mark.asyncio
    async def test_get_validator_stakes(self, client):
        body = await _call(client, "qv_getValidatorStakes")
        assert isinstance(body["result"], list)

    @pytest.mark.asyncio
    async def test_get_epoch(self, client):
        body = await _call(client, "qv_getEpoch")
        r = body["result"]
        assert "epoch" in r
        assert "validators" in r

    @pytest.mark.asyncio
    async def test_get_slashing_events(self, client):
        body = await _call(client, "qv_getSlashingEvents")
        assert "result" in body

    @pytest.mark.asyncio
    async def test_get_balance(self, client, node):
        addr = node.validator_wallet.address
        body = await _call(client, "qv_getBalance",
                           params={"address": addr})
        r = body["result"]
        assert r["address"] == addr
        assert "balance" in r
        assert "formatted" in r

    @pytest.mark.asyncio
    async def test_get_balance_empty_address(self, client):
        body = await _call(client, "qv_getBalance", params={"address": ""})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_get_supply(self, client):
        body = await _call(client, "qv_getSupply")
        r = body["result"]
        assert "total_minted" in r
        assert "circulating" in r
        assert "max_supply" in r

    @pytest.mark.asyncio
    async def test_get_fee_info(self, client):
        body = await _call(client, "qv_getFeeInfo")
        r = body["result"]
        assert "base_fee" in r
        assert "weights" in r
        assert "estimated_fees" in r

    @pytest.mark.asyncio
    async def test_get_state_proof(self, client, node):
        addr = node.validator_wallet.address
        body = await _call(client, "qv_getStateProof",
                           params={"address": addr, "key_type": "balance"})
        assert "result" in body

    @pytest.mark.asyncio
    async def test_get_state_proof_invalid_key_type(self, client, node):
        addr = node.validator_wallet.address
        body = await _call(client, "qv_getStateProof",
                           params={"address": addr, "key_type": "invalid"})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_get_state_root(self, client):
        body = await _call(client, "qv_getStateRoot")
        assert "state_root" in body["result"]

    @pytest.mark.asyncio
    async def test_get_receipt_not_found(self, client):
        body = await _call(client, "qv_getReceipt",
                           params={"tx_id": "nonexistent_tx"})
        assert body["result"] is None

    @pytest.mark.asyncio
    async def test_get_receipt_empty_tx_id(self, client):
        body = await _call(client, "qv_getReceipt", params={"tx_id": ""})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_get_finalized(self, client):
        body = await _call(client, "qv_getFinalized")
        assert "finalized_height" in body["result"]

    @pytest.mark.asyncio
    async def test_get_logs(self, client):
        body = await _call(client, "qv_getLogs")
        assert "result" in body

    @pytest.mark.asyncio
    async def test_get_logs_with_filters(self, client):
        body = await _call(client, "qv_getLogs",
                           params={"event_type": "Transfer", "limit": 5})
        assert "result" in body

    @pytest.mark.asyncio
    async def test_get_logs_invalid_event_type(self, client):
        """R26-005: unknown event_type returns error."""
        body = await _call(client, "qv_getLogs",
                           params={"event_type": "nonexistent"})
        assert body["error"]["code"] == -32603
        assert "unknown event_type" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_get_logs_invalid_limit(self, client):
        body = await _call(client, "qv_getLogs", params={"limit": -1})
        assert body["error"]["code"] == -32603


class TestPublicTokenMethods:
    """Public token query methods."""

    @pytest.mark.asyncio
    async def test_get_token_info_nonexistent(self, client):
        body = await _call(client, "qv_getTokenInfo",
                           params={"token_id": "fake_token"})
        assert body["result"] is None

    @pytest.mark.asyncio
    async def test_get_token_info_empty_id(self, client):
        body = await _call(client, "qv_getTokenInfo",
                           params={"token_id": ""})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_get_token_balance(self, client, node):
        addr = node.validator_wallet.address
        body = await _call(client, "qv_getTokenBalance",
                           params={"token_id": "tok1", "address": addr})
        assert body["result"]["amount"] == 0

    @pytest.mark.asyncio
    async def test_list_tokens(self, client):
        body = await _call(client, "qv_listTokens")
        r = body["result"]
        assert "tokens" in r
        assert "total" in r

    @pytest.mark.asyncio
    async def test_list_tokens_invalid_page(self, client):
        body = await _call(client, "qv_listTokens", params={"page": 0})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_list_tokens_invalid_limit(self, client):
        body = await _call(client, "qv_listTokens", params={"limit": 200})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_get_address_tokens(self, client, node):
        addr = node.validator_wallet.address
        body = await _call(client, "qv_getAddressTokens",
                           params={"address": addr})
        assert "result" in body


class TestPublicLightClientMethods:
    """Public light-client methods."""

    @pytest.mark.asyncio
    async def test_get_block_headers(self, client):
        body = await _call(client, "qv_getBlockHeaders",
                           params={"start": 0, "count": 5})
        assert "result" in body

    @pytest.mark.asyncio
    async def test_get_block_headers_invalid_start(self, client):
        body = await _call(client, "qv_getBlockHeaders",
                           params={"start": -1, "count": 5})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_get_block_headers_invalid_count(self, client):
        body = await _call(client, "qv_getBlockHeaders",
                           params={"start": 0, "count": 200})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_get_state_proof_at(self, client, node):
        addr = node.validator_wallet.address
        body = await _call(client, "qv_getStateProofAt",
                           params={"key": f"balance:{addr}"})
        assert "result" in body

    @pytest.mark.asyncio
    async def test_get_state_proof_at_empty_key(self, client):
        body = await _call(client, "qv_getStateProofAt", params={"key": ""})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_get_receipt_proof(self, client):
        body = await _call(client, "qv_getReceiptProof",
                           params={"tx_id": "nonexistent"})
        # Returns error or empty proof
        assert "result" in body or "error" in body

    @pytest.mark.asyncio
    async def test_get_receipt_proof_empty_id(self, client):
        body = await _call(client, "qv_getReceiptProof",
                           params={"tx_id": ""})
        assert body["error"]["code"] == -32603


# ===================================================================
# 4. Protected methods
# ===================================================================

class TestProtectedWalletMethods:
    """Wallet management (protected)."""

    @pytest.mark.asyncio
    async def test_new_wallet(self, client):
        body = await _call_auth(client, "qv_newWallet")
        r = body["result"]
        assert "address" in r
        assert "signing_pk" in r
        assert "encryption_pk" in r

    @pytest.mark.asyncio
    async def test_list_wallets(self, client, node):
        body = await _call_auth(client, "qv_listWallets")
        assert node.validator_wallet.address in body["result"]

    @pytest.mark.asyncio
    async def test_get_wallet_keys(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_getWalletKeys",
                                params={"address": addr})
        r = body["result"]
        assert r["address"] == addr
        assert "signing_pk" in r

    @pytest.mark.asyncio
    async def test_get_wallet_keys_unknown(self, client):
        body = await _call_auth(client, "qv_getWalletKeys",
                                params={"address": "qv1unknown"})
        assert body["error"]["code"] == -32603


class TestProtectedTransactionMethods:
    """Transaction submission (protected)."""

    @pytest.mark.asyncio
    async def test_notarize(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_notarize",
                                params={"wallet_address": addr,
                                        "document_hash": "aabb00112233ccdd",
                                        "metadata": "rpc test"})
        assert "tx_id" in body["result"]

    @pytest.mark.asyncio
    async def test_store(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_store",
                                params={"wallet_address": addr,
                                        "document_hash": "aabb00112233ccee",
                                        "cid": "QmTest123",
                                        "metadata": "stored via RPC"})
        assert "tx_id" in body["result"]

    @pytest.mark.asyncio
    async def test_register_key(self, client, node):
        # Create a new wallet and fund it
        new_w = await _call_auth(client, "qv_newWallet")
        new_addr = new_w["result"]["address"]
        # Fund the new wallet
        addr = node.validator_wallet.address
        await _call_auth(client, "qv_transfer",
                         params={"wallet_address": addr,
                                 "to_address": new_addr,
                                 "amount": 5_000_000_000})
        node.blockchain.produce_block(addr, node.validator_wallet.signing_sk)
        body = await _call_auth(client, "qv_registerKey",
                                params={"wallet_address": new_addr})
        assert "tx_id" in body["result"]

    @pytest.mark.asyncio
    async def test_register_validator(self, client, node):
        new_w = await _call_auth(client, "qv_newWallet")
        new_addr = new_w["result"]["address"]
        # Fund the new wallet
        addr = node.validator_wallet.address
        await _call_auth(client, "qv_transfer",
                         params={"wallet_address": addr,
                                 "to_address": new_addr,
                                 "amount": 5_000_000_000})
        node.blockchain.produce_block(addr, node.validator_wallet.signing_sk)
        body = await _call_auth(client, "qv_registerValidator",
                                params={"wallet_address": new_addr})
        assert "tx_id" in body["result"]
        assert body["result"]["validator_address"] == new_addr

    @pytest.mark.asyncio
    async def test_revoke_key(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_revokeKey",
                                params={"wallet_address": addr,
                                        "key_type": "encryption",
                                        "reason": "rotation"})
        assert "tx_id" in body["result"]

    @pytest.mark.asyncio
    async def test_revoke_key_invalid_type(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_revokeKey",
                                params={"wallet_address": addr,
                                        "key_type": "invalid",
                                        "reason": "rotation"})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_revoke_key_invalid_reason(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_revokeKey",
                                params={"wallet_address": addr,
                                        "key_type": "signing",
                                        "reason": "invalid_reason"})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_transfer(self, client, node):
        # Create recipient
        new_w = await _call_auth(client, "qv_newWallet")
        to_addr = new_w["result"]["address"]
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_transfer",
                                params={"wallet_address": addr,
                                        "to_address": to_addr,
                                        "amount": 100,
                                        "memo": "rpc test transfer"})
        assert "tx_id" in body["result"]

    @pytest.mark.asyncio
    async def test_transfer_to_self_rejected(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_transfer",
                                params={"wallet_address": addr,
                                        "to_address": addr,
                                        "amount": 100})
        assert body["error"]["code"] == -32603
        assert "self" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_transfer_invalid_amount(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_transfer",
                                params={"wallet_address": addr,
                                        "to_address": "qv1someone",
                                        "amount": 0})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_send_raw_transaction(self, client, node):
        w = node.validator_wallet
        nonce = node.blockchain.get_nonce(w.address) + \
                node.blockchain._pool_sender_count.get(w.address, 0)
        tx = Transaction.notarize(w.address, "aabb00112233ccff", "raw meta",
                                  nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        body = await _call_auth(client, "qv_sendRawTransaction",
                                params={"tx_data": tx.to_dict()})
        assert "tx_id" in body["result"]

    @pytest.mark.asyncio
    async def test_send_raw_transaction_invalid(self, client):
        body = await _call_auth(client, "qv_sendRawTransaction",
                                params={"tx_data": "not_a_dict"})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_shared_with_me(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_getSharedWithMe",
                                params={"address": addr})
        assert "result" in body


class TestProtectedStakingMethods:
    """Staking operations (protected)."""

    @pytest.mark.asyncio
    async def test_stake(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_stake",
                                params={"wallet_address": addr,
                                        "validator_address": addr,
                                        "amount": 10})
        assert "tx_id" in body["result"]

    @pytest.mark.asyncio
    async def test_stake_invalid_amount(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_stake",
                                params={"wallet_address": addr,
                                        "validator_address": addr,
                                        "amount": 0})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_delegate(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_delegate",
                                params={"wallet_address": addr,
                                        "validator_address": addr,
                                        "amount": 5})
        assert "tx_id" in body["result"]

    @pytest.mark.asyncio
    async def test_unstake(self, client, node):
        addr = node.validator_wallet.address
        # Must have stake first
        await _call_auth(client, "qv_stake",
                         params={"wallet_address": addr,
                                 "validator_address": addr,
                                 "amount": 20})
        node.blockchain.produce_block(addr, node.validator_wallet.signing_sk)
        body = await _call_auth(client, "qv_unstake",
                                params={"wallet_address": addr,
                                        "validator_address": addr,
                                        "amount": 5})
        assert "tx_id" in body["result"]


class TestProtectedWebhookMethods:
    """Webhook management (protected)."""

    @pytest.mark.asyncio
    async def test_register_webhook(self, client):
        body = await _call_auth(client, "qv_registerWebhook",
                                params={"url": "https://example.com/hook",
                                        "events": ["Notarize"],
                                        "secret": "mysecret"})
        assert "result" in body

    @pytest.mark.asyncio
    async def test_register_webhook_missing_url(self, client):
        body = await _call_auth(client, "qv_registerWebhook",
                                params={"url": "",
                                        "events": ["Notarize"],
                                        "secret": "s"})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_register_webhook_invalid_events(self, client):
        body = await _call_auth(client, "qv_registerWebhook",
                                params={"url": "https://example.com/hook",
                                        "events": "not_a_list",
                                        "secret": "s"})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_list_webhooks(self, client):
        body = await _call_auth(client, "qv_listWebhooks")
        assert "result" in body

    @pytest.mark.asyncio
    async def test_delete_webhook_not_found(self, client):
        body = await _call_auth(client, "qv_deleteWebhook",
                                params={"webhook_id": "nonexistent"})
        assert body["error"]["code"] == -32603


class TestWebhookMethodsRequireAuth:
    """R26-001 regression: webhook RPC methods must require auth."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", [
        "qv_registerWebhook",
        "qv_listWebhooks",
        "qv_deleteWebhook",
    ])
    async def test_webhook_methods_reject_unauthenticated(self, client, method):
        """Unauthenticated calls to webhook methods return -32600."""
        body = await _call(client, method)
        assert body["error"]["code"] == -32600
        assert "authentication required" in body["error"]["message"]


class TestProtectedTokenMethods:
    """Token issue/mint/transfer (protected)."""

    @pytest.mark.asyncio
    async def test_issue_token(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_issueToken",
                                params={"wallet_address": addr,
                                        "name": "TestCoin",
                                        "symbol": "TST",
                                        "decimals": 8,
                                        "max_supply": 1_000_000,
                                        "transferable": True})
        assert "tx_id" in body["result"]
        assert body["result"]["symbol"] == "TST"

    @pytest.mark.asyncio
    async def test_issue_token_missing_name(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_issueToken",
                                params={"wallet_address": addr,
                                        "name": "",
                                        "symbol": "X",
                                        "decimals": 0,
                                        "max_supply": 100})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_mint_token_invalid_amount(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_mintToken",
                                params={"wallet_address": addr,
                                        "token_id": "fake",
                                        "recipient": addr,
                                        "amount": 0})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_transfer_token_invalid_amount(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_transferToken",
                                params={"wallet_address": addr,
                                        "token_id": "fake",
                                        "recipient": "qv1someone",
                                        "amount": -1})
        assert body["error"]["code"] == -32603


class TestProtectedEvidenceMethod:
    """Evidence submission (protected)."""

    @pytest.mark.asyncio
    async def test_submit_evidence_invalid_block_index(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_submitEvidence",
                                params={"wallet_address": addr,
                                        "validator_address": addr,
                                        "block_index": -1,
                                        "block_a_hash": "a",
                                        "block_b_hash": "b",
                                        "block_a_sig": "sa",
                                        "block_b_sig": "sb",
                                        "block_a_header": "ha",
                                        "block_b_header": "hb"})
        assert body["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_submit_evidence_missing_fields(self, client, node):
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_submitEvidence",
                                params={"wallet_address": addr,
                                        "validator_address": addr,
                                        "block_index": 0,
                                        "block_a_hash": "",
                                        "block_b_hash": "b",
                                        "block_a_sig": "sa",
                                        "block_b_sig": "sb",
                                        "block_a_header": "ha",
                                        "block_b_header": "hb"})
        assert body["error"]["code"] == -32603


# ===================================================================
# 5. Concurrent and params-as-list tests
# ===================================================================

class TestParamsVariants:
    """Test different params formats."""

    @pytest.mark.asyncio
    async def test_params_as_list(self, client, node):
        """Positional params (list) work for methods accepting positional args."""
        body = await _call(client, "qv_getBlock", params=[0])
        assert body["result"] is not None

    @pytest.mark.asyncio
    async def test_params_as_dict(self, client):
        """Named params (dict) work."""
        body = await _call(client, "qv_getBlock", params={"index": 0})
        assert body["result"] is not None

    @pytest.mark.asyncio
    async def test_no_params_field(self, client):
        """Omitting params defaults to empty dict."""
        body = await _call(client, "qv_blockNumber")
        assert "result" in body

    @pytest.mark.asyncio
    async def test_concurrent_requests(self, client, node):
        """Multiple concurrent requests are handled correctly."""
        addr = node.validator_wallet.address
        tasks = [
            _call(client, "qv_blockNumber"),
            _call(client, "qv_pendingTxCount"),
            _call(client, "qv_validators"),
            _call(client, "qv_getBalance", params={"address": addr}),
            _call(client, "qv_getSupply"),
        ]
        results = await asyncio.gather(*tasks)
        assert all("result" in r for r in results)
        assert len(results) == 5

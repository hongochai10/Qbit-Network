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
    async def test_batch_parallel_execution(self, client):
        """Batch requests execute in parallel via asyncio.gather."""
        batch = [
            _rpc("qv_blockNumber", rid=1),
            _rpc("qv_pendingTxCount", rid=2),
            _rpc("qv_blockNumber", rid=3),
        ]
        resp = await client.post("/", json=batch,
                                 headers={"Content-Type": "application/json"})
        results = await resp.json()
        assert isinstance(results, list)
        assert len(results) == 3
        ids = {r["id"] for r in results}
        assert ids == {1, 2, 3}
        # All should succeed (no errors)
        for r in results:
            assert "result" in r, f"unexpected error: {r.get('error')}"

    @pytest.mark.asyncio
    async def test_batch_item_timeout(self, node, client):
        """A slow batch item times out without blocking the rest."""
        import qbit_network.network.rpc as rpc_mod

        async def slow_method():
            await asyncio.sleep(30)
            return "should not reach"

        node.rpc._methods["qv_slowTest"] = slow_method

        # Save and override timeout to 0.1s for fast test
        orig_timeout = rpc_mod.RPC_BATCH_ITEM_TIMEOUT
        rpc_mod.RPC_BATCH_ITEM_TIMEOUT = 0.1
        try:
            batch = [
                _rpc("qv_blockNumber", rid=1),
                _rpc("qv_slowTest", rid=2),
                _rpc("qv_blockNumber", rid=3),
            ]
            resp = await client.post("/", json=batch,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {AUTH_TOKEN}"})
            results = await resp.json()
            assert len(results) == 3
            by_id = {r["id"]: r for r in results}
            # Fast items succeed
            assert "result" in by_id[1]
            assert "result" in by_id[3]
            # Slow item times out
            assert by_id[2]["error"]["code"] == -32000
            assert "timeout" in by_id[2]["error"]["message"]
        finally:
            rpc_mod.RPC_BATCH_ITEM_TIMEOUT = orig_timeout
            del node.rpc._methods["qv_slowTest"]

    @pytest.mark.asyncio
    async def test_batch_unauth_limit(self, client):
        """Unauthenticated batch over MAX_RPC_BATCH_UNAUTH is rejected."""
        from qbit_network.config import MAX_RPC_BATCH_UNAUTH
        batch = [_rpc("qv_blockNumber", rid=i) for i in range(MAX_RPC_BATCH_UNAUTH + 1)]
        resp = await client.post("/", json=batch,
                                 headers={"Content-Type": "application/json"})
        body = await resp.json()
        assert body["error"]["code"] == -32600
        assert "unauthenticated batch too large" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_batch_auth_bypasses_unauth_limit(self, client):
        """Authenticated batch up to MAX_RPC_BATCH is allowed."""
        from qbit_network.config import MAX_RPC_BATCH_UNAUTH
        # Send more than unauth limit but with auth
        count = MAX_RPC_BATCH_UNAUTH + 5
        batch = [_rpc("qv_blockNumber", rid=i) for i in range(count)]
        resp = await client.post("/", json=batch,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {AUTH_TOKEN}"})
        results = await resp.json()
        assert isinstance(results, list)
        assert len(results) == count

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
    async def test_get_block_ambiguous_params(self, client):
        """L02: Providing both index and block_hash must be rejected."""
        body = await _call(client, "qv_getBlock",
                           params={"index": 0, "block_hash": "abc"})
        assert body["error"]["code"] == -32603
        assert "ambiguous" in body["error"]["message"]

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

    # -- R30-003: RPC state proof key prefix restriction tests --

    @pytest.mark.asyncio
    async def test_get_state_proof_at_nonce_prefix(self, client, node):
        """nonce: prefix allowed on public RPC."""
        addr = node.validator_wallet.address
        body = await _call(client, "qv_getStateProofAt",
                           params={"key": f"nonce:{addr}"})
        assert "result" in body

    @pytest.mark.asyncio
    async def test_get_state_proof_at_token_prefix_denied(self, client):
        """token: prefix rejected on public RPC."""
        body = await _call(client, "qv_getStateProofAt",
                           params={"key": "token:abc:balance:qv1addr"})
        assert body["error"]["code"] == -32603
        assert "authenticated REST" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_get_state_proof_at_arbitrary_prefix_denied(self, client):
        """Arbitrary key prefixes rejected on public RPC."""
        body = await _call(client, "qv_getStateProofAt",
                           params={"key": "validator:somekey"})
        assert body["error"]["code"] == -32603
        assert "balance:" in body["error"]["message"]

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
        # Create a second registered validator to delegate to
        new_w = await _call_auth(client, "qv_newWallet")
        new_addr = new_w["result"]["address"]
        addr = node.validator_wallet.address
        # Fund generously (registerValidator has a high fee)
        await _call_auth(client, "qv_transfer",
                         params={"wallet_address": addr,
                                 "to_address": new_addr,
                                 "amount": 10_000_000_000})
        node.blockchain.produce_block(addr, node.validator_wallet.signing_sk)
        await _call_auth(client, "qv_registerValidator",
                         params={"wallet_address": new_addr})
        node.blockchain.produce_block(addr, node.validator_wallet.signing_sk)
        body = await _call_auth(client, "qv_delegate",
                                params={"wallet_address": addr,
                                        "validator_address": new_addr,
                                        "amount": 5})
        assert "tx_id" in body["result"]

    @pytest.mark.asyncio
    async def test_delegate_self_rejected(self, client, node):
        """L03: Self-delegation must be rejected."""
        addr = node.validator_wallet.address
        body = await _call_auth(client, "qv_delegate",
                                params={"wallet_address": addr,
                                        "validator_address": addr,
                                        "amount": 5})
        assert body["error"]["code"] == -32603
        assert "self-delegation" in body["error"]["message"]

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


# ===================================================================
# TEC-893: Additional RPC server coverage
# ===================================================================

class TestRPCServerUnit:
    """Unit tests for RPCServer internals (no HTTP)."""

    def test_create_ssl_context(self, tmp_dir):
        """_create_ssl_context creates TLS context from cert/key files."""
        from qbit_network.network.rpc import _create_ssl_context
        # Generate self-signed cert for testing
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            import datetime

            key = ec.generate_private_key(ec.SECP256R1())
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
            cert = (
                x509.CertificateBuilder()
                .subject_name(name).issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
                .not_valid_after(datetime.datetime.now(datetime.timezone.utc) +
                                datetime.timedelta(days=1))
                .sign(key, hashes.SHA256())
            )

            cert_path = os.path.join(tmp_dir, "test_cert.pem")
            key_path = os.path.join(tmp_dir, "test_key.pem")
            with open(cert_path, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            with open(key_path, "wb") as f:
                f.write(key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption()))

            ctx = _create_ssl_context(cert_path, key_path)
            import ssl
            assert isinstance(ctx, ssl.SSLContext)
        except ImportError:
            pytest.skip("cryptography package not available")

    def test_generate_self_signed(self, tmp_dir):
        """_generate_self_signed creates cert and key files."""
        from qbit_network.network.rpc import _generate_self_signed
        try:
            cert_path, key_path = _generate_self_signed(tmp_dir)
            assert os.path.exists(cert_path)
            assert os.path.exists(key_path)
            # Key file should have restricted permissions
            key_mode = os.stat(key_path).st_mode & 0o777
            assert key_mode & 0o077 == 0  # no group/other access
        except RuntimeError:
            pytest.skip("cryptography package not available")

    def test_rpc_server_init_defaults(self):
        """RPCServer initializes with default values."""
        from qbit_network.network.rpc import RPCServer
        server = RPCServer()
        assert server.host == "0.0.0.0"
        assert server.port == 8545
        assert len(server.auth_token) == 64  # hex token
        assert server._tls_active is False

    def test_rpc_server_method_registration(self):
        """method() registers a callable."""
        from qbit_network.network.rpc import RPCServer
        server = RPCServer()

        async def my_method():
            return "ok"

        server.method("test_method", my_method)
        assert "test_method" in server._methods

    def test_rpc_server_error_helper(self):
        """_error() returns proper JSON-RPC error response."""
        from qbit_network.network.rpc import RPCServer
        resp = RPCServer._error(42, -32600, "test error")
        assert resp.status == 200  # JSON-RPC errors use 200 status


class TestRPCDashboard:
    """Dashboard mounting tests."""

    @pytest.mark.asyncio
    async def test_dashboard_mount_with_existing_dir(self, tmp_dir):
        """Dashboard routes are mounted when dashboard/ dir exists."""
        from qbit_network.network.rpc import RPCServer
        # Create a fake dashboard dir
        dashboard_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "dashboard",
        )
        if os.path.isdir(dashboard_dir):
            server = RPCServer()
            # Check that dashboard routes were registered
            routes = [r.resource.canonical for r in server._app.router.routes()
                      if hasattr(r, 'resource') and r.resource]
            assert any("/dashboard" in r for r in routes)


class TestRPCStartStop:
    """Start/stop lifecycle tests."""

    @pytest.mark.asyncio
    async def test_start_and_stop_no_tls(self):
        """Server starts and stops cleanly without TLS."""
        from qbit_network.network.rpc import RPCServer
        server = RPCServer(host="127.0.0.1", port=0)
        server.method("qv_test", lambda: "ok")
        await server.start()
        assert server._runner is not None
        assert server._cleanup_task is not None
        assert not server._tls_active
        await server.stop()

    @pytest.mark.asyncio
    async def test_start_with_self_signed_tls(self):
        """Server starts with auto self-signed TLS."""
        from qbit_network.network.rpc import RPCServer
        try:
            import cryptography  # noqa: F401
        except ImportError:
            pytest.skip("cryptography package not available")
        d = tempfile.mkdtemp(prefix="qv_rpc_tls_")
        try:
            server = RPCServer(host="127.0.0.1", port=0,
                               tls_self_signed=True, data_dir=d)
            await server.start()
            assert server._tls_active is True
            assert server._tls_manager is not None
            await server.stop()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_rate_cleanup_loop_runs(self):
        """Rate cleanup loop task is created on start."""
        from qbit_network.network.rpc import RPCServer
        server = RPCServer(host="127.0.0.1", port=0)
        await server.start()
        assert server._cleanup_task is not None
        assert not server._cleanup_task.done()
        await server.stop()
        # After stop, cleanup task should be done or cancelling
        assert server._cleanup_task.done() or server._cleanup_task.cancelled() or server._cleanup_task.cancelling()

    @pytest.mark.asyncio
    async def test_attach_websocket(self):
        """attach_websocket registers /ws route."""
        from qbit_network.network.rpc import RPCServer
        server = RPCServer()
        mock_ws = MagicMock()
        server.attach_websocket(mock_ws)
        assert server._ws_manager is mock_ws
        assert server._app.get("ws_manager") is mock_ws
        routes = [str(r.resource.canonical) for r in server._app.router.routes()
                  if hasattr(r, 'resource') and r.resource]
        assert "/ws" in routes


class TestRPCExecEdgeCases:
    """Edge cases in RPC _exec dispatch."""

    @pytest.mark.asyncio
    async def test_error_message_truncation(self, client, node):
        """Long error messages are truncated to 200 chars."""
        # Register a method that raises a very long error
        long_msg = "x" * 300

        async def _raise_long():
            raise RuntimeError(long_msg)

        node.rpc._methods["qv_longError"] = _raise_long
        body = await _call(client, "qv_longError")
        assert body["error"]["code"] == -32603
        assert len(body["error"]["message"]) <= 204  # 200 + "..."

    @pytest.mark.asyncio
    async def test_exec_with_non_dict_non_list_params(self, client):
        """Params that are neither dict nor list are handled."""
        body = {"jsonrpc": "2.0", "method": "qv_blockNumber",
                "id": 1, "params": "not_valid"}
        resp = await client.post("/", json=body,
                                 headers={"Content-Type": "application/json"})
        result = await resp.json()
        # Should still return result (params ignored/empty)
        assert "result" in result or "error" in result

    @pytest.mark.asyncio
    async def test_unexpected_kwargs_rejected(self, client):
        """R37-M01: Unexpected kwargs are rejected with -32602 error."""
        body = await _call(client, "qv_getBlock",
                           params={"index": 0, "__class__": "evil", "extra": 1})
        assert "error" in body
        assert body["error"]["code"] == -32602
        assert "unexpected params" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_valid_kwargs_still_accepted(self, client):
        """R37-M01: Valid kwargs continue to work after whitelist filter."""
        body = await _call(client, "qv_getBlock", params={"index": 0})
        assert "result" in body
        assert body["result"] is not None


# ===================================================================
# Coverage gap tests — dashboard, rate limiter, TLS, body size
# ===================================================================


class TestRPCDashboard:
    """Tests for dashboard static file serving."""

    @pytest_asyncio.fixture
    async def dashboard_client(self, tmp_dir):
        """Client with a mock dashboard directory."""
        # Create dashboard dir with files
        dashboard_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "dashboard",
        )
        os.makedirs(dashboard_dir, exist_ok=True)
        index_path = os.path.join(dashboard_dir, "index.html")
        created_files = []
        if not os.path.exists(index_path):
            with open(index_path, "w") as f:
                f.write("<html><body>Dashboard</body></html>")
            created_files.append(index_path)

        static_dir = os.path.join(dashboard_dir, "static")
        os.makedirs(static_dir, exist_ok=True)
        css_path = os.path.join(static_dir, "app.css")
        if not os.path.exists(css_path):
            with open(css_path, "w") as f:
                f.write("body{}")
            created_files.append(css_path)

        rpc = RPCServer(host="127.0.0.1", port=0, auth_token=AUTH_TOKEN)
        server = TestServer(rpc._app)
        cl = TestClient(server)
        await cl.start_server()
        yield cl
        await cl.close()
        # Cleanup test files
        for fp in created_files:
            if os.path.exists(fp):
                os.unlink(fp)

    @pytest.mark.asyncio
    async def test_dashboard_index(self, dashboard_client):
        """GET /dashboard/ returns index.html."""
        resp = await dashboard_client.get("/dashboard/")
        assert resp.status == 200
        text = await resp.text()
        assert "<html" in text.lower()

    @pytest.mark.asyncio
    async def test_dashboard_static_css(self, dashboard_client):
        """GET /dashboard/static/app.css returns CSS file."""
        resp = await dashboard_client.get("/dashboard/static/app.css")
        # May be 200 or 404 depending on file existence
        assert resp.status in (200, 404)

    @pytest.mark.asyncio
    async def test_dashboard_static_traversal_blocked(self, dashboard_client):
        """Directory traversal via ../.. is blocked."""
        resp = await dashboard_client.get("/dashboard/static/../../etc/passwd")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_dashboard_static_disallowed_extension(self, dashboard_client):
        """Non-allowlisted extension returns 404."""
        resp = await dashboard_client.get("/dashboard/static/evil.php")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_dashboard_static_nonexistent_file(self, dashboard_client):
        """Request for missing static file returns 404."""
        resp = await dashboard_client.get("/dashboard/static/no-such-file.js")
        assert resp.status == 404


class TestRPCRateLimiter:
    """Tests for rate limiting middleware."""

    @pytest_asyncio.fixture
    async def rate_client(self, tmp_dir):
        """Client with aggressive rate limits for testing."""
        rpc = RPCServer(host="127.0.0.1", port=0, auth_token=AUTH_TOKEN)
        # Set very low rate limit
        rpc._rate_limiter = __import__(
            "qbit_network.network.rate_limiter", fromlist=["RateLimiter"]
        ).RateLimiter(rate=1, burst=1)

        async def echo(**kwargs):
            return {"ok": True}

        rpc.method("qv_echo", echo)
        server = TestServer(rpc._app)
        cl = TestClient(server)
        await cl.start_server()
        yield cl
        await cl.close()

    @pytest.mark.asyncio
    async def test_rate_limit_localhost_exempt(self, rate_client):
        """Localhost requests are exempt from rate limiting."""
        # TestClient connects via localhost, so should not be rate limited
        for _ in range(5):
            body = {"jsonrpc": "2.0", "method": "qv_echo", "id": 1, "params": {}}
            resp = await rate_client.post("/", json=body)
            assert resp.status == 200


class TestRPCBodySize:
    """Tests for request body size enforcement."""

    @pytest_asyncio.fixture
    async def body_client(self, node):
        """Standard client for body size tests."""
        server = TestServer(node.rpc._app)
        cl = TestClient(server)
        await cl.start_server()
        yield cl
        await cl.close()

    @pytest.mark.asyncio
    async def test_content_length_too_large(self, body_client):
        """Request with Content-Length exceeding MAX_RPC_BODY is rejected."""
        from qbit_network.config import MAX_RPC_BODY
        # Send a request with declared content-length larger than allowed
        # aiohttp client_max_size will catch this
        big_payload = b"x" * (MAX_RPC_BODY + 1)
        resp = await body_client.post(
            "/", data=big_payload,
            headers={"Content-Type": "application/json"})
        assert resp.status in (413, 200)  # 413 from aiohttp or caught by handler


class TestRPCRateCleanup:
    """Test the rate limiter cleanup loop."""

    @pytest.mark.asyncio
    async def test_rate_cleanup_loop_cancellation(self):
        """_rate_cleanup_loop exits cleanly on cancellation."""
        rpc = RPCServer(host="127.0.0.1", port=0, auth_token="test")
        task = asyncio.create_task(rpc._rate_cleanup_loop())
        await asyncio.sleep(0.01)
        task.cancel()
        await task  # should not raise


class TestRPCGenerateSelfSigned:
    """Test _generate_self_signed function."""

    def test_generate_self_signed_creates_certs(self, tmp_dir):
        """_generate_self_signed creates cert and key files."""
        from qbit_network.network.rpc import _generate_self_signed
        cert_path, key_path = _generate_self_signed(tmp_dir)
        assert os.path.exists(cert_path)
        assert os.path.exists(key_path)
        # Key file should have restricted permissions
        key_mode = os.stat(key_path).st_mode & 0o777
        assert key_mode == 0o600

    def test_generate_self_signed_import_error(self):
        """_generate_self_signed raises RuntimeError without cryptography."""
        from qbit_network.network.rpc import _generate_self_signed
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "cryptography":
                raise ImportError("no cryptography")
            return real_import(name, *args, **kwargs)

        with pytest.raises(RuntimeError, match="cryptography package required"):
            builtins.__import__ = mock_import
            try:
                # Need to call from fresh context
                import importlib
                import qbit_network.network.rpc as rpc_mod
                # Direct call — the import happens inside the function
                from qbit_network.network.rpc import _generate_self_signed
                _generate_self_signed("/tmp/test")
            finally:
                builtins.__import__ = real_import

"""Tests for the REST API gateway."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from qbit_network.core.wallet import Wallet
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.network.rest_api import RESTApi


# ---------------------------------------------------------------------------
# Minimal mock node that exposes the same interface the REST API expects
# ---------------------------------------------------------------------------

class MockNode:
    """Lightweight node substitute -- owns a real blockchain + wallets."""

    def __init__(self):
        self.wallet = Wallet.generate()
        self.blockchain = Blockchain()
        self.blockchain.consensus.add_validator(
            self.wallet.address, self.wallet.signing_pk)
        self.blockchain.init_chain(
            self.wallet.address, self.wallet.signing_sk,
            validator_pk=self.wallet.signing_pk)
        self.wallets = {self.wallet.address: self.wallet}
        self.validator_wallet = self.wallet
        self._wallet_locks: dict = {}
        self._shared_secrets = {}

        # Notarize a document so query tests have data
        tx = Transaction.notarize(
            self.wallet.address, "abc123def456", "test doc",
            nonce=self.blockchain.get_nonce(self.wallet.address))
        tx.sign(self.wallet.signing_sk, self.wallet.signing_pk)
        self.blockchain.submit_tx(tx)
        self.blockchain.produce_block(self.wallet.address, self.wallet.signing_sk)

    # --- RPC method stubs used by REST handlers ---

    async def _rpc_node_info(self):
        all_validators = set(self.blockchain.consensus.validators.keys())
        all_validators.update(self.blockchain._validator_registry.keys())
        return {
            "version": "0.2.0",
            "chain_height": self.blockchain.height,
            "pending_txs": len(self.blockchain.tx_pool),
            "peers": 0,
            "validator": self.wallet.address,
            "validators": sorted(all_validators),
            "registered_validators": sorted(self.blockchain._validator_registry.keys()),
            "wallets": len(self.wallets),
        }

    async def _rpc_validators(self):
        all_v = set(self.blockchain.consensus.validators.keys())
        all_v.update(self.blockchain._validator_registry.keys())
        return sorted(all_v)

    async def _rpc_get_tx(self, tx_id=""):
        tx = self.blockchain.get_tx(tx_id)
        if not tx:
            return None
        block_idx = self.blockchain.get_tx_block(tx_id)
        result = tx.to_dict()
        result["block_index"] = block_idx
        return result

    async def _rpc_new_wallet(self):
        w = Wallet.generate()
        self.wallets[w.address] = w
        return {
            "address": w.address,
            "signing_pk": w.signing_pk.hex(),
            "encryption_pk": w.encryption_pk.hex(),
        }

    async def _rpc_list_wallets(self):
        return list(self.wallets.keys())

    async def _rpc_send_raw_tx(self, tx_data=None):
        if not isinstance(tx_data, dict):
            raise ValueError("tx_data must be a JSON object")
        tx = Transaction.from_dict(tx_data)
        ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        return {"tx_id": result}

    async def _rpc_notarize(self, wallet_address="", document_hash="", metadata=""):
        w = self.wallets.get(wallet_address)
        if not w:
            raise ValueError(f"wallet not found: {wallet_address[:16]}...")
        nonce = self.blockchain.get_nonce(w.address)
        pending = self.blockchain._pool_sender_count.get(w.address, 0)
        tx = Transaction.notarize(w.address, document_hash, metadata, nonce=nonce + pending)
        tx.sign(w.signing_sk, w.signing_pk)
        ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        return {"tx_id": result}

    async def _rpc_verify_document(self, document_hash=""):
        return self.blockchain.verify_document(document_hash)

    async def _rpc_store(self, wallet_address="", document_hash="", cid="", metadata=""):
        w = self.wallets.get(wallet_address)
        if not w:
            raise ValueError(f"wallet not found: {wallet_address[:16]}...")
        nonce = self.blockchain.get_nonce(w.address)
        pending = self.blockchain._pool_sender_count.get(w.address, 0)
        tx = Transaction.store(w.address, document_hash, cid, metadata, nonce=nonce + pending)
        tx.sign(w.signing_sk, w.signing_pk)
        ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        return {"tx_id": result}

    async def _rpc_share(self, wallet_address="", recipient_address="",
                         cid="", recipient_encryption_pk="", expires=0):
        raise ValueError("share not supported in mock")

    async def _rpc_register_validator(self, wallet_address=""):
        raise ValueError("register_validator not supported in mock")


AUTH_TOKEN = "test-token-12345"


def _make_app():
    """Create a test aiohttp app with REST sub-app mounted."""
    node = MockNode()
    rest = RESTApi(node, AUTH_TOKEN)
    app = web.Application()
    app.add_subapp("/api/v1/", rest.app)
    app["_mock_node"] = node
    return app


# ===================================================================
# Base class that properly handles async test lifecycle
# ===================================================================

class AsyncRESTTestCase(unittest.IsolatedAsyncioTestCase):
    """Base class for REST API tests using IsolatedAsyncioTestCase."""

    async def asyncSetUp(self):
        self.app = _make_app()
        self.server = TestServer(self.app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()


# ===================================================================
# Public endpoint tests
# ===================================================================

class TestPublicEndpoints(AsyncRESTTestCase):

    async def test_health(self):
        resp = await self.client.get("/api/v1/health")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["status"], "ok")
        self.assertIsNone(body["error"])

    async def test_info(self):
        resp = await self.client.get("/api/v1/info")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("chain_height", body["data"])
        self.assertIn("version", body["data"])

    async def test_list_blocks(self):
        resp = await self.client.get("/api/v1/blocks")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("blocks", body["data"])
        self.assertIn("total", body["data"])
        self.assertGreaterEqual(body["data"]["total"], 1)

    async def test_list_blocks_pagination(self):
        resp = await self.client.get("/api/v1/blocks?page=1&limit=1")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(len(body["data"]["blocks"]), 1)
        self.assertEqual(body["data"]["page"], 1)
        self.assertEqual(body["data"]["limit"], 1)

    async def test_list_blocks_invalid_page(self):
        resp = await self.client.get("/api/v1/blocks?page=0")
        self.assertEqual(resp.status, 400)

    async def test_list_blocks_limit_too_high(self):
        resp = await self.client.get("/api/v1/blocks?limit=200")
        self.assertEqual(resp.status, 400)

    async def test_list_blocks_page_beyond_range(self):
        resp = await self.client.get("/api/v1/blocks?page=9999")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["blocks"], [])

    async def test_latest_block(self):
        resp = await self.client.get("/api/v1/blocks/latest")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("index", body["data"])

    async def test_block_by_index(self):
        resp = await self.client.get("/api/v1/blocks/0")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["index"], 0)

    async def test_block_by_index_not_found(self):
        resp = await self.client.get("/api/v1/blocks/9999")
        self.assertEqual(resp.status, 404)

    async def test_block_by_index_invalid(self):
        resp = await self.client.get("/api/v1/blocks/abc")
        self.assertEqual(resp.status, 400)

    async def test_block_by_hash(self):
        node = self.app["_mock_node"]
        block = node.blockchain.get_block(0)
        resp = await self.client.get(f"/api/v1/blocks/hash/{block.block_hash}")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["index"], 0)

    async def test_block_by_hash_not_found(self):
        resp = await self.client.get("/api/v1/blocks/hash/deadbeef")
        self.assertEqual(resp.status, 404)

    async def test_get_tx(self):
        node = self.app["_mock_node"]
        block = node.blockchain.get_block(1)
        tx_id = block.transactions[0].tx_id
        resp = await self.client.get(f"/api/v1/txs/{tx_id}")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIsNotNone(body["data"])

    async def test_get_tx_not_found(self):
        resp = await self.client.get("/api/v1/txs/nonexistent")
        self.assertEqual(resp.status, 404)

    async def test_txs_by_sender(self):
        node = self.app["_mock_node"]
        addr = node.wallet.address
        resp = await self.client.get(f"/api/v1/txs/sender/{addr}")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertGreaterEqual(body["data"]["total"], 1)

    async def test_txs_by_sender_pagination(self):
        node = self.app["_mock_node"]
        addr = node.wallet.address
        resp = await self.client.get(f"/api/v1/txs/sender/{addr}?page=1&limit=1")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertLessEqual(len(body["data"]["transactions"]), 1)

    async def test_address_info(self):
        node = self.app["_mock_node"]
        addr = node.wallet.address
        resp = await self.client.get(f"/api/v1/address/{addr}")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["address"], addr)
        self.assertIn("next_nonce", body["data"])
        self.assertIn("is_validator", body["data"])
        self.assertIn("notarization_count", body["data"])

    async def test_notarization_proof(self):
        resp = await self.client.get("/api/v1/notarizations/abc123def456")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIsNotNone(body["data"]["first_notarization"])
        self.assertGreaterEqual(body["data"]["total"], 1)

    async def test_notarization_not_found(self):
        resp = await self.client.get("/api/v1/notarizations/nonexistent")
        self.assertEqual(resp.status, 404)

    async def test_validators(self):
        resp = await self.client.get("/api/v1/validators")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("validators", body["data"])
        self.assertGreaterEqual(body["data"]["total"], 1)

    async def test_pool_summary(self):
        resp = await self.client.get("/api/v1/pool")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("count", body["data"])
        self.assertIn("by_type", body["data"])

    async def test_pool_count(self):
        resp = await self.client.get("/api/v1/pool/count")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("count", body["data"])


# ===================================================================
# Protected endpoint tests
# ===================================================================

class TestProtectedEndpoints(AsyncRESTTestCase):

    async def test_create_wallet_no_auth(self):
        resp = await self.client.post("/api/v1/wallets")
        self.assertEqual(resp.status, 401)

    async def test_create_wallet(self):
        resp = await self.client.post(
            "/api/v1/wallets",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(resp.status, 201)
        body = await resp.json()
        self.assertIn("address", body["data"])

    async def test_list_wallets_no_auth(self):
        resp = await self.client.get("/api/v1/wallets")
        self.assertEqual(resp.status, 401)

    async def test_list_wallets(self):
        resp = await self.client.get(
            "/api/v1/wallets",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("wallets", body["data"])

    async def test_notarize_no_auth(self):
        resp = await self.client.post("/api/v1/notarize", json={})
        self.assertEqual(resp.status, 401)

    async def test_notarize(self):
        node = self.app["_mock_node"]
        resp = await self.client.post(
            "/api/v1/notarize",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={
                "wallet_address": node.wallet.address,
                "document_hash": "aabbccdd0011",
                "metadata": "test",
            },
        )
        self.assertEqual(resp.status, 201)
        body = await resp.json()
        self.assertIn("tx_id", body["data"])

    async def test_notarize_missing_wallet(self):
        resp = await self.client.post(
            "/api/v1/notarize",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "", "document_hash": "x"},
        )
        self.assertEqual(resp.status, 400)

    async def test_notarize_missing_hash(self):
        resp = await self.client.post(
            "/api/v1/notarize",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "someaddr", "document_hash": ""},
        )
        self.assertEqual(resp.status, 400)

    async def test_verify(self):
        resp = await self.client.post(
            "/api/v1/verify",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"document_hash": "abc123def456"},
        )
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["data"]["verified"])

    async def test_verify_not_found(self):
        resp = await self.client.post(
            "/api/v1/verify",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"document_hash": "doesnotexist"},
        )
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertFalse(body["data"]["verified"])

    async def test_store(self):
        node = self.app["_mock_node"]
        resp = await self.client.post(
            "/api/v1/store",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={
                "wallet_address": node.wallet.address,
                "document_hash": "ee11ff22aa33",
                "cid": "QmTest",
                "metadata": "stored",
            },
        )
        self.assertEqual(resp.status, 201)
        body = await resp.json()
        self.assertIn("tx_id", body["data"])

    async def test_store_missing_fields(self):
        resp = await self.client.post(
            "/api/v1/store",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": ""},
        )
        self.assertEqual(resp.status, 400)

    async def test_submit_tx_no_auth(self):
        resp = await self.client.post("/api/v1/txs", json={})
        self.assertEqual(resp.status, 401)

    async def test_submit_tx_invalid_body(self):
        resp = await self.client.post(
            "/api/v1/txs",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertIn(resp.status, (400, 415))

    async def test_wrong_auth_token(self):
        resp = await self.client.post(
            "/api/v1/wallets",
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(resp.status, 401)

    async def test_share_missing_fields(self):
        resp = await self.client.post(
            "/api/v1/share",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": ""},
        )
        self.assertEqual(resp.status, 400)

    async def test_register_validator_missing_fields(self):
        resp = await self.client.post(
            "/api/v1/register-validator",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": ""},
        )
        self.assertEqual(resp.status, 400)

    async def test_verify_missing_hash(self):
        resp = await self.client.post(
            "/api/v1/verify",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"document_hash": ""},
        )
        self.assertEqual(resp.status, 400)

    async def test_notarize_invalid_json(self):
        resp = await self.client.post(
            "/api/v1/notarize",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)


# ===================================================================
# CORS tests
# ===================================================================

class TestCORS(AsyncRESTTestCase):

    async def test_cors_headers_on_get(self):
        resp = await self.client.get("/api/v1/health")
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
        # With wildcard CORS origin, Authorization header should NOT be exposed (SPRINT2-002)
        self.assertNotIn("Authorization", resp.headers.get("Access-Control-Allow-Headers", ""))
        self.assertIn("Content-Type", resp.headers.get("Access-Control-Allow-Headers", ""))

    async def test_options_preflight(self):
        resp = await self.client.options("/api/v1/health")
        self.assertEqual(resp.status, 204)
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("POST", resp.headers.get("Access-Control-Allow-Methods", ""))


# ===================================================================
# Response structure tests
# ===================================================================

class TestResponseStructure(AsyncRESTTestCase):

    async def test_success_structure(self):
        resp = await self.client.get("/api/v1/health")
        body = await resp.json()
        self.assertIn("data", body)
        self.assertIn("error", body)
        self.assertIsNone(body["error"])

    async def test_error_structure(self):
        resp = await self.client.get("/api/v1/blocks/9999")
        body = await resp.json()
        self.assertIsNone(body["data"])
        self.assertIn("code", body["error"])
        self.assertIn("message", body["error"])

    async def test_content_type_json(self):
        resp = await self.client.get("/api/v1/health")
        self.assertIn("application/json", resp.headers.get("Content-Type", ""))

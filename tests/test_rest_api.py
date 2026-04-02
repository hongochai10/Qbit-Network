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

class _MockP2P:
    """Minimal P2P mock for REST API tests."""

    def __init__(self):
        self._peer_count = 0

    def peer_count(self) -> int:
        return self._peer_count

    def best_peer_height(self) -> int:
        return -1


class MockNode:
    """Lightweight node substitute -- owns a real blockchain + wallets."""

    class _MockWebhookManager:
        """Minimal webhook manager mock."""
        def __init__(self):
            self._hooks = {}
            self._next_id = 1

        def register(self, url, events, secret):
            wid = f"wh-{self._next_id}"
            self._next_id += 1
            self._hooks[wid] = {"id": wid, "url": url, "events": events}
            return {"id": wid, "url": url, "events": events}

        def list_webhooks(self):
            return list(self._hooks.values())

        def delete(self, webhook_id):
            return self._hooks.pop(webhook_id, None) is not None

        async def deliver(self, *args, **kwargs):
            pass

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
        self.p2p = _MockP2P()
        self.webhook_manager = self._MockWebhookManager()

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

    # --- dPoS stubs ---

    async def _rpc_get_validator_stakes(self):
        return [
            {"validator": self.wallet.address, "stake": 1000, "delegated": 500}
        ]

    async def _rpc_get_stake(self, validator_address=""):
        if not validator_address:
            raise ValueError("validator_address required")
        if validator_address == self.wallet.address:
            return {"validator": validator_address, "stake": 1000, "delegated": 500}
        return {"validator": validator_address, "stake": 0, "delegated": 0}

    async def _rpc_stake(self, wallet_address="", validator_address="", amount=0):
        if not wallet_address:
            raise ValueError("wallet_address required")
        if not validator_address:
            raise ValueError("validator_address required")
        if not isinstance(amount, int) or amount < 1:
            raise ValueError("amount must be positive integer")
        return {"tx_id": "mock-stake-tx-id", "status": "pending"}

    async def _rpc_delegate(self, wallet_address="", validator_address="", amount=0):
        if not wallet_address:
            raise ValueError("wallet_address required")
        if not validator_address:
            raise ValueError("validator_address required")
        if not isinstance(amount, int) or amount < 1:
            raise ValueError("amount must be positive integer")
        return {"tx_id": "mock-delegate-tx-id", "status": "pending"}

    async def _rpc_unstake(self, wallet_address="", validator_address="", amount=0):
        if not wallet_address:
            raise ValueError("wallet_address required")
        if not validator_address:
            raise ValueError("validator_address required")
        if not isinstance(amount, int) or amount < 1:
            raise ValueError("amount must be positive integer")
        return {"tx_id": "mock-unstake-tx-id", "status": "pending"}

    async def _rpc_get_epoch(self):
        return {"epoch": 0, "epoch_length": 100, "validators": [self.wallet.address]}

    async def _rpc_get_slashing_events(self, validator=""):
        return []

    async def _rpc_submit_evidence(self, **kwargs):
        raise ValueError("evidence submission not supported in mock")

    async def _rpc_get_fee_info(self):
        from qbit_network.config import TX_WEIGHTS
        return {
            "base_fee": 10,
            "next_base_fee": 11,
            "suggested_priority_fee": 1,
            "weights": dict(TX_WEIGHTS),
            "estimated_fees": {
                "TRANSFER": {"min": 1_000_000, "suggested": 1_100_000},
            },
            "fee_model": "dynamic",
        }

    # --- Transfer / Evidence / Token / Light client stubs ---

    async def _rpc_transfer(self, wallet_address="", to_address="",
                            amount=0, memo=""):
        if not wallet_address:
            raise ValueError("wallet_address required")
        return {"tx_id": "mock-transfer-tx", "status": "pending"}

    async def _rpc_get_state_proof(self, address="", key_type="balance"):
        if key_type not in ("balance", "nonce"):
            raise ValueError(f"invalid key_type: {key_type}")
        return {"address": address, "key_type": key_type, "value": 0, "proof": []}

    async def _rpc_get_state_root(self):
        return {"state_root": "0" * 64, "height": self.blockchain.height}

    async def _rpc_get_balance(self, address=""):
        if not address:
            raise ValueError("address required")
        return {"address": address, "balance": 0, "formatted": "0"}

    async def _rpc_get_supply(self):
        return {"total_minted": 0, "total_burned": 0, "circulating": 0,
                "max_supply": "unlimited"}

    async def _rpc_get_receipt(self, tx_id=""):
        if not tx_id:
            raise ValueError("tx_id required")
        return None

    async def _rpc_get_logs(self, event_type="", block_index=None,
                            sender="", limit=20):
        return []

    async def _rpc_get_finalized(self):
        return {"finalized_height": self.blockchain.height, "finalized_hash": "abc"}

    async def _rpc_issue_token(self, wallet_address, name, symbol,
                               decimals, max_supply, transferable=True):
        if not name:
            raise ValueError("name required")
        return {"tx_id": "mock-issue-tx", "symbol": symbol}

    async def _rpc_mint_token(self, wallet_address, token_id,
                              recipient, amount):
        if not token_id:
            raise ValueError("token_id required")
        return {"tx_id": "mock-mint-tx"}

    async def _rpc_transfer_token(self, wallet_address, token_id,
                                  recipient, amount, memo=""):
        if not token_id:
            raise ValueError("token_id required")
        return {"tx_id": "mock-transfer-token-tx"}

AUTH_TOKEN = "test-token-12345"


def _make_app(cors_origins="*"):
    """Create a test aiohttp app with REST sub-app mounted."""
    node = MockNode()
    rest = RESTApi(node, AUTH_TOKEN, cors_origins=cors_origins)
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
        data = body["data"]
        self.assertEqual(data["status"], "ok")
        self.assertIn("chain_height", data)
        self.assertIn("peers", data)
        self.assertIn("last_block_time", data)
        self.assertIn("synced", data)
        self.assertIn("tx_pool_size", data)
        self.assertIsInstance(data["chain_height"], int)
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
    """Tests for wildcard CORS mode (dev)."""

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


class TestCORSRestrictive(unittest.IsolatedAsyncioTestCase):
    """Tests for default restrictive CORS (no cross-origin access)."""

    async def asyncSetUp(self):
        self.app = _make_app(cors_origins="")
        self.server = TestServer(self.app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_no_cors_headers_on_get(self):
        resp = await self.client.get("/api/v1/health")
        self.assertNotIn("Access-Control-Allow-Origin", resp.headers)

    async def test_options_preflight_rejected(self):
        resp = await self.client.options("/api/v1/health")
        self.assertEqual(resp.status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", resp.headers)

    async def test_endpoints_still_work(self):
        resp = await self.client.get("/api/v1/health")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["status"], "ok")


class TestCORSSpecificOrigin(unittest.IsolatedAsyncioTestCase):
    """Tests for specific origin CORS configuration."""

    async def asyncSetUp(self):
        self.app = _make_app(cors_origins="https://app.example.com")
        self.server = TestServer(self.app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_specific_origin_in_header(self):
        resp = await self.client.get("/api/v1/health")
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"),
                         "https://app.example.com")

    async def test_authorization_header_allowed(self):
        """Specific origin should allow Authorization header (SPRINT2-002)."""
        resp = await self.client.get("/api/v1/health")
        self.assertIn("Authorization", resp.headers.get("Access-Control-Allow-Headers", ""))

    async def test_options_preflight(self):
        resp = await self.client.options("/api/v1/health")
        self.assertEqual(resp.status, 204)
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"),
                         "https://app.example.com")


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


# ===================================================================
# Sprint 3: REST API edge cases
# ===================================================================

class TestBlockHashValidation(AsyncRESTTestCase):
    """Block hash hex validation tests (Sprint 3)."""

    async def test_non_hex_block_hash_rejected(self):
        """Non-hex characters in block hash path return 400."""
        resp = await self.client.get("/api/v1/blocks/hash/not-valid-hex!!!")
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertIsNotNone(body["error"])
        self.assertIn("invalid hash", body["error"]["message"].lower())

    async def test_uppercase_hex_accepted(self):
        """Uppercase hex in block hash is valid."""
        node = self.app["_mock_node"]
        block = node.blockchain.get_block(0)
        resp = await self.client.get(f"/api/v1/blocks/hash/{block.block_hash.upper()}")
        # Either 200 (found) or 404 (not found by uppercase hash) depending on case-sensitivity
        # The key check is that it doesn't return 400 (invalid format)
        self.assertNotEqual(resp.status, 400)

    async def test_all_zeros_hash_returns_404(self):
        """A valid hex hash that doesn't exist returns 404."""
        resp = await self.client.get("/api/v1/blocks/hash/" + "00" * 32)
        self.assertEqual(resp.status, 404)

    async def test_short_hex_returns_404(self):
        """A short but valid hex string that doesn't match any block returns 404."""
        resp = await self.client.get("/api/v1/blocks/hash/abcdef")
        self.assertEqual(resp.status, 404)


class TestVerifyPublicEndpoint(AsyncRESTTestCase):
    """/verify is accessible without auth token."""

    async def test_verify_no_auth_required(self):
        """POST /verify works without Authorization header."""
        resp = await self.client.post(
            "/api/v1/verify",
            json={"document_hash": "abc123def456"},
        )
        # Should succeed (200) even without auth
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("verified", body["data"])

    async def test_verify_not_found_no_auth(self):
        """POST /verify returns false for unknown hash without auth."""
        resp = await self.client.post(
            "/api/v1/verify",
            json={"document_hash": "doesnotexist123"},
        )
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertFalse(body["data"]["verified"])

    async def test_verify_missing_hash_no_auth(self):
        """POST /verify with empty hash returns 400 without auth."""
        resp = await self.client.post(
            "/api/v1/verify",
            json={"document_hash": ""},
        )
        self.assertEqual(resp.status, 400)


class TestPaginationEdgeCases(AsyncRESTTestCase):
    """Pagination edge cases (Sprint 3)."""

    async def test_page_beyond_total_returns_empty(self):
        """Requesting page beyond total returns empty list, not error."""
        resp = await self.client.get("/api/v1/blocks?page=9999&limit=10")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["blocks"], [])
        self.assertGreaterEqual(body["data"]["total"], 0)

    async def test_page_zero_rejected(self):
        """page=0 returns 400."""
        resp = await self.client.get("/api/v1/blocks?page=0")
        self.assertEqual(resp.status, 400)

    async def test_page_negative_rejected(self):
        """Negative page returns 400."""
        resp = await self.client.get("/api/v1/blocks?page=-1")
        self.assertEqual(resp.status, 400)

    async def test_limit_zero_rejected(self):
        """limit=0 returns 400."""
        resp = await self.client.get("/api/v1/blocks?limit=0")
        self.assertEqual(resp.status, 400)

    async def test_limit_above_max_rejected(self):
        """limit=101 (above MAX=100) returns 400."""
        resp = await self.client.get("/api/v1/blocks?limit=101")
        self.assertEqual(resp.status, 400)

    async def test_txs_sender_page_beyond_total(self):
        """Sender tx pagination beyond total returns empty list."""
        node = self.app["_mock_node"]
        addr = node.wallet.address
        resp = await self.client.get(f"/api/v1/txs/sender/{addr}?page=999&limit=10")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        # Empty list when page exceeds total
        self.assertEqual(len(body["data"]["transactions"]), 0)

    async def test_page_one_is_valid(self):
        """page=1 is the minimum valid page."""
        resp = await self.client.get("/api/v1/blocks?page=1")
        self.assertEqual(resp.status, 200)

    async def test_limit_one_returns_single_block(self):
        """limit=1 returns exactly one block."""
        resp = await self.client.get("/api/v1/blocks?page=1&limit=1")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertLessEqual(len(body["data"]["blocks"]), 1)


class TestOversizedPOST(AsyncRESTTestCase):
    """Oversized POST body is rejected (Sprint 3)."""

    async def test_oversized_submit_tx_rejected(self):
        """POST body larger than client_max_size (1 MB) is rejected."""
        huge_body = b"x" * (1024 * 1024 + 1)  # 1 MB + 1 byte
        resp = await self.client.post(
            "/api/v1/txs",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=huge_body,
        )
        # aiohttp returns 413 or 400 for oversized body
        self.assertIn(resp.status, (400, 413, 500))

    async def test_oversized_notarize_rejected(self):
        """POST /notarize with huge body returns error."""
        huge_body = b"x" * (1024 * 1024 + 1)
        resp = await self.client.post(
            "/api/v1/notarize",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=huge_body,
        )
        self.assertIn(resp.status, (400, 413, 500))


class TestAdditionalProtectedEndpoints(AsyncRESTTestCase):
    """Additional protected endpoint auth checks (Sprint 3)."""

    async def test_store_no_auth_rejected(self):
        """POST /store without auth returns 401."""
        resp = await self.client.post("/api/v1/store", json={})
        self.assertEqual(resp.status, 401)

    async def test_share_no_auth_rejected(self):
        """POST /share without auth returns 401."""
        resp = await self.client.post("/api/v1/share", json={})
        self.assertEqual(resp.status, 401)

    async def test_register_validator_no_auth_rejected(self):
        """POST /register-validator without auth returns 401."""
        resp = await self.client.post("/api/v1/register-validator", json={})
        self.assertEqual(resp.status, 401)

    async def test_bearer_prefix_required(self):
        """Auth token without 'Bearer' prefix returns 401."""
        resp = await self.client.post(
            "/api/v1/wallets",
            headers={"Authorization": AUTH_TOKEN},  # missing "Bearer " prefix
        )
        self.assertEqual(resp.status, 401)

    async def test_token_with_prefix_garbage_rejected(self):
        """Token with garbage prefix is rejected."""
        resp = await self.client.post(
            "/api/v1/wallets",
            headers={"Authorization": f"Token {AUTH_TOKEN}"},  # "Token" instead of "Bearer"
        )
        self.assertEqual(resp.status, 401)

    async def test_info_endpoint_public(self):
        """/info endpoint is accessible without auth."""
        resp = await self.client.get("/api/v1/info")
        self.assertEqual(resp.status, 200)

    async def test_validators_endpoint_public(self):
        """/validators endpoint is accessible without auth."""
        resp = await self.client.get("/api/v1/validators")
        self.assertEqual(resp.status, 200)

    async def test_pool_endpoint_public(self):
        """/pool endpoint is accessible without auth."""
        resp = await self.client.get("/api/v1/pool")
        self.assertEqual(resp.status, 200)

    async def test_notarize_non_json_body(self):
        """POST /notarize with non-JSON returns 400."""
        resp = await self.client.post(
            "/api/v1/notarize",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}",
                     "Content-Type": "text/plain"},
            data=b"this is not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_block_by_index_string_rejected(self):
        """GET /blocks/abc returns 400 (invalid index)."""
        resp = await self.client.get("/api/v1/blocks/abc")
        self.assertEqual(resp.status, 400)

    async def test_block_by_index_float_rejected(self):
        """GET /blocks/1.5 returns 400 (float is not valid index)."""
        resp = await self.client.get("/api/v1/blocks/1.5")
        self.assertEqual(resp.status, 400)


# ===================================================================
# dPoS REST API endpoint tests
# ===================================================================

class TestDPoSRESTEndpoints(AsyncRESTTestCase):
    """Tests for staking, epoch, slashing, and evidence REST endpoints."""

    # ---- Public read endpoints ----

    async def test_get_all_stakes_returns_200(self):
        """/stakes returns list of validator stakes."""
        resp = await self.client.get("/api/v1/stakes")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIsNone(body["error"])
        self.assertIn("validators", body["data"])
        self.assertIn("total", body["data"])

    async def test_get_all_stakes_total_matches_list(self):
        """/stakes total matches length of validators list."""
        resp = await self.client.get("/api/v1/stakes")
        body = await resp.json()
        self.assertEqual(body["data"]["total"], len(body["data"]["validators"]))

    async def test_get_validator_stake_known_address(self):
        """/stakes/{validator} for known validator returns stake data."""
        node = self.app["_mock_node"]
        resp = await self.client.get(f"/api/v1/stakes/{node.wallet.address}")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIsNone(body["error"])
        self.assertIn("stake", body["data"])
        self.assertEqual(body["data"]["validator"], node.wallet.address)

    async def test_get_validator_stake_unknown_address_returns_zero_stake(self):
        """/stakes/{validator} for unknown validator returns zero stake."""
        resp = await self.client.get("/api/v1/stakes/qv1unknownaddress")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["stake"], 0)

    async def test_get_current_epoch_returns_200(self):
        """/epochs/current returns epoch data."""
        resp = await self.client.get("/api/v1/epochs/current")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIsNone(body["error"])
        self.assertIn("epoch", body["data"])
        self.assertIn("validators", body["data"])

    async def test_get_slashing_events_returns_200(self):
        """/slashing-events returns list (may be empty)."""
        resp = await self.client.get("/api/v1/slashing-events")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIsNone(body["error"])
        self.assertIsInstance(body["data"], list)

    async def test_get_slashing_events_with_validator_query(self):
        """/slashing-events?validator=... is accepted."""
        node = self.app["_mock_node"]
        resp = await self.client.get(
            f"/api/v1/slashing-events?validator={node.wallet.address}"
        )
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIsInstance(body["data"], list)

    # ---- Protected write endpoints ----

    async def test_stake_no_auth_returns_401(self):
        """POST /stake without auth returns 401."""
        resp = await self.client.post("/api/v1/stake", json={})
        self.assertEqual(resp.status, 401)

    async def test_delegate_no_auth_returns_401(self):
        """POST /delegate without auth returns 401."""
        resp = await self.client.post("/api/v1/delegate", json={})
        self.assertEqual(resp.status, 401)

    async def test_unstake_no_auth_returns_401(self):
        """POST /unstake without auth returns 401."""
        resp = await self.client.post("/api/v1/unstake", json={})
        self.assertEqual(resp.status, 401)

    async def test_evidence_no_auth_returns_401(self):
        """POST /evidence without auth returns 401."""
        resp = await self.client.post("/api/v1/evidence", json={})
        self.assertEqual(resp.status, 401)

    async def test_stake_valid_request_returns_201(self):
        """POST /stake with valid body returns 201."""
        node = self.app["_mock_node"]
        resp = await self.client.post(
            "/api/v1/stake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={
                "wallet_address": node.wallet.address,
                "validator_address": node.wallet.address,
                "amount": 100,
            },
        )
        self.assertEqual(resp.status, 201)
        body = await resp.json()
        self.assertIsNone(body["error"])
        self.assertIn("tx_id", body["data"])

    async def test_stake_missing_wallet_returns_400(self):
        """POST /stake without wallet_address returns 400."""
        resp = await self.client.post(
            "/api/v1/stake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"validator_address": "qv1abc", "amount": 100},
        )
        self.assertEqual(resp.status, 400)

    async def test_stake_missing_validator_returns_400(self):
        """POST /stake without validator_address returns 400."""
        resp = await self.client.post(
            "/api/v1/stake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1abc", "amount": 100},
        )
        self.assertEqual(resp.status, 400)

    async def test_stake_zero_amount_returns_400(self):
        """POST /stake with amount=0 returns 400."""
        resp = await self.client.post(
            "/api/v1/stake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1abc", "validator_address": "qv1abc", "amount": 0},
        )
        self.assertEqual(resp.status, 400)

    async def test_stake_negative_amount_returns_400(self):
        """POST /stake with negative amount returns 400."""
        resp = await self.client.post(
            "/api/v1/stake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1abc", "validator_address": "qv1abc", "amount": -5},
        )
        self.assertEqual(resp.status, 400)

    async def test_stake_non_integer_amount_returns_400(self):
        """POST /stake with float amount returns 400."""
        resp = await self.client.post(
            "/api/v1/stake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1abc", "validator_address": "qv1abc", "amount": 1.5},
        )
        self.assertEqual(resp.status, 400)

    async def test_stake_invalid_json_returns_400(self):
        """POST /stake with non-JSON body returns 400."""
        resp = await self.client.post(
            "/api/v1/stake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}",
                     "Content-Type": "text/plain"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_delegate_valid_request_returns_201(self):
        """POST /delegate with valid body returns 201."""
        node = self.app["_mock_node"]
        resp = await self.client.post(
            "/api/v1/delegate",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={
                "wallet_address": node.wallet.address,
                "validator_address": node.wallet.address,
                "amount": 50,
            },
        )
        self.assertEqual(resp.status, 201)
        body = await resp.json()
        self.assertIsNone(body["error"])
        self.assertIn("tx_id", body["data"])

    async def test_delegate_missing_amount_returns_400(self):
        """POST /delegate with missing amount defaults to 0 => 400."""
        resp = await self.client.post(
            "/api/v1/delegate",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1abc", "validator_address": "qv1abc"},
        )
        self.assertEqual(resp.status, 400)

    async def test_unstake_valid_request_returns_201(self):
        """POST /unstake with valid body returns 201."""
        node = self.app["_mock_node"]
        resp = await self.client.post(
            "/api/v1/unstake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={
                "wallet_address": node.wallet.address,
                "validator_address": node.wallet.address,
                "amount": 25,
            },
        )
        self.assertEqual(resp.status, 201)
        body = await resp.json()
        self.assertIsNone(body["error"])
        self.assertIn("tx_id", body["data"])

    async def test_unstake_missing_wallet_returns_400(self):
        """POST /unstake without wallet_address returns 400."""
        resp = await self.client.post(
            "/api/v1/unstake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"validator_address": "qv1abc", "amount": 10},
        )
        self.assertEqual(resp.status, 400)

    async def test_evidence_with_auth_invalid_data_returns_400(self):
        """POST /evidence with auth but unsupported data returns 400 from mock."""
        resp = await self.client.post(
            "/api/v1/evidence",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1abc"},
        )
        self.assertEqual(resp.status, 400)

    async def test_evidence_invalid_json_returns_400(self):
        """POST /evidence with non-JSON body returns 400."""
        resp = await self.client.post(
            "/api/v1/evidence",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}",
                     "Content-Type": "text/plain"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_stakes_endpoint_is_public(self):
        """/stakes endpoint does not require auth."""
        resp = await self.client.get("/api/v1/stakes")
        self.assertEqual(resp.status, 200)

    async def test_epochs_endpoint_is_public(self):
        """/epochs/current does not require auth."""
        resp = await self.client.get("/api/v1/epochs/current")
        self.assertEqual(resp.status, 200)

    async def test_slashing_events_endpoint_is_public(self):
        """/slashing-events does not require auth."""
        resp = await self.client.get("/api/v1/slashing-events")
        self.assertEqual(resp.status, 200)


# ===================================================================
# EIP-1559 Fee endpoint tests
# ===================================================================

class TestFeeEndpoint(AsyncRESTTestCase):
    """Tests for GET /api/v1/fee endpoint."""

    async def test_fee_endpoint_returns_200(self):
        """/fee endpoint returns 200."""
        resp = await self.client.get("/api/v1/fee")
        self.assertEqual(resp.status, 200)

    async def test_fee_endpoint_is_public(self):
        """/fee endpoint does not require auth."""
        resp = await self.client.get("/api/v1/fee")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIsNone(body["error"])


# ===================================================================
# R25-002: Auth middleware enforcement — exhaustive 401 checks
# ===================================================================

class TestAuthMiddlewareEnforcement(AsyncRESTTestCase):
    """Verify that EVERY protected endpoint returns 401 without auth.

    This catches the R25-002 finding: if a new endpoint forgets per-handler
    _check_auth(), middleware now enforces auth at the route-group level.
    """

    # -- Protected POST endpoints (must return 401 without auth) --

    async def test_transfer_no_auth(self):
        resp = await self.client.post("/api/v1/transfer", json={})
        self.assertEqual(resp.status, 401)

    async def test_txs_submit_no_auth(self):
        resp = await self.client.post("/api/v1/txs", json={})
        self.assertEqual(resp.status, 401)

    async def test_evidence_no_auth(self):
        resp = await self.client.post("/api/v1/evidence", json={})
        self.assertEqual(resp.status, 401)

    async def test_wallets_create_no_auth(self):
        resp = await self.client.post("/api/v1/wallets")
        self.assertEqual(resp.status, 401)

    async def test_wallets_list_no_auth(self):
        resp = await self.client.get("/api/v1/wallets")
        self.assertEqual(resp.status, 401)

    async def test_notarize_no_auth(self):
        resp = await self.client.post("/api/v1/notarize", json={})
        self.assertEqual(resp.status, 401)

    async def test_store_no_auth(self):
        resp = await self.client.post("/api/v1/store", json={})
        self.assertEqual(resp.status, 401)

    async def test_share_no_auth(self):
        resp = await self.client.post("/api/v1/share", json={})
        self.assertEqual(resp.status, 401)

    async def test_register_validator_no_auth(self):
        resp = await self.client.post("/api/v1/register-validator", json={})
        self.assertEqual(resp.status, 401)

    async def test_stake_no_auth(self):
        resp = await self.client.post("/api/v1/stake", json={})
        self.assertEqual(resp.status, 401)

    async def test_delegate_no_auth(self):
        resp = await self.client.post("/api/v1/delegate", json={})
        self.assertEqual(resp.status, 401)

    async def test_unstake_no_auth(self):
        resp = await self.client.post("/api/v1/unstake", json={})
        self.assertEqual(resp.status, 401)

    # -- Webhook endpoints (protected) --

    async def test_webhook_register_no_auth(self):
        resp = await self.client.post("/api/v1/webhooks", json={})
        self.assertEqual(resp.status, 401)

    async def test_webhook_list_no_auth(self):
        resp = await self.client.get("/api/v1/webhooks")
        self.assertEqual(resp.status, 401)

    async def test_webhook_delete_no_auth(self):
        resp = await self.client.delete("/api/v1/webhooks/some-id")
        self.assertEqual(resp.status, 401)

    # -- Token write endpoints (protected) --

    async def test_issue_token_no_auth(self):
        resp = await self.client.post("/api/v1/issue-token", json={})
        self.assertEqual(resp.status, 401)

    async def test_mint_token_no_auth(self):
        resp = await self.client.post("/api/v1/mint-token", json={})
        self.assertEqual(resp.status, 401)

    async def test_transfer_token_no_auth(self):
        resp = await self.client.post("/api/v1/transfer-token", json={})
        self.assertEqual(resp.status, 401)

    # -- Wrong token should also return 401 --

    async def test_transfer_wrong_token(self):
        resp = await self.client.post(
            "/api/v1/transfer", json={},
            headers={"Authorization": "Bearer wrong-token"})
        self.assertEqual(resp.status, 401)

    # -- Public endpoints still accessible without auth --

    async def test_public_health_no_auth(self):
        resp = await self.client.get("/api/v1/health")
        self.assertEqual(resp.status, 200)

    async def test_public_info_no_auth(self):
        resp = await self.client.get("/api/v1/info")
        self.assertEqual(resp.status, 200)

    async def test_public_blocks_no_auth(self):
        resp = await self.client.get("/api/v1/blocks")
        self.assertEqual(resp.status, 200)

    async def test_public_verify_no_auth(self):
        resp = await self.client.post(
            "/api/v1/verify", json={"document_hash": "abc123def456"})
        self.assertEqual(resp.status, 200)

    async def test_public_tokens_no_auth(self):
        resp = await self.client.get("/api/v1/tokens")
        self.assertEqual(resp.status, 200)

    async def test_public_supply_no_auth(self):
        resp = await self.client.get("/api/v1/supply")
        # Supply is public — must not return 401 (may be 500 if mock lacks method)
        self.assertNotEqual(resp.status, 401)

    async def test_public_fee_no_auth(self):
        resp = await self.client.get("/api/v1/fee")
        self.assertEqual(resp.status, 200)

    async def test_public_headers_no_auth(self):
        resp = await self.client.get("/api/v1/headers")
        self.assertEqual(resp.status, 200)

    async def test_fee_endpoint_has_required_fields(self):
        """/fee returns all required fee info fields."""
        resp = await self.client.get("/api/v1/fee")
        body = await resp.json()
        data = body["data"]
        self.assertIn("base_fee", data)
        self.assertIn("next_base_fee", data)
        self.assertIn("suggested_priority_fee", data)
        self.assertIn("weights", data)
        self.assertIn("estimated_fees", data)
        self.assertIn("fee_model", data)

    async def test_fee_endpoint_base_fee_is_integer(self):
        """/fee base_fee is an integer."""
        resp = await self.client.get("/api/v1/fee")
        body = await resp.json()
        self.assertIsInstance(body["data"]["base_fee"], int)

    async def test_fee_endpoint_weights_is_dict(self):
        """/fee weights is a dict."""
        resp = await self.client.get("/api/v1/fee")
        body = await resp.json()
        self.assertIsInstance(body["data"]["weights"], dict)

    async def test_fee_endpoint_estimated_fees_has_transfer(self):
        """/fee estimated_fees includes TRANSFER."""
        resp = await self.client.get("/api/v1/fee")
        body = await resp.json()
        self.assertIn("TRANSFER", body["data"]["estimated_fees"])


# ===================================================================
# Health & Readiness endpoint tests
# ===================================================================

class TestHealthEndpointDetails(AsyncRESTTestCase):
    """Detailed tests for the enhanced /health endpoint."""

    async def test_health_returns_chain_height(self):
        resp = await self.client.get("/api/v1/health")
        body = await resp.json()
        data = body["data"]
        # MockNode produces at least one block in setUp
        self.assertGreaterEqual(data["chain_height"], 1)

    async def test_health_returns_last_block_time(self):
        resp = await self.client.get("/api/v1/health")
        body = await resp.json()
        data = body["data"]
        self.assertIsNotNone(data["last_block_time"])
        self.assertIsInstance(data["last_block_time"], int)

    async def test_health_returns_peer_count(self):
        resp = await self.client.get("/api/v1/health")
        body = await resp.json()
        self.assertEqual(body["data"]["peers"], 0)

    async def test_health_synced_when_no_peers(self):
        """With no peers, best_peer_height is -1 so synced should be True."""
        resp = await self.client.get("/api/v1/health")
        body = await resp.json()
        self.assertTrue(body["data"]["synced"])

    async def test_health_tx_pool_size(self):
        resp = await self.client.get("/api/v1/health")
        body = await resp.json()
        self.assertIsInstance(body["data"]["tx_pool_size"], int)


class TestReadinessEndpoint(AsyncRESTTestCase):
    """Tests for /health/ready (Kubernetes readiness probe)."""

    async def test_readiness_returns_200_when_chain_exists(self):
        """Node with chain height >= 0 and no peers reporting higher is ready."""
        resp = await self.client.get("/api/v1/health/ready")
        # MockNode has chain height >= 1, mock P2P has 0 peers but height >= 0 → ready
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["data"]["ready"])
        self.assertIn("chain_height", body["data"])
        self.assertIn("synced", body["data"])

    async def test_readiness_is_public(self):
        """Readiness endpoint should not require auth."""
        resp = await self.client.get("/api/v1/health/ready")
        self.assertNotEqual(resp.status, 401)


# ===================================================================
# Metrics endpoint tests
# ===================================================================

class TestMetricsEndpoint(AsyncRESTTestCase):
    """Tests for /metrics (Prometheus text exposition)."""

    async def test_metrics_without_registry(self):
        """Without a metrics registry bound, returns 503."""
        resp = await self.client.get("/api/v1/metrics")
        self.assertEqual(resp.status, 503)

    async def test_metrics_with_registry(self):
        """With a metrics registry, returns Prometheus text format."""
        from qbit_network.network.metrics import MetricsRegistry
        node = self.app["_mock_node"]
        node.metrics = MetricsRegistry()
        node.metrics.bind_node(node)

        resp = await self.client.get("/api/v1/metrics")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("qbit_chain_height", text)
        self.assertIn("qbit_peers_connected", text)
        self.assertIn("qbit_tx_pool_size", text)
        self.assertIn("# TYPE", text)
        self.assertIn("# HELP", text)

    async def test_metrics_content_type(self):
        """Prometheus expects text/plain content type."""
        from qbit_network.network.metrics import MetricsRegistry
        node = self.app["_mock_node"]
        node.metrics = MetricsRegistry()
        node.metrics.bind_node(node)

        resp = await self.client.get("/api/v1/metrics")
        self.assertIn("text/plain", resp.headers.get("Content-Type", ""))

    async def test_metrics_counters(self):
        """Counter metrics should be present in output."""
        from qbit_network.network.metrics import MetricsRegistry
        node = self.app["_mock_node"]
        node.metrics = MetricsRegistry()
        node.metrics.bind_node(node)
        # Increment some counters
        node.metrics.blocks_produced_total.inc()
        node.metrics.tx_processed_total.inc(5)
        node.metrics.rpc_requests_total.inc(method="getInfo")

        resp = await self.client.get("/api/v1/metrics")
        text = await resp.text()
        self.assertIn("qbit_blocks_produced_total 1", text)
        self.assertIn("qbit_tx_processed_total 5", text)
        self.assertIn('qbit_rpc_requests_total{method="getInfo"} 1', text)

    async def test_metrics_histogram(self):
        """Histogram buckets should appear in output."""
        from qbit_network.network.metrics import MetricsRegistry
        node = self.app["_mock_node"]
        node.metrics = MetricsRegistry()
        node.metrics.bind_node(node)
        node.metrics.rpc_request_duration.observe(0.05, method="transfer")

        resp = await self.client.get("/api/v1/metrics")
        text = await resp.text()
        self.assertIn("qbit_rpc_request_duration_seconds_bucket", text)
        self.assertIn("qbit_rpc_request_duration_seconds_sum", text)
        self.assertIn("qbit_rpc_request_duration_seconds_count", text)

    async def test_metrics_is_public(self):
        """Metrics endpoint should not require auth."""
        from qbit_network.network.metrics import MetricsRegistry
        node = self.app["_mock_node"]
        node.metrics = MetricsRegistry()
        node.metrics.bind_node(node)

        resp = await self.client.get("/api/v1/metrics")
        self.assertNotEqual(resp.status, 401)


# ===================================================================
# TEC-893: Additional coverage — transfer, state, receipt, events,
#          finalized, webhooks, tokens, light client
# ===================================================================

class TestTransferEndpoint(AsyncRESTTestCase):
    """POST /transfer endpoint tests."""

    async def test_transfer_valid(self):
        node = self.app["_mock_node"]
        resp = await self.client.post(
            "/api/v1/transfer",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": node.wallet.address,
                  "to_address": "qv1recipient", "amount": 100, "memo": "test"},
        )
        self.assertEqual(resp.status, 201)
        body = await resp.json()
        self.assertIn("tx_id", body["data"])

    async def test_transfer_missing_wallet(self):
        resp = await self.client.post(
            "/api/v1/transfer",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"to_address": "qv1x", "amount": 100},
        )
        self.assertEqual(resp.status, 400)

    async def test_transfer_missing_to_address(self):
        resp = await self.client.post(
            "/api/v1/transfer",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1x", "amount": 100},
        )
        self.assertEqual(resp.status, 400)

    async def test_transfer_zero_amount(self):
        resp = await self.client.post(
            "/api/v1/transfer",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1x", "to_address": "qv1y", "amount": 0},
        )
        self.assertEqual(resp.status, 400)

    async def test_transfer_non_integer_amount(self):
        resp = await self.client.post(
            "/api/v1/transfer",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1x", "to_address": "qv1y", "amount": 1.5},
        )
        self.assertEqual(resp.status, 400)

    async def test_transfer_invalid_json(self):
        resp = await self.client.post(
            "/api/v1/transfer",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_transfer_non_dict_body(self):
        resp = await self.client.post(
            "/api/v1/transfer",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json=[1, 2, 3],
        )
        self.assertEqual(resp.status, 400)

    async def test_transfer_memo_must_be_string(self):
        resp = await self.client.post(
            "/api/v1/transfer",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1x", "to_address": "qv1y",
                  "amount": 100, "memo": 123},
        )
        self.assertEqual(resp.status, 400)


class TestStateEndpoints(AsyncRESTTestCase):
    """State proof, state root, balance, supply, finalized endpoints."""

    async def test_state_proof(self):
        node = self.app["_mock_node"]
        resp = await self.client.get(
            f"/api/v1/state-proof/{node.wallet.address}")
        self.assertEqual(resp.status, 200)

    async def test_state_proof_with_key_type(self):
        node = self.app["_mock_node"]
        resp = await self.client.get(
            f"/api/v1/state-proof/{node.wallet.address}?key=nonce")
        self.assertEqual(resp.status, 200)

    async def test_state_proof_invalid_key_type(self):
        node = self.app["_mock_node"]
        resp = await self.client.get(
            f"/api/v1/state-proof/{node.wallet.address}?key=invalid")
        self.assertEqual(resp.status, 400)

    async def test_state_root(self):
        resp = await self.client.get("/api/v1/state-root")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("state_root", body["data"])

    async def test_balance(self):
        node = self.app["_mock_node"]
        resp = await self.client.get(f"/api/v1/balance/{node.wallet.address}")
        self.assertEqual(resp.status, 200)

    async def test_supply(self):
        resp = await self.client.get("/api/v1/supply")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("total_minted", body["data"])

    async def test_finalized(self):
        resp = await self.client.get("/api/v1/finalized")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("finalized_height", body["data"])


class TestReceiptEndpoint(AsyncRESTTestCase):
    """Receipt and events endpoints."""

    async def test_receipt_not_found(self):
        resp = await self.client.get("/api/v1/receipt/aabbccdd00112233")
        self.assertEqual(resp.status, 404)

    async def test_receipt_invalid_txid(self):
        resp = await self.client.get("/api/v1/receipt/not-hex!!!")
        self.assertEqual(resp.status, 400)

    async def test_events_default(self):
        resp = await self.client.get("/api/v1/events")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("events", body["data"])

    async def test_events_with_type(self):
        resp = await self.client.get("/api/v1/events?type=Transfer")
        self.assertEqual(resp.status, 200)

    async def test_events_with_block_index(self):
        resp = await self.client.get("/api/v1/events?block_index=0")
        self.assertEqual(resp.status, 200)

    async def test_events_invalid_limit(self):
        resp = await self.client.get("/api/v1/events?limit=abc")
        self.assertEqual(resp.status, 400)

    async def test_events_limit_out_of_range(self):
        resp = await self.client.get("/api/v1/events?limit=0")
        self.assertEqual(resp.status, 400)

    async def test_events_invalid_block_index(self):
        resp = await self.client.get("/api/v1/events?block_index=abc")
        self.assertEqual(resp.status, 400)


class TestWebhookEndpoints(AsyncRESTTestCase):
    """Webhook CRUD endpoints."""

    async def test_register_webhook(self):
        resp = await self.client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"url": "https://example.com/hook",
                  "events": ["Notarize"], "secret": "s3cret"},
        )
        self.assertEqual(resp.status, 201)
        body = await resp.json()
        self.assertIn("id", body["data"])

    async def test_register_webhook_missing_url(self):
        resp = await self.client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"events": ["Notarize"], "secret": "s"},
        )
        self.assertEqual(resp.status, 400)

    async def test_register_webhook_empty_events(self):
        resp = await self.client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"url": "https://x.com", "events": [], "secret": "s"},
        )
        self.assertEqual(resp.status, 400)

    async def test_register_webhook_missing_secret(self):
        resp = await self.client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"url": "https://x.com", "events": ["Notarize"]},
        )
        self.assertEqual(resp.status, 400)

    async def test_register_webhook_invalid_json(self):
        resp = await self.client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_register_webhook_non_dict(self):
        resp = await self.client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json=[1, 2],
        )
        self.assertEqual(resp.status, 400)

    async def test_list_webhooks(self):
        resp = await self.client.get(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("webhooks", body["data"])

    async def test_delete_webhook_not_found(self):
        resp = await self.client.delete(
            "/api/v1/webhooks/nonexistent",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(resp.status, 404)

    async def test_webhook_register_and_delete(self):
        # Register
        resp = await self.client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"url": "https://example.com/hook2",
                  "events": ["Transfer"], "secret": "sec"},
        )
        body = await resp.json()
        wid = body["data"]["id"]
        # Delete
        resp = await self.client.delete(
            f"/api/v1/webhooks/{wid}",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["data"]["deleted"])


class TestTokenEndpoints(AsyncRESTTestCase):
    """Token read/write endpoints."""

    async def test_list_tokens(self):
        resp = await self.client.get("/api/v1/tokens")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("tokens", body["data"])

    async def test_get_token_not_found(self):
        resp = await self.client.get("/api/v1/tokens/nonexistent")
        self.assertEqual(resp.status, 404)

    async def test_get_token_holders_not_found(self):
        resp = await self.client.get("/api/v1/tokens/nonexistent/holders")
        self.assertEqual(resp.status, 404)

    async def test_get_token_holders_invalid_offset(self):
        node = self.app["_mock_node"]
        bc = node.blockchain
        bc._token_registry["tok1"] = {"id": "tok1"}
        resp = await self.client.get("/api/v1/tokens/tok1/holders?offset=abc")
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertIn("offset", body["error"]["message"])

    async def test_get_token_holders_invalid_limit(self):
        node = self.app["_mock_node"]
        bc = node.blockchain
        bc._token_registry["tok1"] = {"id": "tok1"}
        resp = await self.client.get("/api/v1/tokens/tok1/holders?limit=xyz")
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertIn("limit", body["error"]["message"])

    async def test_get_address_tokens(self):
        node = self.app["_mock_node"]
        resp = await self.client.get(
            f"/api/v1/address/{node.wallet.address}/tokens")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("tokens", body["data"])

    async def test_get_address_tokens_invalid_offset(self):
        resp = await self.client.get("/api/v1/address/someaddr/tokens?offset=abc")
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertIn("offset", body["error"]["message"])

    async def test_get_address_tokens_invalid_limit(self):
        resp = await self.client.get("/api/v1/address/someaddr/tokens?limit=xyz")
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertIn("limit", body["error"]["message"])

    # -- R31-002 boundary tests --

    async def test_list_tokens_negative_offset(self):
        resp = await self.client.get("/api/v1/tokens?offset=-1")
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertIn("offset", body["error"]["message"])

    async def test_list_tokens_zero_limit(self):
        resp = await self.client.get("/api/v1/tokens?limit=0")
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertIn("limit", body["error"]["message"])

    async def test_list_tokens_limit_exceeds_max(self):
        resp = await self.client.get("/api/v1/tokens?limit=1001")
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertIn("limit", body["error"]["message"])

    async def test_list_tokens_valid_offset_limit(self):
        resp = await self.client.get("/api/v1/tokens?offset=0&limit=100")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["offset"], 0)
        self.assertEqual(body["data"]["limit"], 100)

    async def test_list_tokens_max_valid_limit(self):
        resp = await self.client.get("/api/v1/tokens?limit=1000")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["limit"], 1000)

    async def test_list_tokens_default_pagination(self):
        resp = await self.client.get("/api/v1/tokens")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["offset"], 0)
        self.assertEqual(body["data"]["limit"], 100)

    async def test_token_holders_negative_offset(self):
        node = self.app["_mock_node"]
        bc = node.blockchain
        bc._token_registry["tok1"] = {"id": "tok1"}
        resp = await self.client.get("/api/v1/tokens/tok1/holders?offset=-5")
        self.assertEqual(resp.status, 400)

    async def test_token_holders_limit_exceeds_max(self):
        node = self.app["_mock_node"]
        bc = node.blockchain
        bc._token_registry["tok1"] = {"id": "tok1"}
        resp = await self.client.get("/api/v1/tokens/tok1/holders?limit=1001")
        self.assertEqual(resp.status, 400)

    async def test_address_tokens_negative_offset(self):
        resp = await self.client.get("/api/v1/address/someaddr/tokens?offset=-1")
        self.assertEqual(resp.status, 400)

    async def test_address_tokens_limit_exceeds_max(self):
        resp = await self.client.get("/api/v1/address/someaddr/tokens?limit=2000")
        self.assertEqual(resp.status, 400)

    async def test_issue_token(self):
        resp = await self.client.post(
            "/api/v1/issue-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "qv1w", "name": "TestCoin", "symbol": "TC",
                  "decimals": 8, "max_supply": 1000000},
        )
        self.assertEqual(resp.status, 201)

    async def test_issue_token_missing_fields(self):
        resp = await self.client.post(
            "/api/v1/issue-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "qv1w"},
        )
        self.assertEqual(resp.status, 400)

    async def test_issue_token_invalid_json(self):
        resp = await self.client.post(
            "/api/v1/issue-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_mint_token(self):
        resp = await self.client.post(
            "/api/v1/mint-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "qv1w", "token_id": "tok1",
                  "recipient": "qv1r", "amount": 100},
        )
        self.assertEqual(resp.status, 201)

    async def test_mint_token_missing_fields(self):
        resp = await self.client.post(
            "/api/v1/mint-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "qv1w"},
        )
        self.assertEqual(resp.status, 400)

    async def test_mint_token_invalid_json(self):
        resp = await self.client.post(
            "/api/v1/mint-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_transfer_token(self):
        resp = await self.client.post(
            "/api/v1/transfer-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "qv1w", "token_id": "tok1",
                  "recipient": "qv1r", "amount": 50},
        )
        self.assertEqual(resp.status, 201)

    async def test_transfer_token_missing_fields(self):
        resp = await self.client.post(
            "/api/v1/transfer-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "qv1w"},
        )
        self.assertEqual(resp.status, 400)

    async def test_transfer_token_invalid_json(self):
        resp = await self.client.post(
            "/api/v1/transfer-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)


class TestLightClientEndpoints(AsyncRESTTestCase):
    """Light client endpoints."""

    async def test_list_headers(self):
        resp = await self.client.get("/api/v1/headers")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertIn("headers", body["data"])
        self.assertIn("height", body["data"])

    async def test_list_headers_with_params(self):
        resp = await self.client.get("/api/v1/headers?start=0&count=5")
        self.assertEqual(resp.status, 200)

    async def test_list_headers_invalid_start(self):
        resp = await self.client.get("/api/v1/headers?start=-1")
        self.assertEqual(resp.status, 400)

    async def test_list_headers_invalid_count(self):
        resp = await self.client.get("/api/v1/headers?count=0")
        self.assertEqual(resp.status, 400)

    async def test_list_headers_count_too_large(self):
        resp = await self.client.get("/api/v1/headers?count=200")
        self.assertEqual(resp.status, 400)

    async def test_list_headers_non_integer(self):
        resp = await self.client.get("/api/v1/headers?start=abc")
        self.assertEqual(resp.status, 400)

    async def test_get_header_by_index(self):
        resp = await self.client.get("/api/v1/headers/0")
        self.assertEqual(resp.status, 200)

    async def test_get_header_not_found(self):
        resp = await self.client.get("/api/v1/headers/99999")
        self.assertEqual(resp.status, 404)

    async def test_state_proof_by_key(self):
        node = self.app["_mock_node"]
        resp = await self.client.get(
            f"/api/v1/proofs/state/balance:{node.wallet.address}")
        # May return 200 or 404 depending on state trie
        self.assertIn(resp.status, (200, 404))

    async def test_state_proof_by_key_with_block(self):
        node = self.app["_mock_node"]
        resp = await self.client.get(
            f"/api/v1/proofs/state/balance:{node.wallet.address}?block=0")
        self.assertIn(resp.status, (200, 404))

    async def test_state_proof_by_key_invalid_block(self):
        node = self.app["_mock_node"]
        resp = await self.client.get(
            f"/api/v1/proofs/state/balance:{node.wallet.address}?block=abc")
        self.assertEqual(resp.status, 400)

    # -- R30-003: State proof key prefix restriction tests --

    async def test_state_proof_nonce_prefix_allowed(self):
        """nonce: prefix is public — should not return 403."""
        node = self.app["_mock_node"]
        resp = await self.client.get(
            f"/api/v1/proofs/state/nonce:{node.wallet.address}")
        self.assertIn(resp.status, (200, 404))

    async def test_state_proof_token_prefix_denied_without_auth(self):
        """token: prefix requires auth — returns 403 without Bearer token."""
        resp = await self.client.get(
            "/api/v1/proofs/state/token:abc:balance:qv1someaddr")
        self.assertEqual(resp.status, 403)
        body = await resp.json()
        self.assertIn("authentication required", body["error"]["message"])

    async def test_state_proof_token_prefix_allowed_with_auth(self):
        """token: prefix is accessible with valid auth token."""
        resp = await self.client.get(
            "/api/v1/proofs/state/token:abc:balance:qv1someaddr",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
        # Should pass prefix check; 200 or 404 depending on state trie
        self.assertIn(resp.status, (200, 404))

    async def test_state_proof_arbitrary_key_denied(self):
        """Arbitrary key prefixes (not balance:/nonce:/token:) are denied."""
        resp = await self.client.get("/api/v1/proofs/state/validator:somekey")
        self.assertEqual(resp.status, 403)
        body = await resp.json()
        self.assertIn("not allowed", body["error"]["message"])

    async def test_state_proof_empty_key_prefix_denied(self):
        """Keys without a recognized prefix are denied."""
        resp = await self.client.get("/api/v1/proofs/state/randomkey123")
        self.assertEqual(resp.status, 403)

    async def test_receipt_proof(self):
        resp = await self.client.get("/api/v1/proofs/receipt/aabbccdd")
        # Either 200 or 404
        self.assertIn(resp.status, (200, 404))


class TestSubmitTxEndpoint(AsyncRESTTestCase):
    """POST /txs submit transaction endpoint."""

    async def test_submit_tx_non_dict(self):
        resp = await self.client.post(
            "/api/v1/txs",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json=[1, 2, 3],
        )
        self.assertEqual(resp.status, 400)

    async def test_submit_evidence_non_dict(self):
        resp = await self.client.post(
            "/api/v1/evidence",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json=[1, 2, 3],
        )
        self.assertEqual(resp.status, 400)

    async def test_block_by_index_negative(self):
        """GET /blocks/-1 returns 400."""
        resp = await self.client.get("/api/v1/blocks/-1")
        self.assertEqual(resp.status, 400)

    async def test_pagination_non_integer_page(self):
        resp = await self.client.get("/api/v1/blocks?page=abc")
        self.assertEqual(resp.status, 400)

    async def test_pagination_non_integer_limit(self):
        resp = await self.client.get("/api/v1/blocks?limit=abc")
        self.assertEqual(resp.status, 400)


class TestShareEndpointValidation(AsyncRESTTestCase):
    """Detailed validation for POST /share endpoint."""

    async def test_share_valid_request(self):
        node = self.app["_mock_node"]
        resp = await self.client.post(
            "/api/v1/share",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={
                "wallet_address": node.wallet.address,
                "recipient_address": "qv1recipient",
                "cid": "QmTest",
                "recipient_encryption_pk": "aabb",
                "expires": 0,
            },
        )
        # Mock raises "share not supported" → 400
        self.assertEqual(resp.status, 400)

    async def test_share_non_string_cid(self):
        resp = await self.client.post(
            "/api/v1/share",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={
                "wallet_address": "qv1w",
                "recipient_address": "qv1r",
                "cid": 123,
            },
        )
        self.assertEqual(resp.status, 400)

    async def test_share_non_integer_expires(self):
        resp = await self.client.post(
            "/api/v1/share",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={
                "wallet_address": "qv1w",
                "recipient_address": "qv1r",
                "cid": "Qm",
                "recipient_encryption_pk": "pk",
                "expires": "not_int",
            },
        )
        self.assertEqual(resp.status, 400)

    async def test_share_invalid_json(self):
        resp = await self.client.post(
            "/api/v1/share",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_share_non_dict(self):
        resp = await self.client.post(
            "/api/v1/share",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json="string_body",
        )
        self.assertEqual(resp.status, 400)


class TestRegisterValidatorEndpoint(AsyncRESTTestCase):
    """POST /register-validator endpoint."""

    async def test_register_validator_valid(self):
        resp = await self.client.post(
            "/api/v1/register-validator",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "qv1valid"},
        )
        # Mock raises error → 400
        self.assertEqual(resp.status, 400)

    async def test_register_validator_invalid_json(self):
        resp = await self.client.post(
            "/api/v1/register-validator",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_register_validator_non_dict(self):
        resp = await self.client.post(
            "/api/v1/register-validator",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json=[1, 2],
        )
        self.assertEqual(resp.status, 400)


class TestDelegateUnstakeValidation(AsyncRESTTestCase):
    """Detailed validation for delegate/unstake."""

    async def test_delegate_invalid_json(self):
        resp = await self.client.post(
            "/api/v1/delegate",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_delegate_non_dict(self):
        resp = await self.client.post(
            "/api/v1/delegate",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json="string",
        )
        self.assertEqual(resp.status, 400)

    async def test_unstake_invalid_json(self):
        resp = await self.client.post(
            "/api/v1/unstake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            data=b"not json",
        )
        self.assertEqual(resp.status, 400)

    async def test_unstake_non_dict(self):
        resp = await self.client.post(
            "/api/v1/unstake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json=[1],
        )
        self.assertEqual(resp.status, 400)


class TestPoolSummaryWithPendingTxs(AsyncRESTTestCase):
    """Pool summary when there are pending transactions."""

    async def test_pool_summary_with_pending_txs(self):
        node = self.app["_mock_node"]
        # Inject a transaction directly into the pool
        tx = Transaction.notarize(
            node.wallet.address, "pendingdochash", "pending meta",
            nonce=node.blockchain.get_nonce(node.wallet.address))
        tx.sign(node.wallet.signing_sk, node.wallet.signing_pk)
        node.blockchain.tx_pool.append(tx)

        resp = await self.client.get("/api/v1/pool")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertGreater(body["data"]["count"], 0)
        self.assertIn("NOTARIZE", body["data"]["by_type"])


class TestAuthEdgeCases(AsyncRESTTestCase):
    """Edge cases for auth middleware."""

    async def test_bearer_empty_token(self):
        """Bearer prefix but empty token after stripping."""
        resp = await self.client.post(
            "/api/v1/wallets",
            headers={"Authorization": "Bearer   "},
        )
        self.assertEqual(resp.status, 401)


class TestReceiptFoundPath(AsyncRESTTestCase):
    """Test receipt endpoint when receipt is found."""

    async def test_receipt_found(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_receipt = AsyncMock(return_value={
            "tx_id": "abc123", "status": "confirmed", "block_index": 1,
            "gas_used": 100,
        })
        resp = await self.client.get("/api/v1/receipt/abc123")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["data"]["tx_id"], "abc123")

    async def test_receipt_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_receipt = AsyncMock(side_effect=Exception("db error"))
        resp = await self.client.get("/api/v1/receipt/abc123")
        self.assertEqual(resp.status, 500)


class TestErrorPathsViaPatching(AsyncRESTTestCase):
    """Test error/exception paths in various endpoints using mock patching."""

    async def test_state_root_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_state_root = AsyncMock(
            side_effect=Exception("state error"))
        resp = await self.client.get("/api/v1/state-root")
        self.assertEqual(resp.status, 500)

    async def test_balance_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_balance = AsyncMock(
            side_effect=Exception("balance error"))
        resp = await self.client.get(
            f"/api/v1/balance/{node.wallet.address}")
        self.assertEqual(resp.status, 500)

    async def test_supply_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_supply = AsyncMock(
            side_effect=Exception("supply error"))
        resp = await self.client.get("/api/v1/supply")
        self.assertEqual(resp.status, 500)

    async def test_fee_info_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_fee_info = AsyncMock(
            side_effect=Exception("fee error"))
        resp = await self.client.get("/api/v1/fee")
        self.assertEqual(resp.status, 500)

    async def test_events_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_logs = AsyncMock(
            side_effect=ValueError("invalid event type"))
        resp = await self.client.get("/api/v1/events")
        self.assertEqual(resp.status, 400)

    async def test_events_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_logs = AsyncMock(
            side_effect=Exception("events error"))
        resp = await self.client.get("/api/v1/events")
        self.assertEqual(resp.status, 500)

    async def test_finalized_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_finalized = AsyncMock(
            side_effect=Exception("finalized error"))
        resp = await self.client.get("/api/v1/finalized")
        self.assertEqual(resp.status, 500)

    async def test_state_proof_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_state_proof = AsyncMock(
            side_effect=Exception("proof error"))
        resp = await self.client.get(
            f"/api/v1/state-proof/{node.wallet.address}")
        self.assertEqual(resp.status, 500)

    async def test_transfer_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_transfer = AsyncMock(
            side_effect=ValueError("insufficient balance"))
        resp = await self.client.post(
            "/api/v1/transfer",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "to_address": "w2",
                  "amount": 100},
        )
        self.assertEqual(resp.status, 400)

    async def test_transfer_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_transfer = AsyncMock(
            side_effect=Exception("transfer error"))
        resp = await self.client.post(
            "/api/v1/transfer",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "to_address": "w2",
                  "amount": 100},
        )
        self.assertEqual(resp.status, 500)

    async def test_submit_evidence_success(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_submit_evidence = AsyncMock(
            return_value={"tx_id": "ev-123"})
        valid_body = {
            "wallet_address": "w1", "validator_address": "v1",
            "block_index": 5, "block_a_hash": "ha", "block_b_hash": "hb",
            "block_a_sig": "sa", "block_b_sig": "sb",
            "block_a_header": "hda", "block_b_header": "hdb",
        }
        resp = await self.client.post(
            "/api/v1/evidence",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json=valid_body,
        )
        self.assertEqual(resp.status, 201)
        node._rpc_submit_evidence.assert_called_once_with(
            wallet_address="w1", validator_address="v1",
            block_index=5, block_a_hash="ha", block_b_hash="hb",
            block_a_sig="sa", block_b_sig="sb",
            block_a_header="hda", block_b_header="hdb",
            max_fee_per_weight=None, max_priority_fee=None,
        )

    async def test_submit_evidence_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_submit_evidence = AsyncMock(
            side_effect=Exception("evidence error"))
        valid_body = {
            "wallet_address": "w1", "validator_address": "v1",
            "block_index": 5, "block_a_hash": "ha", "block_b_hash": "hb",
            "block_a_sig": "sa", "block_b_sig": "sb",
            "block_a_header": "hda", "block_b_header": "hdb",
        }
        resp = await self.client.post(
            "/api/v1/evidence",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json=valid_body,
        )
        self.assertEqual(resp.status, 500)

    async def test_submit_evidence_rejects_unknown_fields(self):
        """R31-001: unknown fields in evidence body are rejected with 400."""
        resp = await self.client.post(
            "/api/v1/evidence",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={
                "wallet_address": "w1", "validator_address": "v1",
                "block_index": 5, "block_a_hash": "ha", "block_b_hash": "hb",
                "block_a_sig": "sa", "block_b_sig": "sb",
                "block_a_header": "hda", "block_b_header": "hdb",
                "__proto__": "injected", "evil_param": True,
            },
        )
        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertIn("unknown fields", data["error"]["message"])

    async def test_submit_evidence_missing_wallet_address(self):
        resp = await self.client.post(
            "/api/v1/evidence",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={
                "validator_address": "v1", "block_index": 5,
                "block_a_hash": "ha", "block_b_hash": "hb",
                "block_a_sig": "sa", "block_b_sig": "sb",
                "block_a_header": "hda", "block_b_header": "hdb",
            },
        )
        self.assertEqual(resp.status, 400)

    async def test_submit_tx_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_send_raw_tx = AsyncMock(
            side_effect=ValueError("invalid tx"))
        resp = await self.client.post(
            "/api/v1/txs",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"tx_type": "TRANSFER"},
        )
        self.assertEqual(resp.status, 400)

    async def test_submit_tx_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_send_raw_tx = AsyncMock(
            side_effect=Exception("submit error"))
        resp = await self.client.post(
            "/api/v1/txs",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"tx_type": "TRANSFER"},
        )
        self.assertEqual(resp.status, 500)

    async def test_create_wallet_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_new_wallet = AsyncMock(
            side_effect=Exception("wallet error"))
        resp = await self.client.post(
            "/api/v1/wallets",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(resp.status, 500)

    async def test_list_wallets_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_list_wallets = AsyncMock(
            side_effect=Exception("wallets error"))
        resp = await self.client.get(
            "/api/v1/wallets",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(resp.status, 500)

    async def test_notarize_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_notarize = AsyncMock(
            side_effect=ValueError("wallet not found"))
        resp = await self.client.post(
            "/api/v1/notarize",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "document_hash": "h1"},
        )
        self.assertEqual(resp.status, 400)

    async def test_notarize_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_notarize = AsyncMock(
            side_effect=Exception("notarize error"))
        resp = await self.client.post(
            "/api/v1/notarize",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "document_hash": "h1"},
        )
        self.assertEqual(resp.status, 500)

    async def test_verify_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_verify_document = AsyncMock(
            side_effect=Exception("verify error"))
        resp = await self.client.post(
            "/api/v1/verify",
            json={"document_hash": "h1"},
        )
        self.assertEqual(resp.status, 500)

    async def test_store_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_store = AsyncMock(
            side_effect=ValueError("store failed"))
        resp = await self.client.post(
            "/api/v1/store",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "document_hash": "h1"},
        )
        self.assertEqual(resp.status, 400)

    async def test_store_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_store = AsyncMock(
            side_effect=Exception("store error"))
        resp = await self.client.post(
            "/api/v1/store",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "document_hash": "h1"},
        )
        self.assertEqual(resp.status, 500)

    async def test_share_success(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_share = AsyncMock(
            return_value={"tx_id": "share-123"})
        resp = await self.client.post(
            "/api/v1/share",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "recipient_address": "w2",
                  "cid": "qm123", "recipient_encryption_pk": "pk1",
                  "expires": 0},
        )
        self.assertEqual(resp.status, 201)

    async def test_share_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_share = AsyncMock(
            side_effect=Exception("share error"))
        resp = await self.client.post(
            "/api/v1/share",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "recipient_address": "w2",
                  "cid": "qm123", "recipient_encryption_pk": "pk1",
                  "expires": 0},
        )
        self.assertEqual(resp.status, 500)

    async def test_register_validator_success(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_register_validator = AsyncMock(
            return_value={"tx_id": "rv-123"})
        resp = await self.client.post(
            "/api/v1/register-validator",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1"},
        )
        self.assertEqual(resp.status, 201)

    async def test_register_validator_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_register_validator = AsyncMock(
            side_effect=Exception("register error"))
        resp = await self.client.post(
            "/api/v1/register-validator",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1"},
        )
        self.assertEqual(resp.status, 500)

    async def test_all_stakes_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_validator_stakes = AsyncMock(
            side_effect=Exception("stakes error"))
        resp = await self.client.get("/api/v1/stakes")
        self.assertEqual(resp.status, 500)

    async def test_validator_stake_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_stake = AsyncMock(
            side_effect=ValueError("invalid address"))
        resp = await self.client.get(
            f"/api/v1/stakes/{node.wallet.address}")
        self.assertEqual(resp.status, 400)

    async def test_validator_stake_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_get_stake = AsyncMock(
            side_effect=Exception("stake error"))
        resp = await self.client.get(
            f"/api/v1/stakes/{node.wallet.address}")
        self.assertEqual(resp.status, 500)

    async def test_stake_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_stake = AsyncMock(
            side_effect=ValueError("insufficient balance"))
        resp = await self.client.post(
            "/api/v1/stake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "validator_address": "v1",
                  "amount": 100},
        )
        self.assertEqual(resp.status, 400)

    async def test_stake_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_stake = AsyncMock(
            side_effect=Exception("stake error"))
        resp = await self.client.post(
            "/api/v1/stake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "validator_address": "v1",
                  "amount": 100},
        )
        self.assertEqual(resp.status, 500)

    async def test_delegate_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_delegate = AsyncMock(
            side_effect=ValueError("delegate error"))
        resp = await self.client.post(
            "/api/v1/delegate",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "validator_address": "v1",
                  "amount": 100},
        )
        self.assertEqual(resp.status, 400)

    async def test_delegate_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_delegate = AsyncMock(
            side_effect=Exception("delegate error"))
        resp = await self.client.post(
            "/api/v1/delegate",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "validator_address": "v1",
                  "amount": 100},
        )
        self.assertEqual(resp.status, 500)

    async def test_unstake_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_unstake = AsyncMock(
            side_effect=ValueError("unstake error"))
        resp = await self.client.post(
            "/api/v1/unstake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "validator_address": "v1",
                  "amount": 100},
        )
        self.assertEqual(resp.status, 400)

    async def test_unstake_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_unstake = AsyncMock(
            side_effect=Exception("unstake error"))
        resp = await self.client.post(
            "/api/v1/unstake",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "validator_address": "v1",
                  "amount": 100},
        )
        self.assertEqual(resp.status, 500)

    async def test_webhook_register_value_error(self):
        from unittest.mock import MagicMock
        node = self.app["_mock_node"]
        node.webhook_manager.register = MagicMock(
            side_effect=ValueError("invalid url"))
        resp = await self.client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"url": "http://bad", "events": ["block"],
                  "secret": "s3cret"},
        )
        self.assertEqual(resp.status, 400)

    async def test_webhook_register_internal_error(self):
        from unittest.mock import MagicMock
        node = self.app["_mock_node"]
        node.webhook_manager.register = MagicMock(
            side_effect=Exception("webhook error"))
        resp = await self.client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"url": "http://bad", "events": ["block"],
                  "secret": "s3cret"},
        )
        self.assertEqual(resp.status, 500)

    async def test_list_webhooks_error(self):
        from unittest.mock import MagicMock
        node = self.app["_mock_node"]
        node.webhook_manager.list_webhooks = MagicMock(
            side_effect=Exception("list error"))
        resp = await self.client.get(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(resp.status, 500)

    async def test_delete_webhook_value_error(self):
        from unittest.mock import MagicMock
        node = self.app["_mock_node"]
        node.webhook_manager.delete = MagicMock(
            side_effect=ValueError("bad id"))
        resp = await self.client.delete(
            "/api/v1/webhooks/wh-99",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(resp.status, 400)

    async def test_delete_webhook_internal_error(self):
        from unittest.mock import MagicMock
        node = self.app["_mock_node"]
        node.webhook_manager.delete = MagicMock(
            side_effect=Exception("delete error"))
        resp = await self.client.delete(
            "/api/v1/webhooks/wh-99",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(resp.status, 500)

    async def test_issue_token_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_issue_token = AsyncMock(
            side_effect=ValueError("token exists"))
        resp = await self.client.post(
            "/api/v1/issue-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "w1", "name": "TC", "symbol": "TC",
                  "decimals": 8, "max_supply": 100},
        )
        self.assertEqual(resp.status, 400)

    async def test_issue_token_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_issue_token = AsyncMock(
            side_effect=Exception("issue error"))
        resp = await self.client.post(
            "/api/v1/issue-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "w1", "name": "TC", "symbol": "TC",
                  "decimals": 8, "max_supply": 100},
        )
        self.assertEqual(resp.status, 500)

    async def test_mint_token_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_mint_token = AsyncMock(
            side_effect=ValueError("over max supply"))
        resp = await self.client.post(
            "/api/v1/mint-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "w1", "token_id": "t1",
                  "recipient": "r1", "amount": 100},
        )
        self.assertEqual(resp.status, 400)

    async def test_mint_token_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_mint_token = AsyncMock(
            side_effect=Exception("mint error"))
        resp = await self.client.post(
            "/api/v1/mint-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "w1", "token_id": "t1",
                  "recipient": "r1", "amount": 100},
        )
        self.assertEqual(resp.status, 500)

    async def test_transfer_token_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_transfer_token = AsyncMock(
            side_effect=ValueError("insufficient token balance"))
        resp = await self.client.post(
            "/api/v1/transfer-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "w1", "token_id": "t1",
                  "recipient": "r1", "amount": 50},
        )
        self.assertEqual(resp.status, 400)

    async def test_transfer_token_internal_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_transfer_token = AsyncMock(
            side_effect=Exception("transfer error"))
        resp = await self.client.post(
            "/api/v1/transfer-token",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet": "w1", "token_id": "t1",
                  "recipient": "r1", "amount": 50},
        )
        self.assertEqual(resp.status, 500)

    async def test_notarize_non_string_metadata(self):
        resp = await self.client.post(
            "/api/v1/notarize",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "document_hash": "h1",
                  "metadata": 123},
        )
        self.assertEqual(resp.status, 400)

    async def test_store_non_string_cid(self):
        resp = await self.client.post(
            "/api/v1/store",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "document_hash": "h1",
                  "cid": 123},
        )
        self.assertEqual(resp.status, 400)

    async def test_store_non_string_metadata(self):
        resp = await self.client.post(
            "/api/v1/store",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "document_hash": "h1",
                  "cid": "qm1", "metadata": 123},
        )
        self.assertEqual(resp.status, 400)

    async def test_share_non_string_encryption_pk(self):
        resp = await self.client.post(
            "/api/v1/share",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"wallet_address": "w1", "recipient_address": "w2",
                  "cid": "qm1", "recipient_encryption_pk": 123,
                  "expires": 0},
        )
        self.assertEqual(resp.status, 400)

    async def test_verify_value_error(self):
        from unittest.mock import AsyncMock
        node = self.app["_mock_node"]
        node._rpc_verify_document = AsyncMock(
            side_effect=ValueError("bad hash"))
        resp = await self.client.post(
            "/api/v1/verify",
            json={"document_hash": "h1"},
        )
        self.assertEqual(resp.status, 400)

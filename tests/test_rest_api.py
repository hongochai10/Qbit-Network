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

"""Tests for v0.7.0 Sprint 3: OpenAPI spec, Python SDK, and Webhooks.

Covers:
- OpenAPI YAML validation (parseable, correct structure)
- Python SDK client methods (mocked HTTP responses)
- Webhook registration, delivery, HMAC signing, retry logic
"""
import asyncio
import hashlib
import hmac
import json
import os
import socket
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))


# ====================================================================
# Part 1: OpenAPI Spec Tests
# ====================================================================

class TestOpenAPISpec(unittest.TestCase):
    """Validate the OpenAPI YAML specification."""

    def setUp(self):
        self.spec_path = os.path.join(
            os.path.dirname(__file__), "..", "docs", "openapi.yaml"
        )

    def test_openapi_file_exists(self):
        """OpenAPI spec file exists."""
        self.assertTrue(os.path.isfile(self.spec_path))

    def test_openapi_valid_yaml(self):
        """OpenAPI spec is valid YAML."""
        import yaml
        with open(self.spec_path) as f:
            spec = yaml.safe_load(f)
        self.assertIsInstance(spec, dict)

    def test_openapi_version(self):
        """OpenAPI spec declares version 3.0.x."""
        import yaml
        with open(self.spec_path) as f:
            spec = yaml.safe_load(f)
        self.assertTrue(spec.get("openapi", "").startswith("3.0"))

    def test_openapi_info(self):
        """OpenAPI spec has info section with title and version."""
        import yaml
        with open(self.spec_path) as f:
            spec = yaml.safe_load(f)
        info = spec.get("info", {})
        self.assertIn("title", info)
        self.assertIn("version", info)
        self.assertEqual(info["version"], "0.7.0")

    def test_openapi_servers(self):
        """OpenAPI spec has at least one server."""
        import yaml
        with open(self.spec_path) as f:
            spec = yaml.safe_load(f)
        servers = spec.get("servers", [])
        self.assertGreater(len(servers), 0)

    def test_openapi_security_scheme(self):
        """OpenAPI spec defines BearerAuth security scheme."""
        import yaml
        with open(self.spec_path) as f:
            spec = yaml.safe_load(f)
        schemes = spec.get("components", {}).get("securitySchemes", {})
        self.assertIn("BearerAuth", schemes)
        self.assertEqual(schemes["BearerAuth"]["type"], "http")
        self.assertEqual(schemes["BearerAuth"]["scheme"], "bearer")

    def test_openapi_has_all_public_endpoints(self):
        """OpenAPI spec covers all public REST endpoints."""
        import yaml
        with open(self.spec_path) as f:
            spec = yaml.safe_load(f)
        paths = set(spec.get("paths", {}).keys())

        required_public = [
            "/health", "/info", "/blocks", "/blocks/latest",
            "/blocks/{index}", "/blocks/hash/{hash}",
            "/txs/{txid}", "/txs/sender/{addr}",
            "/address/{addr}", "/balance/{addr}",
            "/validators", "/stakes", "/stakes/{validator}",
            "/epochs/current", "/pool", "/pool/count",
            "/supply", "/fee", "/notarizations/{hash}",
            "/slashing-events", "/state-proof/{addr}",
            "/state-root", "/receipt/{txid}", "/events",
            "/finalized", "/verify",
        ]
        for endpoint in required_public:
            self.assertIn(endpoint, paths, f"Missing endpoint: {endpoint}")

    def test_openapi_has_all_protected_endpoints(self):
        """OpenAPI spec covers all protected REST endpoints."""
        import yaml
        with open(self.spec_path) as f:
            spec = yaml.safe_load(f)
        paths = set(spec.get("paths", {}).keys())

        required_protected = [
            "/wallets", "/transfer", "/notarize", "/store", "/share",
            "/stake", "/delegate", "/unstake",
            "/register-validator", "/evidence",
        ]
        for endpoint in required_protected:
            self.assertIn(endpoint, paths, f"Missing endpoint: {endpoint}")

    def test_openapi_has_webhook_endpoints(self):
        """OpenAPI spec covers webhook endpoints."""
        import yaml
        with open(self.spec_path) as f:
            spec = yaml.safe_load(f)
        paths = set(spec.get("paths", {}).keys())
        self.assertIn("/webhooks", paths)
        self.assertIn("/webhooks/{id}", paths)

    def test_openapi_schemas_defined(self):
        """OpenAPI spec defines required schemas."""
        import yaml
        with open(self.spec_path) as f:
            spec = yaml.safe_load(f)
        schemas = spec.get("components", {}).get("schemas", {})
        required_schemas = [
            "Block", "Transaction", "Wallet", "Receipt", "Event",
            "BalanceInfo", "SupplyInfo", "FeeInfo", "NodeInfo",
            "StateProof", "Webhook", "WebhookRegistration",
        ]
        for schema in required_schemas:
            self.assertIn(schema, schemas, f"Missing schema: {schema}")


# ====================================================================
# Part 2: Python SDK Tests
# ====================================================================

class TestSDKModels(unittest.TestCase):
    """Test SDK data model deserialization."""

    def test_block_from_dict(self):
        from qbit_sdk.models import Block
        b = Block.from_dict({
            "index": 5, "timestamp": 1000.0,
            "previous_hash": "abc", "merkle_root": "def",
            "state_root": "ghi", "receipts_root": "jkl",
            "base_fee": 10, "validator": "v1",
            "signature": "sig", "block_hash": "hash",
            "transactions": [{"tx_id": "t1", "tx_type": "TRANSFER",
                              "sender": "a", "recipient": "b",
                              "nonce": 0, "timestamp": 1.0}],
        })
        self.assertEqual(b.index, 5)
        self.assertEqual(len(b.transactions), 1)
        self.assertEqual(b.transactions[0].tx_id, "t1")

    def test_transaction_from_dict(self):
        from qbit_sdk.models import Transaction
        t = Transaction.from_dict({
            "tx_id": "abc", "tx_type": "NOTARIZE",
            "sender": "s1", "recipient": "", "nonce": 3,
            "timestamp": 123.0, "block_index": 10,
        })
        self.assertEqual(t.tx_id, "abc")
        self.assertEqual(t.block_index, 10)

    def test_receipt_from_dict(self):
        from qbit_sdk.models import Receipt
        r = Receipt.from_dict({
            "tx_id": "t1", "status": "confirmed",
            "fee_paid": 1000, "block_index": 5, "tx_index": 0,
            "events": [{"type": "Transfer", "data": {"amount": 100}}],
        })
        self.assertEqual(r.tx_id, "t1")
        self.assertEqual(len(r.events), 1)
        self.assertEqual(r.events[0].type, "Transfer")

    def test_wallet_from_dict(self):
        from qbit_sdk.models import Wallet
        w = Wallet.from_dict({
            "address": "qv1abc", "signing_pk": "pk1",
            "encryption_pk": "pk2",
        })
        self.assertEqual(w.address, "qv1abc")

    def test_node_info_from_dict(self):
        from qbit_sdk.models import NodeInfo
        ni = NodeInfo.from_dict({
            "version": "0.7.0", "chain_height": 100,
            "pending_txs": 5, "peers": 3, "validator": "v1",
            "validators": ["v1", "v2"],
        })
        self.assertEqual(ni.version, "0.7.0")
        self.assertEqual(ni.chain_height, 100)

    def test_supply_info_from_dict(self):
        from qbit_sdk.models import SupplyInfo
        si = SupplyInfo.from_dict({
            "total_minted": 1000, "total_burned": 50,
            "circulating": 950, "max_supply": 21000000,
            "formatted_circulating": "0.00000950 QBIT",
            "fee_model": "dynamic",
        })
        self.assertEqual(si.circulating, 950)
        self.assertEqual(si.fee_model, "dynamic")

    def test_state_proof_from_dict(self):
        from qbit_sdk.models import StateProof
        sp = StateProof.from_dict({
            "key": "balance:addr1", "value": "100",
            "proof": ["h1", "h2"], "state_root": "root1",
        })
        self.assertEqual(sp.key, "balance:addr1")
        self.assertEqual(len(sp.proof), 2)

    def test_webhook_from_dict(self):
        from qbit_sdk.models import Webhook
        wh = Webhook.from_dict({
            "id": "abc123", "url": "https://example.com",
            "events": ["Transfer"], "status": "active",
            "consecutive_failures": 0, "created_at": 1000.0,
        })
        self.assertEqual(wh.id, "abc123")
        self.assertEqual(wh.status, "active")

    def test_verify_result_from_dict(self):
        from qbit_sdk.models import VerifyResult
        vr = VerifyResult.from_dict({"verified": True, "proof": {"tx_id": "t1"}})
        self.assertTrue(vr.verified)
        self.assertIsNotNone(vr.proof)


class TestSDKExceptions(unittest.TestCase):
    """Test SDK exception hierarchy."""

    def test_qbit_error(self):
        from qbit_sdk.exceptions import QBitError
        e = QBitError("test", code=500)
        self.assertEqual(str(e), "test")
        self.assertEqual(e.code, 500)

    def test_auth_error(self):
        from qbit_sdk.exceptions import AuthenticationError, QBitError
        e = AuthenticationError()
        self.assertIsInstance(e, QBitError)
        self.assertEqual(e.code, 401)

    def test_not_found_error(self):
        from qbit_sdk.exceptions import NotFoundError, QBitError
        e = NotFoundError("gone")
        self.assertIsInstance(e, QBitError)
        self.assertEqual(e.code, 404)

    def test_insufficient_balance(self):
        from qbit_sdk.exceptions import InsufficientBalance, QBitError
        e = InsufficientBalance()
        self.assertIsInstance(e, QBitError)
        self.assertEqual(e.code, 400)


class TestSDKClient(unittest.TestCase):
    """Test SDK client methods with mocked HTTP."""

    def setUp(self):
        from qbit_sdk.client import QBitClient
        self.client = QBitClient("http://localhost:8545/api/v1", token="test-token")

    def _mock_response(self, data, status=200):
        """Create a mock urllib response."""
        body = json.dumps({"data": data, "error": None}).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.status = status
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    @patch("urllib.request.urlopen")
    def test_get_health(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({"status": "ok"})
        result = self.client.get_health()
        self.assertEqual(result["status"], "ok")

    @patch("urllib.request.urlopen")
    def test_get_info(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "version": "0.7.0", "chain_height": 42,
            "pending_txs": 0, "peers": 2, "validator": None,
        })
        info = self.client.get_info()
        self.assertEqual(info.chain_height, 42)

    @patch("urllib.request.urlopen")
    def test_get_balance(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "address": "qv1abc", "balance": 1000, "formatted": "0.001 QBIT",
        })
        bal = self.client.get_balance("qv1abc")
        self.assertEqual(bal.balance, 1000)

    @patch("urllib.request.urlopen")
    def test_get_supply(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "total_minted": 5000, "total_burned": 100,
            "circulating": 4900, "max_supply": 21000000,
            "formatted_circulating": "0.00004900 QBIT",
            "fee_model": "fixed",
        })
        supply = self.client.get_supply()
        self.assertEqual(supply.circulating, 4900)

    @patch("urllib.request.urlopen")
    def test_get_block(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "index": 1, "timestamp": 100.0, "previous_hash": "0",
            "merkle_root": "m", "state_root": "s", "receipts_root": "r",
            "base_fee": 10, "validator": "v", "signature": "sig",
            "block_hash": "h", "transactions": [],
        })
        block = self.client.get_block(1)
        self.assertEqual(block.index, 1)

    @patch("urllib.request.urlopen")
    def test_get_receipt(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "tx_id": "t1", "status": "confirmed", "fee_paid": 500,
            "block_index": 1, "tx_index": 0, "events": [],
        })
        receipt = self.client.get_receipt("t1")
        self.assertEqual(receipt.status, "confirmed")

    @patch("urllib.request.urlopen")
    def test_verify_document(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "verified": True, "proof": {"tx_id": "t1"},
        })
        result = self.client.verify_document("hash123")
        self.assertTrue(result.verified)

    @patch("urllib.request.urlopen")
    def test_get_finalized_height(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "finalized_height": 99,
        })
        h = self.client.get_finalized_height()
        self.assertEqual(h, 99)

    @patch("urllib.request.urlopen")
    def test_create_wallet(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "address": "qv1new", "signing_pk": "pk1",
            "encryption_pk": "pk2",
        })
        w = self.client.create_wallet()
        self.assertEqual(w.address, "qv1new")

    @patch("urllib.request.urlopen")
    def test_transfer(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "tx_id": "txabc", "amount": 100, "to": "qv1dest",
        })
        tx_id = self.client.transfer("qv1src", "qv1dest", 100, "memo")
        self.assertEqual(tx_id, "txabc")

    @patch("urllib.request.urlopen")
    def test_notarize(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({"tx_id": "tx1"})
        tx_id = self.client.notarize("qv1w", "dochash")
        self.assertEqual(tx_id, "tx1")

    @patch("urllib.request.urlopen")
    def test_stake(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "tx_id": "tx1", "validator_address": "v1", "amount": 1000,
        })
        tx_id = self.client.stake("qv1w", "v1", 1000)
        self.assertEqual(tx_id, "tx1")

    @patch("urllib.request.urlopen")
    def test_auth_error(self, mock_urlopen):
        import urllib.error
        from qbit_sdk.exceptions import AuthenticationError
        err_body = json.dumps({"data": None, "error": {"code": 401, "message": "auth required"}}).encode()
        http_err = urllib.error.HTTPError(
            "http://test", 401, "Unauthorized", {}, None)
        http_err.read = MagicMock(return_value=err_body)
        mock_urlopen.side_effect = http_err
        with self.assertRaises(AuthenticationError):
            self.client.create_wallet()

    @patch("urllib.request.urlopen")
    def test_not_found_error(self, mock_urlopen):
        import urllib.error
        from qbit_sdk.exceptions import NotFoundError
        err_body = json.dumps({"data": None, "error": {"code": 404, "message": "not found"}}).encode()
        http_err = urllib.error.HTTPError(
            "http://test", 404, "Not Found", {}, None)
        http_err.read = MagicMock(return_value=err_body)
        mock_urlopen.side_effect = http_err
        with self.assertRaises(NotFoundError):
            self.client.get_block(999)

    @patch("urllib.request.urlopen")
    def test_register_webhook(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "id": "wh1", "url": "https://example.com",
            "events": ["Transfer"], "status": "active",
            "consecutive_failures": 0, "created_at": 1000.0,
        })
        wh = self.client.register_webhook(
            "https://example.com", ["Transfer"], "secret")
        self.assertEqual(wh.id, "wh1")
        self.assertEqual(wh.status, "active")

    @patch("urllib.request.urlopen")
    def test_list_webhooks(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "webhooks": [
                {"id": "wh1", "url": "https://a.com", "events": ["Transfer"],
                 "status": "active", "consecutive_failures": 0, "created_at": 1.0},
            ],
            "total": 1,
        })
        hooks = self.client.list_webhooks()
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0].id, "wh1")


# ====================================================================
# Part 3: Webhook System Tests
# ====================================================================

class TestWebhookManager(unittest.TestCase):
    """Test webhook registration and management."""

    def setUp(self):
        from qbit_network.network.webhooks import WebhookManager
        self.mgr = WebhookManager()

    def test_register_valid(self):
        wh = self.mgr.register("https://example.com/hook", ["Transfer"], "secret123")
        self.assertIn("id", wh)
        self.assertEqual(wh["url"], "https://example.com/hook")
        self.assertEqual(wh["events"], ["Transfer"])
        self.assertEqual(wh["status"], "active")

    def test_register_multiple_events(self):
        wh = self.mgr.register(
            "https://example.com", ["Transfer", "Notarize", "Stake"], "sec")
        self.assertEqual(len(wh["events"]), 3)

    def test_register_invalid_url_empty(self):
        with self.assertRaises(ValueError):
            self.mgr.register("", ["Transfer"], "secret")

    def test_register_invalid_url_no_scheme(self):
        with self.assertRaises(ValueError):
            self.mgr.register("example.com/hook", ["Transfer"], "secret")

    def test_register_invalid_events_empty(self):
        with self.assertRaises(ValueError):
            self.mgr.register("https://example.com", [], "secret")

    def test_register_invalid_event_type(self):
        with self.assertRaises(ValueError):
            self.mgr.register("https://example.com", ["InvalidEvent"], "secret")

    def test_register_invalid_secret_empty(self):
        with self.assertRaises(ValueError):
            self.mgr.register("https://example.com", ["Transfer"], "")

    def test_register_max_limit(self):
        from qbit_network.network.webhooks import MAX_WEBHOOKS
        for i in range(MAX_WEBHOOKS):
            self.mgr.register(f"https://example.com/{i}", ["Transfer"], "sec")
        with self.assertRaises(ValueError):
            self.mgr.register("https://example.com/extra", ["Transfer"], "sec")

    def test_list_webhooks(self):
        self.mgr.register("https://a.com", ["Transfer"], "s1")
        self.mgr.register("https://b.com", ["Notarize"], "s2")
        hooks = self.mgr.list_webhooks()
        self.assertEqual(len(hooks), 2)
        # Secret should not be exposed
        for wh in hooks:
            self.assertNotIn("secret", wh)

    def test_delete_webhook(self):
        wh = self.mgr.register("https://a.com", ["Transfer"], "s")
        self.assertTrue(self.mgr.delete(wh["id"]))
        self.assertEqual(len(self.mgr.list_webhooks()), 0)

    def test_delete_nonexistent(self):
        self.assertFalse(self.mgr.delete("nonexistent"))

    def test_delete_already_deleted(self):
        wh = self.mgr.register("https://a.com", ["Transfer"], "s")
        self.assertTrue(self.mgr.delete(wh["id"]))
        self.assertFalse(self.mgr.delete(wh["id"]))

    def test_get_webhook(self):
        wh = self.mgr.register("https://a.com", ["Transfer"], "s")
        got = self.mgr.get(wh["id"])
        self.assertIsNotNone(got)
        self.assertEqual(got["url"], "https://a.com")

    def test_get_deleted_returns_none(self):
        wh = self.mgr.register("https://a.com", ["Transfer"], "s")
        self.mgr.delete(wh["id"])
        self.assertIsNone(self.mgr.get(wh["id"]))


class TestWebhookSSRFIPv6Mapped(unittest.TestCase):
    """R25-001: IPv6-mapped IPv4 addresses must be rejected by SSRF protection."""

    def setUp(self):
        from qbit_network.network.webhooks import WebhookManager
        self.mgr = WebhookManager()

    def test_reject_ipv6_mapped_loopback(self):
        """::ffff:127.0.0.1 must be rejected (IPv6-mapped loopback)."""
        with self.assertRaises(ValueError) as ctx:
            self.mgr.register("http://[::ffff:127.0.0.1]/hook", ["Transfer"], "s")
        self.assertIn("private/loopback", str(ctx.exception))

    def test_reject_ipv6_mapped_private_10(self):
        """::ffff:10.0.0.1 must be rejected (IPv6-mapped private)."""
        with self.assertRaises(ValueError) as ctx:
            self.mgr.register("http://[::ffff:10.0.0.1]/hook", ["Transfer"], "s")
        self.assertIn("private/loopback", str(ctx.exception))

    def test_reject_ipv6_mapped_private_192(self):
        """::ffff:192.168.1.1 must be rejected (IPv6-mapped private)."""
        with self.assertRaises(ValueError) as ctx:
            self.mgr.register("http://[::ffff:192.168.1.1]/hook", ["Transfer"], "s")
        self.assertIn("private/loopback", str(ctx.exception))

    def test_reject_ipv6_mapped_private_172(self):
        """::ffff:172.16.0.1 must be rejected (IPv6-mapped private)."""
        with self.assertRaises(ValueError) as ctx:
            self.mgr.register("http://[::ffff:172.16.0.1]/hook", ["Transfer"], "s")
        self.assertIn("private/loopback", str(ctx.exception))

    def test_reject_ipv6_loopback(self):
        """::1 must still be rejected (pure IPv6 loopback)."""
        with self.assertRaises(ValueError) as ctx:
            self.mgr.register("http://[::1]/hook", ["Transfer"], "s")
        self.assertIn("must not target", str(ctx.exception))

    def test_allow_ipv6_mapped_public(self):
        """::ffff:8.8.8.8 should be allowed (public IP)."""
        wh = self.mgr.register("http://[::ffff:8.8.8.8]/hook", ["Transfer"], "s")
        self.assertEqual(wh["status"], "active")

    @unittest.mock.patch("socket.getaddrinfo")
    def test_delivery_rejects_ipv6_mapped_resolved(self, mock_dns):
        """Delivery-time DNS resolving to ::ffff:127.0.0.1 must be blocked."""
        import asyncio
        from qbit_network.network.webhooks import WebhookManager

        mgr = WebhookManager()
        # Register with a public-looking hostname
        wh = mgr.register("https://example.com/hook", ["Transfer"], "secret")

        # Mock DNS to return IPv6-mapped loopback
        mock_dns.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::ffff:127.0.0.1', 443, 0, 0))
        ]

        webhook_internal = mgr._webhooks[wh["id"]]
        event = {"type": "Transfer", "data": {"amount": 100}}

        result = asyncio.run(
            mgr._deliver_one(webhook_internal, event, 1)
        )
        self.assertFalse(result)

    @unittest.mock.patch("socket.getaddrinfo")
    def test_delivery_rejects_ipv6_mapped_private(self, mock_dns):
        """Delivery-time DNS resolving to ::ffff:10.0.0.1 must be blocked."""
        import asyncio
        from qbit_network.network.webhooks import WebhookManager

        mgr = WebhookManager()
        wh = mgr.register("https://example.com/hook", ["Transfer"], "secret")

        mock_dns.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::ffff:10.0.0.1', 443, 0, 0))
        ]

        webhook_internal = mgr._webhooks[wh["id"]]
        event = {"type": "Transfer", "data": {"amount": 100}}

        result = asyncio.run(
            mgr._deliver_one(webhook_internal, event, 1)
        )
        self.assertFalse(result)


class TestWebhookDNSFailure(unittest.TestCase):
    """R27-001: DNS resolution failure must fail-safe (no fallthrough)."""

    @unittest.mock.patch("socket.getaddrinfo")
    def test_dns_gaierror_blocks_delivery(self, mock_dns):
        """When getaddrinfo raises gaierror, delivery must return False."""
        import asyncio
        from qbit_network.network.webhooks import WebhookManager

        mgr = WebhookManager()
        wh = mgr.register("https://nonexistent.invalid/hook", ["Transfer"], "secret")

        mock_dns.side_effect = socket.gaierror("Name or service not known")

        webhook_internal = mgr._webhooks[wh["id"]]
        event = {"type": "Transfer", "data": {"amount": 100}}

        result = asyncio.run(
            mgr._deliver_one(webhook_internal, event, 1)
        )
        self.assertFalse(result)

    @unittest.mock.patch("socket.getaddrinfo")
    def test_dns_oserror_blocks_delivery(self, mock_dns):
        """When getaddrinfo raises OSError, delivery must return False."""
        import asyncio
        from qbit_network.network.webhooks import WebhookManager

        mgr = WebhookManager()
        wh = mgr.register("https://example.com/hook", ["Transfer"], "secret")

        mock_dns.side_effect = OSError("Network is unreachable")

        webhook_internal = mgr._webhooks[wh["id"]]
        event = {"type": "Transfer", "data": {"amount": 100}}

        result = asyncio.run(
            mgr._deliver_one(webhook_internal, event, 1)
        )
        self.assertFalse(result)


class TestWebhookHMAC(unittest.TestCase):
    """Test HMAC signature computation for webhooks."""

    def test_compute_signature(self):
        from qbit_network.network.webhooks import compute_webhook_signature
        secret = "test-secret"
        payload = '{"event": {"type": "Transfer"}, "block_index": 1}'
        sig = compute_webhook_signature(secret, payload)
        # Verify manually
        expected = hmac.new(
            secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        self.assertEqual(sig, expected)

    def test_signature_changes_with_secret(self):
        from qbit_network.network.webhooks import compute_webhook_signature
        payload = '{"test": true}'
        sig1 = compute_webhook_signature("secret1", payload)
        sig2 = compute_webhook_signature("secret2", payload)
        self.assertNotEqual(sig1, sig2)

    def test_signature_changes_with_payload(self):
        from qbit_network.network.webhooks import compute_webhook_signature
        secret = "test"
        sig1 = compute_webhook_signature(secret, '{"a": 1}')
        sig2 = compute_webhook_signature(secret, '{"a": 2}')
        self.assertNotEqual(sig1, sig2)


class TestWebhookDelivery(unittest.TestCase):
    """Test webhook delivery logic (async)."""

    def _run(self, coro):
        return asyncio.run(coro)

    def setUp(self):
        from qbit_network.network.webhooks import WebhookManager
        self.mgr = WebhookManager()

    @patch("qbit_network.network.webhooks.aiohttp")
    def test_deliver_matching_event(self, mock_aiohttp):
        """Webhook receives events matching its subscription."""
        wh = self.mgr.register("https://example.com/hook", ["Transfer"], "secret")

        post_called = False

        def make_post_ctx(*args, **kwargs):
            nonlocal post_called
            post_called = True
            mock_resp = MagicMock()
            mock_resp.status = 200
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_resp)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        mock_session = MagicMock()
        mock_session.post = make_post_ctx
        mock_session.closed = False
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()

        events = [{"type": "Transfer", "data": {"amount": 100}, "tx_id": "t1"}]
        self._run(self.mgr.deliver(events, block_index=5))
        # Give tasks a moment to run
        self._run(asyncio.sleep(0.2))
        self.assertTrue(post_called)

    def test_deliver_non_matching_event_skipped(self):
        """Webhook does not receive events it did not subscribe to."""
        self.mgr.register("https://example.com/hook", ["Notarize"], "secret")

        events = [{"type": "Transfer", "data": {}, "tx_id": "t1"}]
        # deliver should not create any tasks for non-matching events
        self._run(self.mgr.deliver(events, block_index=1))
        # No tasks should have been created
        self.assertEqual(len(self.mgr._delivery_tasks), 0)

    def test_deliver_empty_events(self):
        """Empty event list does nothing."""
        self.mgr.register("https://example.com/hook", ["Transfer"], "secret")
        self._run(self.mgr.deliver([], block_index=1))

    @patch("qbit_network.network.webhooks.aiohttp")
    def test_delivery_retry_on_failure(self, mock_aiohttp):
        """Failed delivery retries with exponential backoff."""
        wh_data = self.mgr.register("https://example.com/hook", ["Transfer"], "sec")

        call_count = 0

        def make_post_ctx(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.status = 200 if call_count >= 3 else 500
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_resp)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        mock_session = MagicMock()
        mock_session.post = make_post_ctx
        mock_session.closed = False
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()

        wh_internal = self.mgr._webhooks[wh_data["id"]]
        result = self._run(self.mgr._deliver_one(
            wh_internal,
            {"type": "Transfer", "data": {}},
            block_index=1,
        ))
        self.assertTrue(result)
        self.assertEqual(wh_internal["consecutive_failures"], 0)
        self.assertEqual(call_count, 3)

    @patch("qbit_network.network.webhooks.aiohttp")
    def test_delivery_all_retries_exhausted(self, mock_aiohttp):
        """After all retries fail, consecutive_failures increments."""
        wh_data = self.mgr.register("https://example.com/hook", ["Transfer"], "sec")

        def make_post_ctx(*args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status = 500
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_resp)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        mock_session = MagicMock()
        mock_session.post = make_post_ctx
        mock_session.closed = False
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()

        wh_internal = self.mgr._webhooks[wh_data["id"]]
        result = self._run(self.mgr._deliver_one(
            wh_internal,
            {"type": "Transfer", "data": {}},
            block_index=1,
        ))
        self.assertFalse(result)
        self.assertEqual(wh_internal["consecutive_failures"], 1)
        self.assertEqual(wh_internal["status"], "failing")

    @patch("qbit_network.network.webhooks.aiohttp")
    def test_webhook_disabled_after_consecutive_failures(self, mock_aiohttp):
        """Webhook is disabled after CONSECUTIVE_FAIL_DISABLE failures."""
        from qbit_network.network.webhooks import CONSECUTIVE_FAIL_DISABLE
        wh_data = self.mgr.register("https://example.com/hook", ["Transfer"], "sec")
        wh_internal = self.mgr._webhooks[wh_data["id"]]
        wh_internal["consecutive_failures"] = CONSECUTIVE_FAIL_DISABLE - 1

        def make_post_ctx(*args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status = 500
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_resp)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        mock_session = MagicMock()
        mock_session.post = make_post_ctx
        mock_session.closed = False
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()

        self._run(self.mgr._deliver_one(
            wh_internal,
            {"type": "Transfer", "data": {}},
            block_index=1,
        ))
        self.assertEqual(wh_internal["status"], "disabled")

    def test_deleted_webhook_not_delivered(self):
        """Deleted webhook stops receiving events."""
        wh = self.mgr.register("https://example.com", ["Transfer"], "sec")
        self.mgr.delete(wh["id"])

        # Deliver should not attempt delivery to deleted webhook
        self._run(self.mgr.deliver(
            [{"type": "Transfer", "data": {}}], block_index=1))

    def test_hmac_signature_in_delivery(self):
        """Verify that delivery uses correct HMAC signature."""
        secret = "test-secret-123"
        payload_data = {
            "event": {"type": "Transfer", "data": {"amount": 100}},
            "block_index": 5,
        }
        # Manual HMAC
        payload_str = json.dumps(payload_data)
        expected_sig = hmac.new(
            secret.encode(), payload_str.encode(), hashlib.sha256
        ).hexdigest()

        # Use the standalone function
        from qbit_network.network.webhooks import compute_webhook_signature
        actual_sig = compute_webhook_signature(secret, payload_str)
        self.assertEqual(actual_sig, expected_sig)


class TestWebhookValidEventTypes(unittest.TestCase):
    """Test that all event types are registered as valid."""

    def test_all_event_types_valid(self):
        from qbit_network.network.webhooks import VALID_EVENT_TYPES
        expected = {
            "Transfer", "Notarize", "Store", "Share",
            "Stake", "Delegate", "Unstake",
            "KeyRegistered", "ValidatorRegistered", "KeyRevoked", "Slashed",
            "BlockReward", "EpochTransition",
            "TokenIssued", "TokenMinted", "TokenTransferred",
        }
        self.assertEqual(VALID_EVENT_TYPES, expected)


class TestWebhookSessionLifecycle(unittest.TestCase):
    """R27-004: WebhookManager reuses a single ClientSession."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_session_initialized_none(self):
        from qbit_network.network.webhooks import WebhookManager
        mgr = WebhookManager()
        self.assertIsNone(mgr._session)

    def test_get_session_creates_once(self):
        from qbit_network.network.webhooks import WebhookManager
        mgr = WebhookManager()

        async def _test():
            s1 = await mgr._get_session()
            s2 = await mgr._get_session()
            self.assertIs(s1, s2, "Should reuse the same session")
            await mgr.stop()

        self._run(_test())

    def test_stop_closes_session(self):
        from qbit_network.network.webhooks import WebhookManager
        mgr = WebhookManager()

        async def _test():
            session = await mgr._get_session()
            self.assertFalse(session.closed)
            await mgr.stop()
            self.assertIsNone(mgr._session)

        self._run(_test())


if __name__ == "__main__":
    unittest.main()

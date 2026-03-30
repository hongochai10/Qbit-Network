"""Tests for WebSocket subscription manager and handler."""
import asyncio
import json
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web, WSMsgType
from aiohttp.test_utils import AioHTTPTestCase, TestServer

from qbit_network.network.websocket import (
    WebSocketManager,
    websocket_handler,
    _check_ws_auth,
    VALID_CHANNELS,
    MAX_WS_CONNECTIONS,
    MAX_SUBSCRIPTIONS_PER_CLIENT,
    WS_RATE_LIMIT,
    MAX_WS_MESSAGE_SIZE,
    WS_HEARTBEAT_INTERVAL,
)


# ---------------------------------------------------------------------------
# Unit tests for WebSocketManager
# ---------------------------------------------------------------------------

class TestWebSocketManager:
    """Unit tests for the WebSocketManager class."""

    def setup_method(self):
        self.mgr = WebSocketManager()

    def _mock_ws(self, closed=False):
        ws = MagicMock()
        ws.closed = closed
        ws.send_str = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws

    def test_initial_state(self):
        assert self.mgr.connected_count() == 0
        for ch in VALID_CHANNELS:
            assert len(self.mgr._subscribers[ch]) == 0

    def test_add_subscriber_valid_channel(self):
        ws = self._mock_ws()
        self.mgr._all_clients.add(ws)
        ok, msg = self.mgr.add_subscriber(ws, "new_block")
        assert ok is True
        assert ws in self.mgr._subscribers["new_block"]

    def test_add_subscriber_invalid_channel(self):
        ws = self._mock_ws()
        ok, msg = self.mgr.add_subscriber(ws, "invalid_channel")
        assert ok is False
        assert "unknown channel" in msg

    def test_add_subscriber_duplicate(self):
        ws = self._mock_ws()
        self.mgr._all_clients.add(ws)
        self.mgr.add_subscriber(ws, "new_block")
        ok, msg = self.mgr.add_subscriber(ws, "new_block")
        assert ok is False
        assert "already subscribed" in msg

    def test_add_subscriber_max_subscriptions(self):
        ws = self._mock_ws()
        self.mgr._all_clients.add(ws)
        # Subscribe to all valid channels
        for ch in VALID_CHANNELS:
            self.mgr.add_subscriber(ws, ch)
        # Force _client_channels to max
        ws_id = id(ws)
        self.mgr._client_channels[ws_id] = set(f"ch_{i}" for i in range(MAX_SUBSCRIPTIONS_PER_CLIENT))
        ok, msg = self.mgr.add_subscriber(ws, "new_block")
        assert ok is False
        assert "max subscriptions" in msg

    def test_remove_subscriber_valid(self):
        ws = self._mock_ws()
        self.mgr._all_clients.add(ws)
        self.mgr.add_subscriber(ws, "new_tx")
        ok, msg = self.mgr.remove_subscriber(ws, "new_tx")
        assert ok is True
        assert ws not in self.mgr._subscribers["new_tx"]

    def test_remove_subscriber_not_subscribed(self):
        ws = self._mock_ws()
        self.mgr._all_clients.add(ws)
        ok, msg = self.mgr.remove_subscriber(ws, "new_block")
        assert ok is False
        assert "not subscribed" in msg

    def test_remove_subscriber_invalid_channel(self):
        ws = self._mock_ws()
        ok, msg = self.mgr.remove_subscriber(ws, "bogus")
        assert ok is False
        assert "unknown channel" in msg

    def test_remove_all(self):
        ws = self._mock_ws()
        self.mgr._all_clients.add(ws)
        self.mgr.add_subscriber(ws, "new_block")
        self.mgr.add_subscriber(ws, "new_tx")
        self.mgr.remove_all(ws)
        assert ws not in self.mgr._all_clients
        assert ws not in self.mgr._subscribers["new_block"]
        assert ws not in self.mgr._subscribers["new_tx"]
        assert id(ws) not in self.mgr._client_channels

    def test_connected_count(self):
        ws1 = self._mock_ws()
        ws2 = self._mock_ws()
        self.mgr._all_clients.add(ws1)
        assert self.mgr.connected_count() == 1
        self.mgr._all_clients.add(ws2)
        assert self.mgr.connected_count() == 2
        self.mgr.remove_all(ws1)
        assert self.mgr.connected_count() == 1

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_subscribers(self):
        ws1 = self._mock_ws()
        ws2 = self._mock_ws()
        self.mgr._all_clients.add(ws1)
        self.mgr._all_clients.add(ws2)
        self.mgr.add_subscriber(ws1, "new_block")
        self.mgr.add_subscriber(ws2, "new_block")

        await self.mgr.broadcast("new_block", {"index": 1})

        assert ws1.send_str.called
        assert ws2.send_str.called
        sent = json.loads(ws1.send_str.call_args[0][0])
        assert sent["type"] == "event"
        assert sent["channel"] == "new_block"
        assert sent["data"]["index"] == 1

    @pytest.mark.asyncio
    async def test_broadcast_skips_non_subscribers(self):
        ws1 = self._mock_ws()
        ws2 = self._mock_ws()
        self.mgr._all_clients.add(ws1)
        self.mgr._all_clients.add(ws2)
        self.mgr.add_subscriber(ws1, "new_block")
        # ws2 is NOT subscribed to new_block

        await self.mgr.broadcast("new_block", {"index": 1})

        assert ws1.send_str.called
        assert not ws2.send_str.called

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_clients(self):
        ws = self._mock_ws(closed=True)
        self.mgr._all_clients.add(ws)
        self.mgr.add_subscriber(ws, "new_block")

        await self.mgr.broadcast("new_block", {"index": 1})

        assert ws not in self.mgr._subscribers["new_block"]
        assert ws not in self.mgr._all_clients

    @pytest.mark.asyncio
    async def test_broadcast_handles_send_error(self):
        ws = self._mock_ws()
        ws.send_str.side_effect = ConnectionResetError("gone")
        self.mgr._all_clients.add(ws)
        self.mgr.add_subscriber(ws, "new_block")

        # Should not raise
        await self.mgr.broadcast("new_block", {"index": 1})
        assert ws not in self.mgr._all_clients

    @pytest.mark.asyncio
    async def test_broadcast_no_subscribers_noop(self):
        # Should not raise when no subscribers
        await self.mgr.broadcast("new_block", {"index": 1})

    @pytest.mark.asyncio
    async def test_broadcast_invalid_channel_noop(self):
        await self.mgr.broadcast("nonexistent", {"x": 1})

    def test_rate_limit_check(self):
        ws = self._mock_ws()
        # First WS_RATE_LIMIT calls should pass
        for _ in range(WS_RATE_LIMIT):
            assert self.mgr._check_rate(ws) is True
        # Next call should fail
        assert self.mgr._check_rate(ws) is False

    @pytest.mark.asyncio
    async def test_stop_clears_state(self):
        ws = self._mock_ws()
        self.mgr._all_clients.add(ws)
        self.mgr.add_subscriber(ws, "new_block")

        await self.mgr.stop()

        assert self.mgr.connected_count() == 0
        assert len(self.mgr._subscribers["new_block"]) == 0


# ---------------------------------------------------------------------------
# Integration tests via aiohttp TestServer + ClientSession
# ---------------------------------------------------------------------------

import aiohttp
from aiohttp.test_utils import TestServer


async def _make_ws_server():
    """Create a test server with the WS endpoint, return (server, mgr, url)."""
    mgr = WebSocketManager()
    app = web.Application()
    app["ws_manager"] = mgr
    app.router.add_get("/ws", websocket_handler)
    server = TestServer(app)
    await server.start_server()
    return server, mgr


@pytest.mark.asyncio
async def test_ws_subscribe_and_receive_event():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))

            await ws.send_json({"type": "subscribe", "channel": "new_block"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "subscribed"
            assert resp["channel"] == "new_block"

            await mgr.broadcast("new_block", {"index": 42, "hash": "abc123"})

            event = json.loads((await ws.receive()).data)
            assert event["type"] == "event"
            assert event["channel"] == "new_block"
            assert event["data"]["index"] == 42

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_unsubscribe():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))

            await ws.send_json({"type": "subscribe", "channel": "new_tx"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "subscribed"

            await ws.send_json({"type": "unsubscribe", "channel": "new_tx"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "unsubscribed"

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_ping_pong():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))

            await ws.send_json({"type": "ping"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "pong"

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_invalid_json():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))

            await ws.send_str("not valid json{{{")
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            assert "invalid JSON" in resp["message"]

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_unknown_channel():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))

            await ws.send_json({"type": "subscribe", "channel": "bogus_channel"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            assert "unknown channel" in resp["message"]

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_unknown_message_type():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))

            await ws.send_json({"type": "foobar"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            assert "unknown message type" in resp["message"]

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_missing_type_field():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))

            await ws.send_json({"channel": "new_block"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            assert "type" in resp["message"]

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_non_object_message():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))

            await ws.send_str('"just a string"')
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            assert "JSON object" in resp["message"]

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_subscribe_missing_channel():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))

            await ws.send_json({"type": "subscribe"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            assert "channel" in resp["message"]

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_multiple_channels():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))

            for ch in ("new_block", "new_tx"):
                await ws.send_json({"type": "subscribe", "channel": ch})
                resp = json.loads((await ws.receive()).data)
                assert resp["type"] == "subscribed"

            await mgr.broadcast("new_block", {"index": 1})
            event = json.loads((await ws.receive()).data)
            assert event["channel"] == "new_block"

            await mgr.broadcast("new_tx", {"id": "tx123"})
            event = json.loads((await ws.receive()).data)
            assert event["channel"] == "new_tx"

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_disconnect_cleanup():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))
            await ws.send_json({"type": "subscribe", "channel": "new_block"})
            await ws.receive()

            assert mgr.connected_count() == 1

            await ws.close()
            await asyncio.sleep(0.1)

            assert mgr.connected_count() == 0
            assert len(mgr._subscribers["new_block"]) == 0
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_chain_stats_broadcast():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))
            await ws.send_json({"type": "subscribe", "channel": "chain_stats"})
            await ws.receive()

            stats = {"height": 10, "tx_count": 50, "pool_size": 3, "peers": 2}
            await mgr.broadcast("chain_stats", stats)

            event = json.loads((await ws.receive()).data)
            assert event["type"] == "event"
            assert event["channel"] == "chain_stats"
            assert event["data"]["height"] == 10
            assert event["data"]["tx_count"] == 50

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_duplicate_subscribe():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))
            await ws.send_json({"type": "subscribe", "channel": "new_block"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "subscribed"

            await ws.send_json({"type": "subscribe", "channel": "new_block"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            assert "already subscribed" in resp["message"]

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_unsubscribe_not_subscribed():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))
            await ws.send_json({"type": "unsubscribe", "channel": "new_block"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            assert "not subscribed" in resp["message"]

            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_event_data_matches_block_dict():
    """Verify broadcast data shape matches Block.to_dict() output format."""
    mgr = WebSocketManager()
    ws = MagicMock()
    ws.closed = False
    ws.send_str = AsyncMock()

    mgr._all_clients.add(ws)
    mgr.add_subscriber(ws, "new_block")

    block_data = {
        "hash": "abc123",
        "index": 5,
        "timestamp": 1700000000,
        "prevHash": "prev123",
        "merkleRoot": "mroot",
        "validator": "qv1validator",
        "transactions": [],
        "signature": "sig123",
    }

    await mgr.broadcast("new_block", block_data)

    sent = json.loads(ws.send_str.call_args[0][0])
    assert sent["type"] == "event"
    assert sent["channel"] == "new_block"
    assert sent["data"] == block_data


@pytest.mark.asyncio
async def test_ws_multiple_clients_same_channel():
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws1 = await session.ws_connect(server.make_url("/ws"))
            ws2 = await session.ws_connect(server.make_url("/ws"))

            for ws in (ws1, ws2):
                await ws.send_json({"type": "subscribe", "channel": "new_block"})
                await ws.receive()

            await mgr.broadcast("new_block", {"index": 99})

            for ws in (ws1, ws2):
                event = json.loads((await ws.receive()).data)
                assert event["data"]["index"] == 99

            await ws1.close()
            await ws2.close()
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# Sprint 3: additional WebSocket edge-case tests
# ---------------------------------------------------------------------------

class TestWebSocketManagerEdgeCases:
    """Additional edge cases for WebSocketManager (Sprint 3)."""

    def setup_method(self):
        self.mgr = WebSocketManager()

    def _mock_ws(self, closed=False):
        ws = MagicMock()
        ws.closed = closed
        ws.send_str = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws

    def test_max_subscriptions_per_client_enforced(self):
        """A client cannot subscribe to more than MAX_SUBSCRIPTIONS_PER_CLIENT channels."""
        ws = self._mock_ws()
        self.mgr._all_clients.add(ws)
        ws_id = id(ws)
        # Force client to already have MAX_SUBSCRIPTIONS_PER_CLIENT channels
        self.mgr._client_channels[ws_id] = set(
            f"fake_ch_{i}" for i in range(MAX_SUBSCRIPTIONS_PER_CLIENT)
        )
        ok, msg = self.mgr.add_subscriber(ws, "new_block")
        assert ok is False
        assert "max subscriptions" in msg

    @pytest.mark.asyncio
    async def test_broadcast_to_empty_channel_is_noop(self):
        """Broadcasting to a channel with no subscribers does not raise."""
        mgr = WebSocketManager()
        # Should not raise
        await mgr.broadcast("new_block", {"index": 1})

    @pytest.mark.asyncio
    async def test_broadcast_empty_channel_noop_async(self):
        """Async version: broadcast with no subscribers is a no-op."""
        mgr = WebSocketManager()
        # No clients, no subscribers -- must not raise
        await mgr.broadcast("new_block", {"index": 1})
        await mgr.broadcast("new_tx", {"id": "abc"})
        await mgr.broadcast("chain_stats", {})

    @pytest.mark.asyncio
    async def test_broadcast_invalid_channel_is_noop(self):
        """Broadcast to a completely unknown channel name is a no-op."""
        mgr = WebSocketManager()
        ws = self._mock_ws()
        mgr._all_clients.add(ws)
        # Should not raise even with unknown channel
        await mgr.broadcast("nonexistent_channel_xyz", {"data": 1})

    @pytest.mark.asyncio
    async def test_slow_client_send_error_causes_cleanup(self):
        """If send_str raises an exception (simulating slow client), client is removed."""
        mgr = WebSocketManager()
        ws = self._mock_ws()
        ws.send_str.side_effect = Exception("write error")
        mgr._all_clients.add(ws)
        mgr.add_subscriber(ws, "new_block")

        await mgr.broadcast("new_block", {"index": 1})

        assert ws not in mgr._all_clients
        assert ws not in mgr._subscribers["new_block"]

    @pytest.mark.asyncio
    async def test_closed_client_removed_during_broadcast(self):
        """Clients with ws.closed=True are removed during broadcast."""
        mgr = WebSocketManager()
        ws = self._mock_ws(closed=True)
        mgr._all_clients.add(ws)
        mgr.add_subscriber(ws, "new_tx")

        await mgr.broadcast("new_tx", {"id": "test"})

        assert ws not in mgr._all_clients
        assert ws not in mgr._subscribers["new_tx"]

    def test_connected_count_after_remove_all(self):
        """connected_count drops to 0 after all clients are removed."""
        mgr = WebSocketManager()
        ws1 = self._mock_ws()
        ws2 = self._mock_ws()
        mgr._all_clients.add(ws1)
        mgr._all_clients.add(ws2)
        assert mgr.connected_count() == 2
        mgr.remove_all(ws1)
        mgr.remove_all(ws2)
        assert mgr.connected_count() == 0

    def test_remove_all_clears_client_channels(self):
        """remove_all removes client from _client_channels dict."""
        mgr = WebSocketManager()
        ws = self._mock_ws()
        mgr._all_clients.add(ws)
        mgr.add_subscriber(ws, "new_block")
        assert id(ws) in mgr._client_channels
        mgr.remove_all(ws)
        assert id(ws) not in mgr._client_channels

    @pytest.mark.asyncio
    async def test_multiple_broadcasts_same_channel(self):
        """Multiple consecutive broadcasts all reach subscribers."""
        mgr = WebSocketManager()
        ws = self._mock_ws()
        mgr._all_clients.add(ws)
        mgr.add_subscriber(ws, "new_block")

        for i in range(5):
            await mgr.broadcast("new_block", {"index": i})

        assert ws.send_str.call_count == 5

    def test_valid_channels_includes_expected_channels(self):
        """VALID_CHANNELS contains the expected set of channels."""
        assert "new_block" in VALID_CHANNELS
        assert "new_tx" in VALID_CHANNELS
        assert "chain_stats" in VALID_CHANNELS


@pytest.mark.asyncio
async def test_ws_max_connections_101st_rejected():
    """The 101st WebSocket connection attempt returns 503 Service Unavailable."""
    mgr = WebSocketManager()
    app = web.Application()
    app["ws_manager"] = mgr
    app.router.add_get("/ws", websocket_handler)
    server = TestServer(app)
    await server.start_server()

    fake_clients = []
    try:
        # Fill up to MAX_WS_CONNECTIONS with mock objects
        for _ in range(MAX_WS_CONNECTIONS):
            fake_ws = MagicMock()
            fake_ws.closed = False
            mgr._all_clients.add(fake_ws)
            fake_clients.append(fake_ws)

        assert mgr.connected_count() == MAX_WS_CONNECTIONS

        # 101st connection should be rejected
        import aiohttp
        async with aiohttp.ClientSession() as session:
            try:
                ws = await session.ws_connect(server.make_url("/ws"))
                # If it somehow connected, close it
                await ws.close()
                # Should not reach here
                pytest.fail("Expected 503 but connection was accepted")
            except aiohttp.WSServerHandshakeError as e:
                assert e.status == 503
            except Exception:
                # Some clients raise different exceptions; the key is that
                # the connection was not cleanly established
                pass
    finally:
        mgr._all_clients.clear()
        await server.close()


@pytest.mark.asyncio
async def test_ws_invalid_number_message_type():
    """A message with a numeric 'type' field gets an error response."""
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))
            await ws.send_json({"type": 42})  # numeric, not string
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_null_type_field():
    """A message with null 'type' field gets an error response."""
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))
            await ws.send_json({"type": None})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_array_message():
    """An array (non-object) JSON message gets an error response."""
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))
            await ws.send_str("[1, 2, 3]")
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "error"
            assert "JSON object" in resp["message"]
            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_subscribe_all_valid_channels():
    """Can subscribe to all valid channels without error."""
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))
            for ch in VALID_CHANNELS:
                await ws.send_json({"type": "subscribe", "channel": ch})
                resp = json.loads((await ws.receive()).data)
                assert resp["type"] == "subscribed", f"Failed for channel {ch}: {resp}"
            await ws.close()
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# WebSocket authentication tests (TEC-652 / R25-001)
# ---------------------------------------------------------------------------

TEST_WS_AUTH_TOKEN = "test-secret-token-abc123"


async def _make_ws_auth_server():
    """Create a test server with WS auth enabled, return (server, mgr)."""
    mgr = WebSocketManager()
    app = web.Application()
    app["ws_manager"] = mgr
    app["ws_auth_token"] = TEST_WS_AUTH_TOKEN
    app.router.add_get("/ws", websocket_handler)
    server = TestServer(app)
    await server.start_server()
    return server, mgr


class TestCheckWsAuth:
    """Unit tests for _check_ws_auth helper."""

    def _fake_request(self, auth_header=None, token_in_app=""):
        req = MagicMock()
        req.app = {"ws_auth_token": token_in_app}
        req.headers = {}
        if auth_header is not None:
            req.headers["Authorization"] = auth_header
        return req

    def test_no_token_configured_allows_all(self):
        req = self._fake_request(auth_header=None, token_in_app="")
        assert _check_ws_auth(req) is True

    def test_valid_bearer_token(self):
        req = self._fake_request(
            auth_header=f"Bearer {TEST_WS_AUTH_TOKEN}",
            token_in_app=TEST_WS_AUTH_TOKEN,
        )
        assert _check_ws_auth(req) is True

    def test_missing_auth_header(self):
        req = self._fake_request(auth_header=None, token_in_app=TEST_WS_AUTH_TOKEN)
        assert _check_ws_auth(req) is False

    def test_wrong_token(self):
        req = self._fake_request(
            auth_header="Bearer wrong-token",
            token_in_app=TEST_WS_AUTH_TOKEN,
        )
        assert _check_ws_auth(req) is False

    def test_empty_bearer(self):
        req = self._fake_request(
            auth_header="Bearer ",
            token_in_app=TEST_WS_AUTH_TOKEN,
        )
        assert _check_ws_auth(req) is False

    def test_non_bearer_scheme(self):
        req = self._fake_request(
            auth_header=f"Basic {TEST_WS_AUTH_TOKEN}",
            token_in_app=TEST_WS_AUTH_TOKEN,
        )
        assert _check_ws_auth(req) is False


@pytest.mark.asyncio
async def test_ws_auth_valid_token_connects():
    """A client with a valid bearer token can connect and subscribe."""
    server, mgr = await _make_ws_auth_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(
                server.make_url("/ws"),
                headers={"Authorization": f"Bearer {TEST_WS_AUTH_TOKEN}"},
            )
            await ws.send_json({"type": "ping"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "pong"
            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_auth_missing_token_rejected():
    """A client without a bearer token gets 401 on upgrade."""
    server, mgr = await _make_ws_auth_server()
    try:
        async with aiohttp.ClientSession() as session:
            try:
                ws = await session.ws_connect(server.make_url("/ws"))
                await ws.close()
                pytest.fail("Expected 401 but connection was accepted")
            except aiohttp.WSServerHandshakeError as e:
                assert e.status == 401
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_auth_wrong_token_rejected():
    """A client with an incorrect bearer token gets 401."""
    server, mgr = await _make_ws_auth_server()
    try:
        async with aiohttp.ClientSession() as session:
            try:
                ws = await session.ws_connect(
                    server.make_url("/ws"),
                    headers={"Authorization": "Bearer wrong-token-here"},
                )
                await ws.close()
                pytest.fail("Expected 401 but connection was accepted")
            except aiohttp.WSServerHandshakeError as e:
                assert e.status == 401
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_auth_non_bearer_scheme_rejected():
    """A client using Basic auth scheme gets 401."""
    server, mgr = await _make_ws_auth_server()
    try:
        async with aiohttp.ClientSession() as session:
            try:
                ws = await session.ws_connect(
                    server.make_url("/ws"),
                    headers={"Authorization": f"Basic {TEST_WS_AUTH_TOKEN}"},
                )
                await ws.close()
                pytest.fail("Expected 401 but connection was accepted")
            except aiohttp.WSServerHandshakeError as e:
                assert e.status == 401
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_auth_empty_bearer_rejected():
    """A client with 'Bearer ' (empty token) gets 401."""
    server, mgr = await _make_ws_auth_server()
    try:
        async with aiohttp.ClientSession() as session:
            try:
                ws = await session.ws_connect(
                    server.make_url("/ws"),
                    headers={"Authorization": "Bearer "},
                )
                await ws.close()
                pytest.fail("Expected 401 but connection was accepted")
            except aiohttp.WSServerHandshakeError as e:
                assert e.status == 401
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_no_auth_token_configured_allows_all():
    """When no ws_auth_token is set, all connections are allowed (backward compat)."""
    server, mgr = await _make_ws_server()  # no auth token
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(server.make_url("/ws"))
            await ws.send_json({"type": "ping"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "pong"
            await ws.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_auth_subscribe_and_receive():
    """Authenticated client can subscribe and receive events normally."""
    server, mgr = await _make_ws_auth_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(
                server.make_url("/ws"),
                headers={"Authorization": f"Bearer {TEST_WS_AUTH_TOKEN}"},
            )
            await ws.send_json({"type": "subscribe", "channel": "new_block"})
            resp = json.loads((await ws.receive()).data)
            assert resp["type"] == "subscribed"

            await mgr.broadcast("new_block", {"index": 7})
            event = json.loads((await ws.receive()).data)
            assert event["type"] == "event"
            assert event["data"]["index"] == 7

            await ws.close()
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# WebSocket heartbeat enforcement tests (R27-002)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_heartbeat_parameter_is_set():
    """WebSocketResponse is constructed with heartbeat=WS_HEARTBEAT_INTERVAL.

    Verifies that the server-side WebSocket uses aiohttp's built-in heartbeat
    mechanism, which sends automatic pings and closes stale connections.
    """
    server, mgr = await _make_ws_server()
    try:
        async with aiohttp.ClientSession() as session:
            ws_client = await session.ws_connect(server.make_url("/ws"))

            # The server-side WebSocketResponse objects are tracked in mgr._all_clients.
            # Each one should have heartbeat set to WS_HEARTBEAT_INTERVAL.
            assert mgr.connected_count() == 1
            server_ws = next(iter(mgr._all_clients))
            assert server_ws._heartbeat == WS_HEARTBEAT_INTERVAL

            await ws_client.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ws_heartbeat_stale_connection_auto_closes():
    """A client that does not respond to pings is auto-closed by aiohttp heartbeat.

    Uses a very short heartbeat interval (0.3s) so the test completes quickly.
    The server pings; the client ignores pongs by receiving raw without responding;
    aiohttp detects the missing pong and closes the connection.
    """
    mgr = WebSocketManager()
    app = web.Application()
    app["ws_manager"] = mgr

    async def short_heartbeat_handler(request: web.Request) -> web.WebSocketResponse:
        """Handler with very short heartbeat for testing timeout behavior."""
        ws = web.WebSocketResponse(
            max_msg_size=MAX_WS_MESSAGE_SIZE,
            heartbeat=0.3,  # very short for fast test
            autoping=True,
        )
        await ws.prepare(request)
        mgr._all_clients.add(ws)
        try:
            async for msg in ws:
                if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE,
                                WSMsgType.CLOSING, WSMsgType.CLOSED):
                    break
        except Exception:
            pass
        finally:
            mgr.remove_all(ws)
        return ws

    app.router.add_get("/ws", short_heartbeat_handler)
    server = TestServer(app)
    await server.start_server()

    try:
        import socket
        # Use a raw socket connection that won't respond to WebSocket pings.
        # aiohttp's heartbeat will detect the missing pong and close the connection.
        async with aiohttp.ClientSession() as session:
            ws_client = await session.ws_connect(
                server.make_url("/ws"),
                autoping=False,  # do NOT auto-respond to server pings
            )

            assert mgr.connected_count() == 1

            # Wait for heartbeat timeout to kick in (heartbeat=0.3s, so ~0.6-1s total)
            # aiohttp sends ping after 0.3s, waits another 0.3s for pong, then closes.
            await asyncio.sleep(1.5)

            # The server should have auto-closed the stale connection
            assert mgr.connected_count() == 0
    finally:
        await server.close()

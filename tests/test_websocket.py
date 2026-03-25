"""Tests for WebSocket subscription manager and handler."""
import asyncio
import json
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestServer

from qbit_network.network.websocket import (
    WebSocketManager,
    websocket_handler,
    VALID_CHANNELS,
    MAX_WS_CONNECTIONS,
    MAX_SUBSCRIPTIONS_PER_CLIENT,
    WS_RATE_LIMIT,
    MAX_WS_MESSAGE_SIZE,
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

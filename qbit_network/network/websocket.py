"""WebSocket subscription manager for real-time blockchain events."""
import asyncio
import hmac
import json
import logging
import time
from aiohttp import web, WSMsgType

logger = logging.getLogger("qbit_network.ws")

# Limits
MAX_WS_CONNECTIONS = 100
MAX_SUBSCRIPTIONS_PER_CLIENT = 10
MAX_WS_MESSAGE_SIZE = 8 * 1024  # 8 KB
WS_RATE_LIMIT = 10  # max messages per second per client
WS_HEARTBEAT_INTERVAL = 30.0  # seconds between server pings
WS_HEARTBEAT_TIMEOUT = 10.0  # seconds to wait for pong

VALID_CHANNELS = {"new_block", "new_tx", "chain_stats", "finalized"}


class WebSocketManager:
    """Manages WebSocket connections and channel subscriptions."""

    def __init__(self):
        self._subscribers: dict[str, set[web.WebSocketResponse]] = {
            ch: set() for ch in VALID_CHANNELS
        }
        self._client_channels: dict[int, set[str]] = {}  # id(ws) -> set of channels
        self._all_clients: set[web.WebSocketResponse] = set()
        self._client_rate: dict[int, list[float]] = {}  # id(ws) -> list of timestamps
        self._stats_task: asyncio.Task | None = None
        self._get_stats_fn = None  # set by node.py

    def connected_count(self) -> int:
        """Total number of connected WebSocket clients."""
        return len(self._all_clients)

    def add_subscriber(self, ws: web.WebSocketResponse, channel: str) -> tuple[bool, str]:
        """Register a client for a channel. Returns (ok, message)."""
        if channel not in VALID_CHANNELS:
            return False, f"unknown channel: {channel}"

        ws_id = id(ws)
        client_channels = self._client_channels.get(ws_id, set())
        if channel in client_channels:
            return False, f"already subscribed to {channel}"

        if len(client_channels) >= MAX_SUBSCRIPTIONS_PER_CLIENT:
            return False, f"max subscriptions ({MAX_SUBSCRIPTIONS_PER_CLIENT}) reached"

        self._subscribers[channel].add(ws)
        client_channels.add(channel)
        self._client_channels[ws_id] = client_channels
        return True, f"subscribed to {channel}"

    def remove_subscriber(self, ws: web.WebSocketResponse, channel: str) -> tuple[bool, str]:
        """Unregister a client from a channel. Returns (ok, message)."""
        if channel not in VALID_CHANNELS:
            return False, f"unknown channel: {channel}"

        ws_id = id(ws)
        client_channels = self._client_channels.get(ws_id, set())
        if channel not in client_channels:
            return False, f"not subscribed to {channel}"

        self._subscribers[channel].discard(ws)
        client_channels.discard(channel)
        return True, f"unsubscribed from {channel}"

    def remove_all(self, ws: web.WebSocketResponse):
        """Cleanup all subscriptions for a disconnected client."""
        ws_id = id(ws)
        channels = self._client_channels.pop(ws_id, set())
        for ch in channels:
            self._subscribers[ch].discard(ws)
        self._all_clients.discard(ws)
        self._client_rate.pop(ws_id, None)

    def _check_rate(self, ws: web.WebSocketResponse) -> bool:
        """Return True if client is within rate limit, False if exceeded."""
        ws_id = id(ws)
        now = time.monotonic()
        timestamps = self._client_rate.get(ws_id, [])

        # Prune timestamps older than 1 second
        cutoff = now - 1.0
        timestamps = [t for t in timestamps if t > cutoff]
        timestamps.append(now)
        self._client_rate[ws_id] = timestamps

        return len(timestamps) <= WS_RATE_LIMIT

    async def broadcast(self, channel: str, data: dict):
        """Send an event to all subscribers of a channel. Non-blocking, error-safe."""
        if channel not in self._subscribers:
            return

        subscribers = set(self._subscribers[channel])
        if not subscribers:
            return

        message = json.dumps({
            "type": "event",
            "channel": channel,
            "data": data,
        })

        dead = []
        for ws in subscribers:
            try:
                if ws.closed:
                    dead.append(ws)
                    continue
                await asyncio.wait_for(ws.send_str(message), timeout=5.0)
            except asyncio.TimeoutError:
                logger.debug("WS broadcast timeout, dropping slow client")
                dead.append(ws)
                try:
                    await ws.close()
                except Exception:
                    pass
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.remove_all(ws)

    async def start_stats_loop(self):
        """Start periodic chain_stats broadcast (every 5 seconds)."""
        self._stats_task = asyncio.create_task(self._stats_loop())

    async def _stats_loop(self):
        """Broadcast chain_stats every 5 seconds if there are subscribers."""
        try:
            while True:
                await asyncio.sleep(5)
                if not self._subscribers.get("chain_stats"):
                    continue
                if self._get_stats_fn is None:
                    continue
                try:
                    stats = self._get_stats_fn()
                    await self.broadcast("chain_stats", stats)
                except Exception as e:
                    logger.debug(f"chain_stats broadcast error: {e}")
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Cancel the stats loop and close all client connections."""
        if self._stats_task and not self._stats_task.done():
            self._stats_task.cancel()

        # Close all connected clients
        for ws in set(self._all_clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._all_clients.clear()
        self._client_channels.clear()
        self._client_rate.clear()
        for ch in self._subscribers:
            self._subscribers[ch].clear()


def _check_ws_auth(request: web.Request) -> bool:
    """Verify bearer token on WebSocket upgrade request."""
    expected = request.app.get("ws_auth_token", "")
    if not expected:
        return True  # no token configured — allow (backward compat)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[7:].strip()
    if not token:
        return False
    return hmac.compare_digest(token, expected)


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    """Handle incoming WebSocket connections at /ws."""
    # Authenticate before upgrade
    if not _check_ws_auth(request):
        raise web.HTTPUnauthorized(
            text="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    manager: WebSocketManager = request.app["ws_manager"]

    if manager.connected_count() >= MAX_WS_CONNECTIONS:
        raise web.HTTPServiceUnavailable(
            text="too many WebSocket connections")

    ws = web.WebSocketResponse(
        max_msg_size=MAX_WS_MESSAGE_SIZE,
        heartbeat=WS_HEARTBEAT_INTERVAL,
        autoping=True,
    )
    await ws.prepare(request)

    manager._all_clients.add(ws)
    logger.info(f"WS client connected ({manager.connected_count()} total)")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                # Rate limit check
                if not manager._check_rate(ws):
                    try:
                        await ws.send_json({
                            "type": "error",
                            "message": "rate limit exceeded",
                        })
                    except Exception:
                        pass
                    continue

                # Parse message
                try:
                    data = json.loads(msg.data)
                except (json.JSONDecodeError, ValueError):
                    try:
                        await ws.send_json({
                            "type": "error",
                            "message": "invalid JSON",
                        })
                    except Exception:
                        pass
                    continue

                if not isinstance(data, dict):
                    try:
                        await ws.send_json({
                            "type": "error",
                            "message": "message must be a JSON object",
                        })
                    except Exception:
                        pass
                    continue

                msg_type = data.get("type")
                if not isinstance(msg_type, str):
                    try:
                        await ws.send_json({
                            "type": "error",
                            "message": "missing or invalid 'type' field",
                        })
                    except Exception:
                        pass
                    continue

                if msg_type == "ping":
                    try:
                        await ws.send_json({"type": "pong"})
                    except Exception:
                        pass

                elif msg_type == "subscribe":
                    channel = data.get("channel")
                    if not isinstance(channel, str):
                        try:
                            await ws.send_json({
                                "type": "error",
                                "message": "missing or invalid 'channel' field",
                            })
                        except Exception:
                            pass
                        continue

                    ok, result_msg = manager.add_subscriber(ws, channel)
                    if ok:
                        try:
                            await ws.send_json({
                                "type": "subscribed",
                                "channel": channel,
                            })
                        except Exception:
                            pass
                    else:
                        try:
                            await ws.send_json({
                                "type": "error",
                                "message": result_msg,
                            })
                        except Exception:
                            pass

                elif msg_type == "unsubscribe":
                    channel = data.get("channel")
                    if not isinstance(channel, str):
                        try:
                            await ws.send_json({
                                "type": "error",
                                "message": "missing or invalid 'channel' field",
                            })
                        except Exception:
                            pass
                        continue

                    ok, result_msg = manager.remove_subscriber(ws, channel)
                    if ok:
                        try:
                            await ws.send_json({
                                "type": "unsubscribed",
                                "channel": channel,
                            })
                        except Exception:
                            pass
                    else:
                        try:
                            await ws.send_json({
                                "type": "error",
                                "message": result_msg,
                            })
                        except Exception:
                            pass

                else:
                    try:
                        await ws.send_json({
                            "type": "error",
                            "message": f"unknown message type: {msg_type}",
                        })
                    except Exception:
                        pass

            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE,
                              WSMsgType.CLOSING, WSMsgType.CLOSED):
                break
    except Exception as e:
        logger.debug(f"WS client error: {e}")
    finally:
        manager.remove_all(ws)
        logger.info(f"WS client disconnected ({manager.connected_count()} total)")

    return ws

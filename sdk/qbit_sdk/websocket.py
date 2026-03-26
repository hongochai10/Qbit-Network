"""QBit SDK WebSocket subscription client — stdlib only.

This module provides a simple WebSocket client for subscribing to
real-time blockchain events. It uses a basic WebSocket implementation
over stdlib sockets. For production use with reconnection and TLS,
consider using the ``websockets`` package.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import threading
from typing import Any, Callable
from urllib.parse import urlparse


class QBitWebSocket:
    """Simple WebSocket client for QBit event subscriptions.

    Parameters
    ----------
    ws_url : str
        WebSocket URL (e.g. ``ws://localhost:8545/ws``).

    Example
    -------
    ::

        ws = QBitWebSocket("ws://localhost:8545/ws")
        ws.connect()
        ws.subscribe("new_block", lambda data: print(data))
        ws.run_forever()  # blocks
    """

    def __init__(self, ws_url: str = "ws://localhost:8545/ws"):
        self._url = ws_url
        self._sock: socket.socket | None = None
        self._callbacks: dict[str, list[Callable]] = {}
        self._running = False
        self._thread: threading.Thread | None = None

    def connect(self) -> None:
        """Open the WebSocket connection via HTTP upgrade handshake."""
        parsed = urlparse(self._url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/ws"

        self._sock = socket.create_connection((host, port), timeout=10)

        # WebSocket upgrade handshake
        key = os.urandom(16)
        import base64
        ws_key = base64.b64encode(key).decode()

        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._sock.sendall(handshake.encode())

        # Read response (simplified — reads until double CRLF)
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake failed: no response")
            response += chunk

        if b"101" not in response.split(b"\r\n")[0]:
            raise ConnectionError(
                f"WebSocket handshake failed: {response.split(b'\\r\\n')[0].decode()}"
            )

    def close(self) -> None:
        """Close the WebSocket connection."""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def subscribe(self, channel: str, callback: Callable[[dict], Any]) -> None:
        """Subscribe to a channel and register a callback.

        Parameters
        ----------
        channel : str
            Channel name (``new_block``, ``new_tx``, ``chain_stats``, ``finalized``).
        callback : callable
            Called with the event data dict when a message arrives on this channel.
        """
        self._callbacks.setdefault(channel, []).append(callback)
        self._send_json({"type": "subscribe", "channel": channel})

    def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel."""
        self._callbacks.pop(channel, None)
        self._send_json({"type": "unsubscribe", "channel": channel})

    def _send_json(self, data: dict) -> None:
        """Send a JSON message as a masked WebSocket text frame."""
        payload = json.dumps(data).encode()
        self._send_frame(payload, opcode=0x1)

    def _send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        """Send a WebSocket frame (client frames are always masked)."""
        if not self._sock:
            raise ConnectionError("not connected")

        frame = bytearray()
        frame.append(0x80 | opcode)  # FIN + opcode

        length = len(payload)
        if length < 126:
            frame.append(0x80 | length)  # MASK bit set
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack("!Q", length))

        mask = os.urandom(4)
        frame.extend(mask)

        masked = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))
        frame.extend(masked)

        self._sock.sendall(bytes(frame))

    def _recv_frame(self) -> tuple[int, bytes]:
        """Receive a single WebSocket frame. Returns (opcode, payload)."""
        if not self._sock:
            raise ConnectionError("not connected")

        def _recv_exact(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = self._sock.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("connection closed")
                buf += chunk
            return buf

        header = _recv_exact(2)
        opcode = header[0] & 0x0F
        masked = (header[1] & 0x80) != 0
        length = header[1] & 0x7F

        if length == 126:
            length = struct.unpack("!H", _recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _recv_exact(8))[0]

        if masked:
            mask = _recv_exact(4)
            data = bytearray(_recv_exact(length))
            for i in range(len(data)):
                data[i] ^= mask[i % 4]
            return opcode, bytes(data)

        return opcode, _recv_exact(length)

    def run_forever(self) -> None:
        """Block and dispatch incoming messages to registered callbacks.

        Call ``close()`` from another thread or a callback to stop.
        """
        self._running = True
        try:
            while self._running:
                try:
                    opcode, payload = self._recv_frame()
                except (ConnectionError, OSError):
                    break

                if opcode == 0x8:  # Close frame
                    break
                if opcode == 0x9:  # Ping
                    self._send_frame(payload, opcode=0xA)  # Pong
                    continue
                if opcode == 0x1:  # Text
                    try:
                        msg = json.loads(payload.decode())
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue

                    if isinstance(msg, dict) and msg.get("type") == "event":
                        channel = msg.get("channel", "")
                        data = msg.get("data", {})
                        for cb in self._callbacks.get(channel, []):
                            try:
                                cb(data)
                            except Exception:
                                pass
        finally:
            self._running = False

    def run_in_background(self) -> threading.Thread:
        """Start ``run_forever()`` in a daemon thread. Returns the thread."""
        self._thread = threading.Thread(target=self.run_forever, daemon=True)
        self._thread.start()
        return self._thread

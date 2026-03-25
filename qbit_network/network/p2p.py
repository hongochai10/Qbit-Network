"""P2P networking layer using asyncio TCP + newline-delimited JSON."""
import asyncio
import ipaddress
import json
import logging
import time
from ..config import MAX_PEERS

logger = logging.getLogger("qbit_network.p2p")

MSG_HELLO = "hello"
MSG_NEW_BLOCK = "new_block"
MSG_NEW_TX = "new_tx"
MSG_GET_BLOCKS = "get_blocks"
MSG_BLOCKS = "blocks"
MSG_GET_PEERS = "get_peers"
MSG_PEERS = "peers"
MSG_STATUS = "status"

_READER_LIMIT = 10 * 1024 * 1024  # 10 MB

_BLOCKED_PORTS = {22, 23, 25, 53, 80, 443, 445, 3306, 5432, 6379, 8080, 8443}


def _is_safe_peer(host: str, port: int, own_host: str, own_port: int) -> bool:
    if port <= 0 or port > 65535:
        return False
    if port in _BLOCKED_PORTS:
        return False
    if host in ("127.0.0.1", "localhost", "::1", own_host) and port == own_port:
        return False
    from ..config import ALLOW_PRIVATE_PEERS
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_link_local or addr.is_reserved:
            return False
        if not ALLOW_PRIVATE_PEERS and (addr.is_private or addr.is_loopback):
            return False
    except ValueError:
        lower = host.lower()
        if lower in ("localhost", "metadata.google.internal", "instance-data"):
            return False
    return True


class Peer:
    __slots__ = ('host', 'port', 'reader', 'writer', 'node_id',
                 'connected', 'last_seen', 'height')

    def __init__(self, host: str, port: int, reader=None, writer=None):
        self.host = host
        self.port = port
        self.reader = reader
        self.writer = writer
        self.node_id = ""
        self.connected = False
        self.last_seen = time.time()
        self.height = -1

    @property
    def addr(self) -> str:
        return f"{self.host}:{self.port}"

    async def send(self, msg_type: str, data: dict):
        if not self.writer or self.writer.is_closing():
            return
        try:
            line = json.dumps({"type": msg_type, "data": data}) + "\n"
            self.writer.write(line.encode())
            await self.writer.drain()
        except Exception:
            self.connected = False

    async def close(self):
        if self.writer and not self.writer.is_closing():
            self.writer.close()
        self.connected = False


class P2PNode:
    """Async TCP P2P node."""

    def __init__(self, host: str = "0.0.0.0", port: int = 9000, node_id: str = ""):
        self.host = host
        self.port = port
        self.node_id = node_id
        self.peers: dict[str, Peer] = {}
        self._handlers: dict[str, object] = {}
        self._server = None

    def on(self, msg_type: str, handler):
        self._handlers[msg_type] = handler

    async def start(self):
        self._server = await asyncio.start_server(
            self._on_connect, self.host, self.port, limit=_READER_LIMIT)
        logger.info(f"P2P listening on {self.host}:{self.port}")

    async def stop(self):
        for peer in list(self.peers.values()):
            await peer.close()
        self.peers.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def connect(self, host: str, port: int):
        if len(self.peers) >= MAX_PEERS:
            return
        addr = f"{host}:{port}"
        if addr in self.peers:
            return
        if not _is_safe_peer(host, port, self.host, self.port):
            logger.warning(f"Blocked unsafe peer: {addr}")
            return
        try:
            reader, writer = await asyncio.open_connection(
                host, port, limit=_READER_LIMIT)
            peer = Peer(host, port, reader, writer)
            peer.connected = True
            self.peers[addr] = peer
            await peer.send(MSG_HELLO, {"node_id": self.node_id, "port": self.port})
            asyncio.create_task(self._read_loop(peer))
            logger.info(f"Connected to {addr}")
        except Exception as e:
            logger.debug(f"Failed to connect to {addr}: {e}")

    async def broadcast(self, msg_type: str, data: dict, exclude: str = ""):
        tasks = []
        for addr, peer in list(self.peers.items()):
            if addr != exclude and peer.connected:
                tasks.append(peer.send(msg_type, data))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def peer_count(self) -> int:
        return sum(1 for p in self.peers.values() if p.connected)

    def best_peer_height(self) -> int:
        heights = [p.height for p in self.peers.values() if p.connected and p.height >= 0]
        return max(heights) if heights else -1

    async def _on_connect(self, reader, writer):
        if len(self.peers) >= MAX_PEERS:
            writer.close()
            return
        info = writer.get_extra_info('peername')
        host = info[0] if info else "unknown"
        temp_key = f"_inbound_{host}_{id(writer)}"
        peer = Peer(host, 0, reader, writer)
        peer.connected = True
        self.peers[temp_key] = peer
        peer_key = temp_key  # tracks current key in self.peers
        hello_done = False
        try:
            # Require HELLO within 10 seconds to prevent idle socket DoS
            try:
                first_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.debug(f"Inbound {host}: no HELLO within timeout")
                return
            if not first_line:
                return
            msg = self._parse(first_line)
            if msg:
                mt, data = msg
                if mt == MSG_HELLO:
                    hello_done = True
                    peer.node_id = data.get("node_id", "")
                    new_port = data.get("port", 0)
                    if isinstance(new_port, int) and 0 < new_port <= 65535:
                        self.peers.pop(peer_key, None)
                        peer.port = new_port
                        real_key = peer.addr
                        if real_key not in self.peers:
                            self.peers[real_key] = peer
                            peer_key = real_key
                        else:
                            self.peers[temp_key] = peer
                            peer_key = temp_key
                    await peer.send(MSG_HELLO, {
                        "node_id": self.node_id, "port": self.port})
                await self._dispatch(mt, peer, data)
                peer.last_seen = time.time()

            while True:
                line = await reader.readline()
                if not line:
                    break
                msg = self._parse(line)
                if not msg:
                    continue
                mt, data = msg
                if mt == MSG_STATUS:
                    h = data.get("height", -1)
                    if isinstance(h, int) and -1 <= h <= 10_000_000:
                        peer.height = h
                await self._dispatch(mt, peer, data)
                peer.last_seen = time.time()
        except asyncio.LimitOverrunError:
            logger.warning(f"Oversized message from {peer.addr}")
        except Exception:
            pass
        finally:
            peer.connected = False
            self.peers.pop(peer_key, None)

    async def _read_loop(self, peer: Peer):
        try:
            while True:
                line = await peer.reader.readline()
                if not line:
                    break
                msg = self._parse(line)
                if not msg:
                    continue
                mt, data = msg
                if mt == MSG_STATUS:
                    h = data.get("height", -1)
                    if isinstance(h, int) and -1 <= h <= 10_000_000:
                        peer.height = h
                await self._dispatch(mt, peer, data)
                peer.last_seen = time.time()
        except asyncio.LimitOverrunError:
            logger.warning(f"Oversized message from {peer.addr}")
        except Exception:
            pass
        finally:
            peer.connected = False
            self.peers.pop(peer.addr, None)

    async def _dispatch(self, msg_type: str, peer: Peer, data: dict):
        handler = self._handlers.get(msg_type)
        if handler:
            await handler(peer, data)

    @staticmethod
    def _parse(line: bytes):
        try:
            obj = json.loads(line.decode().strip())
            return obj.get("type", ""), obj.get("data", {})
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

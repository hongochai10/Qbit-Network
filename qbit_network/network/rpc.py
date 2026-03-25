"""JSON-RPC 2.0 API server with bearer token authentication."""
import hmac
import json
import logging
import secrets
from aiohttp import web
from ..config import MAX_RPC_BODY, MAX_RPC_BATCH

logger = logging.getLogger("qbit_network.rpc")


class RPCServer:
    """JSON-RPC server over HTTP with auth token."""

    # Methods that require authentication (write / sensitive operations)
    PROTECTED_METHODS = {
        "qv_newWallet", "qv_listWallets", "qv_getWalletKeys",
        "qv_notarize", "qv_store", "qv_share", "qv_registerKey",
        "qv_sendRawTransaction",
        "qv_getSharedSecret", "qv_decapsulateShared",
        "qv_getSharedWithMe",
    }

    def __init__(self, host: str = "0.0.0.0", port: int = 8545,
                 auth_token: str = ""):
        self.host = host
        self.port = port
        self.auth_token = auth_token or secrets.token_hex(32)
        self._methods: dict[str, object] = {}
        self._app = web.Application(client_max_size=MAX_RPC_BODY)
        self._app.router.add_post("/", self._handle)
        self._app.router.add_get("/", self._info)
        self._runner = None

    def method(self, name: str, fn):
        self._methods[name] = fn

    async def start(self):
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info(f"RPC server on http://{self.host}:{self.port}")
        logger.info(f"RPC auth token: {self.auth_token[:8]}...{self.auth_token[-4:]}")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    async def _info(self, request):
        public = sorted(m for m in self._methods if m not in self.PROTECTED_METHODS)
        return web.json_response({
            "name": "QVault PQC Blockchain",
            "methods": public,
        })

    async def _handle(self, request):
        # Body size limit (#S13)
        if request.content_length and request.content_length > MAX_RPC_BODY:
            return self._error(None, -32600, "request body too large")

        try:
            raw = await request.read()
            if len(raw) > MAX_RPC_BODY:
                return self._error(None, -32600, "request body too large")
            body = json.loads(raw)
        except Exception:
            return self._error(None, -32700, "Parse error")

        # Extract auth token from header
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""

        if isinstance(body, list):
            # Batch limit (#S12)
            if len(body) > MAX_RPC_BATCH:
                return self._error(
                    None, -32600,
                    f"batch too large: {len(body)} > {MAX_RPC_BATCH}")
            results = [await self._exec(r, token) for r in body]
            return web.json_response(results)

        return web.json_response(await self._exec(body, token))

    async def _exec(self, req: dict, token: str) -> dict:
        rid = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        fn = self._methods.get(method)
        if not fn:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"unknown method: {method}"}}

        # Auth check — constant-time comparison to prevent timing attacks
        if method in self.PROTECTED_METHODS:
            if not hmac.compare_digest(token, self.auth_token):
                return {"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32600,
                                  "message": "authentication required for this method"}}

        try:
            if isinstance(params, list):
                result = await fn(*params)
            elif isinstance(params, dict):
                result = await fn(**params)
            else:
                result = await fn()
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except Exception as e:
            logger.error(f"RPC {method}: {e}")
            # Sanitize error message (#S18)
            msg = str(e)
            if len(msg) > 200:
                msg = msg[:200] + "..."
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": msg}}

    @staticmethod
    def _error(rid, code, msg):
        return web.json_response(
            {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}})

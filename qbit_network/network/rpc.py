"""JSON-RPC 2.0 API server with bearer token authentication and optional TLS."""
import hmac
import json
import logging
import os
import secrets
import ssl
import tempfile
from aiohttp import web
from ..config import MAX_RPC_BODY, MAX_RPC_BATCH

logger = logging.getLogger("qbit_network.rpc")


def _create_ssl_context(cert_path: str, key_path: str) -> ssl.SSLContext:
    """Create a TLS context with strong defaults."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


def _generate_self_signed(data_dir: str) -> tuple[str, str]:
    """Generate a self-signed certificate for development use."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        import datetime

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "qbit-node")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )

        cert_dir = os.path.join(data_dir or tempfile.gettempdir(), "tls")
        os.makedirs(cert_dir, exist_ok=True)
        cert_path = os.path.join(cert_dir, "cert.pem")
        key_path = os.path.join(cert_dir, "key.pem")

        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.Format.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))
        os.chmod(key_path, 0o600)

        return cert_path, key_path
    except ImportError:
        raise RuntimeError("cryptography package required for --tls-self-signed")


import ipaddress  # needed for SAN in self-signed cert


class RPCServer:
    """JSON-RPC server over HTTP/HTTPS with auth token."""

    PROTECTED_METHODS = {
        "qv_newWallet", "qv_listWallets", "qv_getWalletKeys",
        "qv_notarize", "qv_store", "qv_share", "qv_registerKey",
        "qv_sendRawTransaction",
        "qv_getSharedSecret", "qv_decapsulateShared",
        "qv_getSharedWithMe",
    }

    # Methods that MUST NOT be served over plain HTTP (secret-returning)
    TLS_REQUIRED_METHODS = {"qv_getSharedSecret", "qv_decapsulateShared"}

    def __init__(self, host: str = "0.0.0.0", port: int = 8545,
                 auth_token: str = "", tls_cert: str = "", tls_key: str = "",
                 tls_self_signed: bool = False, data_dir: str = ""):
        self.host = host
        self.port = port
        self.auth_token = auth_token or secrets.token_hex(32)
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self.tls_self_signed = tls_self_signed
        self.data_dir = data_dir
        self._tls_active = False
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

        ssl_ctx = None
        if self.tls_cert and self.tls_key:
            # Check key file permissions
            key_mode = os.stat(self.tls_key).st_mode & 0o777
            if key_mode & 0o077:
                logger.warning(
                    f"TLS key file {self.tls_key} has permissive permissions "
                    f"({oct(key_mode)}), recommend 0o600")
            ssl_ctx = _create_ssl_context(self.tls_cert, self.tls_key)
            self._tls_active = True
        elif self.tls_self_signed:
            cert, key = _generate_self_signed(self.data_dir)
            ssl_ctx = _create_ssl_context(cert, key)
            self._tls_active = True
            logger.warning("Using self-signed TLS certificate (development only)")

        site = web.TCPSite(self._runner, self.host, self.port, ssl_context=ssl_ctx)
        await site.start()

        proto = "https" if self._tls_active else "http"
        logger.info(f"RPC server on {proto}://{self.host}:{self.port}")
        if not self._tls_active:
            logger.warning("RPC running WITHOUT TLS — secrets transmitted in cleartext")
        logger.info(f"RPC auth token: {self.auth_token[:8]}...{self.auth_token[-4:]}")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    async def _info(self, request):
        public = sorted(m for m in self._methods if m not in self.PROTECTED_METHODS)
        return web.json_response({
            "name": "QBit Network PQC Blockchain",
            "tls": self._tls_active,
            "methods": public,
        })

    async def _handle(self, request):
        if request.content_length and request.content_length > MAX_RPC_BODY:
            return self._error(None, -32600, "request body too large")

        try:
            raw = await request.read()
            if len(raw) > MAX_RPC_BODY:
                return self._error(None, -32600, "request body too large")
            body = json.loads(raw)
        except Exception:
            return self._error(None, -32700, "Parse error")

        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""

        if isinstance(body, list):
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

        # Auth check
        if method in self.PROTECTED_METHODS:
            if not hmac.compare_digest(token, self.auth_token):
                return {"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32600,
                                  "message": "authentication required for this method"}}

        # TLS enforcement for secret-returning methods
        if method in self.TLS_REQUIRED_METHODS and not self._tls_active:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32600,
                              "message": f"{method} requires TLS. "
                                         f"Start node with --tls-cert/--tls-key or --tls-self-signed"}}

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
            msg = str(e)
            if len(msg) > 200:
                msg = msg[:200] + "..."
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": msg}}

    @staticmethod
    def _error(rid, code, msg):
        return web.json_response(
            {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}})

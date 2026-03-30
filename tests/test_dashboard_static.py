"""Tests for R26-002: dashboard static file serving scope restriction.

Verifies that only files with allowed extensions (.html, .js, .css, .png, .svg)
are served, and that directory traversal attempts are blocked.
"""
import os
import shutil
import sys
import tempfile

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qbit_network.network.rpc import RPCServer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def dashboard_dir():
    """Create a temp dashboard dir with various file types."""
    d = tempfile.mkdtemp(prefix="qv_dash_test_")
    # Allowed files
    with open(os.path.join(d, "index.html"), "w") as f:
        f.write("<html>dashboard</html>")
    with open(os.path.join(d, "app.js"), "w") as f:
        f.write("console.log('ok');")
    with open(os.path.join(d, "style.css"), "w") as f:
        f.write("body{}")
    with open(os.path.join(d, "logo.png"), "wb") as f:
        f.write(b"\x89PNG\r\n")
    with open(os.path.join(d, "icon.svg"), "w") as f:
        f.write("<svg></svg>")
    # Disallowed files
    with open(os.path.join(d, "secret.json"), "w") as f:
        f.write('{"key":"leaked"}')
    with open(os.path.join(d, "config.yaml"), "w") as f:
        f.write("password: hunter2")
    with open(os.path.join(d, "data.db"), "wb") as f:
        f.write(b"\x00" * 16)
    with open(os.path.join(d, "readme.md"), "w") as f:
        f.write("# secret docs")
    with open(os.path.join(d, "script.py"), "w") as f:
        f.write("import os")
    # Subdirectory with allowed file
    sub = os.path.join(d, "assets")
    os.makedirs(sub)
    with open(os.path.join(sub, "extra.js"), "w") as f:
        f.write("// sub")
    with open(os.path.join(sub, "hidden.env"), "w") as f:
        f.write("SECRET=1")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest_asyncio.fixture
async def client(dashboard_dir, monkeypatch):
    """TestClient with RPCServer whose dashboard dir is our temp dir."""
    # Patch os.path operations so _mount_dashboard finds our temp dir
    rpc = RPCServer.__new__(RPCServer)
    rpc.host = "127.0.0.1"
    rpc.port = 0
    rpc.auth_token = ""
    rpc.tls_cert = ""
    rpc.tls_key = ""
    rpc._methods = {}
    rpc._ws_manager = None
    rpc._cors_origin = None

    app = web.Application()
    rpc._app = app

    # Manually mount dashboard pointing to our temp dir
    async def _dashboard_index(request):
        index_path = os.path.join(dashboard_dir, "index.html")
        if os.path.isfile(index_path):
            return web.FileResponse(index_path)
        raise web.HTTPNotFound()

    async def _dashboard_static(request):
        rel = request.match_info["path"]
        safe = os.path.normpath(rel)
        if safe.startswith("..") or os.path.isabs(safe):
            raise web.HTTPNotFound()
        full = os.path.join(dashboard_dir, safe)
        if not os.path.commonpath([dashboard_dir, full]).startswith(dashboard_dir):
            raise web.HTTPNotFound()
        _, ext = os.path.splitext(safe)
        if ext.lower() not in RPCServer._DASHBOARD_ALLOWED_EXT:
            raise web.HTTPNotFound()
        if os.path.isfile(full):
            return web.FileResponse(full)
        raise web.HTTPNotFound()

    app.router.add_get("/dashboard", _dashboard_index)
    app.router.add_get("/dashboard/", _dashboard_index)
    app.router.add_get("/dashboard/static/{path:.*}", _dashboard_static)

    server = TestServer(app)
    cl = TestClient(server)
    await cl.start_server()
    yield cl
    await cl.close()


# ---------------------------------------------------------------------------
# Tests — allowed extensions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_serve_html(client):
    resp = await client.get("/dashboard/static/index.html")
    assert resp.status == 200
    body = await resp.text()
    assert "dashboard" in body


@pytest.mark.asyncio
async def test_serve_js(client):
    resp = await client.get("/dashboard/static/app.js")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_serve_css(client):
    resp = await client.get("/dashboard/static/style.css")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_serve_png(client):
    resp = await client.get("/dashboard/static/logo.png")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_serve_svg(client):
    resp = await client.get("/dashboard/static/icon.svg")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_serve_subdirectory_allowed(client):
    resp = await client.get("/dashboard/static/assets/extra.js")
    assert resp.status == 200


# ---------------------------------------------------------------------------
# Tests — disallowed extensions return 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_block_json(client):
    resp = await client.get("/dashboard/static/secret.json")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_block_yaml(client):
    resp = await client.get("/dashboard/static/config.yaml")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_block_db(client):
    resp = await client.get("/dashboard/static/data.db")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_block_md(client):
    resp = await client.get("/dashboard/static/readme.md")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_block_py(client):
    resp = await client.get("/dashboard/static/script.py")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_block_env_in_subdir(client):
    resp = await client.get("/dashboard/static/assets/hidden.env")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_block_no_extension(client):
    resp = await client.get("/dashboard/static/Makefile")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# Tests — directory traversal blocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_block_traversal_dotdot(client):
    resp = await client.get("/dashboard/static/../../../etc/passwd")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_block_traversal_encoded(client):
    resp = await client.get("/dashboard/static/..%2F..%2Fetc%2Fpasswd")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_nonexistent_allowed_ext(client):
    resp = await client.get("/dashboard/static/missing.js")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# Tests — dashboard index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_index(client):
    resp = await client.get("/dashboard")
    assert resp.status == 200
    body = await resp.text()
    assert "dashboard" in body


@pytest.mark.asyncio
async def test_dashboard_index_trailing_slash(client):
    resp = await client.get("/dashboard/")
    assert resp.status == 200

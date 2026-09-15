"""P0 proxy hardening: loopback-only Host gate + loopback-only CORS reflection."""

import asyncio
import json
import types

import pytest
from aiohttp import web

from genesis_memory.proxy.proxy_server import (
    cors_allow_loopback_origin,
    is_loopback_host,
    loopback_only_middleware,
)


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1:8000", True),
    ("127.0.0.1", True),
    ("localhost:8000", True),
    ("LOCALHOST", True),
    ("[::1]:8000", True),
    ("::1", True),
    ("evil.com", False),
    ("evil.com:8000", False),
    ("127.0.0.1.evil.com", False),  # suffix trick must not pass
    ("localhost.evil.com:8000", False),
    ("", False),
])
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected


def _req(host="127.0.0.1:8000", origin=None):
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    return types.SimpleNamespace(host=host, headers=headers)


async def _ok_handler(request):
    return web.json_response({"ok": True})


def test_middleware_blocks_foreign_host():
    resp = asyncio.run(loopback_only_middleware(_req(host="evil.com"), _ok_handler))
    assert resp.status == 403
    body = json.loads(resp.text)
    assert "loopback" in body["error"]


def test_middleware_passes_loopback_host():
    resp = asyncio.run(loopback_only_middleware(_req(host="127.0.0.1:8000"), _ok_handler))
    assert resp.status == 200


def test_cors_reflects_only_loopback_origins():
    resp = cors_allow_loopback_origin(
        _req(origin="http://127.0.0.1:8090"), web.json_response({"ok": True}))
    assert resp.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:8090"
    assert resp.headers["Vary"] == "Origin"


def test_cors_sends_nothing_to_foreign_origin():
    for origin in ("https://evil.com", "http://127.0.0.1.evil.com", ""):
        resp = cors_allow_loopback_origin(
            _req(origin=origin), web.json_response({"ok": True}))
        assert "Access-Control-Allow-Origin" not in resp.headers

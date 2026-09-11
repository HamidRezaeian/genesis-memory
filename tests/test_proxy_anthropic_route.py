"""Anthropic Messages API route on the GENESIS gateway (ANTHROPIC_BASE_URL drop-in)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
from aiohttp import web

from genesis_memory.proxy.proxy_server import GenesisProxyServer, ProxyTelemetry


class MockAnthropicUpstream:
    def __init__(self) -> None:
        self.app = web.Application()
        self.app.router.add_post("/v1/messages", self.handle)
        self.runner: web.AppRunner | None = None
        self.last_payload: dict | None = None
        self.last_headers: dict | None = None

    async def start(self) -> str:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/v1"

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.last_headers = dict(request.headers)
        self.last_payload = await request.json()
        if self.last_headers.get("x-api-key") == "bad":
            return web.json_response({"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}, status=401)
        if self.last_payload.get("stream"):
            resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            for ev in ({"type": "message_start"}, {"type": "content_block_delta", "delta": {"text": "hi"}},
                       {"type": "message_stop"}):
                await resp.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
                await asyncio.sleep(0.005)
            await resp.write_eof()
            return resp
        return web.json_response({
            "id": "msg_1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "Non-streaming Anthropic answer"}],
            "usage": {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 80},
        })


class MockStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = [{"id": 1, "kind": "decision", "text": "Always use WAL mode for sqlite"}]

    def recall(self, query: str, limit: int = 3, fallback: bool = False) -> dict:
        hits = [r for r in self.rows if any(t.lower() in r["text"].lower() for t in query.split())]
        return {"results": hits[:limit]}


async def _harness(store=None, mode="live"):
    upstream = MockAnthropicUpstream()
    url = await upstream.start()
    proxy = GenesisProxyServer(upstream_url="http://127.0.0.1:1/v1", telemetry=ProxyTelemetry(), store=store, mode=mode)
    proxy.anthropic_upstream_url = url
    proxy.anthropic_key = "test-key"
    runner = web.AppRunner(proxy.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    return upstream, proxy, runner, base


def test_anthropic_non_streaming_compacts_and_counts_usage():
    async def _run():
        upstream, proxy, runner, base = await _harness(store=MockStore())
        try:
            msgs = [{"role": "user", "content": "first sqlite question"},
                    {"role": "assistant", "content": [{"type": "text", "text": "long old answer " * 40}]},
                    {"role": "user", "content": "second sqlite question"},
                    {"role": "assistant", "content": "another long answer " * 40},
                    {"role": "user", "content": [{"type": "text", "text": "which sqlite mode again?"}]}]
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{base}/v1/messages", json={"model": "claude-3-5-sonnet", "max_tokens": 100,
                                                                "messages": msgs}) as r:
                    assert r.status == 200
                    data = await r.json()
                    assert data["content"][0]["text"] == "Non-streaming Anthropic answer"
            sent = upstream.last_payload["messages"]
            assert len(sent) < len(msgs)  # history compacted
            assert json.dumps(sent).count("WAL mode") >= 1  # memory capsule injected
            assert upstream.last_headers["x-api-key"] == "test-key"
            assert upstream.last_headers["anthropic-version"]
            snap = proxy.telemetry.get_snapshot()
            assert snap["requests_total"] == 1
            assert snap["tokens"]["prompt_total"] == 120 and snap["tokens"]["prompt_cached"] == 80
            assert snap["tokens"]["completion"] == 30
            assert snap["tokens"]["prompt_stripped_est"] > 0
        finally:
            await runner.cleanup()
            await upstream.stop()
    asyncio.run(_run())


def test_anthropic_streaming_passthrough_and_error_forwarding():
    async def _run():
        upstream, proxy, runner, base = await _harness()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{base}/v1/messages", json={"model": "m", "max_tokens": 10, "stream": True,
                                                                "messages": [{"role": "user", "content": "hi"}]}) as r:
                    assert r.status == 200
                    assert r.headers["Content-Type"].startswith("text/event-stream")
                    body = (await r.read()).decode()
                    assert "message_start" in body and "message_stop" in body
                async with s.post(f"{base}/v1/messages", headers={"x-api-key": "bad"},
                                  json={"model": "m", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}) as r:
                    assert r.status == 401
                    assert (await r.json())["error"]["type"] == "authentication_error"
                async with s.post(f"{base}/v1/messages", data=b"not json") as r:
                    assert r.status == 400
            snap = proxy.telemetry.get_snapshot()
            assert snap["streaming_requests"] == 1
            assert snap["upstream"]["status_codes"]["4xx"] == 1
        finally:
            await runner.cleanup()
            await upstream.stop()
    asyncio.run(_run())


def test_anthropic_shadow_mode_never_mutates():
    async def _run():
        upstream, proxy, runner, base = await _harness(store=MockStore(), mode="shadow")
        try:
            msgs = [{"role": "user", "content": "sqlite one"}, {"role": "assistant", "content": "x " * 200},
                    {"role": "user", "content": "sqlite two"}]
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{base}/v1/messages", json={"model": "m", "max_tokens": 10, "messages": msgs}) as r:
                    assert r.status == 200
            assert upstream.last_payload["messages"] == msgs
        finally:
            await runner.cleanup()
            await upstream.stop()
    asyncio.run(_run())

"""Production Integration Test Suite for GENESIS Reverse Proxy.

Verifies:
1. Shadow Mode: 100% verbatim passthrough to upstream with shadow token accounting.
2. Live Mode: Real structural compaction and token reduction.
3. Fail-Open Bypass: Corrupted/invalid turns trigger bypass with specific reason codes.
4. Multi-Session Isolation: Multiple clients maintain isolated 1-turn locality buffers (TLBs).
5. Resilient /models fallback: Returns OpenAI-compatible catalog even when upstream fails.
6. Latency Percentiles & Upstream Error Accounting: p50/p95 TTFT and 2xx/4xx/5xx/429 status counters.
"""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List

import aiohttp
from aiohttp import web
import pytest

REPO = Path(__file__).resolve().parent.parent

from genesis_memory.proxy.proxy_server import GenesisProxyServer, ProxyTelemetry
from genesis_memory.daemon.server import Store


class MockEchoUpstream:
    """Mock upstream server that records received payloads."""

    def __init__(self) -> None:
        self.app = web.Application()
        self.app.router.add_post("/chat/completions", self.handle_chat)
        self.app.router.add_post("/v1/chat/completions", self.handle_chat)
        self.app.router.add_get("/models", self.handle_models)
        self.app.router.add_get("/v1/models", self.handle_models)
        self.runner: Any = None
        self.site: Any = None
        self.port: int = 0
        self.last_received_payload: Dict[str, Any] = {}
        self.fail_models: bool = False

    async def start(self) -> int:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        assert self.site._server is not None
        self.port = self.site._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def handle_chat(self, request: web.Request) -> web.Response:
        self.last_received_payload = await request.json()
        return web.json_response({
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "gpt-4o",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Understood, processing your request.",
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 15,
                "total_tokens": 135,
                "prompt_tokens_details": {"cached_tokens": 60},
            },
        })

    async def handle_models(self, request: web.Request) -> web.Response:
        if self.fail_models:
            return web.json_response({"error": "upstream down"}, status=500)
        return web.json_response({
            "object": "list",
            "data": [{"id": "upstream-model-1", "object": "model"}],
        })


def test_shadow_mode_verbatim_forwarding(tmp_path: Path) -> None:
    """In shadow mode, prompt is forwarded 100% verbatim, while tokens are tracked as stripped."""
    async def _run() -> None:
        db_file = tmp_path / "test_memory_shadow.db"
        temp_store = Store(str(db_file))
        temp_store.remember("The API port is 8080", kind="fact")

        mock = MockEchoUpstream()
        mock_port = await mock.start()

        proxy = GenesisProxyServer(
            upstream_url=f"http://127.0.0.1:{mock_port}",
            store=temp_store,
            mode="shadow",
            max_history_turns=0,
        )
        runner = web.AppRunner(proxy.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        proxy_port = site._server.sockets[0].getsockname()[1]

        try:
            messages = [
                {"role": "user", "content": "Can you review the full server configuration, timeout policies, and cluster limits for production deployment?"},
                {"role": "assistant", "content": "Atlas cluster configuration specifies max_pool_size=20, timeout_ms=30000, and keep_alive=true across all nodes."},
                {"role": "user", "content": "Please explain the rate limiting thresholds, burst parameters, and token lifecycle rules."},
                {"role": "assistant", "content": "Rate limits are set to 100 requests per minute with a burst allowance of 150. JWT expiry is 15 minutes with 7-day refresh tokens."},
                {"role": "user", "content": "Which port does API listen on?"},
            ]

            async with aiohttp.ClientSession() as client:
                async with client.post(
                    f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": messages, "stream": False},
                ) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert "choices" in data

            # In shadow mode: upstream received all 5 messages completely untouched!
            received_msgs = mock.last_received_payload.get("messages", [])
            assert len(received_msgs) == 5, f"Shadow mode altered messages: {len(received_msgs)} != 5"
            assert received_msgs[0]["content"].startswith("Can you review")

            # But telemetry counted prospective compaction!
            snap = proxy.telemetry.get_snapshot()
            assert snap["mode"] == "shadow"
            assert snap["compaction"]["tokens_stripped_total"] > 0
            assert snap["upstream"]["status_codes"]["2xx"] >= 1
        finally:
            await runner.cleanup()
            await mock.stop()
            temp_store.db.close()

    asyncio.run(_run())


def test_shadow_mode_no_anaphora_mutation_leak(tmp_path: Path) -> None:
    """Verify shadow mode leak fix: anaphoric query with rich TLB does NOT mutate upstream payload."""
    async def _run() -> None:
        db_file = tmp_path / "test_memory_shadow_leak.db"
        temp_store = Store(str(db_file))
        mock = MockEchoUpstream()
        mock_port = await mock.start()

        proxy = GenesisProxyServer(
            upstream_url=f"http://127.0.0.1:{mock_port}",
            store=temp_store,
            mode="shadow",
            enable_anaphora_rewrite=True,
        )
        # Pre-seed TLB with salient entities
        proxy.tlb["errors"] = ["ZeroDivisionError"]
        proxy.tlb["touched_files"] = ["src/tax_engine.py"]

        runner = web.AppRunner(proxy.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        proxy_port = site._server.sockets[0].getsockname()[1]

        try:
            messages = [
                {"role": "user", "content": "Fix this bug"}
            ]
            async with aiohttp.ClientSession() as client:
                async with client.post(
                    f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": messages, "stream": False},
                ) as resp:
                    assert resp.status == 200

            # In shadow mode: upstream MUST receive verbatim raw content without leak
            received_msgs = mock.last_received_payload.get("messages", [])
            assert len(received_msgs) == 1
            assert received_msgs[0]["content"] == "Fix this bug", "Shadow mode leaked rewritten query to upstream!"

            # Telemetry recorded prospective rewrite
            snap = proxy.telemetry.get_snapshot()
            assert snap["distillation"]["anaphora_rewrites"] == 1
        finally:
            await runner.cleanup()
            await mock.stop()
            temp_store.db.close()

    asyncio.run(_run())


def test_live_mode_real_compaction(tmp_path: Path) -> None:
    """In live mode, history older than max_history_turns is stripped and pinned into capsule."""
    async def _run() -> None:
        db_file = tmp_path / "test_memory_live.db"
        temp_store = Store(str(db_file))
        temp_store.remember("The API port is 8080", kind="fact")

        mock = MockEchoUpstream()
        mock_port = await mock.start()

        proxy = GenesisProxyServer(
            upstream_url=f"http://127.0.0.1:{mock_port}",
            store=temp_store,
            mode="live",
            max_history_turns=0,
        )
        runner = web.AppRunner(proxy.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        proxy_port = site._server.sockets[0].getsockname()[1]

        try:
            messages = [
                {"role": "user", "content": "Can you review the full server configuration, timeout policies, and cluster limits for production deployment?"},
                {"role": "assistant", "content": "Atlas cluster configuration specifies max_pool_size=20, timeout_ms=30000, and keep_alive=true across all nodes."},
                {"role": "user", "content": "Please explain the rate limiting thresholds, burst parameters, and token lifecycle rules."},
                {"role": "assistant", "content": "Rate limits are set to 100 requests per minute with a burst allowance of 150. JWT expiry is 15 minutes with 7-day refresh tokens."},
                {"role": "user", "content": "Which port does API listen on?"},
            ]

            async with aiohttp.ClientSession() as client:
                async with client.post(
                    f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": messages, "stream": False},
                ) as resp:
                    assert resp.status == 200

            # In live mode: upstream receives compacted messages (older turns stripped)!
            received_msgs = mock.last_received_payload.get("messages", [])
            assert len(received_msgs) < 5
            assert proxy.telemetry.mode == "live"
        finally:
            await runner.cleanup()
            await mock.stop()
            temp_store.db.close()

    asyncio.run(_run())


def test_fail_open_bypass_with_reason_code(tmp_path: Path) -> None:
    """Validator rejections or malformed structures trigger transparent fail-open bypass with recorded reason code."""
    async def _run() -> None:
        db_file = tmp_path / "test_memory_bypass.db"
        temp_store = Store(str(db_file))

        mock = MockEchoUpstream()
        mock_port = await mock.start()

        proxy = GenesisProxyServer(
            upstream_url=f"http://127.0.0.1:{mock_port}",
            store=temp_store,
            mode="live",
        )
        runner = web.AppRunner(proxy.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        proxy_port = site._server.sockets[0].getsockname()[1]

        try:
            # Send an orphaned tool_use without a matching tool_result (integrity violation)
            corrupted_messages = [
                {"role": "user", "content": "Run tool"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "call_orphan", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
                {"role": "user", "content": "Now what?"},
            ]

            async with aiohttp.ClientSession() as client:
                async with client.post(
                    f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": corrupted_messages},
                ) as resp:
                    assert resp.status == 200

            # Fail-Open: Upstream STILL received the request (user work did NOT halt!)
            assert len(mock.last_received_payload.get("messages", [])) == 3
            # Telemetry recorded bypass and reason code
            snap = proxy.telemetry.get_snapshot()
            assert snap["bypass"]["bypasses_total"] >= 1
            assert snap["bypass"]["reasons"]["validator_reject"] >= 1
        finally:
            await runner.cleanup()
            await mock.stop()
            temp_store.db.close()

    asyncio.run(_run())


def test_multi_session_tlb_isolation(tmp_path: Path) -> None:
    """Two different sessions maintain separate 1-turn locality buffers without cross-talk."""
    db_file = tmp_path / "test_memory_tlb.db"
    temp_store = Store(str(db_file))
    proxy = GenesisProxyServer(store=temp_store)
    
    # Session Alpha receives turn with touched file A
    proxy._update_tlb_and_distill(
        messages=[{"role": "user", "content": "cat file_alpha.py"}],
        resp_text="content of alpha",
        resp_tool_calls=[{"function": {"name": "read_file", "arguments": json.dumps({"path": "file_alpha.py"})}}],
        session_id="session_alpha",
    )

    # Session Beta receives turn with touched file B
    proxy._update_tlb_and_distill(
        messages=[{"role": "user", "content": "cat file_beta.py"}],
        resp_text="content of beta",
        resp_tool_calls=[{"function": {"name": "read_file", "arguments": json.dumps({"path": "file_beta.py"})}}],
        session_id="session_beta",
    )

    tlb_alpha = proxy._get_tlb("session_alpha")
    tlb_beta = proxy._get_tlb("session_beta")

    assert "file_alpha.py" in tlb_alpha["touched_files"]
    assert "file_beta.py" not in tlb_alpha["touched_files"]

    assert "file_beta.py" in tlb_beta["touched_files"]
    assert "file_alpha.py" not in tlb_beta["touched_files"]
    temp_store.db.close()


def test_resilient_models_fallback() -> None:
    """When upstream fails or is down, /v1/models and /models return standard fallback catalog."""
    async def _run() -> None:
        mock = MockEchoUpstream()
        mock.fail_models = True
        mock_port = await mock.start()

        proxy = GenesisProxyServer(upstream_url=f"http://127.0.0.1:{mock_port}")
        runner = web.AppRunner(proxy.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        proxy_port = site._server.sockets[0].getsockname()[1]

        try:
            async with aiohttp.ClientSession() as client:
                # Test /v1/models
                async with client.get(f"http://127.0.0.1:{proxy_port}/v1/models") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["object"] == "list"
                    model_ids = [m["id"] for m in data["data"]]
                    assert "gpt-4o" in model_ids
                    assert "genesis-stateless" in model_ids

                # Test /models (without /v1)
                async with client.get(f"http://127.0.0.1:{proxy_port}/models") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["object"] == "list"
        finally:
            await runner.cleanup()
            await mock.stop()

    asyncio.run(_run())


def test_ttft_percentiles_and_telemetry_health() -> None:
    """Verifies that p50/p95 TTFT and health endpoint return clean metrics."""
    telemetry = ProxyTelemetry(mode="shadow")
    for latency in [10.0, 20.0, 30.0, 40.0, 100.0]:
        telemetry.record_request(
            is_streaming=True,
            ttft_ms=latency,
            total_latency_ms=latency * 2,
            prompt_tokens=100,
            cached_tokens=50,
            completion_tokens=20,
        )

    snap = telemetry.get_snapshot()
    perf = snap["performance"]
    assert perf["p50_ttft_ms"] == 30.0
    assert perf["p95_ttft_ms"] == 100.0
    assert perf["max_ttft_ms"] == 100.0
    assert perf["mean_ttft_ms"] == 40.0
    assert snap["cache_hit_rate"] == 0.5


def test_candidate_model_auto_failover_on_rate_limit() -> None:
    """Verifies that when upstream returns 429 Quota Exceeded on primary model,
    the proxy automatically fails over to the next candidate model and returns 200 OK."""
    async def _run() -> None:
        attempts: List[str] = []

        class MockQuotaUpstream:
            def __init__(self) -> None:
                self.app = web.Application()
                self.app.router.add_post("/v1/chat/completions", self.handle_chat)
                self.runner: Any = None
                self.site: Any = None

            async def start(self) -> int:
                self.runner = web.AppRunner(self.app)
                await self.runner.setup()
                self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
                await self.site.start()
                return self.site._server.sockets[0].getsockname()[1]

            async def stop(self) -> None:
                if self.runner:
                    await self.runner.cleanup()

            async def handle_chat(self, request: web.Request) -> web.Response:
                payload = await request.json()
                model = payload.get("model")
                attempts.append(model)
                if model == "gemini-3.5-flash":
                    return web.json_response(
                        {"error": {"code": 429, "message": "Resource exhausted (quota limit: 20)"}},
                        status=429,
                    )
                return web.json_response({
                    "id": "chatcmpl-failover",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "Failover successful!"},
                        "finish_reason": "stop",
                    }],
                })

        mock = MockQuotaUpstream()
        mock_port = await mock.start()

        proxy = GenesisProxyServer(
            upstream_url=f"http://127.0.0.1:{mock_port}/v1",
            target_model="gemini-3.5-flash",
        )
        runner = web.AppRunner(proxy.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        proxy_port = site._server.sockets[0].getsockname()[1]

        try:
            async with aiohttp.ClientSession() as client:
                req_payload = {
                    "model": "genesis-stateless",
                    "messages": [{"role": "user", "content": "Hello!"}],
                    "stream": False,
                }
                async with client.post(
                    f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                    json=req_payload,
                ) as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert body["choices"][0]["message"]["content"] == "Failover successful!"
                    # Primary was attempted and failed, then failover model was tried
                    assert "gemini-3.5-flash" in attempts
                    assert "gemini-3-flash-preview" in attempts
        finally:
            await runner.cleanup()
            await mock.stop()

    asyncio.run(_run())


def test_gemini_thought_signature_automatic_injection() -> None:
    """Verifies that assistant tool calls stripped by OpenAI-compatible clients (like OpenCode)
    have their thought_signature automatically re-attached or injected before forwarding to Gemini."""
    async def _run() -> None:
        received_payload: Dict[str, Any] = {}

        class MockGeminiUpstream:
            def __init__(self) -> None:
                self.app = web.Application()
                self.app.router.add_post("/v1/chat/completions", self.handle_chat)
                self.runner: Any = None
                self.site: Any = None

            async def start(self) -> int:
                self.runner = web.AppRunner(self.app)
                await self.runner.setup()
                self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
                await self.site.start()
                return self.site._server.sockets[0].getsockname()[1]

            async def stop(self) -> None:
                if self.runner:
                    await self.runner.cleanup()

            async def handle_chat(self, request: web.Request) -> web.Response:
                nonlocal received_payload
                received_payload = await request.json()
                return web.json_response({
                    "id": "chatcmpl-sig",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "gemini-3.5-flash",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "Tool result processed successfully."},
                        "finish_reason": "stop",
                    }],
                })

        mock = MockGeminiUpstream()
        mock_port = await mock.start()

        proxy = GenesisProxyServer(
            upstream_url=f"http://127.0.0.1:{mock_port}/v1",
            target_model="gemini-3.5-flash",
        )
        runner = web.AppRunner(proxy.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        proxy_port = site._server.sockets[0].getsockname()[1]

        try:
            async with aiohttp.ClientSession() as client:
                # OpenCode sends stripped assistant tool_call (missing extra_content)
                req_payload = {
                    "model": "genesis-stateless",
                    "messages": [
                        {"role": "user", "content": "List files in directory"},
                        {
                            "role": "assistant",
                            "tool_calls": [{
                                "id": "call_glob_abc123",
                                "type": "function",
                                "function": {"name": "default_api:glob", "arguments": "{\"pattern\": \"*\"}"}
                            }]
                        },
                        {
                            "role": "tool",
                            "tool_call_id": "call_glob_abc123",
                            "content": "file1.txt\nfile2.txt"
                        }
                    ],
                    "stream": False,
                }
                async with client.post(
                    f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                    json=req_payload,
                ) as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert body["choices"][0]["message"]["content"] == "Tool result processed successfully."

                    # Verify that upstream received the injected thought_signature!
                    upstream_messages = received_payload.get("messages", [])
                    assistant_msg = upstream_messages[1]
                    assert assistant_msg["role"] == "assistant"
                    tc = assistant_msg["tool_calls"][0]
                    assert "extra_content" in tc
                    assert tc["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator"
        finally:
            await runner.cleanup()
            await mock.stop()

    asyncio.run(_run())



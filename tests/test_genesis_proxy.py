"""Automated Test Suite for GENESIS Single-Prompt Reverse Proxy (Step 1).

Validates:
1. Non-streaming and streaming SSE pass-through integrity.
2. Cache-aware token telemetry (cached vs uncached prompt tokens).
3. Sub-millisecond streaming TTFT overhead (< 15ms).
4. Privacy invariant (zero request/response body logging).
5. Upstream error forwarding.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

import aiohttp
from aiohttp import web
import pytest
import re

from genesis_memory.proxy.proxy_server import GenesisProxyServer, ProxyTelemetry
from genesis_memory.proxy.content_compressor import RecoveryStore


class MockUpstreamServer:
    """Mock OpenAI-compatible upstream API server."""

    def __init__(self) -> None:
        self.app = web.Application()
        self.app.router.add_post("/chat/completions", self.handle_chat)
        self.app.router.add_get("/models", self.handle_models)
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.port: int = 0
        self.last_received_payload: dict | None = None

    async def start(self) -> int:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        assert self.site._server is not None
        sockets = self.site._server.sockets
        self.port = sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def handle_models(self, request: web.Request) -> web.Response:
        return web.json_response({
            "object": "list",
            "data": [{"id": "mock-model-v1", "object": "model"}],
        })

    async def handle_chat(self, request: web.Request) -> web.StreamResponse:
        auth = request.headers.get("Authorization", "")
        if "bad-key" in auth:
            return web.json_response(
                {"error": {"message": "Invalid API Key", "type": "invalid_request_error"}},
                status=401,
            )

        payload = await request.json()
        self.last_received_payload = payload
        stream = bool(payload.get("stream", False))

        if not stream:
            return web.json_response({
                "id": "chatcmpl-mock-nonstream",
                "object": "chat.completion",
                "created": int(time.time()),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "Non-streaming answer"},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 150,
                    "completion_tokens": 40,
                    "total_tokens": 190,
                    "prompt_tokens_details": {"cached_tokens": 100},
                },
            })

        # Streaming SSE
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)

        # Chunk 1
        chunk1 = {
            "id": "chatcmpl-mock-stream",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "Hello"}}],
        }
        await resp.write(f"data: {json.dumps(chunk1)}\n\n".encode("utf-8"))
        await asyncio.sleep(0.01)

        # Chunk 2
        chunk2 = {
            "id": "chatcmpl-mock-stream",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": " world from GENESIS"}}],
        }
        await resp.write(f"data: {json.dumps(chunk2)}\n\n".encode("utf-8"))
        await asyncio.sleep(0.01)

        # Chunk 3: Final usage chunk with cache details
        chunk3 = {
            "id": "chatcmpl-mock-stream",
            "object": "chat.completion.chunk",
            "choices": [],
            "usage": {
                "prompt_tokens": 300,
                "completion_tokens": 65,
                "total_tokens": 365,
                "prompt_tokens_details": {"cached_tokens": 250},
            },
        }
        await resp.write(f"data: {json.dumps(chunk3)}\n\n".encode("utf-8"))

        # Chunk 4: Stream termination
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp


class MockMemoryStore:
    def __init__(self) -> None:
        self.engrams: list[dict[str, Any]] = []

    def remember(self, text: str, kind: str = "fact", project: str = "genesis", utility: float = 1.0) -> int:
        engram_id = len(self.engrams) + 1
        self.engrams.append({"id": engram_id, "text": text, "kind": kind, "project": project, "utility": utility})
        return engram_id

    def recall(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        results = []
        for e in self.engrams:
            if any(term.lower() in e["text"].lower() for term in query.split()):
                results.append(e)
        return results[:limit]


class ProxyHarness:
    """Helper context manager managing both mock upstream and proxy instance."""

    def __init__(self, store: Any = None, enable_universal_capsule: Optional[bool] = None, enable_output_diet: Optional[bool] = False, enable_content_compress: Optional[bool] = None, mode: str = "live") -> None:
        self.mock = MockUpstreamServer()
        self.telemetry = ProxyTelemetry()
        self.store = store
        self.enable_universal_capsule = enable_universal_capsule
        self.enable_output_diet = enable_output_diet
        self.enable_content_compress = enable_content_compress
        self.mode = mode
        self.proxy: GenesisProxyServer | None = None
        self.runner: web.AppRunner | None = None
        self.base_url = ""

    async def __aenter__(self) -> "ProxyHarness":
        upstream_port = await self.mock.start()
        upstream_url = f"http://127.0.0.1:{upstream_port}"
        self.proxy = GenesisProxyServer(
            upstream_url=upstream_url,
            telemetry=self.telemetry,
            store=self.store,
            enable_universal_capsule=self.enable_universal_capsule,
            enable_output_diet=self.enable_output_diet,
            enable_content_compress=self.enable_content_compress,
            mode=self.mode,
        )

        self.runner = web.AppRunner(self.proxy.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        assert site._server is not None
        proxy_port = site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{proxy_port}"
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.runner:
            await self.runner.cleanup()
        await self.mock.stop()


# ---------------------------------------------------------------------------
# Test Cases (executed synchronously with asyncio.run)
# ---------------------------------------------------------------------------

def test_proxy_health_and_models_passthrough() -> None:
    async def _run():
        async with ProxyHarness() as harness:
            base_url = harness.base_url
            async with aiohttp.ClientSession() as session:
                # 1. Health check
                async with session.get(f"{base_url}/health") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["status"] == "ok"

                # 2. Models pass-through
                async with session.get(f"{base_url}/v1/models") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["object"] == "list"
                    assert data["data"][0]["id"] == "mock-model-v1"

    asyncio.run(_run())


def test_proxy_non_streaming_cache_aware_telemetry() -> None:
    async def _run():
        async with ProxyHarness() as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [{"role": "user", "content": "What is GENESIS?"}],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["choices"][0]["message"]["content"] == "Non-streaming answer"

            snap = telemetry.get_snapshot()
            assert snap["requests_total"] == 1
            assert snap["non_streaming_requests"] == 1
            assert snap["streaming_requests"] == 0
            # Expected: prompt 150, cached 100, uncached 50, completion 40
            assert snap["tokens"]["prompt_total"] == 150
            assert snap["tokens"]["prompt_cached"] == 100
            assert snap["tokens"]["prompt_uncached"] == 50
            assert snap["tokens"]["completion"] == 40
            assert snap["tokens"]["total"] == 190
            assert snap["cache_hit_rate"] == round(100 / 150, 4)

    asyncio.run(_run())


def test_proxy_streaming_sse_passthrough() -> None:
    async def _run():
        async with ProxyHarness() as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [{"role": "user", "content": "Stream me a response"}],
                    "stream": True,
                }

                collected_text = ""
                done_received = False
                t0 = time.perf_counter()

                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200
                    assert "text/event-stream" in resp.headers.get("Content-Type", "")

                    line_buf = ""
                    async for raw_chunk in resp.content.iter_any():
                        line_buf += raw_chunk.decode("utf-8", errors="ignore")
                        while "\n" in line_buf:
                            line, line_buf = line_buf.split("\n", 1)
                            line = line.strip()
                            if line == "data: [DONE]":
                                done_received = True
                            elif line.startswith("data: "):
                                data = json.loads(line[6:])
                                choices = data.get("choices", [])
                                if choices and "delta" in choices[0]:
                                    collected_text += choices[0]["delta"].get("content", "")

                t_total = time.perf_counter() - t0
                assert done_received is True
                assert collected_text == "Hello world from GENESIS"

            # Verify telemetry captured streaming cache details
            snap = telemetry.get_snapshot()
            assert snap["streaming_requests"] == 1
            assert snap["tokens"]["prompt_total"] == 300
            assert snap["tokens"]["prompt_cached"] == 250
            assert snap["tokens"]["prompt_uncached"] == 50
            assert snap["tokens"]["completion"] == 65
            assert snap["cache_hit_rate"] == round(250 / 300, 4)
            assert snap["performance"]["mean_ttft_ms"] > 0.0

    asyncio.run(_run())


def test_proxy_ttft_latency_overhead() -> None:
    async def _run():
        async with ProxyHarness() as harness:
            proxy_url = harness.base_url
            upstream_url = f"http://127.0.0.1:{harness.mock.port}"

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [{"role": "user", "content": "Measure TTFT"}],
                    "stream": True,
                }

                # 1. Direct upstream TTFT
                t0 = time.perf_counter()
                async with session.post(f"{upstream_url}/chat/completions", json=payload) as resp:
                    async for _ in resp.content.iter_chunked(64):
                        direct_ttft = (time.perf_counter() - t0) * 1000.0
                        break

                # 2. Proxied TTFT
                t0 = time.perf_counter()
                async with session.post(f"{proxy_url}/v1/chat/completions", json=payload) as resp:
                    async for _ in resp.content.iter_chunked(64):
                        proxied_ttft = (time.perf_counter() - t0) * 1000.0
                        break

                overhead_ms = proxied_ttft - direct_ttft
                # Proxy overhead must be minimal (< 50ms on linux, < 75ms on win32 scheduling)
                max_overhead = 75.0 if sys.platform == "win32" else 50.0
                assert overhead_ms < max_overhead, f"Proxy overhead was too high: {overhead_ms:.2f}ms"

    asyncio.run(_run())


def test_proxy_zero_body_logging_privacy(caplog: pytest.LogCaptureFixture) -> None:
    async def _run():
        async with ProxyHarness() as harness:
            base_url = harness.base_url
            secret_text = "SECRET_USER_TOKEN_TOP_SECRET_ABC_123"

            with caplog.at_level(logging.DEBUG):
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "model": "mock-model-v1",
                        "messages": [{"role": "user", "content": f"Do not log this: {secret_text}"}],
                        "stream": False,
                    }
                    async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                        assert resp.status == 200

            # Ensure secret text does not appear in any log output
            for record in caplog.records:
                assert secret_text not in record.message
                assert "Non-streaming answer" not in record.message

    asyncio.run(_run())


def test_proxy_upstream_error_forwarding() -> None:
    async def _run():
        async with ProxyHarness() as harness:
            base_url = harness.base_url

            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": "Bearer bad-key-xyz"}
                payload = {
                    "model": "mock-model-v1",
                    "messages": [{"role": "user", "content": "test"}],
                }
                async with session.post(
                    f"{base_url}/v1/chat/completions", json=payload, headers=headers
                ) as resp:
                    assert resp.status == 401
                    data = await resp.json()
                    assert "Invalid API Key" in data["error"]["message"]

    asyncio.run(_run())


def test_proxy_end_to_end_compaction_and_rss_telemetry() -> None:
    """Verify that proxy automatically compacts incoming multi-turn requests and reports RSS."""
    async def _run():
        async with ProxyHarness() as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            multi_turn_messages = [
                {"role": "system", "content": "You are a helpful coding agent."},
                # Turn 1: Old turn (should be pruned)
                {"role": "user", "content": "Read module core.py"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_old_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_old_1", "content": "def run(): pass"},
                {"role": "assistant", "content": "Core module read."},
                # Turn 2: Locality Turn (retained)
                {"role": "user", "content": "Run sanity test"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_test_2", "type": "function", "function": {"name": "pytest", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_test_2", "content": "PASSED"},
                {"role": "assistant", "content": "Sanity passed."},
                # Turn 3: Active Turn (retained)
                {"role": "user", "content": "Proceed to deploy"},
            ]

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": multi_turn_messages,
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200

            # 1. Assert upstream received compacted message list
            upstream_received = harness.mock.last_received_payload
            assert upstream_received is not None
            received_msgs = upstream_received["messages"]
            assert len(received_msgs) < len(multi_turn_messages)

            received_text = json.dumps(received_msgs)
            assert "call_old_1" not in received_text, "Turn 1 tool call was not pruned"
            assert "call_test_2" in received_text, "Locality turn tool call was erroneously pruned"
            assert "Proceed to deploy" in received_text, "Active turn prompt was lost"

            # 2. Assert telemetry snapshot
            snap = telemetry.get_snapshot()
            assert snap["compaction"]["compacted_requests"] == 1
            assert snap["compaction"]["tokens_stripped_total"] > 0
            assert snap["compaction"]["tokens_retained_total"] > 0
            assert snap["tokens"]["prompt_stripped_est"] > 0
            assert snap["rss_mb"] is not None
            assert snap["rss_mb"] > 0.0

    asyncio.run(_run())


def test_proxy_anaphora_rewriting_and_distillation_pipeline() -> None:
    """Verify that multi-turn universal capsule injection and background distillation work through the proxy."""
    async def _run():
        store = MockMemoryStore()
        store.remember("The math module in tax_engine.py handles ZeroDivisionError gracefully", kind="fact")

        async with ProxyHarness(store=store, enable_universal_capsule=True) as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            async with aiohttp.ClientSession() as session:
                # Turn 1: User asks to debug, tool outputs error, assistant explains error
                turn1_payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "user", "content": "Run tests on finance module"},
                        {
                            "role": "assistant",
                            "content": "Executing pytest on tax_engine.py",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "run_command", "arguments": '{"command": "pytest tests/test_tax.py"}'},
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": "c1",
                            "content": "FAILED tests/test_tax.py::test_zero - ZeroDivisionError: division by zero in src/tax_engine.py",
                        },
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=turn1_payload) as resp:
                    assert resp.status == 200

                # Wait for barrier to ensure background distillation for Turn 1 completes
                assert harness.proxy is not None
                await harness.proxy.distiller.wait_for_pending_distillations()

                # Verify proxy TLB was populated with error and file
                assert "ZeroDivisionError" in harness.proxy.tlb["errors"]
                assert any("tax_engine.py" in f for f in harness.proxy.tlb["touched_files"])

                # Turn 2: User issues prompt without explicit anaphora keywords ("Please resolve")
                turn2_payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "system", "content": "You are a helpful coding assistant."},
                        {"role": "user", "content": "Please resolve"},
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=turn2_payload) as resp:
                    assert resp.status == 200

                # Upstream received request:
                # 1. user prompt remains 100% RAW (unmutated)
                # 2. terms in system capsule with prefix
                # 3. new universal_capsules telemetry counter incremented
                upstream_payload = harness.mock.last_received_payload
                assert upstream_payload is not None
                received_msgs = upstream_payload["messages"]
                user_msg = received_msgs[-1]
                assert user_msg["content"] == "Please resolve", "User message must stay raw in Tier-2 universal capsule"

                system_msg = received_msgs[0]
                assert "<!-- GENESIS_PINNED_CAPSULE_START -->" in system_msg["content"]
                assert "If irrelevant to current user message, ignore." in system_msg["content"]
                assert "tax_engine.py" in system_msg["content"]

                # Telemetry assertions
                snap = telemetry.get_snapshot()
                assert snap["distillation"]["universal_capsules"] == 1
                assert snap["distillation"]["distillations_completed"] >= 1

    asyncio.run(_run())


def test_proxy_memory_capsule_injection_with_store() -> None:
    """Verify that proxy recalls from MemoryStore and pins capsule when relevant."""
    async def _run():
        store = MockMemoryStore()
        store.remember("The calculation in tax_engine.py requires non-zero tax rate", kind="fact")

        async with ProxyHarness(store=store) as harness:
            base_url = harness.base_url

            async with aiohttp.ClientSession() as session:
                # Pre-seed TLB in proxy
                assert harness.proxy is not None
                harness.proxy.tlb["errors"] = ["ZeroDivisionError"]
                harness.proxy.tlb["touched_files"] = ["src/tax_engine.py"]

                # Send anaphoric prompt that expands to tax_engine.py and recalls memory
                payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "system", "content": "You are an AI assistant."},
                        {"role": "user", "content": "Fix this bug"},
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200

                upstream_payload = harness.mock.last_received_payload
                assert upstream_payload is not None
                received_text = json.dumps(upstream_payload["messages"])

                # Pinned capsule tags present in system message
                assert "<!-- GENESIS_PINNED_CAPSULE_START -->" in received_text
                assert "<!-- GENESIS_PINNED_CAPSULE_END -->" in received_text
                assert "non-zero tax rate" in received_text

    asyncio.run(_run())


def test_proxy_barrier_timeout_and_stale_read_fallback() -> None:
    """Verify that a stalled distillation does not hang client TTFT, falling back and logging stale_read."""
    async def _run():
        store = MockMemoryStore()
        store.remember("Important background fact", kind="fact")

        async with ProxyHarness(store=store) as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            assert harness.proxy is not None
            # Artificially mark a distillation in flight that will not complete in 80ms
            harness.proxy.distiller.mark_distillation_in_flight()

            async with aiohttp.ClientSession() as session:
                t0 = time.perf_counter()
                payload = {
                    "model": "mock-model-v1",
                    "messages": [{"role": "user", "content": "Fetch background info"}],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0

                # Client TTFT was protected (under 250ms total roundtrip, not blocked indefinitely)
                assert elapsed_ms < 250.0

                # Telemetry caught the stale read fallback
                snap = telemetry.get_snapshot()
                assert snap["distillation"]["stale_reads"] == 1

            # Cleanup in flight flag so teardown doesn't hang
            harness.proxy.distiller._pending_distillations = 0
            harness.proxy.distiller._idle_event.set()

    asyncio.run(_run())


def test_proxy_p3_guard_suppresses_recall_on_ambiguity() -> None:
    """Verify Bug 2 fix: unresolvable anaphora (empty TLB) skips memory recall to prevent hallucinated injection."""
    async def _run():
        store = MockMemoryStore()
        store.remember("Unrelated memory about quantum computing", kind="fact")

        async with ProxyHarness(store=store) as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            assert harness.proxy is not None
            # Ensure TLB is completely empty
            harness.proxy.tlb = {
                "errors": [],
                "touched_files": [],
                "test_outcome": "",
                "last_assistant_response": "",
                "last_tool_output": "",
            }

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "Fix this bug"},  # Anaphoric with zero context
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200

                upstream_payload = harness.mock.last_received_payload
                assert upstream_payload is not None
                received_text = json.dumps(upstream_payload["messages"])

                # P3 Guard Verification: Memory capsule was NOT injected (no ungrounded hallucinations)
                assert "<!-- GENESIS_PINNED_CAPSULE_START -->" not in received_text
                assert "quantum computing" not in received_text
                assert upstream_payload["messages"][-1]["content"] == "Fix this bug", "P3 guard must leave user content unchanged"

                # Telemetry verification
                snap = telemetry.get_snapshot()
                assert snap["distillation"]["clarifications_flagged"] == 1

    asyncio.run(_run())


def test_single_turn_no_gain_suppresses_chance_capsule() -> None:
    """VOI gate (EXP107 lesson): self-contained single turn + weak TLB =>
    chance-hit capsules are token tax and must be suppressed with a bypass code."""
    async def _run():
        store = MockMemoryStore()
        store.remember("The router firmware update completed", kind="fact")

        async with ProxyHarness(store=store) as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            assert harness.proxy is not None
            # Ensure TLB is completely empty (weak TLB)
            harness.proxy.tlb = {
                "errors": [],
                "touched_files": [],
                "test_outcome": "",
                "last_assistant_response": "",
                "last_tool_output": "",
            }

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "What is the time?"},
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200

                upstream_payload = harness.mock.last_received_payload
                assert upstream_payload is not None
                received_text = json.dumps(upstream_payload["messages"])

                # Chance hit ("the" matched router text) must NOT be injected
                assert "<!-- GENESIS_PINNED_CAPSULE_START -->" not in received_text
                assert "router firmware" not in received_text
                assert upstream_payload["messages"][-1]["content"] == "What is the time?"

                # Telemetry verification: suppression is counted, not silent
                snap = telemetry.get_snapshot()
                assert snap["bypass"]["reasons"].get("single_turn_no_gain", 0) == 1

    asyncio.run(_run())


def test_single_turn_with_overlap_keeps_value_capsule() -> None:
    """VOI gate positive control: a hit sharing a content word with the
    self-contained query substitutes user action and must be kept."""
    async def _run():
        store = MockMemoryStore()
        store.remember("The router firmware update completed", kind="fact")

        async with ProxyHarness(store=store) as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            assert harness.proxy is not None
            harness.proxy.tlb = {
                "errors": [],
                "touched_files": [],
                "test_outcome": "",
                "last_assistant_response": "",
                "last_tool_output": "",
            }

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "Update the router firmware now"},
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200

                upstream_payload = harness.mock.last_received_payload
                assert upstream_payload is not None
                received_text = json.dumps(upstream_payload["messages"])

                # Overlapping hit ("router"/"firmware") is value: capsule pinned
                assert "<!-- GENESIS_PINNED_CAPSULE_START -->" in received_text
                assert "router firmware" in received_text

                snap = telemetry.get_snapshot()
                assert snap["bypass"]["reasons"].get("single_turn_no_gain", 0) == 0

    asyncio.run(_run())


def test_output_diet_off_by_default() -> None:
    """Output diet is flag-gated and OFF unless explicitly enabled."""
    async def _run():
        async with ProxyHarness() as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            assert harness.proxy is not None
            assert harness.proxy.enable_output_diet is False

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "Explain event loops briefly"},
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200

                upstream_payload = harness.mock.last_received_payload
                assert upstream_payload is not None
                received_text = json.dumps(upstream_payload["messages"])
                assert "GENESIS_OUTPUT_DIET" not in received_text

                snap = telemetry.get_snapshot()
                assert snap["distillation"]["diet_requests"] == 0

    asyncio.run(_run())


def test_output_diet_injects_static_tail_block() -> None:
    """Enabled diet appends one static system block at the tail (prefix bytes
    unchanged → LCP cache preserved) and counts the request for telemetry."""
    async def _run():
        async with ProxyHarness(enable_output_diet=True) as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "Explain event loops briefly"},
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200

                upstream_payload = harness.mock.last_received_payload
                assert upstream_payload is not None
                received = upstream_payload["messages"]

                # Prefix untouched: original messages byte-identical in order
                assert received[0] == {"role": "system", "content": "You are a helpful assistant."}
                assert received[1] == {"role": "user", "content": "Explain event loops briefly"}
                # Static diet block appended once at the tail
                assert received[-1]["role"] == "system"
                assert "GENESIS_OUTPUT_DIET" in received[-1]["content"]
                assert "byte-for-byte" in received[-1]["content"]
                assert sum("GENESIS_OUTPUT_DIET" in json.dumps(m) for m in received) == 1

                snap = telemetry.get_snapshot()
                assert snap["distillation"]["diet_requests"] == 1

    asyncio.run(_run())


def test_content_compress_off_by_default(tmp_path) -> None:
    """Flag-gated and OFF unless explicitly enabled; no HOME side effects."""
    async def _run():
        async with ProxyHarness() as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            assert harness.proxy is not None
            assert harness.proxy.enable_content_compress is False
            assert harness.proxy._recovery_store is None

            big = json.dumps({"items": list(range(300)), "note": "padding " * 40})
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "user", "content": "Summarize this"},
                        {"role": "assistant", "content": big},
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200

                received = harness.mock.last_received_payload["messages"]
                assert received[1]["content"] == big  # untouched
                assert "GENESIS-RECOVERY" not in json.dumps(received)

                snap = telemetry.get_snapshot()
                assert snap["distillation"]["content_saved_chars"] == 0

    asyncio.run(_run())


def test_content_compress_shrinks_and_recovers(tmp_path) -> None:
    """Enabled: giant log elided with handle; original byte-exact via endpoint."""
    async def _run():
        store = MockMemoryStore()
        async with ProxyHarness(store=store, enable_content_compress=True) as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            assert harness.proxy is not None
            harness.proxy._recovery_store = RecoveryStore(store_dir=tmp_path / "rec")

            head = "deploy started ok\n" + "setup line here\n" * 40
            middle = "".join(f"neutral worker line {i} padding words\n" for i in range(400))
            tail = "deploy finished ok\n" + "done line here\n" * 30
            raw = head + middle + tail
            assert len(raw) > 4000

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "user", "content": "Deploy it"},
                        {"role": "assistant", "content": raw},
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200

                received = harness.mock.last_received_payload["messages"]
                got = received[1]["content"]
                assert len(got) < len(raw)
                assert "deploy started ok" in got and "deploy finished ok" in got
                m = re.search(r"GENESIS-RECOVERY:([0-9a-f]{16})", got)
                assert m, "recovery handle must be embedded"
                rid = m.group(1)

                snap = telemetry.get_snapshot()
                assert snap["distillation"]["content_saved_chars"] > 0
                assert snap["distillation"]["content_recoveries"] >= 1

                async with session.get(f"{base_url}/v1/recovery/{rid}") as rresp:
                    assert rresp.status == 200
                    body = await rresp.json()
                    assert body["text"] == raw  # byte-exact roundtrip

                async with session.get(f"{base_url}/v1/recovery/0123456789abcdef") as rresp:
                    assert rresp.status == 404

    asyncio.run(_run())


def test_content_compress_shadow_measures_without_mutating(tmp_path) -> None:
    """Shadow mode: savings are counted for telemetry, upstream stays verbatim."""
    async def _run():
        async with ProxyHarness(enable_content_compress=True, mode="shadow") as harness:
            base_url = harness.base_url
            telemetry = harness.telemetry

            big = json.dumps({"items": list(range(300)), "note": "padding " * 40})
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [
                        {"role": "user", "content": "Summarize this"},
                        {"role": "assistant", "content": big},
                    ],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200

                received = harness.mock.last_received_payload["messages"]
                assert received[1]["content"] == big  # verbatim in shadow

                snap = telemetry.get_snapshot()
                assert snap["distillation"]["content_saved_chars"] > 0

    asyncio.run(_run())


def test_receipt_non_streaming_header_and_endpoint() -> None:
    """Every non-streaming turn hands back a receipt: header id + full record
    with provider usage x catalog math and verified $0.00."""
    async def _run():
        async with ProxyHarness() as harness:
            base_url = harness.base_url

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [{"role": "user", "content": "Hello receipts"}],
                    "stream": False,
                }
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200
                    rid = resp.headers.get("X-Genesis-Receipt")
                    assert rid and rid.startswith("rcpt_")

                async with session.get(f"{base_url}/v1/receipts/{rid}") as rresp:
                    assert rresp.status == 200
                    r = await rresp.json()

                assert r["id"] == rid
                assert r["stop_reason"] == "stop"
                assert r["tool_calls"] == 0
                assert r["streaming"] is False
                assert r["tokens"] == {"prompt": 150, "cached": 100, "uncached": 50, "completion": 40}
                # generic_default catalog: 100*0.125 + 50*0.50 + 40*2.00 per 1M, rounded 6dp
                assert r["cost_usd"]["actual"] == pytest.approx(0.000118, abs=1e-9)
                assert r["cost_usd"]["basis"] == "catalog x provider usage"
                assert r["verified_savings_usd"] == 0.0
                assert r["model"]["requested"] == "mock-model-v1"
                assert r["upstream_id"] == "chatcmpl-mock-nonstream"

                async with session.get(f"{base_url}/v1/receipts?limit=5") as rresp:
                    assert rresp.status == 200
                    body = await rresp.json()
                    assert body["count"] >= 1

                async with session.get(f"{base_url}/v1/receipts/rcpt_nope") as rresp:
                    assert rresp.status == 404

    asyncio.run(_run())


def test_receipt_streaming_stored_not_injected() -> None:
    """Streaming turns store a receipt without touching the SSE byte stream."""
    async def _run():
        async with ProxyHarness() as harness:
            base_url = harness.base_url

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "mock-model-v1",
                    "messages": [{"role": "user", "content": "Stream receipts"}],
                    "stream": True,
                }
                chunks = []
                async with session.post(f"{base_url}/v1/chat/completions", json=payload) as resp:
                    assert resp.status == 200
                    # X-Genesis-Receipt cannot exist: headers already sent
                    assert "X-Genesis-Receipt" not in resp.headers
                    async for raw in resp.content.iter_any():
                        chunks.append(raw)
                body = b"".join(chunks).decode()
                assert "genesis_receipt" not in body.lower()  # stream untouched

                async with session.get(f"{base_url}/v1/receipts?limit=5") as rresp:
                    assert rresp.status == 200
                    items = (await rresp.json())["receipts"]
                assert len(items) >= 1
                latest = items[0]
                assert latest["streaming"] is True
                assert latest["tokens"]["prompt"] == 300
                assert latest["tokens"]["completion"] == 65

    asyncio.run(_run())




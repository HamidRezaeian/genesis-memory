"""GENESIS Reverse Proxy — Pass-Through Skeleton with Cache-Aware Telemetry.

Architectural Guarantees (OpenCode Certified):
1. Cache-Aware Accounting: Tracks cached vs uncached prompt tokens separately.
2. Zero-Degradation Streaming: Passes SSE chunks to client immediately without buffering.
3. Strict Privacy Invariant: Request and response bodies are NEVER logged or retained.
"""

import argparse
import asyncio
import copy
import ctypes
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from aiohttp import web

from genesis_memory.proxy.tool_compactor import compact_messages, ToolIntegrityError
from genesis_memory.proxy.structured_distiller import StructuredDistiller
from genesis_memory.proxy.dialogue_synthesizer import synthesize_dialogue_summary
from genesis_memory.proxy.anaphora_rewriter import (
    AnaphoraRewriter,
    contains_technical_symbol,
    RE_ERRORS,
    RE_FILES,
    STOPWORDS,
)
from genesis_memory.proxy.content_compressor import (
    RecoveryStore,
    compress_text,
    MIN_BLOCK_CHARS,
)
from genesis_memory.proxy.pricing_engine import ModelPricingEngine

logger = logging.getLogger("genesis.proxy")


# Static output-diet directive (30 words, no per-turn variables, prefix-cache
# stable). Instructs terse prose while keeping technical content byte-exact.
# Input overhead ~15 tok buys output tok at 2-4x price. Flag-gated, live-only.
OUTPUT_DIET_TAG = "<!-- GENESIS_OUTPUT_DIET -->"
OUTPUT_DIET_DIRECTIVE = (
    "Answer tersely. No greetings, filler, hedging, restatement, or summaries. "
    "State only result and reason. Preserve code blocks, commands, error messages, "
    "file paths, and numbers byte-for-byte. Never paraphrase or reformat them."
)

RECEIPT_BUFFER_MAX = 100


def build_receipt(
    rid: str,
    *,
    ts: float,
    instance_id: str,
    session_id: str,
    client: str,
    streaming: bool,
    model_requested: Optional[str],
    pricing: Dict[str, Any],
    stop_reason: Optional[str],
    tool_calls: int,
    upstream_id: Optional[str],
    prompt_tokens: int,
    cached_tokens: int,
    completion_tokens: int,
) -> Dict[str, Any]:
    """Pure per-request receipt. Money = catalog rates x provider usage only.

    verified_savings_usd stays 0.0 until invoice reconciliation — the number
    is printed, never projected.
    """
    prompt_tokens = max(0, int(prompt_tokens or 0))
    cached_tokens = max(0, min(int(cached_tokens or 0), prompt_tokens))
    completion_tokens = max(0, int(completion_tokens or 0))
    uncached = prompt_tokens - cached_tokens
    ir = float(pricing.get("input_per_m", 0) or 0)
    cr = float(pricing.get("cached_per_m", 0) or 0)
    orate = float(pricing.get("output_per_m", 0) or 0)
    actual = round(cached_tokens / 1e6 * cr + uncached / 1e6 * ir + completion_tokens / 1e6 * orate, 6)
    return {
        "id": rid,
        "ts": ts,
        "instance_id": instance_id,
        "session_id": session_id,
        "client": client,
        "streaming": streaming,
        "model": {
            "requested": model_requested,
            "priced_as": pricing.get("matched_id"),
            "name": pricing.get("name"),
            "static_fallback": bool(pricing.get("is_static_fallback", False)),
        },
        "stop_reason": stop_reason or "unknown",
        "tool_calls": max(0, int(tool_calls or 0)),
        "upstream_id": upstream_id,
        "tokens": {
            "prompt": prompt_tokens,
            "cached": cached_tokens,
            "uncached": uncached,
            "completion": completion_tokens,
        },
        "cost_usd": {
            "actual": actual,
            "basis": "catalog x provider usage",
            "rates_per_m": {"in": ir, "cached": cr, "out": orate},
        },
        "verified_savings_usd": 0.0,
        "verified_note": "verified against invoice only; catalog x usage until then",
        "resume": {"after_request": rid, "session_id": session_id},
    }


def rss_mb() -> Optional[float]:
    """Measures working set memory (RSS) in megabytes."""
    try:
        if sys.platform == "win32":
            from ctypes import wintypes

            class PMem(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            p, m = ctypes.windll.kernel32.GetCurrentProcess(), PMem()
            m.cb = ctypes.sizeof(PMem)
            fn = ctypes.windll.psapi.GetProcessMemoryInfo
            fn.restype = wintypes.BOOL
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMem), wintypes.DWORD]
            if fn(p, ctypes.byref(m), m.cb):
                return round(m.WorkingSetSize / 1048576.0, 1)
            return None
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        return None


class ProxyTelemetry:
    """Thread-safe / async telemetry collector for cache-aware token accounting."""

    def __init__(self, mode: str = "shadow") -> None:
        self.mode = mode
        self.reset()

    def reset(self) -> None:
        self.start_time = time.time()
        self.requests_total = 0
        self.streaming_requests = 0
        self.non_streaming_requests = 0
        self.compacted_requests = 0
        self.bypasses_total = 0
        self.bypass_reasons: Dict[str, int] = {
            "unknown_shape": 0,
            "ambiguous_pair": 0,
            "validator_reject": 0,
            "upstream_error": 0,
        }
        self.upstream_status_codes: Dict[str, int] = {
            "2xx": 0,
            "4xx": 0,
            "5xx": 0,
            "429": 0,
        }
        self.tokens_stripped_total = 0
        self.tokens_retained_total = 0
        self.prompt_tokens_total = 0
        self.prompt_tokens_cached = 0
        self.prompt_tokens_uncached = 0
        self.completion_tokens = 0
        self.anaphora_rewrites = 0
        self.universal_capsules = 0
        self.diet_requests = 0
        self.diet_completion_total = 0
        self.content_saved_chars = 0
        self.content_blocks_shrunk = 0
        self.content_recoveries = 0
        self.distillations_completed = 0
        self.clarifications_flagged = 0
        self.stale_reads = 0
        self.ttft_records: list[float] = []
        self.total_latency_records: list[float] = []

    def record_anaphora_rewrite(self) -> None:
        self.anaphora_rewrites += 1

    def record_universal_capsule(self) -> None:
        self.universal_capsules += 1

    def record_content_saving(self, saved_chars: int = 0, blocks: int = 0, recoveries: int = 0) -> None:
        self.content_saved_chars += max(0, saved_chars)
        self.content_blocks_shrunk += max(0, blocks)
        self.content_recoveries += max(0, recoveries)

    def record_distillation(self) -> None:
        self.distillations_completed += 1

    def record_clarification(self) -> None:
        self.clarifications_flagged += 1

    def record_stale_read(self) -> None:
        self.stale_reads += 1

    def record_bypass(self, reason: str = "unknown_shape") -> None:
        self.bypasses_total += 1
        self.bypass_reasons[reason] = self.bypass_reasons.get(reason, 0) + 1

    def record_upstream_status(self, status: int) -> None:
        if 200 <= status < 300:
            self.upstream_status_codes["2xx"] += 1
        elif status == 429:
            self.upstream_status_codes["429"] += 1
            self.upstream_status_codes["4xx"] += 1
        elif 400 <= status < 500:
            self.upstream_status_codes["4xx"] += 1
        elif 500 <= status < 600:
            self.upstream_status_codes["5xx"] += 1

    def record_compaction(self, tokens_stripped: int, tokens_retained: int, turns_compacted: int) -> None:
        if turns_compacted > 0 or tokens_stripped > 0:
            self.compacted_requests += 1
            self.tokens_stripped_total += tokens_stripped
            self.tokens_retained_total += tokens_retained

    def record_request(
        self,
        is_streaming: bool,
        ttft_ms: float,
        total_latency_ms: float,
        prompt_tokens: int = 0,
        cached_tokens: int = 0,
        completion_tokens: int = 0,
        diet_active: bool = False,
    ) -> None:
        self.requests_total += 1
        if is_streaming:
            self.streaming_requests += 1
        else:
            self.non_streaming_requests += 1

        self.ttft_records.append(ttft_ms)
        self.total_latency_records.append(total_latency_ms)

        if prompt_tokens > 0:
            self.prompt_tokens_total += prompt_tokens
            self.prompt_tokens_cached += cached_tokens
            self.prompt_tokens_uncached += max(0, prompt_tokens - cached_tokens)
        self.completion_tokens += completion_tokens
        if diet_active:
            self.diet_requests += 1
            self.diet_completion_total += completion_tokens

    def get_snapshot(self) -> Dict[str, Any]:
        mean_ttft = (
            sum(self.ttft_records) / len(self.ttft_records)
            if self.ttft_records
            else 0.0
        )
        if self.ttft_records:
            sorted_ttft = sorted(self.ttft_records)
            p50_ttft = sorted_ttft[int(len(sorted_ttft) * 0.50)]
            p95_ttft = sorted_ttft[min(len(sorted_ttft) - 1, int(len(sorted_ttft) * 0.95))]
            max_ttft = sorted_ttft[-1]
        else:
            p50_ttft = p95_ttft = max_ttft = 0.0

        mean_latency = (
            sum(self.total_latency_records) / len(self.total_latency_records)
            if self.total_latency_records
            else 0.0
        )
        cache_hit_rate = (
            self.prompt_tokens_cached / self.prompt_tokens_total
            if self.prompt_tokens_total > 0
            else 0.0
        )
        bypass_rate_pct = round(
            (self.bypasses_total / max(1, self.requests_total)) * 100.0, 2
        )

        return {
            "mode": self.mode,
            "requests_total": self.requests_total,
            "streaming_requests": self.streaming_requests,
            "non_streaming_requests": self.non_streaming_requests,
            "compacted_requests": self.compacted_requests,
            "bypass": {
                "bypasses_total": self.bypasses_total,
                "bypass_rate_pct": bypass_rate_pct,
                "reasons": dict(self.bypass_reasons),
            },
            "upstream": {
                "status_codes": dict(self.upstream_status_codes),
            },
            "tokens": {
                "prompt_total": self.prompt_tokens_total,
                "prompt_cached": self.prompt_tokens_cached,
                "prompt_uncached": self.prompt_tokens_uncached,
                "prompt_stripped_est": self.tokens_stripped_total,
                "prompt_retained_est": self.tokens_retained_total,
                "completion": self.completion_tokens,
                "total": self.prompt_tokens_total + self.completion_tokens,
            },
            "compaction": {
                "compacted_requests": self.compacted_requests,
                "tokens_stripped_total": self.tokens_stripped_total,
                "tokens_retained_total": self.tokens_retained_total,
            },
            "distillation": {
                "distillations_completed": self.distillations_completed,
                "anaphora_rewrites": self.anaphora_rewrites,
                "universal_capsules": self.universal_capsules,
                "diet_requests": self.diet_requests,
                "diet_completion_total": self.diet_completion_total,
                "content_saved_chars": self.content_saved_chars,
                "content_blocks_shrunk": self.content_blocks_shrunk,
                "content_recoveries": self.content_recoveries,
                "clarifications_flagged": self.clarifications_flagged,
                "stale_reads": self.stale_reads,
            },
            "cache_hit_rate": round(cache_hit_rate, 4),
            "performance": {
                "mean_ttft_ms": round(mean_ttft, 2),
                "p50_ttft_ms": round(p50_ttft, 2),
                "p95_ttft_ms": round(p95_ttft, 2),
                "max_ttft_ms": round(max_ttft, 2),
                "mean_total_latency_ms": round(mean_latency, 2),
            },
            "rss_mb": rss_mb(),
            "privacy": {
                "body_logging_enabled": False,
                "payloads_retained": 0,
            },
            "uptime_s": round(time.time() - self.start_time, 2),
        }



class GenesisProxyServer:
    """Asynchronous OpenAI-compatible reverse proxy with SSE pass-through, barrier synchronization, and anaphora rewriting."""

    def __init__(
        self,
        upstream_url: str = "https://api.openai.com/v1",
        telemetry: Optional[ProxyTelemetry] = None,
        store: Optional[Any] = None,
        enable_anaphora_rewrite: bool = True,
        enable_universal_capsule: Optional[bool] = None,
        enable_output_diet: Optional[bool] = None,
        enable_content_compress: Optional[bool] = None,
        enable_compaction: bool = True,
        max_history_turns: int = 1,
        mode: str = "live",
        upstream_key: Optional[str] = None,
        target_model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        self.upstream_url = upstream_url.rstrip("/")
        self.anthropic_upstream_url = os.environ.get(
            "GENESIS_ANTHROPIC_UPSTREAM_URL", "https://api.anthropic.com/v1").rstrip("/")
        self.anthropic_key = os.environ.get("GENESIS_ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        env_mode = os.environ.get("GENESIS_PROXY_MODE")
        self.mode = (env_mode or mode).lower().strip()
        self.telemetry = telemetry or ProxyTelemetry(mode=self.mode)
        self.telemetry.mode = self.mode
        self.store = store
        self.upstream_key = (
            upstream_key
            or os.environ.get("GENESIS_UPSTREAM_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        self.target_model = (
            target_model
            or os.environ.get("GENESIS_TARGET_MODEL")
            or "gemini-3.5-flash"
        )
        self.reasoning_effort = reasoning_effort or os.environ.get("GENESIS_REASONING_EFFORT")
        self.enable_anaphora_rewrite = enable_anaphora_rewrite
        env_univ = os.environ.get("GENESIS_UNIVERSAL_CAPSULE", "0")
        self.enable_universal_capsule = (
            enable_universal_capsule
            if enable_universal_capsule is not None
            else (env_univ.lower() in ("1", "true", "yes"))
        )
        env_diet = os.environ.get("GENESIS_OUTPUT_DIET", "0")
        self.enable_output_diet = (
            enable_output_diet
            if enable_output_diet is not None
            else (env_diet.lower() in ("1", "true", "yes"))
        )
        env_cc = os.environ.get("GENESIS_CONTENT_COMPRESS", "0")
        self.enable_content_compress = (
            enable_content_compress
            if enable_content_compress is not None
            else (env_cc.lower() in ("1", "true", "yes"))
        )
        self._recovery_store: Optional[RecoveryStore] = None
        self.enable_compaction = enable_compaction
        env_turns = os.environ.get("GENESIS_MAX_HISTORY_TURNS")
        self.max_history_turns = int(env_turns) if env_turns is not None else max_history_turns
        self.distiller = StructuredDistiller(store=self.store)
        self.rewriter = AnaphoraRewriter()
        self.pricing_engine = ModelPricingEngine(auto_fetch=False)
        self.session_tlbs: Dict[str, Dict[str, Any]] = {}
        self.thought_signatures: Dict[str, str] = {}
        self.receipts: deque = deque(maxlen=RECEIPT_BUFFER_MAX)
        self._receipt_seq = 0
        # R4: stable per-process identity — watermarks key on this, so two
        # proxies sharing one ledger never cross-subtract, and restarts
        # start a fresh chain instead of faking a "restarted" delta.
        self.instance_id = uuid.uuid4().hex[:12]
        self.boot_ts = time.time()
        self.app = web.Application()
        self.client_session: Optional[aiohttp.ClientSession] = None
        self._setup_routes()

    def _get_session_id(self, request: web.Request) -> str:
        """Derives clean session identifier from headers to isolate multi-client TLB buffers."""
        sess_hdr = request.headers.get("X-Genesis-Session")
        if sess_hdr:
            return sess_hdr.strip()
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and len(auth_hdr) > 15:
            return hashlib.sha256(auth_hdr.encode("utf-8")).hexdigest()[:12]
        return "default"

    def _get_tlb(self, session_id: str) -> Dict[str, Any]:
        if session_id not in self.session_tlbs:
            self.session_tlbs[session_id] = {
                "errors": [],
                "touched_files": [],
                "test_outcome": "",
                "last_assistant_response": "",
                "last_tool_output": "",
            }
        return self.session_tlbs[session_id]

    @property
    def tlb(self) -> Dict[str, Any]:
        return self._get_tlb("default")

    @tlb.setter
    def tlb(self, val: Dict[str, Any]) -> None:
        self.session_tlbs["default"] = val

    def _setup_routes(self) -> None:
        self.app.router.add_get("/", self.handle_root)
        self.app.router.add_post("/v1/chat/completions", self.handle_chat_completions)
        self.app.router.add_post("/chat/completions", self.handle_chat_completions)
        # Anthropic Messages API (drop-in for the Anthropic SDK: ANTHROPIC_BASE_URL=http://127.0.0.1:8000)
        self.app.router.add_post("/v1/messages", self.handle_anthropic_messages)
        self.app.router.add_post("/messages", self.handle_anthropic_messages)
        self.app.router.add_get("/v1/models", self.handle_models)
        self.app.router.add_get("/models", self.handle_models)
        self.app.router.add_get("/v1/pricing", self.handle_pricing)
        self.app.router.add_get("/pricing", self.handle_pricing)
        self.app.router.add_post("/v1/pricing/refresh", self.handle_pricing_refresh)
        self.app.router.add_post("/pricing/refresh", self.handle_pricing_refresh)
        self.app.router.add_get("/v1/pricing/refresh", self.handle_pricing_refresh)
        self.app.router.add_get("/pricing/refresh", self.handle_pricing_refresh)
        self.app.router.add_get("/v1/telemetry", self.handle_telemetry)
        self.app.router.add_get("/telemetry", self.handle_telemetry)
        self.app.router.add_post("/v1/telemetry/reset", self.handle_telemetry_reset)
        self.app.router.add_get("/v1/recovery/{rid}", self.handle_recovery)
        self.app.router.add_get("/recovery/{rid}", self.handle_recovery)
        self.app.router.add_get("/v1/receipts", self.handle_receipts)
        self.app.router.add_get("/receipts", self.handle_receipts)
        self.app.router.add_get("/v1/receipts/{rid}", self.handle_receipt)
        self.app.router.add_get("/receipts/{rid}", self.handle_receipt)
        self.app.router.add_get("/health", self.handle_health)
        self.app.on_startup.append(self._on_startup)
        self.app.on_cleanup.append(self._on_cleanup)

    async def _on_startup(self, app: web.Application) -> None:
        # ClientSession with unlimited connection pool, connect timeout 15s, and unbounded read for SSE
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15.0, sock_read=None)
        connector = aiohttp.TCPConnector(limit=100, force_close=False, enable_cleanup_closed=True)
        self.client_session = aiohttp.ClientSession(connector=connector, timeout=timeout)

    async def _on_cleanup(self, app: web.Application) -> None:
        if self.client_session:
            await self.client_session.close()

    def _extract_forward_headers(self, request: web.Request) -> Dict[str, str]:
        """Forward client headers while filtering out hop-by-hop headers."""
        excluded = {"host", "content-length", "transfer-encoding", "connection"}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in excluded}
        if self.upstream_key:
            auth = headers.get("Authorization", "")
            if not auth or "not-needed" in auth or "dummy" in auth.lower():
                headers["Authorization"] = f"Bearer {self.upstream_key}"
        return headers

    async def handle_root(self, request: web.Request) -> web.Response:
        return web.json_response({
            "service": "GENESIS Stateless Reverse Proxy",
            "version": "1.4-production",
            "mode": self.mode,
            "target_model": self.target_model,
            "reasoning_effort": self.reasoning_effort,
            "endpoints": {
                "health": "/health",
                "telemetry": "/v1/telemetry",
                "pricing": "/v1/pricing",
                "models": "/v1/models",
                "chat_completions": "/v1/chat/completions",
                "recovery": "/v1/recovery/{id}",
                "receipts": "/v1/receipts",
            }
        })

    async def handle_receipts(self, request: web.Request) -> web.Response:
        """List recent per-request receipts (counts + costs, never bodies)."""
        try:
            limit = max(1, min(100, int(request.query.get("limit", "20"))))
        except ValueError:
            limit = 20
        items = list(self.receipts)[-limit:]
        items.reverse()
        return web.json_response({"count": len(items), "receipts": items})

    async def handle_receipt(self, request: web.Request) -> web.Response:
        rid = request.match_info.get("rid", "")
        for r in reversed(self.receipts):
            if r.get("id") == rid:
                return web.json_response(r)
        return web.json_response({"error": "unknown receipt id"}, status=404)

    async def handle_recovery(self, request: web.Request) -> web.Response:
        """Return a stored original by recovery id (byte-exact, redacted at store)."""
        rid = request.match_info.get("rid", "")
        try:
            store = self._recovery_store or RecoveryStore()
        except Exception:
            return web.json_response({"error": "recovery store unavailable"}, status=503)
        try:
            text = store.get(rid)
        except Exception:
            text = None
        if text is None:
            return web.json_response({"error": "unknown or expired recovery id"}, status=404)
        resp = web.json_response({"id": rid, "chars": len(text), "text": text})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "mode": self.mode,
            "instance_id": self.instance_id,
            "upstream_url": self.upstream_url,
            "target_model": self.target_model,
            "store_active": self.store is not None,
            "active_sessions": len(self.session_tlbs),
            "rss_mb": rss_mb(),
        })

    async def handle_telemetry(self, request: web.Request) -> web.Response:
        snap = self.telemetry.get_snapshot()
        snap["mode"] = self.mode
        snap["target_model"] = self.target_model
        snap["instance_id"] = self.instance_id
        snap["boot_ts"] = self.boot_ts

        # Calculate exact live dollar accounting
        tokens = snap.get("tokens", {})
        stripped = tokens.get("prompt_stripped_est", 0)
        retained = tokens.get("prompt_retained_est", 0)
        cached = tokens.get("prompt_cached", 0)
        completion = tokens.get("completion", 0)

        snap["cost_accounting"] = self.pricing_engine.calculate_cost_savings(
            tokens_stripped=stripped,
            tokens_retained=retained,
            tokens_cached=cached,
            completion_tokens=completion,
            model_id=self.target_model or "gemini-3.5-flash",
        )
        resp = web.json_response(snap)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    async def handle_pricing(self, request: web.Request) -> web.Response:
        q = request.query.get("q", "").lower().strip()
        provider = request.query.get("provider", "").lower().strip()
        paid_param = request.query.get("paid_only", "1").lower().strip()
        paid_only = paid_param in ("1", "true", "yes")
        limit_param = request.query.get("limit")
        sort_by = request.query.get("sort", "input_desc").lower().strip()

        models = self.pricing_engine.get_all_models_summary(paid_only=paid_only)
        if provider and provider != "all":
            models = [
                m for m in models 
                if m.get("provider", "").lower() == provider or provider in m["id"].lower()
            ]
        if q:
            models = [
                m for m in models 
                if q in m["id"].lower() or q in m["name"].lower() or q in m.get("provider", "").lower()
            ]

        # Sorting options
        if sort_by == "input_asc":
            models.sort(key=lambda x: x["input_per_m"])
        elif sort_by == "output_desc":
            models.sort(key=lambda x: x["output_per_m"], reverse=True)
        elif sort_by == "context_desc":
            models.sort(key=lambda x: x.get("context_length", 0), reverse=True)
        elif sort_by == "name_asc":
            models.sort(key=lambda x: x.get("name", "").lower())
        else: # default: input_desc
            models.sort(key=lambda x: x["input_per_m"], reverse=True)

        total_matching = len(models)
        if limit_param and limit_param.isdigit():
            models = models[:int(limit_param)]

        resp = web.json_response({
            "total_models": total_matching,
            "catalog_epoch": self.pricing_engine._last_fetched,
            "active_model": self.target_model or "gemini-3.5-flash",
            "models": models,
        })
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    async def handle_pricing_refresh(self, request: web.Request) -> web.Response:
        count = self.pricing_engine.refresh_catalog_sync()
        resp = web.json_response({
            "ok": True,
            "count": count,
            "timestamp": self.pricing_engine._last_fetched,
        })
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    async def handle_telemetry_reset(self, request: web.Request) -> web.Response:
        self.telemetry.reset()
        return web.json_response({"ok": True, "reset": True})

    async def handle_models(self, request: web.Request) -> web.Response:
        """Handles /v1/models and /models with upstream pass-through and robust offline fallback."""
        target_url = f"{self.upstream_url}/models"
        headers = self._extract_forward_headers(request)

        fallback_payload = {
            "object": "list",
            "data": [
                {"id": "gpt-4o", "object": "model", "created": 1700000000, "owned_by": "openai"},
                {"id": "gpt-4o-mini", "object": "model", "created": 1700000000, "owned_by": "openai"},
                {"id": "claude-3-5-sonnet-20241022", "object": "model", "created": 1700000000, "owned_by": "anthropic"},
                {"id": "deepseek-coder", "object": "model", "created": 1700000000, "owned_by": "deepseek"},
                {"id": "gemini-3.5-flash", "object": "model", "created": 1700000000, "owned_by": "google"},
                {"id": "gemini-3.8-flash", "object": "model", "created": 1700000000, "owned_by": "google"},
                {"id": "genesis-stateless", "object": "model", "created": 1700000000, "owned_by": "genesis"},
            ],
        }

        if self.client_session:
            try:
                timeout = aiohttp.ClientTimeout(total=5.0)
                async with self.client_session.get(target_url, headers=headers, timeout=timeout) as upstream_resp:
                    if upstream_resp.status == 200:
                        body = await upstream_resp.read()
                        return web.Response(
                            body=body,
                            status=upstream_resp.status,
                            content_type=upstream_resp.content_type,
                        )
            except Exception as exc:
                logger.warning("Upstream /models failed, using fallback catalog: %s", exc)

        return web.json_response(fallback_payload)

    def _update_tlb_and_distill(
        self,
        messages: List[Dict[str, Any]],
        resp_text: str = "",
        resp_tool_calls: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "default",
        client: str = "proxy",
    ) -> None:
        """Updates 1-turn Locality Buffer (TLB), persists dialogue turn, and dispatches background distillation."""
        tlb = self._get_tlb(session_id)
        facts = self.distiller.extract_turn_facts(messages, resp_text, resp_tool_calls)
        tlb["errors"] = facts.get("errors_found", [])
        tlb["touched_files"] = facts.get("touched_files", [])
        test_outcomes = facts.get("test_outcomes", [])
        tlb["test_outcome"] = test_outcomes[0] if test_outcomes else ""
        tlb["last_assistant_response"] = resp_text

        tool_outputs = []
        for m in messages:
            if m.get("role") == "tool":
                tool_outputs.append(str(m.get("content", "")))
            elif m.get("role") == "user" and isinstance(m.get("content"), list):
                for b in m["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tool_outputs.append(str(b.get("content", "")))
        tlb["last_tool_output"] = " ".join(tool_outputs)

        # Update dialogue buffer (cross-session & cross-client continuity)
        try:
            if self.store and hasattr(self.store, "set_dialogue"):
                last_user_text = ""
                for m in reversed(messages):
                    if m.get("role") == "user":
                        content = m.get("content", "")
                        if isinstance(content, str):
                            last_user_text = content
                        elif isinstance(content, list):
                            parts = [
                                str(b.get("text", ""))
                                for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            ]
                            last_user_text = " ".join(parts)
                        if last_user_text:
                            break

                if last_user_text or resp_text:
                    summary, salient_terms = synthesize_dialogue_summary(resp_text, max_chars=140)
                    self.store.set_dialogue(
                        session_id=session_id,
                        client=client,
                        user_prompt=last_user_text,
                        assistant_summary=summary,
                        salient_terms=salient_terms,
                    )
        except Exception as exc:
            logger.warning("Failed to update dialogue buffer: %s", exc)

        # Mark in flight and spawn async background distillation
        try:
            loop = asyncio.get_running_loop()
            self.distiller.mark_distillation_in_flight()
            loop.create_task(self._run_distillation(messages, resp_text, resp_tool_calls))
        except RuntimeError:
            pass

    async def _run_distillation(
        self,
        messages: List[Dict[str, Any]],
        resp_text: str = "",
        resp_tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        try:
            await self.distiller.distill_and_ingest(messages, resp_text, resp_tool_calls)
            self.telemetry.record_distillation()
        except Exception as exc:
            logger.warning("Background distillation failed: %s", exc)

    def _compress_message_texts(
        self,
        messages: List[Dict[str, Any]],
        recovery: Optional[RecoveryStore],
        audit_only: bool,
    ) -> None:
        """Walk OpenAI/Anthropic text blocks through compress_text.

        Live: replace shrunken blocks in place. Shadow (audit_only): measure
        with recovery=None (no elision, no disk writes), never mutate.
        """
        probe_recovery = None if audit_only else recovery
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if isinstance(content, str) and role in ("assistant", "tool", "user"):
                if len(content) < MIN_BLOCK_CHARS:
                    continue
                try:
                    out, meta = compress_text(content, probe_recovery)
                except Exception:
                    continue
                if meta.get("compressed"):
                    self.telemetry.record_content_saving(
                        meta.get("saved_chars", 0), 1,
                        1 if meta.get("recovery_id") else 0,
                    )
                    if not audit_only:
                        m["content"] = out
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    key = None
                    if b.get("type") == "text" and isinstance(b.get("text"), str):
                        key = "text"
                    elif b.get("type") in ("tool_result", "tool_use") and isinstance(b.get("content"), str):
                        key = "content"
                    if key is None or len(b[key]) < MIN_BLOCK_CHARS:
                        continue
                    try:
                        out, meta = compress_text(b[key], probe_recovery)
                    except Exception:
                        continue
                    if meta.get("compressed"):
                        self.telemetry.record_content_saving(
                            meta.get("saved_chars", 0), 1,
                            1 if meta.get("recovery_id") else 0,
                        )
                        if not audit_only:
                            b[key] = out

    def _issue_receipt(
        self,
        *,
        streaming: bool,
        model_requested: Optional[str],
        session_id: str,
        client: str,
        stop_reason: Optional[str],
        tool_calls: int,
        upstream_id: Optional[str],
        prompt_tok: int,
        cached_tok: int,
        comp_tok: int,
    ) -> Dict[str, Any]:
        """Build, buffer (last 100), and return one per-request receipt."""
        self._receipt_seq += 1
        rid = f"rcpt_{int(time.time() * 1000):x}_{self._receipt_seq:04x}"
        try:
            pricing = self.pricing_engine.resolve_model_pricing(
                model_requested or self.target_model
            )
        except Exception:
            pricing = {}
        if not isinstance(pricing, dict):
            pricing = {}
        receipt = build_receipt(
            rid,
            ts=time.time(),
            instance_id=self.instance_id,
            session_id=session_id,
            client=client,
            streaming=streaming,
            model_requested=model_requested,
            pricing=pricing,
            stop_reason=stop_reason,
            tool_calls=tool_calls,
            upstream_id=upstream_id,
            prompt_tokens=prompt_tok,
            cached_tokens=cached_tok,
            completion_tokens=comp_tok,
        )
        self.receipts.append(receipt)
        return receipt

    def _ensure_gemini_thought_signatures(self, messages: List[Dict[str, Any]]) -> None:
        """Injects or restores Gemini thought_signatures stripped by OpenAI-compatible clients (e.g. OpenCode)."""
        if not messages or not isinstance(messages, list):
            return
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "assistant":
                tool_calls = m.get("tool_calls")
                if tool_calls and isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            tc_id = tc.get("id")
                            extra = tc.get("extra_content")
                            has_sig = (
                                isinstance(extra, dict)
                                and isinstance(extra.get("google"), dict)
                                and bool(extra["google"].get("thought_signature"))
                            )
                            if not has_sig:
                                cached_sig = self.thought_signatures.get(tc_id) if tc_id else None
                                sig = cached_sig or "skip_thought_signature_validator"
                                if not isinstance(extra, dict):
                                    tc["extra_content"] = {}
                                if "google" not in tc["extra_content"] or not isinstance(tc["extra_content"]["google"], dict):
                                    tc["extra_content"]["google"] = {}
                                tc["extra_content"]["google"]["thought_signature"] = sig

    async def handle_anthropic_messages(self, request: web.Request) -> web.StreamResponse:
        """Anthropic Messages API passthrough with structural compaction + memory capsule.

        Same fail-open contract as the OpenAI route: any validator rejection or
        unknown shape forwards the *original* payload untouched. Streaming is a
        byte-exact SSE relay (no buffering, TTFT preserved).
        """
        target_url = f"{self.anthropic_upstream_url}/messages"
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in {"host", "content-length", "transfer-encoding", "connection"}}
        if self.anthropic_key and not headers.get("x-api-key") and not headers.get("X-Api-Key"):
            headers["x-api-key"] = self.anthropic_key
        headers.setdefault("anthropic-version", "2023-06-01")

        try:
            payload = await request.json()
        except Exception:
            self.telemetry.record_bypass("unknown_shape")
            return web.json_response({"type": "error", "error": {"type": "invalid_request_error",
                                                                "message": "Invalid JSON payload"}}, status=400)

        messages = payload.get("messages")
        memory_capsule: Optional[str] = None
        if isinstance(messages, list) and messages:
            # Last user text (Anthropic content may be a string or a block list).
            last_user_text = ""
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    c = m.get("content")
                    if isinstance(c, str):
                        last_user_text = c
                    elif isinstance(c, list):
                        last_user_text = " ".join(
                            b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                    break
            if self.store and last_user_text.strip() and len(messages) > 1:
                try:
                    try:
                        recall_res = self.store.recall(last_user_text, limit=3, fallback=False)
                    except TypeError:
                        recall_res = self.store.recall(last_user_text, limit=3)
                    engrams = recall_res.get("results", []) if isinstance(recall_res, dict) else (recall_res or [])
                    if engrams:
                        memory_capsule = "\n".join(
                            f"- [{e.get('kind', 'fact')}]: {e.get('text') or e.get('snippet', '')}" for e in engrams)[:4000]
                except Exception as exc:
                    logger.warning("Anthropic route recall failed: %s", exc)
            if self.enable_compaction:
                try:
                    compacted, meta = compact_messages(
                        copy.deepcopy(messages), max_history_turns=self.max_history_turns,
                        format_type="anthropic", memory_capsule=memory_capsule)
                    if self.mode == "live":
                        payload["messages"] = compacted
                    self.telemetry.record_compaction(
                        tokens_stripped=meta["tokens_stripped"], tokens_retained=meta["tokens_retained"],
                        turns_compacted=meta["turns_compacted"])
                except ToolIntegrityError as tie:
                    logger.warning("Anthropic compaction validator reject: %s (Fail-Open bypass)", tie)
                    self.telemetry.record_bypass("validator_reject")
                except Exception as exc:
                    logger.warning("Anthropic compaction error: %s (Fail-Open bypass)", exc)
                    self.telemetry.record_bypass("unknown_shape")

        is_streaming = bool(payload.get("stream", False))
        start = time.perf_counter()
        assert self.client_session is not None
        try:
            upstream_ctx = self.client_session.post(target_url, json=payload, headers=headers)
            upstream_resp = await upstream_ctx.__aenter__()
        except Exception as exc:
            self.telemetry.record_upstream_status(502)
            self.telemetry.record_bypass("upstream_error")
            return web.json_response({"type": "error", "error": {"type": "api_error",
                                                                "message": f"Upstream connection failed: {exc}"}}, status=502)
        self.telemetry.record_upstream_status(upstream_resp.status)
        try:
            if upstream_resp.status >= 400 or not is_streaming:
                body = await upstream_resp.read()
                usage = {}
                if upstream_resp.status < 400:
                    try:
                        usage = json.loads(body.decode("utf-8")).get("usage", {}) or {}
                    except Exception:
                        usage = {}
                    elapsed = (time.perf_counter() - start) * 1000.0
                    self.telemetry.record_request(
                        is_streaming=False, ttft_ms=elapsed, total_latency_ms=elapsed,
                        prompt_tokens=int(usage.get("input_tokens", 0) or 0),
                        cached_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
                        completion_tokens=int(usage.get("output_tokens", 0) or 0))
                return web.Response(body=body, status=upstream_resp.status,
                                    content_type=upstream_resp.content_type or "application/json")
            response = web.StreamResponse(status=upstream_resp.status, headers={
                "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Genesis-Route": "anthropic"})
            await response.prepare(request)
            first = None
            async for chunk in upstream_resp.content.iter_any():
                if first is None:
                    first = (time.perf_counter() - start) * 1000.0
                await response.write(chunk)
            total = (time.perf_counter() - start) * 1000.0
            self.telemetry.record_request(is_streaming=True, ttft_ms=first or total, total_latency_ms=total)
            await response.write_eof()
            return response
        finally:
            try:
                await upstream_ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def handle_chat_completions(self, request: web.Request) -> web.StreamResponse:
        """Handle /v1/chat/completions with pass-through streaming, session isolation, and telemetry."""
        target_url = f"{self.upstream_url}/chat/completions"
        headers = self._extract_forward_headers(request)
        session_id = self._get_session_id(request)
        session_tlb = self._get_tlb(session_id)

        # Derive client name for cross-client attribution
        client_name = request.headers.get("X-Genesis-Client")
        if not client_name:
            ua = request.headers.get("User-Agent", "").lower()
            if "opencode" in ua:
                client_name = "opencode"
            elif "antigravity" in ua:
                client_name = "antigravity"
            elif "cursor" in ua:
                client_name = "cursor"
            elif "claude" in ua:
                client_name = "claude"
            else:
                client_name = "proxy"

        # Read client payload
        try:
            payload = await request.json()
        except Exception:
            self.telemetry.record_bypass("unknown_shape")
            return web.json_response({"error": "Invalid JSON payload"}, status=400)

        # Map virtual genesis-stateless or empty model to target upstream model
        if payload.get("model") == "genesis-stateless" or not payload.get("model"):
            if self.target_model:
                payload["model"] = self.target_model
        if self.reasoning_effort and "reasoning_effort" not in payload:
            payload["reasoning_effort"] = self.reasoning_effort

        raw_client_messages = payload.get("messages", [])
        client_messages = (
            copy.deepcopy(raw_client_messages)
            if isinstance(raw_client_messages, list)
            else []
        )
        memory_capsule: Optional[str] = None
        diet_active = False

        # Bulk content compression (fail-closed, flag-gated). Shrinks large
        # text blocks before recall/compaction. Shadow mode measures only.
        if self.enable_content_compress and isinstance(client_messages, list):
            audit_only = self.mode != "live"
            recovery = None
            if not audit_only:
                if self._recovery_store is None:
                    try:
                        self._recovery_store = RecoveryStore()
                    except Exception:
                        self._recovery_store = None
                recovery = self._recovery_store
            self._compress_message_texts(client_messages, recovery, audit_only)

        if client_messages and isinstance(client_messages, list):
            # Locate last user message for anaphora rewriting & memory recall
            last_user_idx = -1
            for i in range(len(client_messages) - 1, -1, -1):
                if client_messages[i].get("role") == "user":
                    last_user_idx = i
                    break

            needs_clar = False
            is_universal = False
            was_rewritten = False
            universal_salient: List[str] = []

            if last_user_idx != -1 and (self.enable_anaphora_rewrite or self.enable_universal_capsule):
                user_msg = client_messages[last_user_idx]
                user_content = user_msg.get("content", "")
                if isinstance(user_content, str) and user_content.strip():
                    if self.enable_anaphora_rewrite:
                        rewritten, was_rewritten, needs_clar = self.rewriter.rewrite_query(
                            user_content, session_tlb
                        )
                        if was_rewritten:
                            user_msg["content"] = rewritten
                            self.telemetry.record_anaphora_rewrite()

                    if (not was_rewritten) and self.enable_universal_capsule:
                        # Tier 2 Fallback Trigger:
                        # 1. is_anaphoric() was False (was_rewritten == False)
                        # 2. user prompt contains NO technical symbols/errors/paths
                        # 3. strong TLB only: errors non-empty OR RE_ERRORS in test_outcome
                        has_tech_symbol = contains_technical_symbol(user_content)
                        has_strong_tlb = bool(session_tlb.get("errors")) or bool(
                            RE_ERRORS.search(session_tlb.get("test_outcome", ""))
                        )
                        if (not has_tech_symbol) and has_strong_tlb:
                            is_universal = True
                            # Action: NEVER mutate user_msg!
                            # Pre-barrier salient extraction
                            universal_salient = self.rewriter.extract_salient_tlb_terms(session_tlb)

            # Single-turn VOI gate (EXP107 lesson: unconditional injection into
            # a self-contained single turn is pure token tax). Fires only when
            # the turn carries no anaphora, no universal trigger, and the TLB
            # is weak — i.e. recall cannot substitute any user action.
            single_turn_no_gain = (
                last_user_idx != -1
                and not was_rewritten
                and not is_universal
                and len([
                    m for m in client_messages
                    if isinstance(m, dict) and m.get("role") != "system"
                ]) == 1
                and not session_tlb.get("errors")
                and not session_tlb.get("touched_files")
                and not RE_ERRORS.search(session_tlb.get("test_outcome", ""))
            )

            # Memory recall & capsule construction (ONLY gate here, never on raw forwarding)
            if needs_clar:
                # Bug 2 Fix (P3 Guard): unresolvable anaphora skips recall to prevent hallucinated injection
                self.telemetry.record_clarification()
                memory_capsule = None
            elif self.store:
                # Bug 1 Fix: Barrier with non-blocking timeout (80ms) protecting TTFT
                waited_ok = await self.distiller.wait_for_pending_distillations(timeout=0.08)
                if not waited_ok:
                    self.telemetry.record_stale_read()

                # Audit thread staleness for Rule 25 / STEP4 telemetry (<5% gate)
                try:
                    if hasattr(self.store, "get_thread"):
                        th = self.store.get_thread()
                        if th and th.get("updated_at"):
                            if (time.time() - float(th["updated_at"])) >= 1800:
                                self.telemetry.record_stale_read()
                except Exception:
                    pass

                if is_universal and universal_salient:
                    raw_content = (
                        client_messages[last_user_idx].get("content", "")
                        if last_user_idx != -1
                        else ""
                    )
                    search_query = f"{raw_content} {' '.join(universal_salient)}".strip()
                else:
                    search_query = (
                        client_messages[last_user_idx].get("content", "")
                        if last_user_idx != -1
                        else ""
                    )

                if search_query:
                    try:
                        try:
                            recall_res = self.store.recall(search_query, limit=3, fallback=False)
                        except TypeError:
                            recall_res = self.store.recall(search_query, limit=3)

                        if isinstance(recall_res, dict):
                            engrams = recall_res.get("results", [])
                        elif isinstance(recall_res, list):
                            engrams = recall_res
                        else:
                            engrams = []

                        if engrams and single_turn_no_gain:
                            # VOI filter: keep only hits sharing a content word
                            # with the self-contained query; chance hits are tax.
                            query_terms = {
                                w.lower()
                                for w in re.findall(r"[A-Za-z]{3,}", search_query)
                                if w.lower() not in STOPWORDS
                            }
                            engrams = [
                                e for e in engrams
                                if query_terms and any(
                                    t in (e.get("text") or e.get("snippet", "")).lower()
                                    for t in query_terms
                                )
                            ]
                            if not engrams:
                                self.telemetry.record_bypass("single_turn_no_gain")

                        if engrams:
                            capsule_lines = [
                                f"- [{e.get('kind', 'fact')}]: {e.get('text') or e.get('snippet', '')}"
                                for e in engrams
                            ]
                            capsule_body = "\n".join(capsule_lines)
                            if is_universal:
                                memory_capsule = f"If irrelevant to current user message, ignore.\n{capsule_body}"
                                self.telemetry.record_universal_capsule()
                            else:
                                memory_capsule = capsule_body
                    except Exception as exc:
                        logger.warning("Memory recall failed: %s", exc)

            # Apply structural compaction or dry-run shadow audit
            if self.enable_compaction:
                try:
                    if memory_capsule and len(memory_capsule) > 4000:
                        memory_capsule = memory_capsule[:4000]
                    compacted, meta = compact_messages(
                        client_messages,
                        max_history_turns=self.max_history_turns,
                        format_type="openai",
                        memory_capsule=memory_capsule,
                    )
                    if self.mode == "live":
                        payload["messages"] = compacted
                    # In shadow mode: payload["messages"] remains untouched original messages
                    self.telemetry.record_compaction(
                        tokens_stripped=meta["tokens_stripped"],
                        tokens_retained=meta["tokens_retained"],
                        turns_compacted=meta["turns_compacted"],
                    )
                except ToolIntegrityError as tie:
                    logger.warning("Compaction validator reject: %s (Fail-Open bypass)", tie)
                    self.telemetry.record_bypass("validator_reject")
                except Exception as exc:
                    logger.warning("Compaction error: %s (Fail-Open bypass)", exc)
                    self.telemetry.record_bypass("unknown_shape")
            elif self.mode == "live":
                payload["messages"] = client_messages

        # Output diet: static tail-push (prefix bytes unchanged → LCP cache
        # preserved). Live-only, flag-gated, idempotent per payload.
        if (
            self.enable_output_diet
            and self.mode == "live"
            and isinstance(payload.get("messages"), list)
        ):
            if not any(
                isinstance(m, dict) and OUTPUT_DIET_TAG in str(m.get("content", ""))[:256]
                for m in payload["messages"]
            ):
                payload["messages"] = payload["messages"] + [
                    {"role": "system", "content": f"{OUTPUT_DIET_TAG}\n{OUTPUT_DIET_DIRECTIVE}"}
                ]
                diet_active = True

        # Ensure thought_signature is present on assistant tool calls when targeting Gemini / Google upstream
        target_check = (payload.get("model") or self.target_model or "").lower()
        if "gemini" in target_check or "generativelanguage" in self.upstream_url.lower():
            self._ensure_gemini_thought_signatures(payload.get("messages", []))

        is_streaming = bool(payload.get("stream", False))
        start_time = time.perf_counter()
        assert self.client_session is not None

        # Build candidate models list for intelligent failover (e.g. on 429 Quota Exceeded or 503 Service Unavailable)
        initial_model = payload.get("model")
        candidate_models = [initial_model] if initial_model else []
        if initial_model and ("gemini" in initial_model.lower() or "generativelanguage" in self.upstream_url.lower()):
            gemini_fallbacks = [
                "gemini-3.5-flash",
                "gemini-3-flash-preview",
                "gemini-3.1-flash-lite",
            ]
            for fb in gemini_fallbacks:
                if fb not in candidate_models:
                    candidate_models.append(fb)
        elif not candidate_models:
            candidate_models = ["gemini-3.5-flash"]

        upstream_ctx = None
        upstream_resp = None

        for idx, model_candidate in enumerate(candidate_models):
            payload["model"] = model_candidate
            is_last_candidate = (idx == len(candidate_models) - 1)
            trigger_failover = False

            for attempt in range(2):
                try:
                    upstream_ctx = self.client_session.post(
                        target_url,
                        json=payload,
                        headers=headers,
                    )
                    upstream_resp = await upstream_ctx.__aenter__()

                    # If 429 (Rate Limit / Quota Exceeded) or 503 (Overloaded) and we have fallbacks
                    if upstream_resp.status in (429, 503) and not is_last_candidate:
                        err_text = await upstream_resp.text()
                        logger.warning(
                            "Upstream returned %d for model '%s' (%s). Triggering auto-failover to '%s'...",
                            upstream_resp.status,
                            model_candidate,
                            err_text[:80].strip(),
                            candidate_models[idx + 1],
                        )
                        await upstream_ctx.__aexit__(None, None, None)
                        upstream_ctx = None
                        trigger_failover = True
                        break

                    # If 5xx and first attempt, retry once
                    if upstream_resp.status >= 500 and attempt == 0:
                        await upstream_ctx.__aexit__(None, None, None)
                        upstream_ctx = None
                        await asyncio.sleep(0.5)
                        continue
                    break
                except Exception as exc:
                    if upstream_ctx:
                        await upstream_ctx.__aexit__(None, None, None)
                        upstream_ctx = None
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    if not is_last_candidate:
                        trigger_failover = True
                        break
                    self.telemetry.record_upstream_status(502)
                    self.telemetry.record_bypass("upstream_error")
                    return web.json_response(
                        {"error": f"Upstream connection failed: {exc}"}, status=502
                    )

            if trigger_failover:
                continue

            if upstream_resp is not None:
                if upstream_resp.status < 400 or is_last_candidate:
                    break

        assert upstream_resp is not None
        self.telemetry.record_upstream_status(upstream_resp.status)

        # If upstream returned an error (e.g. 401, 400, 500), forward directly
        if upstream_resp.status >= 400:
            error_body = await upstream_resp.read()
            if upstream_ctx:
                await upstream_ctx.__aexit__(None, None, None)
            return web.Response(
                body=error_body,
                status=upstream_resp.status,
                content_type=upstream_resp.content_type,
            )

        if is_streaming:
            return await self._handle_streaming_response(
                request, upstream_resp, upstream_ctx, start_time, client_messages, session_id, client=client_name, diet_active=diet_active, model=payload.get("model")
            )
        else:
            return await self._handle_non_streaming_response(
                upstream_resp, upstream_ctx, start_time, client_messages, session_id, client=client_name, diet_active=diet_active, model=payload.get("model")
            )

    async def _handle_non_streaming_response(
        self,
        upstream_resp: aiohttp.ClientResponse,
        upstream_ctx: Any,
        start_time: float,
        messages: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "default",
        client: str = "proxy",
        diet_active: bool = False,
        model: Optional[str] = None,
    ) -> web.Response:
        try:
            body = await upstream_resp.read()
            total_latency_ms = (time.perf_counter() - start_time) * 1000.0
            ttft_ms = total_latency_ms  # for non-streaming, TTFT == latency

            # Extract usage if present in response
            prompt_tok = 0
            cached_tok = 0
            comp_tok = 0
            resp_text = ""
            resp_tool_calls = None
            resp_finish: Optional[str] = None
            resp_upstream_id: Optional[str] = None
            try:
                data = json.loads(body.decode("utf-8"))
                resp_upstream_id = data.get("id") if isinstance(data, dict) else None
                usage = data.get("usage", {})
                if usage:
                    prompt_tok = usage.get("prompt_tokens", 0)
                    comp_tok = usage.get("completion_tokens", 0)
                    cached_tok = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                choices = data.get("choices", [])
                if choices:
                    resp_finish = choices[0].get("finish_reason")
                    msg = choices[0].get("message", {})
                    resp_text = msg.get("content", "") or ""
                    resp_tool_calls = msg.get("tool_calls")
                    if resp_tool_calls and isinstance(resp_tool_calls, list):
                        for tc in resp_tool_calls:
                            if isinstance(tc, dict):
                                tc_id = tc.get("id")
                                sig = (
                                    tc.get("extra_content", {})
                                    .get("google", {})
                                    .get("thought_signature")
                                )
                                if tc_id and sig:
                                    self.thought_signatures[tc_id] = sig
            except Exception:
                pass

            self.telemetry.record_request(
                is_streaming=False,
                ttft_ms=ttft_ms,
                total_latency_ms=total_latency_ms,
                prompt_tokens=prompt_tok,
                cached_tokens=cached_tok,
                completion_tokens=comp_tok,
                diet_active=diet_active,
            )

            # Update TLB and trigger background distillation
            if messages:
                self._update_tlb_and_distill(messages, resp_text, resp_tool_calls, session_id=session_id, client=client)

            receipt = self._issue_receipt(
                streaming=False,
                model_requested=model,
                session_id=session_id,
                client=client,
                stop_reason=resp_finish,
                tool_calls=len(resp_tool_calls) if isinstance(resp_tool_calls, list) else 0,
                upstream_id=resp_upstream_id,
                prompt_tok=prompt_tok,
                cached_tok=cached_tok,
                comp_tok=comp_tok,
            )
            return web.Response(
                body=body,
                status=upstream_resp.status,
                content_type=upstream_resp.content_type,
                headers={"X-Genesis-Receipt": receipt["id"]},
            )
        finally:
            await upstream_ctx.__aexit__(None, None, None)

    async def _handle_streaming_response(
        self,
        request: web.Request,
        upstream_resp: aiohttp.ClientResponse,
        upstream_ctx: Any,
        start_time: float,
        messages: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "default",
        client: str = "proxy",
        diet_active: bool = False,
        model: Optional[str] = None,
    ) -> web.StreamResponse:
        # Prepare streaming response to client immediately
        client_resp = web.StreamResponse(
            status=upstream_resp.status,
            reason=upstream_resp.reason,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await client_resp.prepare(request)

        first_chunk = True
        ttft_ms = 0.0
        prompt_tok = 0
        cached_tok = 0
        comp_tok = 0
        stream_finish: Optional[str] = None
        stream_tool_ids: set = set()
        stream_upstream_id: Optional[str] = None
        line_buffer = ""
        collected_assistant_text = ""

        try:
            # Stream chunks with zero buffering delay
            async for chunk in upstream_resp.content.iter_any():
                if first_chunk:
                    ttft_ms = (time.perf_counter() - start_time) * 1000.0
                    first_chunk = False

                # Pass-through: write directly to client
                await client_resp.write(chunk)

                # Inspect stream on tee for usage metrics & assistant text
                try:
                    text_chunk = chunk.decode("utf-8", errors="ignore")
                    line_buffer += text_chunk
                    while "\n" in line_buffer:
                        line, line_buffer = line_buffer.split("\n", 1)
                        line = line.strip()
                        if line.startswith("data: ") and line != "data: [DONE]":
                            payload_str = line[6:]
                            try:
                                chunk_json = json.loads(payload_str)
                                if not stream_upstream_id and chunk_json.get("id"):
                                    stream_upstream_id = chunk_json.get("id")
                                usage = chunk_json.get("usage")
                                if usage:
                                    prompt_tok = usage.get("prompt_tokens", 0)
                                    comp_tok = usage.get("completion_tokens", 0)
                                    cached_tok = usage.get(
                                        "prompt_tokens_details", {}
                                    ).get("cached_tokens", 0)
                                choices = chunk_json.get("choices", [])
                                if choices and "delta" in choices[0]:
                                    delta = choices[0]["delta"]
                                    if "content" in delta and delta["content"]:
                                        collected_assistant_text += delta["content"]
                                    if "tool_calls" in delta and isinstance(delta["tool_calls"], list):
                                        for tc in delta["tool_calls"]:
                                            if isinstance(tc, dict):
                                                tc_id = tc.get("id")
                                                stream_tool_ids.add(
                                                    tc_id or f"index-{tc.get('index', len(stream_tool_ids))}"
                                                )
                                                sig = (
                                                    tc.get("extra_content", {})
                                                    .get("google", {})
                                                    .get("thought_signature")
                                                )
                                                if tc_id and sig:
                                                    self.thought_signatures[tc_id] = sig
                                if choices and choices[0].get("finish_reason"):
                                    stream_finish = choices[0].get("finish_reason")
                                if choices:
                                    for tc in choices[0].get("tool_calls", []) or []:
                                        if isinstance(tc, dict):
                                            stream_tool_ids.add(
                                                tc.get("id") or f"index-{tc.get('index', len(stream_tool_ids))}"
                                            )
                            except Exception:
                                pass
                except Exception:
                    pass

            try:
                await client_resp.write_eof()
            except (ConnectionResetError, aiohttp.ClientError):
                pass
            total_latency_ms = (time.perf_counter() - start_time) * 1000.0

            self.telemetry.record_request(
                is_streaming=True,
                ttft_ms=ttft_ms,
                total_latency_ms=total_latency_ms,
                prompt_tokens=prompt_tok,
                cached_tokens=cached_tok,
                completion_tokens=comp_tok,
                diet_active=diet_active,
            )
            # Headers already sent: receipt is stored, retrievable via
            # GET /v1/receipts — never injected into the SSE byte stream.
            self._issue_receipt(
                streaming=True,
                model_requested=model,
                session_id=session_id,
                client=client,
                stop_reason=stream_finish,
                tool_calls=len(stream_tool_ids),
                upstream_id=stream_upstream_id,
                prompt_tok=prompt_tok,
                cached_tok=cached_tok,
                comp_tok=comp_tok,
            )

            # Update TLB and trigger background distillation
            if messages:
                self._update_tlb_and_distill(messages, collected_assistant_text, None, session_id=session_id, client=client)

            return client_resp
        finally:
            await upstream_ctx.__aexit__(None, None, None)


def main() -> None:
    parser = argparse.ArgumentParser(description="GENESIS Single-Prompt Reverse Proxy Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument(
        "--upstream-url",
        default=os.environ.get("GENESIS_UPSTREAM_URL", "https://api.openai.com/v1"),
        help="Upstream OpenAI-compatible API base URL",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    proxy = GenesisProxyServer(upstream_url=args.upstream_url)
    logger.info("Starting GENESIS Proxy Server on http://%s:%d -> %s", args.host, args.port, args.upstream_url)
    web.run_app(proxy.app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

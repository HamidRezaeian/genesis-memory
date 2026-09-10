"""GENESIS Reverse Proxy Unified Production Launcher.

Single-command entrypoint that supervises:
1. In-process episodic memory Store on shared SQLite DB (~/.genesis/memory.db with WAL).
2. Asynchronous OpenAI-compatible proxy with Shadow/Live execution modes.
3. Consolidated health and telemetry monitoring.
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path
import signal
import sys
from typing import Any, Optional

from aiohttp import web
from aiohttp.web_log import AccessLogger

from genesis_memory.proxy.proxy_server import GenesisProxyServer, rss_mb
from genesis_memory.daemon.server import Store

logger = logging.getLogger("genesis.launcher")


class QuietAccessLogger(AccessLogger):
    """Filters out noisy periodic monitoring polls from polluting developer terminals."""

    def log(self, request: Any, response: Any, time: float) -> None:
        if request.path in ("/v1/telemetry", "/telemetry", "/health"):
            return
        super().log(request, response, time)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GENESIS Stateless Reverse Proxy Unified Launcher"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("GENESIS_PROXY_PORT", "8000")),
        help="Port to listen on (default: 8000)",
    )
    parser.add_argument(
        "--upstream",
        default=os.environ.get("GENESIS_UPSTREAM_URL", "https://api.openai.com/v1"),
        help="Upstream OpenAI-compatible API base URL",
    )
    parser.add_argument(
        "--mode",
        choices=["shadow", "live"],
        default=os.environ.get("GENESIS_PROXY_MODE", "shadow"),
        help="Proxy execution mode: 'shadow' (observe-only dry run) or 'live' (strip tokens). Default: shadow",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get(
            "GENESIS_MEMORY_DB", str(Path.home() / ".genesis" / "memory.db")
        ),
        help="Path to shared SQLite memory database (default: ~/.genesis/memory.db)",
    )
    parser.add_argument(
        "--max-history-turns",
        type=int,
        default=int(os.environ.get("GENESIS_MAX_HISTORY_TURNS", "1")),
        help="Number of recent turns to retain before stripping (default: 1)",
    )
    parser.add_argument(
        "--diet",
        dest="output_diet",
        action="store_true",
        default=os.environ.get("GENESIS_OUTPUT_DIET", "0").lower() in ("1", "true", "yes"),
        help="Enable static output-diet directive (terse answers, byte-exact code). Default: off",
    )
    parser.add_argument(
        "--compress",
        dest="content_compress",
        action="store_true",
        default=os.environ.get("GENESIS_CONTENT_COMPRESS", "0").lower() in ("1", "true", "yes"),
        help="Enable bulk content compression with local recovery store (fail-closed). Default: off",
    )
    parser.add_argument(
        "--upstream-key",
        default=os.environ.get("GENESIS_UPSTREAM_KEY", os.environ.get("GEMINI_API_KEY")),
        help="API Key for upstream provider",
    )
    parser.add_argument(
        "--target-model",
        default=os.environ.get("GENESIS_TARGET_MODEL", "gemini-3.5-flash"),
        help="Target upstream model to route genesis-stateless requests to (default: gemini-3.5-flash)",
    )
    parser.add_argument(
        "--effort",
        "--reasoning-effort",
        dest="reasoning_effort",
        default=os.environ.get("GENESIS_REASONING_EFFORT", "high"),
        help="Reasoning effort level (e.g. low, medium, high). Default: high",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser


def print_banner(
    host: str,
    port: int,
    upstream: str,
    mode: str,
    db_path: str,
    episodes_count: int,
    target_model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    output_diet: bool = False,
    content_compress: bool = False,
) -> None:
    mode_str = (
        "🟢 LIVE STRIPPING" if mode == "live" else "🟡 SHADOW MODE (OBSERVE-ONLY)"
    )
    border = "=" * 68
    print(border)
    print("  🚀 GENESIS Stateless Proxy — Production Gateway")
    print(f"  Execution Mode: {mode_str}")
    print(f"  Listening on  : http://{host}:{port}/v1")
    print(f"  Upstream Base : {upstream}")
    if target_model:
        eff = f" ({reasoning_effort} effort)" if reasoning_effort else ""
        print(f"  Target Model  : {target_model}{eff}")
    print(f"  Output Diet   : {'ON (terse answers)' if output_diet else 'off'}")
    print(f"  Compress      : {'ON (recovery store)' if content_compress else 'off'}")
    print(f"  Shared Store  : {db_path} ({episodes_count} active episodes)")
    print(f"  Memory (RSS)  : {rss_mb()} MB")
    print(border)
    print("  Connect your LLM clients via:")
    print(f"    export OPENAI_BASE_URL=http://{host}:{port}/v1")
    print("    export OPENAI_API_KEY=<your-real-key>")
    print(f"  Health Check  : http://{host}:{port}/health")
    print(f"  Live Telemetry: http://{host}:{port}/v1/telemetry")
    print(border + "\n")
    sys.stdout.flush()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    db_path = Path(args.db).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Initialize in-process Store on shared SQLite DB
    logger.info("Initializing in-process episodic Store on %s...", db_path)
    store = Store(str(db_path))
    episodes_count = store.status().get("episodes", 0)

    # Initialize Proxy Server
    proxy = GenesisProxyServer(
        upstream_url=args.upstream,
        store=store,
        enable_anaphora_rewrite=True,
        enable_compaction=True,
        enable_output_diet=args.output_diet,
        enable_content_compress=args.content_compress,
        max_history_turns=args.max_history_turns,
        mode=args.mode,
        upstream_key=args.upstream_key,
        target_model=args.target_model,
        reasoning_effort=args.reasoning_effort,
    )

    print_banner(
        host=args.host,
        port=args.port,
        upstream=args.upstream,
        mode=args.mode,
        db_path=str(db_path),
        episodes_count=episodes_count,
        target_model=args.target_model,
        reasoning_effort=args.reasoning_effort,
        output_diet=args.output_diet,
        content_compress=args.content_compress,
    )

    try:
        web.run_app(
            proxy.app,
            host=args.host,
            port=args.port,
            access_log_class=QuietAccessLogger,
            print=lambda x: None,  # Suppress default noisy aiohttp banner
        )
    except (KeyboardInterrupt, SystemExit):
        print("\nStopping GENESIS Proxy gracefully...")
    finally:
        try:
            store.db.close()
        except Exception:
            pass
        print("GENESIS Proxy stopped cleanly.")


if __name__ == "__main__":
    main()

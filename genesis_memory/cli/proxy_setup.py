"""GENESIS Proxy Setup Wizard & Multi-Client Auto-Configuration.

Allows the user to configure their upstream provider (e.g. OpenRouter, OpenAI, Groq)
with their Base URL, API Key, and target model, and automatically activates the local
GENESIS Proxy across all compatible installed clients with explicit user consent.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from genesis_memory.cli.client_registry import ClientRegistry, PROXY_URL, DiscoveredClient
from genesis_memory.proxy.supervisor import (
    ensure_proxy_running,
    is_proxy_configured,
    is_proxy_healthy,
    load_proxy_config,
    save_proxy_config,
)


def _validate_upstream_url(url: str) -> tuple[bool, str]:
    """Validates the upstream URL.
    
    Invariant: Must not be a local loopback (127.0.0.1 / localhost), because the local
    proxy runs on 127.0.0.1:8000, while the upstream URL is the external provider's API.
    """
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return False, "Upstream URL cannot be empty."
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        return False, "Upstream URL must start with http:// or https:// (e.g. https://openrouter.ai/api/v1)."
    lower = cleaned.lower()
    if "127.0.0.1:8000" in lower or "localhost:8000" in lower:
        return False, (
            "Invalid Upstream URL: '127.0.0.1:8000' is the local GENESIS proxy gateway.\n"
            "Please enter the external provider's API URL where you obtained your key\n"
            "(e.g., 'https://openrouter.ai/api/v1' or 'https://api.openai.com/v1')."
        )
    return True, cleaned


def run_proxy_setup(argv: Optional[List[str]] = None) -> int:
    """CLI handler for `genesis proxy setup` / `genesis proxy config`."""
    parser = argparse.ArgumentParser(
        prog="genesis proxy setup",
        description="Configure upstream LLM provider and auto-activate GENESIS Proxy on installed clients.",
    )
    parser.add_argument(
        "-u", "--upstream-url", "--base-url",
        dest="upstream_url",
        help="Upstream Provider Base URL (e.g., https://openrouter.ai/api/v1, https://api.openai.com/v1)",
    )
    parser.add_argument(
        "-k", "--api-key",
        dest="api_key",
        help="Upstream Provider API Key (e.g., sk-or-v1-..., sk-...)",
    )
    parser.add_argument(
        "-m", "--model",
        dest="model",
        help="Default model name to pass or select (e.g., gpt 6 astra, openai/gpt-4o)",
    )
    parser.add_argument(
        "-y", "--yes",
        dest="assume_yes",
        action="store_true",
        help="Automatically confirm client activation without prompting",
    )
    parser.add_argument(
        "--no-start",
        dest="no_start",
        action="store_true",
        help="Configure without immediately starting the proxy daemon",
    )

    args = parser.parse_args(argv or [])

    existing_cfg = load_proxy_config() or {}
    default_url = existing_cfg.get("upstream_url") or os.environ.get("GENESIS_UPSTREAM_URL") or "https://openrouter.ai/api/v1"
    default_key = existing_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY") or os.environ.get("GENESIS_UPSTREAM_KEY") or ""
    default_model = existing_cfg.get("default_model") or ""

    upstream_url = args.upstream_url
    api_key = args.api_key
    model = args.model

    is_interactive = sys.stdin.isatty() and not (upstream_url and api_key)

    if is_interactive:
        print("╔══════════════════════════════════════════════════════════════════════════════╗")
        print("║                     GENESIS AI Proxy Gateway Setup                           ║")
        print("╚══════════════════════════════════════════════════════════════════════════════╝")
        print("Configure your upstream LLM provider (OpenRouter, OpenAI, Groq, etc.).")
        print("GENESIS Proxy runs locally on http://127.0.0.1:8000/v1, automatically injecting")
        print("subconscious memories and governance before forwarding prompts to your provider.\n")

    # 1. Resolve Upstream Base URL
    while not upstream_url:
        if not sys.stdin.isatty():
            print("[genesis] Error: --upstream-url is required in non-interactive mode.", file=sys.stderr)
            return 1
        prompt_txt = f"? Upstream Provider Base URL [{default_url}]: "
        try:
            val = input(prompt_txt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled.")
            return 1
        val = val or default_url
        ok, res = _validate_upstream_url(val)
        if ok:
            upstream_url = res
            break
        print(f"  ❌ {res}\n")
    else:
        ok, res = _validate_upstream_url(upstream_url)
        if not ok:
            print(f"[genesis] Error: {res}", file=sys.stderr)
            return 1
        upstream_url = res

    # 2. Resolve API Key
    while not api_key:
        if not sys.stdin.isatty():
            print("[genesis] Error: --api-key is required in non-interactive mode.", file=sys.stderr)
            return 1
        mask_hint = f" [{default_key[:6]}...{default_key[-4:]}]" if len(default_key) > 10 else ""
        prompt_txt = f"? Provider API Key{mask_hint}: "
        try:
            val = input(prompt_txt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled.")
            return 1
        val = val or default_key
        if val:
            api_key = val
            break
        print("  ❌ API Key cannot be empty. Please enter your provider API key.\n")

    # 3. Model is not asked upfront; users pick models freely in their client under GENESIS Proxy
    if model is None:
        model = default_model or ""

    # 4. Discover compatible clients
    proxy_clients = ClientRegistry.discover_proxy_clients()
    detected_clients = [c for c in proxy_clients if c.detected or c.id == "sdk_gateway"]

    print("\n══════════════════════════════════════════════════════════════════════════════")
    print(f"  Upstream Provider : {upstream_url}")
    print(f"  API Key           : {api_key[:6]}...{api_key[-4:] if len(api_key) > 10 else '****'}")
    print("  Models            : Select freely in your client under 'GENESIS Proxy'")
    print("══════════════════════════════════════════════════════════════════════════════\n")

    wired_clients: List[str] = []
    if detected_clients:
        print("🔍 Detected compatible AI clients on your system:")
        for c in detected_clients:
            print(f"  • {c.name} ({c.config_path})")

        do_wire = args.assume_yes
        if not do_wire:
            if sys.stdin.isatty():
                try:
                    ans = input("\n? Automatically activate GENESIS Proxy on these clients? [Y/n]: ").strip().lower()
                    do_wire = ans in ("", "y", "yes")
                except (KeyboardInterrupt, EOFError):
                    do_wire = False
            else:
                do_wire = True

        if do_wire:
            print("\n⚙️  Configuring clients for GENESIS Proxy (http://127.0.0.1:8000/v1)...")
            print("──────────────────────────────────────────────────────────────────────────────")
            for c in detected_clients:
                ok, msg = ClientRegistry.wire_proxy_client(c, model_name=model)
                if ok:
                    wired_clients.append(c.id)
                    print(f"  ✅ {c.name:<32} -> {c.config_path}")
                else:
                    print(f"  ⚠️  {c.name:<32} -> {msg}")
            print("──────────────────────────────────────────────────────────────────────────────\n")
        else:
            print("\n  ⚪ Skipped client auto-configuration upon request.")
    else:
        print("ℹ️  No external proxy-compatible clients detected. (SDK gateway will be enabled).")

    # 5. Save the configuration
    save_proxy_config(
        upstream_url=upstream_url,
        api_key=api_key,
        default_model=model,
        wired_clients=wired_clients,
    )
    print(f"💾 Configuration saved to ~/.genesis/proxy_config.json")

    # 6. Start the proxy
    if not args.no_start:
        print("\n🚀 Starting GENESIS Proxy Gateway...")
        ok = ensure_proxy_running(quiet=False)
        if ok:
            print("\n╔══════════════════════════════════════════════════════════════════════════════╗")
            print("║                  🟢 GENESIS Proxy is Active and Online!                      ║")
            print("╚══════════════════════════════════════════════════════════════════════════════╝")
            print("  • Local Endpoint  : http://127.0.0.1:8000/v1")
            print(f"  • Upstream Target : {upstream_url}")
            print("  • Models          : Choose ANY model on the fly in your client under 'GENESIS Proxy'")
            print("\n  💡 All requests routed to http://127.0.0.1:8000/v1 now automatically receive")
            print("     subconscious memories, episodic context, and output governance.\n")
        else:
            print("[genesis] ⚠️ Proxy background process spawned. Check status with: genesis proxy status", file=sys.stderr)

    return 0

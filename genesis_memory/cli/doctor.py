"""GENESIS Diagnostic Suite (genesis doctor).

Performs comprehensive pre-flight, runtime, and environmental integrity checks:
1. Python Runtime & uv Distribution Engine.
2. Local ~/.genesis Storage & SQLite WAL Memory DB.
3. Rule 31 Host RSS Budget & Physical Grounding.
4. Rule 32 Universal Spooling Engine & CLI Allowlist.
  5. On-Demand Stateless Proxy & Telemetry Dashboard Ports.
  6. Native SKILL.md drift across AI clients (read-only reminder).
"""

import os
from pathlib import Path
import platform
import shutil
import sqlite3

from genesis_memory.core import db as _dbx
import sys
import time
from typing import Dict, List, Tuple

GENESIS_DIR = Path.home() / ".genesis"
MEMORY_DB_PATH = GENESIS_DIR / "memory.db"
SPOOL_DIR = GENESIS_DIR / "spool"


def check_python_runtime() -> Tuple[bool, str, str]:
    v = sys.version_info
    ver_str = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 10):
        return True, f"Python {ver_str} ({platform.python_implementation()})", "Meets >= 3.10 requirement"
    return False, f"Python {ver_str}", "GENESIS requires Python 3.10 or higher"


def check_uv_engine() -> Tuple[bool, str, str]:
    uv_path = shutil.which("uv")
    if uv_path:
        return True, f"Detected ({uv_path})", "Optimal isolated tool distribution engine"
    return False, "Not detected on PATH", "Recommended: install uv for fast, isolated tool execution (https://astral.sh/uv)"


def check_storage_dirs() -> Tuple[bool, str, str]:
    try:
        GENESIS_DIR.mkdir(parents=True, exist_ok=True)
        SPOOL_DIR.mkdir(parents=True, exist_ok=True)
        # Test write
        test_file = SPOOL_DIR / ".doctor_write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return True, f"{GENESIS_DIR} (Writable)", "Local storage and spool directories initialized"
    except Exception as exc:
        return False, f"{GENESIS_DIR} (Error)", f"Permission or disk failure: {exc}"


REQUIRED_EPISODE_COLUMNS = {"ts", "project", "kind", "text", "utility", "accesses", "updated"}


def check_sqlite_memory() -> Tuple[bool, str, str]:
    if not MEMORY_DB_PATH.exists():
        return True, "Ready for initialization", "Database will be created automatically on first run"

    try:
        conn = _dbx.connect(str(MEMORY_DB_PATH), readonly=True, busy_timeout_ms=1000)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cursor.fetchall()]
        if "episodes" not in tables:
            conn.close()
            return False, "Schema incomplete (no episodes table)", (
                f"Path: {MEMORY_DB_PATH} — run 'genesis setup --yes' to auto-repair, "
                "or delete the DB to recreate it (loses stored memories)"
            )
        cursor.execute("PRAGMA table_info(episodes);")
        cols = {r[1] for r in cursor.fetchall()}
        missing = sorted(REQUIRED_EPISODE_COLUMNS - cols)
        cursor.execute("PRAGMA user_version;")
        schema_version = cursor.fetchone()[0]
        conn.close()
        if missing:
            return False, f"Schema mismatch (missing: {', '.join(missing)})", (
                f"Path: {MEMORY_DB_PATH} — legacy partial schema from an older installer. "
                "Run 'genesis setup --yes' to repair in place (additive, no data loss)"
            )
        # Functional proof: the daemon's exact recall path must work, not just COUNT(*).
        try:
            from genesis_memory.daemon.server import Store
            probe = Store(str(MEMORY_DB_PATH))
            try:
                probe.recall("__genesis_doctor_probe__", limit=1)
            finally:
                try:
                    probe.db.close()
                except Exception:
                    pass
        except Exception as exc:
            return False, "Unusable (recall probe failed)", (
                f"Path: {MEMORY_DB_PATH} — {type(exc).__name__}: {exc}. "
                "Run 'genesis setup --yes' to repair in place"
            )
        return True, f"Active ({len(tables)} tables, WAL={mode.upper()}, schema v{schema_version})", f"Path: {MEMORY_DB_PATH}"
    except Exception as exc:
        return False, "Corrupt or locked", f"Failed SQLite inspection: {exc}"


def check_rss_budget() -> Tuple[bool, str, str]:
    try:
        import psutil
        proc = psutil.Process()
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        if rss_mb < 100.0:
            return True, f"{rss_mb:.1f} MB (Budget < 100 MB)", "Complies with Rule 31 RSS budget"
        return False, f"{rss_mb:.1f} MB (> 100 MB Limit)", "Memory consumption exceeds Rule 31 budget"
    except ImportError:
        return True, "psutil not available", "Skipped live RSS measurement"


def check_proxy_gateway() -> Tuple[bool, str, str]:
    from genesis_memory.proxy.supervisor import is_proxy_healthy
    host = os.environ.get("GENESIS_PROXY_HOST", "127.0.0.1")
    port = int(os.environ.get("GENESIS_PROXY_PORT", "8000"))

    if is_proxy_healthy(host, port):
        return True, f"Online (http://{host}:{port}/v1)", "Stateless reverse proxy is active and healthy"
    return True, f"On-Demand Standby (http://{host}:{port}/v1)", "Will automatically spawn in background on first prompt"


def check_dashboard_telemetry() -> Tuple[bool, str, str]:
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:8090/health", headers={"User-Agent": "genesis-doctor/1.0"})
        with urllib.request.urlopen(req, timeout=0.5) as res:
            if res.status == 200:
                return True, "Online (http://127.0.0.1:8090/)", "Observation deck dashboard active"
    except Exception:
        pass
    return True, "Offline / Optional (Port 8090)", "Start with: genesis dashboard (Pro channel)"


def check_cli_spooler() -> Tuple[bool, str, str]:
    from genesis_memory.cli.run import ALLOWLIST_COMMANDS
    cmd_count = len(ALLOWLIST_COMMANDS)
    return True, f"Configured ({cmd_count} allowlisted tools)", f"Enforces Rule 32 zero-spam execution for: {', '.join(sorted(ALLOWLIST_COMMANDS))}"


def check_client_integrations() -> Tuple[bool, str, str]:
    from genesis_memory.cli.client_registry import ClientRegistry
    clients = ClientRegistry.discover_all()
    configured = [c.name for c in clients if c.configured]
    detected = [c.name for c in clients if c.detected]
    
    if configured:
        return True, f"Active: {', '.join(configured)}", f"Total detected clients: {len(detected)}"
    elif detected:
        return True, f"Detected ({', '.join(detected)})", "Run 'genesis init' to auto-wire memory into detected clients"
    return True, "No external clients detected", "Snippet available at ~/.genesis/mcp_snippet.json"


def check_skill_sync_drift() -> Tuple[bool, str, str]:
    """Native-skill union drift (read-only). Non-vital: WARN at most, never FAIL."""
    from genesis_memory.cli.skill_sync import sync_status
    try:
        st = sync_status()
    except Exception as exc:
        return True, "Check skipped", f"skill inventory failed: {type(exc).__name__}"
    if not st or st["union"] == 0:
        return True, "No native skills detected", "Install a SKILL.md skill, then run: genesis skill-sync"
    if st["pending"] == 0 and st["conflicts"] == 0:
        return True, f"In sync ({st['union']} skills across {st['locations']} locations)", "Run 'genesis skill-sync' for details"
    bits = []
    if st["pending"]:
        bits.append(f"{st['pending']} pending links")
    if st["conflicts"]:
        bits.append(f"{st['conflicts']} conflicts")
    return False, f"{st['union']} skills, {', '.join(bits)}", "Run 'genesis skill-sync --apply' to share across clients"


def run_doctor(verbose: bool = False) -> int:
    """Executes all diagnostics and prints a structured, high-legibility report."""
    print("=" * 68)
    print("  🩺 GENESIS System & Environment Doctor")
    print(f"  Platform: {platform.system()} {platform.release()} ({platform.machine()})")
    print("=" * 68)

    checks = [
        ("Python Runtime", check_python_runtime, True),
        ("uv Tool Distribution", check_uv_engine, False),
        ("Local Directory Storage", check_storage_dirs, True),
        ("SQLite Subconscious DB", check_sqlite_memory, True),
        ("Memory RSS Discipline", check_rss_budget, False),
        ("Stateless Proxy Gateway", check_proxy_gateway, False),
        ("Telemetry Observation Deck", check_dashboard_telemetry, False),
        ("Rule 32 Spooling Engine", check_cli_spooler, True),
        ("AI Client Integrations", check_client_integrations, False),
        ("Native Skill Sync", check_skill_sync_drift, False),
    ]

    all_vital_passed = True
    for name, func, vital in checks:
        ok, status_text, detail = func()
        if not ok and vital:
            all_vital_passed = False

        symbol = "✅" if ok else ("⚠️" if not vital else "❌")
        tag = "[OK]" if ok else ("[WARN]" if not vital else "[FAIL]")
        print(f"  {symbol} {tag:6} {name:<26} : {status_text}")
        if verbose or not ok:
            print(f"           └── {detail}")

    from genesis_memory.cli.update_checker import print_update_notice_if_available
    print_update_notice_if_available()

    if all_vital_passed:
        print("  🎉 All critical diagnostic checks passed! GENESIS is healthy.")
        return 0
    else:
        print("  ⚠️ One or more critical checks failed. Please resolve the items marked [FAIL].")
        return 1


if __name__ == "__main__":
    sys.exit(run_doctor(verbose=True))

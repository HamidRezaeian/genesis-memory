"""GENESIS Memory Update Checker & Upgrade Engine.

Provides non-blocking, cached (24h TTL) background version checks against PyPI,
terminal notification banners, and a 1-click `genesis upgrade` command.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from typing import Optional, Tuple

from genesis_memory import __version__

CACHE_TTL_SECONDS = 86400  # 24 hours
PYPI_URL = "https://pypi.org/pypi/genesis-memory/json"
TIMEOUT_SECONDS = 0.8


def _get_cache_path() -> str:
    home = os.path.expanduser("~")
    genesis_dir = os.path.join(home, ".genesis")
    os.makedirs(genesis_dir, exist_ok=True)
    return os.path.join(genesis_dir, "update_cache.json")


def _parse_version(v: str) -> Tuple[int, ...]:
    """Parse semver-like strings into tuple of ints for clean comparison."""
    clean = v.lstrip("v").split("+")[0].split("-")[0]
    parts = []
    for part in clean.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def get_latest_pypi_version(force: bool = False) -> Optional[str]:
    """Fetch latest released version from PyPI with a local 24h file cache.
    
    Guaranteed non-blocking with 0.8s timeout; fails silently on air-gap/offline.
    """
    cache_file = _get_cache_path()
    now = time.time()

    if not force and os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                last_checked = data.get("last_checked", 0)
                cached_ver = data.get("latest_version")
                if cached_ver and (now - last_checked < CACHE_TTL_SECONDS):
                    return cached_ver
        except Exception:
            pass

    # Query PyPI
    try:
        req = urllib.request.Request(
            PYPI_URL,
            headers={"User-Agent": f"genesis-memory/{__version__} (update-check)"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                latest_ver = payload.get("info", {}).get("version")
                if latest_ver:
                    try:
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(
                                {"last_checked": now, "latest_version": latest_ver},
                                f,
                            )
                    except Exception:
                        pass
                    return latest_ver
    except Exception:
        # Offline, timeout, or DNS failure - fail silently
        pass

    return None


def check_for_updates() -> Optional[Tuple[str, str]]:
    """Returns (current_version, latest_version) if a newer version exists."""
    latest = get_latest_pypi_version()
    if not latest:
        return None

    try:
        cur_tuple = _parse_version(__version__)
        lat_tuple = _parse_version(latest)
        if lat_tuple > cur_tuple:
            return (__version__, latest)
    except Exception:
        pass

    return None


def render_update_banner() -> Optional[str]:
    """Renders a beautiful, ANSI-colored update banner if a newer version is available."""
    update = check_for_updates()
    if not update:
        return None

    cur, lat = update
    msg = (
        f"\n  ╭──────────────────────────────────────────────────────────────────╮\n"
        f"  │  🔔 Update available: \033[31mv{cur}\033[0m → \033[32mv{lat}\033[0m                              │\n"
        f"  │  Run: \033[36mgenesis upgrade\033[0m  (or pip install -U genesis-memory)         │\n"
        f"  ╰──────────────────────────────────────────────────────────────────╯\n"
    )
    return msg


def print_update_notice_if_available() -> None:
    """Non-blocking helper to print update notice at the end of CLI executions."""
    try:
        banner = render_update_banner()
        if banner:
            print(banner, file=sys.stderr)
    except Exception:
        pass


def run_upgrade() -> int:
    """Executes 1-click self-upgrade via pip."""
    print("╔════════════════════════════════════════════════════════════════════╗")
    print("║             🚀 GENESIS Memory — 1-Click Upgrade Engine             ║")
    print("╚════════════════════════════════════════════════════════════════════╝")
    print(f"  Current installed version: v{__version__}")
    print("  Checking latest releases on PyPI...")

    latest = get_latest_pypi_version(force=True)
    if latest:
        print(f"  Latest available version : v{latest}")
        if _parse_version(latest) <= _parse_version(__version__):
            print(f"\n  ✅ You are already on the latest version (v{__version__})!")
            return 0
    else:
        print("  (Could not reach PyPI directly; attempting pip upgrade...)")

    print(f"\n  Running: {sys.executable} -m pip install --upgrade genesis-memory\n")
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "genesis-memory"],
            check=False,
        )
        if res.returncode == 0:
            print("\n  🎉 Upgrade complete! Run 'genesis doctor' to verify system health.")
        else:
            print("\n  ❌ Upgrade command failed. Try running with elevated permissions (sudo or Run as Administrator).", file=sys.stderr)
        return res.returncode
    except Exception as exc:
        print(f"\n  ❌ Failed to invoke pip: {exc}", file=sys.stderr)
        return 1

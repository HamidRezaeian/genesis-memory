"""GENESIS Proxy Supervisor & On-Demand Lifecycle Manager.

Implements OpenCode's binding architectural specification:
1. On-Demand Lazy Spawning: Runs only when requested, not an intrusive always-on daemon.
2. Single-Flight Concurrency: Lockfile with active PID checking prevents port collisions.
3. Stale-Lock Auto-Pruning: Detects and removes dead locks automatically.
4. Non-Blocking Health Probe: Lightweight socket/HTTP health check with graceful timeout.
"""

import errno
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Optional, Tuple
import urllib.error
import urllib.request

logger = logging.getLogger("genesis.proxy.supervisor")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
GENESIS_DIR = Path.home() / ".genesis"
LOCKFILE_PATH = GENESIS_DIR / "proxy.lock"
PIDFILE_PATH = GENESIS_DIR / "proxy.pid"
PROXY_CONFIG_PATH = GENESIS_DIR / "proxy_config.json"


def get_genesis_dir() -> Path:
    """Ensures and returns the ~/.genesis directory."""
    GENESIS_DIR.mkdir(parents=True, exist_ok=True)
    return GENESIS_DIR


def load_proxy_config() -> Optional[dict]:
    """Loads saved proxy upstream configuration from ~/.genesis/proxy_config.json."""
    if not PROXY_CONFIG_PATH.exists():
        return None
    try:
        content = PROXY_CONFIG_PATH.read_text(encoding="utf-8").strip()
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("Could not read proxy_config.json: %s", exc)
    return None


def save_proxy_config(
    upstream_url: str,
    api_key: str,
    default_model: Optional[str] = None,
    wired_clients: Optional[list] = None,
) -> Path:
    """Saves proxy upstream configuration to ~/.genesis/proxy_config.json with 0o600 permissions."""
    get_genesis_dir()
    data = {
        "upstream_url": upstream_url.strip().rstrip("/"),
        "api_key": api_key.strip(),
        "default_model": default_model.strip() if default_model else "",
        "wired_clients": wired_clients or [],
        "updated_at": time.time(),
    }
    PROXY_CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if sys.platform != "win32":
        try:
            os.chmod(PROXY_CONFIG_PATH, 0o600)
        except OSError:
            pass
    return PROXY_CONFIG_PATH


def is_proxy_configured() -> bool:
    """Checks whether the proxy has a valid Upstream Base URL and API Key configured.
    
    Checks ~/.genesis/proxy_config.json first, then active environment variables.
    The proxy refuses to start without explicit upstream configuration.
    """
    cfg = load_proxy_config()
    if cfg and cfg.get("upstream_url") and cfg.get("api_key"):
        return True

    # Check environment variables
    env_url = os.environ.get("GENESIS_UPSTREAM_URL")
    env_key = (
        os.environ.get("GENESIS_UPSTREAM_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
    )
    if env_url and env_key:
        return True

    return False


def is_pid_alive(pid: int) -> bool:
    """Checks whether a given process ID is currently running on the host OS."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle == 0:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            # Fallback for Windows
            try:
                res = subprocess.run(
                    f'tasklist /FI "PID eq {pid}" /NH',
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                return str(pid) in res.stdout
            except Exception:
                return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError as err:
            return err.errno == errno.EPERM


def is_proxy_healthy(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout_sec: float = 0.5) -> bool:
    """Probes the proxy's /health endpoint with a strict low latency timeout."""
    url = f"http://{host}:{port}/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "genesis-supervisor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def clean_stale_lock() -> None:
    """Removes the lockfile if the recorded process is dead."""
    if not LOCKFILE_PATH.exists():
        return

    try:
        content = LOCKFILE_PATH.read_text(encoding="utf-8").strip()
        data = json.loads(content)
        pid = int(data.get("pid", 0))
        if not is_pid_alive(pid):
            LOCKFILE_PATH.unlink(missing_ok=True)
            PIDFILE_PATH.unlink(missing_ok=True)
            logger.info("Cleaned stale proxy lockfile for dead PID %d", pid)
    except Exception:
        # Corrupt lockfile
        LOCKFILE_PATH.unlink(missing_ok=True)


def acquire_lock(timeout_sec: float = 3.0) -> bool:
    """Acquires single-flight lock with PID registration and stale cleanup."""
    get_genesis_dir()
    clean_stale_lock()

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            # Atomic creation flag
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            fd = os.open(str(LOCKFILE_PATH), flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({
                    "pid": os.getpid(),
                    "time": time.time(),
                    "host": DEFAULT_HOST,
                    "port": DEFAULT_PORT,
                }, f)
            return True
        except FileExistsError:
            clean_stale_lock()
            time.sleep(0.1)
        except Exception:
            return False

    return False


def release_lock() -> None:
    """Releases the lockfile safely if owned by this process."""
    if not LOCKFILE_PATH.exists():
        return
    try:
        content = LOCKFILE_PATH.read_text(encoding="utf-8").strip()
        data = json.loads(content)
        if data.get("pid") == os.getpid():
            LOCKFILE_PATH.unlink(missing_ok=True)
    except Exception:
        LOCKFILE_PATH.unlink(missing_ok=True)


def build_proxy_cmd(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    mode: str = "live",
    upstream_url: Optional[str] = None,
    upstream_key: Optional[str] = None,
    target_model: Optional[str] = None,
) -> Tuple[list, dict]:
    """Builds the child argv + env for the background proxy.

    The API key travels ONLY via the child environment
    (``GENESIS_UPSTREAM_KEY``) — never on argv, where any local process could
    read it from ``/proc/<pid>/cmdline`` (Linux) or process listings.
    """
    launcher_module = "genesis_memory.proxy.launcher"

    cfg = load_proxy_config() or {}
    resolved_upstream = upstream_url or os.environ.get("GENESIS_UPSTREAM_URL") or cfg.get("upstream_url")
    resolved_key = (
        upstream_key
        or os.environ.get("GENESIS_UPSTREAM_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or cfg.get("api_key")
    )
    resolved_model = target_model or os.environ.get("GENESIS_TARGET_MODEL") or cfg.get("default_model")

    env = os.environ.copy()
    env["GENESIS_PROXY_HOST"] = host
    env["GENESIS_PROXY_PORT"] = str(port)
    env["GENESIS_PROXY_MODE"] = mode
    if resolved_upstream:
        env["GENESIS_UPSTREAM_URL"] = resolved_upstream
    if resolved_key:
        env["GENESIS_UPSTREAM_KEY"] = resolved_key
    if resolved_model:
        env["GENESIS_TARGET_MODEL"] = resolved_model

    cmd = [
        sys.executable,
        "-m",
        launcher_module,
        "--host",
        host,
        "--port",
        str(port),
        "--mode",
        mode,
    ]
    if resolved_upstream:
        cmd.extend(["--upstream", resolved_upstream])
    if resolved_model:
        cmd.extend(["--target-model", resolved_model])
    return cmd, env


def spawn_background_proxy(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    mode: str = "live",
    upstream_url: Optional[str] = None,
    upstream_key: Optional[str] = None,
    target_model: Optional[str] = None,
) -> Tuple[bool, str]:
    """Spawns the proxy server as a decoupled background process with configured upstream credentials."""
    cmd, env = build_proxy_cmd(
        host=host, port=port, mode=mode,
        upstream_url=upstream_url, upstream_key=upstream_key,
        target_model=target_model,
    )

    log_file = get_genesis_dir() / "proxy.log"
    flags = 0
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    try:
        with open(log_file, "a", encoding="utf-8") as out:
            proc = subprocess.Popen(
                cmd,
                stdout=out,
                stderr=out,
                stdin=subprocess.DEVNULL,
                env=env,
                cwd=None,  # inherit caller cwd: installed package needs no repo checkout
                creationflags=flags,
                close_fds=(sys.platform != "win32"),
            )
            # Record child PID
            PIDFILE_PATH.write_text(str(proc.pid), encoding="utf-8")
            return True, f"Spawned background proxy with PID {proc.pid}"
    except Exception as exc:
        return False, f"Failed to spawn background proxy: {exc}"


def ensure_proxy_running(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    wait_timeout_sec: float = 3.0,
    quiet: bool = False,
    force_config_check: bool = True,
) -> bool:
    """Main entrypoint: guarantees proxy is healthy, spawning on-demand if missing.
    
    Refuses to start if the proxy has not been configured with an Upstream Base URL and API Key.
    """
    if is_proxy_healthy(host, port):
        return True

    if force_config_check and not is_proxy_configured():
        if not quiet:
            print("\n  ❌ [genesis] Proxy cannot start: Upstream Base URL and API Key are required.")
            print("  👉 Run 'genesis proxy setup' to configure your provider (e.g. OpenRouter, OpenAI, Groq).")
            print("     Example: genesis proxy setup --upstream-url https://openrouter.ai/api/v1 --api-key sk-or-v1-...\n")
        return False

    if not quiet:
        print(f"[genesis] Starting Stateless Proxy Gateway on http://{host}:{port}/v1 ...")

    if not acquire_lock(timeout_sec=2.0):
        # Wait to see if the other competing process brought it up
        deadline = time.time() + wait_timeout_sec
        while time.time() < deadline:
            if is_proxy_healthy(host, port):
                return True
            time.sleep(0.15)
        return is_proxy_healthy(host, port)

    try:
        # Re-check under lock
        if is_proxy_healthy(host, port):
            return True

        success, msg = spawn_background_proxy(host, port)
        if not success:
            logger.error(msg)
            return False

        # Wait for health check confirmation
        deadline = time.time() + wait_timeout_sec
        while time.time() < deadline:
            if is_proxy_healthy(host, port):
                return True
            time.sleep(0.1)

        return is_proxy_healthy(host, port)
    finally:
        release_lock()


def stop_proxy(timeout_sec: float = 3.0) -> bool:
    """Gracefully stops any running proxy process recorded in ~/.genesis/proxy.pid."""
    if not PIDFILE_PATH.exists():
        clean_stale_lock()
        return True

    try:
        pid = int(PIDFILE_PATH.read_text(encoding="utf-8").strip())
        if is_pid_alive(pid):
            if sys.platform == "win32":
                subprocess.run(f"taskkill /F /T /PID {pid}", shell=True, capture_output=True)
            else:
                os.kill(pid, 15)  # SIGTERM
                time.sleep(0.5)
                if is_pid_alive(pid):
                    os.kill(pid, 9)  # SIGKILL

        PIDFILE_PATH.unlink(missing_ok=True)
        LOCKFILE_PATH.unlink(missing_ok=True)
        return True
    except Exception as exc:
        logger.error("Failed to stop proxy: %exc", exc)
        return False

"""GENESIS Unified Command Runner (genesis run).

Production producer for lossless pointer-not-payload spooling.
Intercepts allowlisted commands, enforces non-interactive headless flags,
captures raw stdout/stderr to ~/.genesis/spool/<id>.log, auto-prunes via LRU,
emits a locked ~80-token summary with line anchors to the terminal, and
preserves the child process exit code with 100% fidelity.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from typing import List, Optional

from genesis_memory.proxy.spool import SpoolEngine
from genesis_memory.proxy.summary_parser import parse_pytest_summary, parse_generic_summary

# Commands certified for headless lossless spooling
ALLOWLIST_COMMANDS = {
    "pytest",
    "tsc",
    "cargo",
    "ruff",
    "mypy",
    "git",
    "npm",
    "go",
}

# Subcommands allowed for multi-word tools (e.g., git log, npm test)
ALLOWLIST_SUBCOMMANDS = {
    "git": {"log", "diff", "status", "show", "branch"},
    "npm": {"test", "run", "build", "lint"},
    "cargo": {"test", "build", "check"},
    "go": {"test", "build"},
}


def is_spoolable_command(argv: List[str]) -> bool:
    """Evaluates whether a command line qualifies for headless lossless spooling."""
    if not argv:
        return False

    prog = os.path.basename(argv[0]).lower()
    if prog.endswith(".exe") or prog.endswith(".cmd") or prog.endswith(".ps1"):
        prog = prog.rsplit(".", 1)[0]

    # Handle 'python -m pytest'
    if prog in ("python", "python3", "py") and len(argv) >= 3 and argv[1] == "-m":
        mod = argv[2].lower()
        if mod == "pytest":
            return True

    if prog not in ALLOWLIST_COMMANDS:
        return False

    suballowed = ALLOWLIST_SUBCOMMANDS.get(prog)
    if suballowed is not None:
        if len(argv) < 2:
            return False
        sub = argv[1].lower()
        return sub in suballowed

    return True


def execute_spooled(cmd_args: List[str]) -> int:
    """Executes an allowlisted command with headless environment, spools output, and prints summary."""
    env = os.environ.copy()
    env.update({
        "CI": "1",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "FORCE_COLOR": "0",
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "npm_config_yes": "true",
    })

    is_pytest = (
        "pytest" in cmd_args[0].lower()
        or (len(cmd_args) >= 3 and cmd_args[1] == "-m" and cmd_args[2].lower() == "pytest")
    )

    if is_pytest:
        # Append plugin without overwriting existing user flags (Fable/OpenCode Consensus)
        curr_addopts = env.get("PYTEST_ADDOPTS", "")
        if "genesis_memory.proxy.pytest_spool" not in curr_addopts and "genesis.proxy.pytest_spool" not in curr_addopts:
            env["PYTEST_ADDOPTS"] = f"{curr_addopts} -p genesis_memory.proxy.pytest_spool -ra --tb=long".strip()

    engine = SpoolEngine()

    proc: Optional[subprocess.Popen] = None
    try:
        resolved_exe = shutil.which(cmd_args[0])
        exec_cmd = [resolved_exe] + cmd_args[1:] if resolved_exe else cmd_args
        proc = subprocess.Popen(
            exec_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )

        raw_bytes, _ = proc.communicate(timeout=900)  # 15 minutes safety ceiling
        exit_code = proc.returncode

    except subprocess.TimeoutExpired:
        if proc:
            # Kill child process tree on Windows
            if sys.platform == "win32":
                subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, capture_output=True)
            else:
                proc.kill()
        raw_bytes = b"\n[ERROR: Command timed out after 900s]\n"
        exit_code = 124
    except KeyboardInterrupt:
        if proc:
            if sys.platform == "win32":
                subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, capture_output=True)
            else:
                proc.send_signal(signal.SIGINT)
        return 130

    cmd_str = " ".join(cmd_args)
    spool_id, log_path = engine.write_spool(
        raw_bytes,
        command=cmd_str,
        exit_code=exit_code,
        metadata={"cwd": os.getcwd()},
    )

    # Periodic retention pruning (1-in-10 calls)
    if engine.spools_created % 10 == 0:
        try:
            engine.prune()
        except Exception:
            pass

    # Parse and output deterministic summary
    try:
        decoded_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        decoded_text = raw_bytes.decode("utf-8", errors="replace")

    if is_pytest:
        summary = parse_pytest_summary(decoded_text, exit_code=exit_code, spool_id=spool_id)
    else:
        summary = parse_generic_summary(
            decoded_text,
            exit_code=exit_code,
            spool_id=spool_id,
            command=cmd_str,
        )

    print(summary)
    return exit_code


def main(argv: Optional[List[str]] = None) -> int:
    """Unified CLI entrypoint for GENESIS."""
    args = list(argv) if argv is not None else sys.argv[1:]

    if not args:
        print(
            "Usage: genesis <command> [options]\n\n"
            "Commands:\n"
            "  init       Initialize local environment and zero-friction onboarding\n"
            "  doctor     Diagnose system health, storage, RSS, and connectivity\n"
            "  run        Execute allowlisted commands with headless lossless spooling\n"
            "  proxy      Manage on-demand stateless proxy gateway (start|stop|status)\n"
            "\n"
            "Examples:\n"
            "  genesis init --yes\n"
            "  genesis doctor\n"
            "  genesis run -- pytest tests/\n"
            "  genesis proxy status\n"
            "  genesis sleep [--now | --daemon]\n"
            "  genesis skills",
            file=sys.stderr,
        )
        return 1

    subcmd = args[0].lower()

    if subcmd in ("init", "setup"):
        from genesis_memory.cli.init_cmd import main as init_main
        return init_main(args[1:])

    if subcmd == "doctor":
        from genesis_memory.cli.doctor import run_doctor
        verbose = "-v" in args[1:] or "--verbose" in args[1:]
        return run_doctor(verbose=verbose)

    if subcmd == "sleep":
        from genesis_memory.sleep.sleep_daemon import main as sleep_main
        return sleep_main(args[1:])

    if subcmd in ("skills", "skill"):
        db_path = os.environ.get("GENESIS_DAEMON_DB", os.path.expanduser("~/.genesis/memory.db"))
        if not os.path.exists(db_path):
            print(f"[genesis] Database not found: {db_path}", file=sys.stderr)
            return 1
        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT id, name, action_recipe, confidence, success_count, status FROM skills ORDER BY success_count DESC, confidence DESC"
            ).fetchall()
            if not rows:
                print("[genesis] No synthesized skills recorded yet.")
                return 0
            print(f"[genesis] 🧠 Procedural Skills ({len(rows)} registered):")
            for sid, name, recipe, conf, succ, st in rows:
                print(f"  • [{sid}] {name} (conf={conf}, success={succ}, status={st})")
                print(f"    Recipe: {recipe[:100]}...")
            return 0
        finally:
            conn.close()

    if subcmd == "proxy":
        from genesis_memory.proxy.supervisor import ensure_proxy_running, stop_proxy, is_proxy_healthy
        action = args[1].lower() if len(args) > 1 else "status"
        if action == "start":
            ok = ensure_proxy_running()
            return 0 if ok else 1
        elif action == "stop":
            ok = stop_proxy()
            print("[genesis] Proxy stopped." if ok else "[genesis] Failed to stop proxy.")
            return 0 if ok else 1
        elif action == "status":
            healthy = is_proxy_healthy()
            status_str = "🟢 Online" if healthy else "⚪ Standby (On-Demand)"
            print(f"[genesis] Proxy Gateway: {status_str} (http://127.0.0.1:8000/v1)")
            return 0
        else:
            print(f"Unknown proxy action '{action}'. Use: start | stop | status", file=sys.stderr)
            return 1

    # Strip leading "run" subcommand if invoked as `genesis run ...`
    if subcmd == "run":
        args = args[1:]

    # Remove leading '--' if provided: e.g. "genesis run -- pytest tests/"
    if args and args[0] == "--":
        args = args[1:]

    if not args:
        print("Usage: genesis run [--] <command> [args...]", file=sys.stderr)
        return 1

    if is_spoolable_command(args):
        return execute_spooled(args)
    else:
        # Non-allowlisted interactive/custom commands: direct pass-through
        resolved = shutil.which(args[0])
        exec_args = [resolved] + args[1:] if resolved else args
        res = subprocess.run(exec_args)
        return res.returncode


if __name__ == "__main__":
    sys.exit(main())

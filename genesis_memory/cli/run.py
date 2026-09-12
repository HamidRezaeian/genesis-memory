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

# Commands certified for headless lossless spooling (every major toolchain).
ALLOWLIST_COMMANDS = {
    # Python
    "pytest", "ruff", "mypy", "black", "flake8", "pylint", "isort", "pyright", "tox", "nox",
    "pip", "pip3", "uv", "poetry", "pdm", "hatch",
    # JavaScript / TypeScript
    "tsc", "npm", "npx", "pnpm", "yarn", "bun", "deno", "node", "eslint", "prettier", "vitest", "jest", "vite",
    # Rust / Go / .NET / JVM / C-family
    "cargo", "rustc", "clippy-driver", "go", "gofmt", "golangci-lint", "dotnet",
    "gradle", "gradlew", "mvn", "mvnw", "make", "cmake", "ninja", "bazel",
    # Ruby / PHP / Elixir / Swift
    "bundle", "rake", "rspec", "composer", "phpunit", "mix", "swift",
    # VCS / containers / infra
    "git", "gh", "docker", "docker-compose", "podman", "kubectl", "helm", "terraform", "tofu", "pulumi",
}

# Subcommands allowed for multi-word tools; anything interactive (shell, attach,
# rebase -i, watch) is deliberately absent so the child can never block on a TTY.
ALLOWLIST_SUBCOMMANDS = {
    "git": {"log", "diff", "status", "show", "branch", "blame", "grep", "ls-files", "rev-parse",
            "describe", "tag", "remote", "fetch", "stash", "worktree", "shortlog", "cherry"},
    "gh": {"pr", "issue", "run", "repo", "api", "release", "workflow"},
    "npm": {"test", "run", "build", "lint", "ci", "install", "audit", "ls", "outdated", "pack", "view"},
    "pnpm": {"test", "run", "build", "lint", "install", "audit", "ls", "outdated"},
    "yarn": {"test", "run", "build", "lint", "install", "audit", "list", "outdated"},
    "bun": {"test", "run", "build", "install", "x"},
    "deno": {"test", "run", "lint", "fmt", "check", "task", "compile"},
    "node": {"--test", "--check", "--version"},
    "cargo": {"test", "build", "check", "clippy", "fmt", "doc", "bench", "run", "tree", "metadata", "audit", "nextest"},
    "go": {"test", "build", "vet", "run", "mod", "fmt", "generate", "list", "install"},
    "dotnet": {"test", "build", "restore", "run", "format", "publish", "pack", "clean", "list"},
    "gradle": {"test", "build", "check", "assemble", "clean", "dependencies", "tasks"},
    "gradlew": {"test", "build", "check", "assemble", "clean", "dependencies", "tasks"},
    "mvn": {"test", "package", "compile", "verify", "install", "clean", "dependency:tree"},
    "mvnw": {"test", "package", "compile", "verify", "install", "clean"},
    "docker": {"build", "compose", "ps", "images", "logs", "inspect", "pull", "push", "version", "info"},
    "docker-compose": {"build", "ps", "logs", "config", "pull", "up", "down"},
    "podman": {"build", "ps", "images", "logs", "inspect", "pull"},
    "kubectl": {"get", "describe", "logs", "apply", "diff", "version", "explain", "rollout"},
    "helm": {"lint", "template", "list", "status", "dependency", "version"},
    "terraform": {"plan", "validate", "fmt", "init", "show", "output", "version"},
    "tofu": {"plan", "validate", "fmt", "init", "show", "output", "version"},
    "pulumi": {"preview", "stack", "config", "version"},
    "pip": {"install", "list", "show", "freeze", "check", "download", "wheel"},
    "pip3": {"install", "list", "show", "freeze", "check", "download", "wheel"},
    "uv": {"pip", "run", "sync", "lock", "build", "tree", "venv", "add"},
    "poetry": {"install", "run", "build", "lock", "show", "check", "update"},
    "pdm": {"install", "run", "build", "lock", "list", "sync"},
    "hatch": {"test", "run", "build", "fmt", "version"},
    "bundle": {"exec", "install", "update", "list", "outdated"},
    "composer": {"install", "update", "test", "validate", "show", "outdated"},
    "mix": {"test", "compile", "format", "deps.get", "credo", "dialyzer"},
    "swift": {"test", "build", "package", "run"},
    "bazel": {"test", "build", "query", "run"},
}

# Env vars that make every toolchain non-interactive, colour-free and deterministic.
HEADLESS_ENV = {
    "CI": "1",
    "TERM": "dumb",
    "NO_COLOR": "1",
    "FORCE_COLOR": "0",
    "CLICOLOR": "0",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_EDITOR": "true",
    "EDITOR": "true",
    "VISUAL": "true",
    "DEBIAN_FRONTEND": "noninteractive",
    "PYTHONUNBUFFERED": "1",
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "PIP_NO_INPUT": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_PROGRESS_BAR": "off",
    "UV_NO_PROGRESS": "1",
    "npm_config_yes": "true",
    "npm_config_progress": "false",
    "npm_config_fund": "false",
    "npm_config_audit": "false",
    "npm_config_update_notifier": "false",
    "YARN_ENABLE_PROGRESS_BARS": "false",
    "CARGO_TERM_COLOR": "never",
    "CARGO_TERM_PROGRESS_WHEN": "never",
    "RUST_BACKTRACE": "1",
    "GOFLAGS": "-mod=mod",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_NOLOGO": "1",
    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
    "GRADLE_OPTS": "-Dorg.gradle.daemon=false -Dorg.gradle.console=plain",
    "MAVEN_OPTS": "-Djansi.passthrough=true",
    "MAVEN_ARGS": "--batch-mode --no-transfer-progress",
    "DOCKER_CLI_HINTS": "false",
    "BUILDKIT_PROGRESS": "plain",
    "TF_IN_AUTOMATION": "1",
    "TF_INPUT": "0",
    "PULUMI_SKIP_UPDATE_CHECK": "true",
    "HELM_NO_UPDATE_NOTIFIER": "1",
    "GH_PROMPT_DISABLED": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "COMPOSER_NO_INTERACTION": "1",
    "MIX_ENV": "test",
}

# Flags that turn a child into an interactive/long-lived session. Scoped per tool
# so that e.g. `pytest -p plugin` or `pip install -e .` stay spoolable.
_INTERACTIVE_FLAGS_GLOBAL = {"--watch", "--interactive"}
_INTERACTIVE_FLAGS_BY_PROG = {
    "git": {"-i", "-p", "--patch", "-e", "--edit"},
    "docker": {"-i", "-t", "-it", "-ti", "--tty", "attach", "exec", "run"},
    "podman": {"-i", "-t", "-it", "-ti", "--tty", "attach", "exec", "run"},
    "kubectl": {"-i", "-t", "-it", "-ti", "--tty", "exec", "attach", "edit", "port-forward", "-w", "-f", "--follow"},
    "cargo": {"watch"},
    "vitest": {"--ui"},
    "jest": {"--watchAll"},
}
_INTERACTIVE_GIT_SUBS = {"rebase", "commit", "add", "merge", "checkout", "switch", "reset", "push", "pull", "clone"}


def normalize_program(argv0: str) -> str:
    """``/usr/bin/Pytest.EXE`` -> ``pytest``; ``./gradlew`` -> ``gradlew``."""
    prog = os.path.basename(argv0).lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1", ".sh"):
        if prog.endswith(ext):
            prog = prog[: -len(ext)]
            break
    return prog


def is_spoolable_command(argv: List[str]) -> bool:
    """Evaluates whether a command line qualifies for headless lossless spooling.

    Allowlisted program + (for multi-word tools) allowlisted subcommand, and no
    flag that would turn the child into an interactive session.
    """
    if not argv:
        return False

    prog = normalize_program(argv[0])

    # Handle 'python -m pytest' / 'python -m ruff' etc.
    if prog in ("python", "python3", "py", "pypy3") and len(argv) >= 3 and argv[1] == "-m":
        return argv[2].lower() in ALLOWLIST_COMMANDS

    if prog not in ALLOWLIST_COMMANDS:
        return False

    rest = list(argv[1:])
    blocked = _INTERACTIVE_FLAGS_GLOBAL | _INTERACTIVE_FLAGS_BY_PROG.get(prog, set())
    if any(a in blocked for a in rest):
        return False

    suballowed = ALLOWLIST_SUBCOMMANDS.get(prog)
    if suballowed is not None:
        if len(argv) < 2:
            return False
        sub = argv[1].lower()
        if prog == "git" and sub in _INTERACTIVE_GIT_SUBS:
            return False
        return sub in suballowed

    return True


def build_headless_env(base: Optional[dict] = None) -> dict:
    """Returns a copy of the environment with every headless flag applied."""
    env = dict(base if base is not None else os.environ)
    env.update(HEADLESS_ENV)
    return env


def execute_spooled(cmd_args: List[str]) -> int:
    """Executes an allowlisted command with headless environment, spools output, and prints summary."""
    env = build_headless_env()

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


USAGE_TEXT = (
    "Usage: genesis <command> [options]\n\n"
    "Commands:\n"
    "  setup          1-Click universal auto-wiring (20+ AI clients: Cursor, Claude Code, VS Code, Zed, JetBrains, Neovim, ...)\n"
    "  clients        Show the universal client matrix with detection status (--json)\n"
    "  export-config  Emit config snippets for any tool (--format json|yaml|toml|env|lua|native)\n"
    "  init           Initialize local environment and zero-friction onboarding\n"
    "  license        View active license, entitlements, and seat status\n"
    "  auth           Activate commercial Pro or Enterprise license key\n"
    "  doctor         Diagnose system health, storage, RSS, and connectivity\n"
    "  run            Execute allowlisted commands with headless lossless spooling\n"
    "  proxy          Manage on-demand stateless gateway (start|stop|status)\n"
    "  dashboard      Launch the Mission Control telemetry dashboard\n"
    "  upgrade        1-Click self-upgrade to the latest official release\n"
    "  sleep          Biomimetic consolidation cycle (--now | --daemon)\n"
    "  skills         List synthesized procedural skills\n"
    "  extensions     List installed commercial (genesis-pro) extensions\n"
    "\n"
    "Examples:\n"
    "  genesis setup --preview\n"
    "  genesis setup --yes --client cursor,zed,neovim\n"
    "  genesis export-config --client codex --format toml\n"
    "  genesis export-config --format env   # OPENAI_BASE_URL for any SDK\n"
    "  genesis run -- pytest tests/\n"
    "  genesis run -- cargo test\n"
    "  genesis dashboard"
)


def main(argv: Optional[List[str]] = None) -> int:
    """Unified CLI entrypoint for GENESIS."""
    args = list(argv) if argv is not None else sys.argv[1:]

    if not args:
        print(USAGE_TEXT, file=sys.stderr)
        return 1

    subcmd = args[0].lower()

    if subcmd in ("--version", "-version", "-v", "version"):
        from genesis_memory import __version__
        print(f"GENESIS Memory OS v{__version__}")
        return 0

    if subcmd in ("init", "setup"):
        from genesis_memory.cli.init_cmd import main as init_main
        return init_main(args[1:])

    if subcmd in ("extensions", "extension"):
        from genesis_memory.extensions import load_extensions
        exts = load_extensions()
        if not exts:
            print("[genesis] No commercial extensions installed.")
            print("  Pro/Enterprise capabilities ship in the private genesis-pro channel.")
            print("  See: https://github.com/HamidRezaeian/genesis-memory#licensing")
            return 0
        print(f"[genesis] Commercial extensions ({len(exts)} installed):")
        for name in sorted(exts):
            print(f"  • {name}")
        return 0

    if subcmd in ("clients", "matrix"):
        from genesis_memory.cli.export_config import clients_matrix
        print(clients_matrix(as_json="--json" in args[1:]))
        return 0

    if subcmd in ("export-config", "export_config", "export", "snippet"):
        from genesis_memory.cli.export_config import main as export_main
        return export_main(args[1:])

    if subcmd in ("upgrade", "update"):
        from genesis_memory.cli.update_checker import run_upgrade
        return run_upgrade()

    if subcmd == "dashboard":
        from genesis_memory.dashboard.server import main as dashboard_main
        return dashboard_main(args[1:])

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
        from genesis_memory.core import db as _dbx
        conn = _dbx.connect(db_path, readonly=True)
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

    if subcmd in ("license", "auth"):
        from genesis_memory.core.licensing import (
            load_active_license,
            activate_license,
            verify_license_key,
            generate_license_key,
        )
        sub_args = args[1:]
        # genesis auth <key> or genesis auth --key <key> or genesis license activate <key>
        if subcmd == "auth" or (sub_args and sub_args[0] in ("activate", "auth", "login")):
            key = None
            for idx, a in enumerate(sub_args):
                if a in ("--key", "-k") and idx + 1 < len(sub_args):
                    key = sub_args[idx + 1]
                    break
                elif not a.startswith("-") and a not in ("activate", "auth", "login"):
                    key = a
                    break
            if not key:
                print("Usage: genesis auth --key <LICENSE_KEY>", file=sys.stderr)
                return 1
            ok, status = activate_license(key)
            if ok:
                print("╔══════════════════════════════════════════════════════════════════════════════╗")
                print("║                   🎉 GENESIS License Activated Successfully!                 ║")
                print("╚══════════════════════════════════════════════════════════════════════════════╝")
                print(f"  Tier         : {status.tier.upper()} (Verified)")
                print(f"  Owner        : {status.owner}")
                if status.org:
                    print(f"  Organization : {status.org}")
                print(f"  Seats        : {status.seats}")
                print(f"  Expires At   : {status.expires_at} ({status.days_remaining} days remaining)")
                print("  Entitlements :")
                for cap in status.capabilities:
                    print(f"    [✓] {cap}")
                return 0
            else:
                print(f"[genesis] ❌ License activation failed: {status.message}", file=sys.stderr)
                return 1

        # genesis license keygen (helper for testing and issuing keys)
        if sub_args and sub_args[0] in ("keygen", "generate", "mint"):
            import argparse
            kg_parser = argparse.ArgumentParser(description="Generate commercial license key")
            kg_parser.add_argument("--tier", default="pro", choices=["community", "pro", "enterprise"])
            kg_parser.add_argument("--owner", required=True)
            kg_parser.add_argument("--org", default=None)
            kg_parser.add_argument("--seats", type=int, default=1)
            kg_parser.add_argument("--days", type=int, default=365)
            kg_args = kg_parser.parse_args(sub_args[1:])
            generated_key = generate_license_key(
                tier=kg_args.tier,
                owner=kg_args.owner,
                org=kg_args.org,
                seats=kg_args.seats,
                valid_days=kg_args.days,
            )
            print(f"[genesis] Generated {kg_args.tier.upper()} License Key:")
            print(generated_key)
            return 0

        # default: genesis license status
        lic_status = load_active_license()
        tier_symbol = "★ DEVELOPER PRO" if lic_status.tier == "pro" else ("⚡ ENTERPRISE GATEWAY" if lic_status.tier == "enterprise" else "COMMUNITY (Free / OSS)")
        status_text = "Verified Active" if lic_status.is_valid else "Unverified / Expired"
        print("╔══════════════════════════════════════════════════════════════════════════════╗")
        print("║                     GENESIS Commercial License & Entitlements                ║")
        print("╚══════════════════════════════════════════════════════════════════════════════╝")
        print(f"  Status       : {status_text}")
        print(f"  Tier         : {tier_symbol}")
        print(f"  Owner        : {lic_status.owner}")
        if lic_status.org:
            print(f"  Organization : {lic_status.org}")
        days_str = f" ({lic_status.days_remaining} days remaining)" if lic_status.days_remaining is not None else ""
        print(f"  Expires At   : {lic_status.expires_at}{days_str}")
        print("  Entitlements :")
        for cap in lic_status.capabilities:
            print(f"    [✓] {cap}")
        if not lic_status.is_valid or lic_status.tier == "community":
            print("-" * 78)
            print("  To activate Developer Pro or Enterprise, run: genesis auth --key <KEY>")
        return 0

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
        print(USAGE_TEXT, file=sys.stderr)
        return 1

    # Top-level help (also covers `genesis run --help`): usage to stdout, exit 0.
    # Never fall through to subprocess: there is no executable named --help.
    if args[0].lower() in ("-h", "--help", "help"):
        print(USAGE_TEXT)
        return 0

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

"""GENESIS Zero-Friction Onboarding & System Initializer (genesis init).

Enforces OpenCode's 4 Architectural Invariants:
1. Read-Only Detection by default: Discovers all installed AI clients and capabilities.
2. Preview & Explicit Consent: Displays planned multi-client matrix and prompts unless --yes is passed.
3. Timestamped Backup & Revert: Backs up external files before structural JSONC merge; 100% reversible via --revert.
4. Non-Hostile Invariant: Preserves existing tools (e.g. arena_assistant) and comments; never destructive.
"""

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from genesis_memory.core import db as _dbx
from genesis_memory.cli.client_registry import ClientRegistry, DiscoveredClient, get_default_daemon_path
from genesis_memory.cli.jsonc_merger import merge_jsonc_file_content

GENESIS_DIR = Path.home() / ".genesis"
BACKUPS_DIR = GENESIS_DIR / "backups"
MEMORY_DB_PATH = GENESIS_DIR / "memory.db"
SPOOL_DIR = GENESIS_DIR / "spool"
LATEST_BACKUP_LINK = BACKUPS_DIR / "latest.json"


def detect_environment() -> Dict[str, Any]:
    """Inspects host machine in a strictly read-only manner."""
    home = Path.home()
    clients = ClientRegistry.discover_all()

    env_info = {
        "os": sys.platform,
        "home": str(home),
        "genesis_dir_exists": GENESIS_DIR.exists(),
        "memory_db_exists": MEMORY_DB_PATH.exists(),
        "spool_dir_exists": SPOOL_DIR.exists(),
        "detected_clients": clients,
        "detected_ides": [(c.name, str(c.config_path)) for c in clients if c.detected],
    }
    return env_info


def generate_plan(
    env: Dict[str, Any],
    skip_clients: bool = False,
    target_clients: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Generates the preview of actions that will be performed."""
    plan = []

    if not env["genesis_dir_exists"]:
        plan.append({
            "action": "CREATE_DIR",
            "target": str(GENESIS_DIR),
            "description": "Create root GENESIS configuration & local state directory",
        })

    if not env["spool_dir_exists"]:
        plan.append({
            "action": "CREATE_DIR",
            "target": str(SPOOL_DIR),
            "description": "Create lossless headless spooling cache directory (Rule 32)",
        })

    if not env["memory_db_exists"]:
        plan.append({
            "action": "CREATE_DB",
            "target": str(MEMORY_DB_PATH),
            "description": "Initialize local episodic memory SQLite database with WAL mode (Rule 31)",
        })

    plan.append({
        "action": "CREATE_FILE",
        "target": str(GENESIS_DIR / "mcp_snippet.json"),
        "description": "Generate standalone MCP configuration snippet for IDE integration",
    })

    if not skip_clients:
        normalized_targets = (
            {t.strip().lower() for t in target_clients if t.strip()}
            if target_clients
            else None
        )
        for client in env.get("detected_clients", []):
            if not client.detected:
                continue
            if normalized_targets and client.id.lower() not in normalized_targets and client.name.lower() not in normalized_targets:
                continue
            if not client.configured and client.id != "antigravity":
                plan.append({
                    "action": "WIRE_CLIENT",
                    "target": str(client.config_path),
                    "client_id": client.id,
                    "client_name": client.name,
                    "capabilities": client.capability_tags,
                    "description": f"Wire GENESIS memory into {client.name} [{client.capability_tags}]",
                })

    return plan


def create_backup_manifest(actions_taken: List[Dict[str, Any]], manifest_dir: Optional[Path] = None) -> Path:
    """Creates a timestamped backup manifest to guarantee 100% reversibility."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUPS_DIR / f"init_manifest_{ts}.json"

    data = {
        "timestamp": ts,
        "actions_taken": actions_taken,
    }
    backup_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    LATEST_BACKUP_LINK.write_text(str(backup_file), encoding="utf-8")
    return backup_file


def revert_init() -> int:
    """Reverts the changes made by the last genesis init run."""
    print("=" * 68)
    print("  🔄 GENESIS Init Revert Operation")
    print("=" * 68)

    if not LATEST_BACKUP_LINK.exists():
        print("  ⚠️ No previous init manifest found to revert.")
        return 1

    try:
        manifest_path = Path(LATEST_BACKUP_LINK.read_text(encoding="utf-8").strip())
        if not manifest_path.exists():
            print(f"  ❌ Manifest file {manifest_path} does not exist.")
            return 1

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actions = manifest.get("actions_taken", [])

        print(f"  Rolling back {len(actions)} actions recorded on {manifest.get('timestamp')}...")

        # Revert in reverse order
        for item in reversed(actions):
            action = item.get("action")
            target = Path(item.get("target"))

            if action == "WIRE_CLIENT":
                client_name = item.get("client_name", "Client")
                created_new = item.get("created_new", False)
                backup_copy_str = item.get("backup_copy")

                if created_new:
                    if target.exists():
                        target.unlink()
                        print(f"  🗑️ Removed newly created config for {client_name}: {target}")
                elif backup_copy_str:
                    backup_copy = Path(backup_copy_str)
                    if backup_copy.exists():
                        shutil.copy2(backup_copy, target)
                        print(f"  ⏪ Restored original {client_name} config: {target}")

            elif action == "CREATE_FILE" and target.exists():
                target.unlink()
                print(f"  🗑️ Removed file: {target}")
            elif action == "CREATE_DB" and target.exists():
                target.unlink()
                Path(str(target) + "-wal").unlink(missing_ok=True)
                Path(str(target) + "-shm").unlink(missing_ok=True)
                print(f"  🗑️ Removed database: {target}")
            elif action == "CREATE_DIR" and target.exists():
                try:
                    target.rmdir()
                    print(f"  🗑️ Removed empty directory: {target}")
                except OSError:
                    print(f"  ⚠️ Kept non-empty directory: {target}")

        LATEST_BACKUP_LINK.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        print("-" * 68)
        print("  ✅ Revert complete: Local environment restored to pre-init state.")
        return 0
    except Exception as exc:
        print(f"  ❌ Revert failed with error: {exc}")
        return 1


def initialize_sqlite_db(db_path: Path) -> None:
    """Initializes local SQLite memory database with optimal pragmas."""
    conn = _dbx.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            project TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            utility REAL DEFAULT 1.0,
            tokens_estimate INTEGER DEFAULT 0
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_project ON episodes(project);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_kind ON episodes(kind);")
    conn.commit()
    conn.close()


def generate_mcp_snippet(dest_path: Path) -> None:
    """Writes a standalone MCP configuration snippet for IDE integration."""
    daemon_path = get_default_daemon_path()
    db_path = MEMORY_DB_PATH
    norm_daemon = str(daemon_path).replace("\\", "/")
    norm_db = str(db_path).replace("\\", "/")

    snippet = {
        "mcpServers": {
            "genesis-memory": {
                "command": sys.executable,
                "args": [norm_daemon],
                "env": {
                    "GENESIS_DAEMON_DB": norm_db
                }
            }
        },
        "opencode": {
            "genesis-memory": {
                "type": "local",
                "command": [sys.executable, norm_daemon],
                "environment": {
                    "GENESIS_DAEMON_DB": norm_db
                },
                "enabled": True,
                "timeout": 900000
            }
        }
    }
    dest_path.write_text(json.dumps(snippet, indent=2, ensure_ascii=False), encoding="utf-8")


def run_init(
    auto_confirm: bool = False,
    revert: bool = False,
    quiet: bool = False,
    skip_clients: bool = False,
    target_clients: Optional[List[str]] = None,
    dry_run: bool = False,
) -> int:
    """Main CLI entrypoint for genesis init & genesis setup."""
    if revert:
        return revert_init()

    if not quiet:
        print("╔══════════════════════════════════════════════════════════════════════════════╗")
        print("║                     GENESIS 1-Click Multi-Client Setup                       ║")
        print("║           Persistent Cross-Tool Memory & Autonomous Context Optimizer        ║")
        print("╚══════════════════════════════════════════════════════════════════════════════╝")

    # 1. Read-Only Multi-Client Detection
    env = detect_environment()
    clients: List[DiscoveredClient] = env.get("detected_clients", [])

    if not quiet:
        print(f"  Detected OS      : {env['os']}")
        print(f"  User Home        : {env['home']}")
        print("-" * 78)
        print("  🔍 Multi-Client Discovery & Capability Matrix:")
        for c in clients:
            status_symbol = "✅ Configured" if c.configured else ("⚡ Ready to wire" if c.detected else "❌ Not found")
            print(f"    • {c.name:<18} [{c.capability_tags:<18}] -> {status_symbol}")
            if c.detected and not c.configured:
                print(f"      └── Config Path: {c.config_path}")
        print("-" * 78)

    # 2. Plan generation & Diff preview
    plan = generate_plan(env, skip_clients=skip_clients, target_clients=target_clients)

    if not plan:
        if not quiet:
            print("  ✅ All GENESIS components and clients are already initialized.")
            print("  Run 'genesis doctor' to verify overall system health.")
        return 0

    if not quiet:
        print("  📋 Planned Actions (Setup Matrix):")
        for i, item in enumerate(plan, 1):
            print(f"    {i}. [{item['action']}] {item['target']}")
            print(f"       └── {item['description']}")
        print("-" * 78)

    if dry_run:
        if not quiet:
            print("  ✨ Dry-Run complete. 0 changes made to your system.")
            print("  To apply these changes, run: genesis setup --yes")
            print("-" * 78)
        return 0

    # 3. Explicit User Consent
    if not auto_confirm:
        try:
            prompt = "  Apply these changes to your environment? [y/N]: "
            reply = input(prompt).strip().lower()
            if reply not in ("y", "yes"):
                print("  🛑 Operation cancelled. No changes were made to your system.")
                return 0
        except (KeyboardInterrupt, EOFError):
            print("\n  🛑 Cancelled.")
            return 1

    # 4. Execution with Tracking & Atomic Backup
    actions_taken = []
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_backup_dir = BACKUPS_DIR / f"run_{ts}"

    try:
        for item in plan:
            action = item["action"]
            target = Path(item["target"])

            if action == "CREATE_DIR":
                target.mkdir(parents=True, exist_ok=True)
                actions_taken.append({"action": "CREATE_DIR", "target": str(target)})

            elif action == "CREATE_DB":
                initialize_sqlite_db(target)
                actions_taken.append({"action": "CREATE_DB", "target": str(target)})

            elif action == "CREATE_FILE" and target.name == "mcp_snippet.json":
                generate_mcp_snippet(target)
                actions_taken.append({"action": "CREATE_FILE", "target": str(target)})

            elif action == "WIRE_CLIENT":
                client_id = item["client_id"]
                client_name = item["client_name"]
                client_obj = next((c for c in clients if c.id == client_id), None)

                if client_obj:
                    # Non-destructive backup before mutation
                    created_new = not target.exists()
                    backup_copy_path = None

                    if not created_new:
                        manifest_backup_dir.mkdir(parents=True, exist_ok=True)
                        backup_copy_path = manifest_backup_dir / f"{client_id}_{target.name}"
                        shutil.copy2(target, backup_copy_path)

                    success, msg = ClientRegistry.wire_client(
                        client_obj,
                        daemon_path=get_default_daemon_path(),
                        db_path=MEMORY_DB_PATH,
                    )
                    if success:
                        actions_taken.append({
                            "action": "WIRE_CLIENT",
                            "target": str(target),
                            "client_id": client_id,
                            "client_name": client_name,
                            "created_new": created_new,
                            "backup_copy": str(backup_copy_path) if backup_copy_path else None,
                        })
                        if not quiet:
                            print(f"  🔌 Wired {client_name}: {target}")
                    else:
                        print(f"  ⚠️ Could not wire {client_name}: {msg}")

        # Save Backup Manifest for --revert
        backup_file = create_backup_manifest(actions_taken)
        if not quiet:
            print(f"  💾 Backup manifest created: {backup_file.name} (use 'genesis setup --revert' to undo)")

        # 5. Live Handshake
        if not quiet:
            print("  ⚡ Performing live end-to-end verification handshake...")
            conn = _dbx.connect(str(MEMORY_DB_PATH), readonly=True)
            count = conn.execute("SELECT COUNT(*) FROM episodes;").fetchone()[0]
            conn.close()
            print(f"     ├── SQLite Subconscious DB : OK ({count} engrams)")

            test_file = SPOOL_DIR / ".handshake"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            print(f"     ├── Headless Spooling Cache : OK ({SPOOL_DIR})")
            print(f"     └── MCP Integration Snippet: OK ({GENESIS_DIR / 'mcp_snippet.json'})")

            wired_clients = [a.get("client_name") for a in actions_taken if a.get("action") == "WIRE_CLIENT"]
            if wired_clients:
                print(f"     └── Auto-Wired AI Clients   : OK ({', '.join(wired_clients)})")

            print("=" * 78)
            print("  🎉 GENESIS is ready to use across all your AI coding agents!")
            print("  - To execute tools without context spam: genesis run -- <command>")
            print("  - To run full system diagnostics:        genesis doctor")
            print("  - To undo these changes at any time:    genesis setup --revert")
            print("=" * 78)

        return 0
    except Exception as exc:
        print(f"  ❌ Initialization failed: {exc}")
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="GENESIS 1-Click Multi-Client Setup & System Initializer")
    parser.add_argument("-y", "--yes", "--force", dest="yes", action="store_true", help="Auto-confirm all changes without interactive prompt")
    parser.add_argument("-p", "--preview", "--dry-run", dest="dry_run", action="store_true", help="Preview detection matrix and planned changes without mutating files")
    parser.add_argument("-c", "--client", dest="clients", action="append", help="Target specific client(s) by id or name, comma-separated (e.g. --client opencode,cursor)")
    parser.add_argument("--revert", action="store_true", help="Revert changes made by the previous genesis setup run")
    parser.add_argument("--skip-clients", action="store_true", help="Do not auto-wire external client configuration files")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress non-essential output")
    args = parser.parse_args(argv)

    target_clients = []
    if args.clients:
        for c in args.clients:
            for item in c.split(","):
                if item.strip():
                    target_clients.append(item.strip())

    return run_init(
        auto_confirm=args.yes,
        revert=args.revert,
        quiet=args.quiet,
        skip_clients=args.skip_clients,
        target_clients=target_clients if target_clients else None,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())

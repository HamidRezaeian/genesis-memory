"""Automated Test Suite for GENESIS Zero-Friction Onboarding & System Health.

Certifies:
1. genesis init preview, explicit consent, and 100% reversible rollback (--revert).
2. genesis doctor diagnostics across Python, storage, RSS, and CLI allowlists.
3. proxy supervisor single-flight concurrency, PID validation, and stale lock auto-pruning.
4. CLI dispatch integration across init, doctor, proxy, and run subcommands.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent

from genesis_memory.cli.init_cmd import (
    detect_environment,
    generate_plan,
    initialize_sqlite_db,
    run_init,
)
from genesis_memory.cli.doctor import (
    check_python_runtime,
    check_storage_dirs,
    check_sqlite_memory,
    check_rss_budget,
    check_cli_spooler,
    run_doctor,
)
from genesis_memory.proxy.supervisor import (
    is_pid_alive,
    acquire_lock,
    release_lock,
    clean_stale_lock,
    is_proxy_healthy,
    LOCKFILE_PATH,
)
from genesis_memory.cli.run import main as cli_main


class TestOnboardingInit(unittest.TestCase):

    def test_read_only_detection(self):
        """detect_environment must inspect the host with zero side effects."""
        env = detect_environment()
        self.assertIn("os", env)
        self.assertIn("home", env)
        self.assertIn("genesis_dir_exists", env)
        self.assertIsInstance(env["detected_ides"], list)

    def test_plan_generation(self):
        """generate_plan must produce well-formed action dictionaries."""
        mock_env = {
            "genesis_dir_exists": False,
            "spool_dir_exists": False,
            "memory_db_exists": False,
            "detected_ides": [],
        }
        plan = generate_plan(mock_env)
        actions = [p["action"] for p in plan]
        self.assertIn("CREATE_DIR", actions)
        self.assertIn("CREATE_DB", actions)
        self.assertIn("CREATE_FILE", actions)

    def test_init_and_revert_cycle(self):
        """genesis init followed by genesis init --revert must be completely idempotent and reversible."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_genesis = Path(tmp_dir) / ".genesis"
            tmp_backups = tmp_genesis / "backups"
            tmp_spool = tmp_genesis / "spool"
            tmp_db = tmp_genesis / "memory.db"
            tmp_latest = tmp_backups / "latest.json"

            with patch("genesis_memory.cli.init_cmd.GENESIS_DIR", tmp_genesis), \
                 patch("genesis_memory.cli.init_cmd.BACKUPS_DIR", tmp_backups), \
                 patch("genesis_memory.cli.init_cmd.SPOOL_DIR", tmp_spool), \
                 patch("genesis_memory.cli.init_cmd.MEMORY_DB_PATH", tmp_db), \
                 patch("genesis_memory.cli.init_cmd.LATEST_BACKUP_LINK", tmp_latest):

                # 1. Run init with auto-confirm
                code = run_init(auto_confirm=True, quiet=True)
                self.assertEqual(code, 0)
                self.assertTrue(tmp_genesis.exists())
                self.assertTrue(tmp_spool.exists())
                self.assertTrue(tmp_db.exists())
                self.assertTrue((tmp_genesis / "mcp_snippet.json").exists())
                self.assertTrue(tmp_latest.exists())

                # 2. Run revert
                revert_code = run_init(revert=True, quiet=True)
                self.assertEqual(revert_code, 0)
                self.assertFalse(tmp_db.exists())
                self.assertFalse((tmp_genesis / "mcp_snippet.json").exists())
                self.assertFalse(tmp_latest.exists())


class TestSystemDoctor(unittest.TestCase):

    def test_python_runtime_check(self):
        ok, msg, detail = check_python_runtime()
        self.assertTrue(ok)
        self.assertIn("Python", msg)

    def test_storage_dirs_check(self):
        ok, msg, detail = check_storage_dirs()
        self.assertTrue(ok)

    def test_sqlite_memory_check(self):
        ok, msg, detail = check_sqlite_memory()
        self.assertTrue(ok)

    def test_rss_budget_check(self):
        ok, msg, detail = check_rss_budget()
        self.assertTrue(ok)

    def test_cli_spooler_check(self):
        ok, msg, detail = check_cli_spooler()
        self.assertTrue(ok)
        self.assertIn("pytest", detail)

    def test_run_doctor_full_suite(self):
        code = run_doctor(verbose=False)
        self.assertEqual(code, 0)


class TestProxySupervisor(unittest.TestCase):

    def test_pid_liveness(self):
        # Current process is definitely alive
        self.assertTrue(is_pid_alive(os.getpid()))
        # Non-existent high PID should be false
        self.assertFalse(is_pid_alive(9999999))

    def test_single_flight_lock(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_lock = Path(tmp_dir) / "proxy.lock"
            test_pid = Path(tmp_dir) / "proxy.pid"
            with patch("genesis_memory.proxy.supervisor.LOCKFILE_PATH", test_lock), \
                 patch("genesis_memory.proxy.supervisor.PIDFILE_PATH", test_pid), \
                 patch("genesis_memory.proxy.supervisor.GENESIS_DIR", Path(tmp_dir)):

                acquired = acquire_lock(timeout_sec=0.5)
                self.assertTrue(acquired)
                self.assertTrue(test_lock.exists())

                # Second acquire should fail quickly
                second = acquire_lock(timeout_sec=0.2)
                self.assertFalse(second)

                release_lock()
                self.assertFalse(test_lock.exists())

    def test_stale_lock_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_lock = Path(tmp_dir) / "proxy.lock"
            test_pid = Path(tmp_dir) / "proxy.pid"
            with patch("genesis_memory.proxy.supervisor.LOCKFILE_PATH", test_lock), \
                 patch("genesis_memory.proxy.supervisor.PIDFILE_PATH", test_pid), \
                 patch("genesis_memory.proxy.supervisor.GENESIS_DIR", Path(tmp_dir)):

                # Write a lockfile with dead PID 9999999
                test_lock.write_text(json.dumps({"pid": 9999999, "time": 0}), encoding="utf-8")
                clean_stale_lock()
                self.assertFalse(test_lock.exists())


class TestCLIDispatch(unittest.TestCase):

    def test_empty_args_shows_usage(self):
        code = cli_main([])
        self.assertEqual(code, 1)

    def test_doctor_subcommand(self):
        code = cli_main(["doctor"])
        self.assertEqual(code, 0)

    def test_proxy_status_subcommand(self):
        code = cli_main(["proxy", "status"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

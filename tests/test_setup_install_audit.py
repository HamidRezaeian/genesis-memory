"""Install-audit regression suite: fresh-machine install must yield a usable system.

Guards the v0.14.0 fresh-install failures:
1. ``genesis setup`` created a partial ``episodes`` table (missing ``accesses`` /
   ``updated``) so every ``remember``/``recall`` failed while ``doctor`` stayed green.
2. ``doctor`` only counted tables instead of validating the schema + recall path.
3. The proxy launcher ignored ``GENESIS_DAEMON_DB`` (split-brain databases).
4. Proxy setup was a separate undiscoverable step instead of a setup continuation.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from genesis_memory.cli import init_cmd
from genesis_memory.cli.doctor import check_sqlite_memory
from genesis_memory.daemon.server import Store


def make_legacy_db(path: Path, with_row: bool = True) -> None:
    """Recreate the exact broken DB that ``genesis setup`` <= v0.14.0 produced."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            project TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            utility REAL DEFAULT 1.0,
            tokens_estimate INTEGER DEFAULT 0
        );
    """)
    if with_row:
        conn.execute(
            "INSERT INTO episodes(ts, project, kind, text, utility) VALUES(1.0, 'p', 'fact', 'legacy row', 1.0)")
    conn.commit()
    conn.close()


class TestFreshDbIsUsable(unittest.TestCase):

    def test_initialize_sqlite_db_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "memory.db"
            init_cmd.initialize_sqlite_db(db)
            store = Store(str(db))
            try:
                created = store.remember("WAL timeout must be 5000ms", kind="fact", project="audit")
                rec = store.recall("WAL timeout", project="audit")
                self.assertEqual(len(rec["results"]), 1)
                store.set_thread(topic="t", summary="s")
                self.assertIsNotNone(store.get_thread())
                store.set_dialogue(session_id="s1", client="c", user_prompt="u", assistant_summary="a")
                self.assertIsNotNone(store.get_latest_dialogue(session_id="s1", fallback=False))
                store.forget(created["id"])
                self.assertEqual(store.status()["episodes"], 0)
            finally:
                store.db.close()

    def test_migrate_heals_legacy_db_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "memory.db"
            make_legacy_db(db, with_row=True)
            store = Store(str(db))  # migrate() runs here
            try:
                cols = {r[1] for r in store.db.execute("PRAGMA table_info(episodes)").fetchall()}
                self.assertTrue({"accesses", "updated"}.issubset(cols))
                # legacy row preserved and recallable
                rec = store.recall("legacy row")
                self.assertEqual(len(rec["results"]), 1)
                # new writes work
                store.remember("fresh write after heal", kind="fact", project="audit")
            finally:
                store.db.close()

    def test_handshake_ping_passes_on_good_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "memory.db"
            init_cmd.initialize_sqlite_db(db)
            ok, detail = init_cmd._handshake_ping(db)
            self.assertTrue(ok, detail)
            # ping leaves no residue
            conn = sqlite3.connect(str(db))
            leftover = conn.execute(
                "SELECT COUNT(*) FROM episodes WHERE project = 'genesis_setup_handshake'").fetchone()[0]
            conn.close()
            self.assertEqual(leftover, 0)


class TestRepairPlan(unittest.TestCase):

    def _patched_paths(self, tmp: str):
        tmp_genesis = Path(tmp) / ".genesis"
        return (
            patch.object(init_cmd, "GENESIS_DIR", tmp_genesis),
            patch.object(init_cmd, "BACKUPS_DIR", tmp_genesis / "backups"),
            patch.object(init_cmd, "SPOOL_DIR", tmp_genesis / "spool"),
            patch.object(init_cmd, "MEMORY_DB_PATH", tmp_genesis / "memory.db"),
            patch.object(init_cmd, "LATEST_BACKUP_LINK", tmp_genesis / "backups" / "latest.json"),
        )

    def test_generate_plan_emits_repair_for_legacy_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._patched_paths(tmp)
            for p in patches:
                p.start()
            try:
                db = init_cmd.MEMORY_DB_PATH
                db.parent.mkdir(parents=True, exist_ok=True)
                make_legacy_db(db)
                env = {"genesis_dir_exists": True, "spool_dir_exists": True,
                       "memory_db_exists": True, "detected_clients": []}
                plan = init_cmd.generate_plan(env, skip_clients=True)
                self.assertIn("REPAIR_DB", [a["action"] for a in plan])
                self.assertNotIn("CREATE_DB", [a["action"] for a in plan])
            finally:
                for p in patches:
                    p.stop()

    def test_run_init_heals_legacy_db_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._patched_paths(tmp)
            for p in patches:
                p.start()
            try:
                db = init_cmd.MEMORY_DB_PATH
                (db.parent / "spool").mkdir(parents=True, exist_ok=True)
                make_legacy_db(db)
                code = init_cmd.run_init(auto_confirm=True, quiet=True, skip_clients=True)
                self.assertEqual(code, 0)
                store = Store(str(db))
                try:
                    store.remember("post-repair write", kind="fact", project="audit")
                    self.assertEqual(len(store.recall("post-repair").get("results")), 1)
                finally:
                    store.db.close()
            finally:
                for p in patches:
                    p.stop()


class TestDoctorValidatesSchema(unittest.TestCase):

    def test_doctor_fails_on_legacy_db_and_passes_after_repair(self):
        import genesis_memory.cli.doctor as doctor_mod
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "memory.db"
            make_legacy_db(db)
            with patch.object(doctor_mod, "MEMORY_DB_PATH", db):
                ok, status, _detail = check_sqlite_memory()
                self.assertFalse(ok)
                self.assertIn("accesses", status)
            init_cmd.initialize_sqlite_db(db)
            with patch.object(doctor_mod, "MEMORY_DB_PATH", db):
                ok, status, _detail = check_sqlite_memory()
                self.assertTrue(ok, status)


class TestProxyInstallContinuation(unittest.TestCase):

    def test_launcher_db_prefers_daemon_env(self):
        from genesis_memory.proxy.launcher import build_parser
        old_daemon = os.environ.get("GENESIS_DAEMON_DB")
        old_legacy = os.environ.get("GENESIS_MEMORY_DB")
        try:
            os.environ["GENESIS_DAEMON_DB"] = "C:/db/daemon.db"
            os.environ["GENESIS_MEMORY_DB"] = "C:/db/legacy.db"
            self.assertEqual(build_parser().parse_args([]).db, "C:/db/daemon.db")
            del os.environ["GENESIS_DAEMON_DB"]
            self.assertEqual(build_parser().parse_args([]).db, "C:/db/legacy.db")
        finally:
            if old_daemon is None:
                os.environ.pop("GENESIS_DAEMON_DB", None)
            else:
                os.environ["GENESIS_DAEMON_DB"] = old_daemon
            if old_legacy is None:
                os.environ.pop("GENESIS_MEMORY_DB", None)
            else:
                os.environ["GENESIS_MEMORY_DB"] = old_legacy

    def test_setup_accepts_proxy_flags(self):
        with self.assertRaises(SystemExit) as ctx:
            init_cmd.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        # all new flags parse without error (revert path avoids touching disk)
        with patch.object(init_cmd, "run_init", return_value=0) as mock_run:
            code = init_cmd.main(["--yes", "--proxy", "--proxy-upstream-url", "https://x",
                                  "--proxy-api-key", "k", "--proxy-model", "m", "--no-proxy"])
            self.assertEqual(code, 0)
            _, kwargs = mock_run.call_args
            self.assertTrue(kwargs["run_proxy"])
            self.assertTrue(kwargs["no_proxy"])
            self.assertEqual(kwargs["proxy_upstream_url"], "https://x")
            self.assertEqual(kwargs["proxy_api_key"], "k")
            self.assertEqual(kwargs["proxy_model"], "m")

    def test_setup_proxy_flag_calls_proxy_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            patches = [
                patch.object(init_cmd, "GENESIS_DIR", Path(tmp) / ".genesis"),
                patch.object(init_cmd, "BACKUPS_DIR", Path(tmp) / ".genesis" / "backups"),
                patch.object(init_cmd, "SPOOL_DIR", Path(tmp) / ".genesis" / "spool"),
                patch.object(init_cmd, "MEMORY_DB_PATH", Path(tmp) / ".genesis" / "memory.db"),
                patch.object(init_cmd, "LATEST_BACKUP_LINK",
                             Path(tmp) / ".genesis" / "backups" / "latest.json"),
            ]
            for p in patches:
                p.start()
            try:
                with patch("genesis_memory.cli.proxy_setup.run_proxy_setup",
                           return_value=0) as mock_setup:
                    code = init_cmd.main(["--yes", "--quiet", "--skip-clients", "--proxy",
                                          "--proxy-upstream-url", "https://openrouter.ai/api/v1",
                                          "--proxy-api-key", "sk-or-v1-test"])
                    self.assertEqual(code, 0)
                    mock_setup.assert_called_once()
                    argv = mock_setup.call_args[0][0]
                    self.assertIn("https://openrouter.ai/api/v1", argv)
                    self.assertIn("sk-or-v1-test", argv)
                    self.assertIn("--yes", argv)
            finally:
                for p in patches:
                    p.stop()

    def test_setup_no_proxy_never_calls_proxy_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            patches = [
                patch.object(init_cmd, "GENESIS_DIR", Path(tmp) / ".genesis"),
                patch.object(init_cmd, "BACKUPS_DIR", Path(tmp) / ".genesis" / "backups"),
                patch.object(init_cmd, "SPOOL_DIR", Path(tmp) / ".genesis" / "spool"),
                patch.object(init_cmd, "MEMORY_DB_PATH", Path(tmp) / ".genesis" / "memory.db"),
                patch.object(init_cmd, "LATEST_BACKUP_LINK",
                             Path(tmp) / ".genesis" / "backups" / "latest.json"),
            ]
            for p in patches:
                p.start()
            try:
                with patch("genesis_memory.cli.proxy_setup.run_proxy_setup") as mock_setup:
                    code = init_cmd.main(["--yes", "--quiet", "--skip-clients", "--no-proxy"])
                    self.assertEqual(code, 0)
                    mock_setup.assert_not_called()
            finally:
                for p in patches:
                    p.stop()


if __name__ == "__main__":
    unittest.main()

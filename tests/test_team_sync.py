"""Tests for GENESIS Team Memory Sync Engine.

Covers vector clocks, manifest export/import, conflict detection,
resolution strategies, audit logging, and entitlement gating.
"""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from genesis_memory.core.team_sync import (
    TeamSyncEngine,
    MemoryDiff,
    SyncManifest,
    SyncConflict,
)
from genesis_memory.core.licensing import (
    LicenseStatus,
    TIER_CAPABILITIES,
)


def _create_test_db(db_path: str, episodes: list = None):
    """Create a minimal test database with the episodes table."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            project TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            utility REAL DEFAULT 1.0,
            tokens_estimate INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active'
        );
    """)
    if episodes:
        for ep in episodes:
            conn.execute(
                "INSERT INTO episodes (ts, project, kind, text, utility, status) VALUES (?,?,?,?,?,?)",
                ep,
            )
    conn.commit()
    conn.close()


def _mock_enterprise_license():
    """Returns a mock that makes load_active_license return enterprise."""
    return patch(
        "genesis_memory.core.team_sync.load_active_license",
        return_value=LicenseStatus(
            is_valid=True,
            tier="enterprise",
            owner="admin@corp.com",
            capabilities=TIER_CAPABILITIES["enterprise"],
        ),
    )


def _mock_community_license():
    """Returns a mock that makes load_active_license return community."""
    return patch(
        "genesis_memory.core.team_sync.load_active_license",
        return_value=LicenseStatus(
            is_valid=True,
            tier="community",
            owner="free-user",
            capabilities=TIER_CAPABILITIES["community"],
        ),
    )


class TestTeamSyncEntitlement(unittest.TestCase):
    """Verify that Team Sync requires Enterprise license."""

    def test_export_blocked_on_community(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            with _mock_community_license():
                engine = TeamSyncEngine(db, author="user1")
                with self.assertRaises(PermissionError) as ctx:
                    engine.export_diffs()
                self.assertIn("Enterprise", str(ctx.exception))

    def test_apply_blocked_on_community(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            manifest = SyncManifest(
                manifest_id="test-1", author="user2",
                namespace="default", created_at="2026-01-01",
                diffs=[], base_clock={}, head_clock={},
            )
            with _mock_community_license():
                engine = TeamSyncEngine(db, author="user1")
                with self.assertRaises(PermissionError):
                    engine.apply_manifest(manifest)

    def test_export_allowed_on_enterprise(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            with _mock_enterprise_license():
                engine = TeamSyncEngine(db, author="admin")
                manifest = engine.export_diffs()
                self.assertIsInstance(manifest, SyncManifest)
                self.assertEqual(manifest.author, "admin")


class TestVectorClock(unittest.TestCase):
    """Vector clock operations."""

    def test_initial_clock_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            engine = TeamSyncEngine(db, author="user1")
            clock = engine._get_vector_clock()
            self.assertEqual(clock, {})

    def test_increment_clock_creates_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            engine = TeamSyncEngine(db, author="user1")
            clock = engine._increment_clock()
            self.assertEqual(clock["user1"], 1)

    def test_increment_clock_monotonic(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            engine = TeamSyncEngine(db, author="user1")
            engine._increment_clock()
            engine._increment_clock()
            clock = engine._increment_clock()
            self.assertEqual(clock["user1"], 3)

    def test_clock_status_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            engine = TeamSyncEngine(db, author="dev1")
            engine._increment_clock()
            status = engine.get_vector_clock_status()
            self.assertEqual(status["author"], "dev1")
            self.assertEqual(status["namespace"], "default")
            self.assertIn("dev1", status["clock"])


class TestExportImportFlow(unittest.TestCase):
    """End-to-end export/import workflow."""

    def test_export_empty_db_returns_empty_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            with _mock_enterprise_license():
                engine = TeamSyncEngine(db, author="user1")
                manifest = engine.export_diffs()
                self.assertEqual(len(manifest.diffs), 0)

    def test_export_captures_episodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            now = time.time()
            _create_test_db(db, episodes=[
                (now - 10, "myproject", "decision", "Use SQLite for storage", 2.0, "active"),
                (now - 5, "myproject", "fact", "Python 3.11+ required", 1.5, "active"),
            ])
            with _mock_enterprise_license():
                engine = TeamSyncEngine(db, author="dev1")
                manifest = engine.export_diffs(since_ts=0.0)
                self.assertEqual(len(manifest.diffs), 2)
                self.assertEqual(manifest.diffs[0].kind, "decision")
                self.assertEqual(manifest.diffs[1].kind, "fact")

    def test_manifest_serialization_roundtrip(self):
        diff = MemoryDiff(
            engram_id=1, project="test", kind="decision",
            content="Test content", utility=2.0, status="active",
            timestamp=time.time(), author="user1",
            content_hash="abc123", vector_clock={"user1": 1},
        )
        original = SyncManifest(
            manifest_id="test-1", author="user1",
            namespace="default", created_at="2026-01-01T00:00:00Z",
            diffs=[diff], base_clock={}, head_clock={"user1": 1},
        )
        serialized = json.dumps(original.to_dict())
        parsed = json.loads(serialized)
        restored = SyncManifest.from_dict(parsed)
        self.assertEqual(restored.manifest_id, "test-1")
        self.assertEqual(len(restored.diffs), 1)
        self.assertEqual(restored.diffs[0].content, "Test content")

    def test_import_applies_new_episodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            diff = MemoryDiff(
                engram_id=999, project="shared", kind="decision",
                content="Shared architectural decision", utility=2.0,
                status="active", timestamp=time.time(),
                author="remote_user", content_hash="aaa",
                vector_clock={"remote_user": 1},
            )
            manifest = SyncManifest(
                manifest_id="import-1", author="remote_user",
                namespace="default", created_at="2026-01-01T00:00:00Z",
                diffs=[diff], base_clock={},
                head_clock={"remote_user": 1},
            )
            with _mock_enterprise_license():
                engine = TeamSyncEngine(db, author="local_user")
                result = engine.apply_manifest(manifest)
                self.assertEqual(result["applied"], 1)
                self.assertEqual(result["conflicts"], 0)
                # Verify in database
                conn = sqlite3.connect(db)
                row = conn.execute(
                    "SELECT text FROM episodes WHERE kind='decision'"
                ).fetchone()
                conn.close()
                self.assertIsNotNone(row)
                self.assertIn("Shared architectural", row[0])


class TestConflictDetection(unittest.TestCase):
    """Merge conflict detection and resolution."""

    def test_no_conflict_for_new_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            diff = MemoryDiff(
                engram_id=999, project="p", kind="fact",
                content="New fact", utility=1.0, status="active",
                timestamp=time.time(), author="remote",
                content_hash="xxx",
            )
            manifest = SyncManifest(
                manifest_id="m1", author="remote",
                namespace="default", created_at="now",
                diffs=[diff], base_clock={}, head_clock={},
            )
            engine = TeamSyncEngine(db, author="local")
            conflicts = engine.detect_conflicts(manifest)
            self.assertEqual(len(conflicts), 0)

    def test_conflict_detected_for_diverged_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db, episodes=[
                (time.time(), "proj", "decision", "Local version of rule", 2.0, "active"),
            ])
            # Get the ID of the inserted row
            conn = sqlite3.connect(db)
            row_id = conn.execute("SELECT id FROM episodes LIMIT 1").fetchone()[0]
            conn.close()

            diff = MemoryDiff(
                engram_id=row_id, project="proj", kind="decision",
                content="Remote version of rule", utility=2.0,
                status="active", timestamp=time.time(),
                author="remote_user",
                content_hash=TeamSyncEngine(db, "x")._content_hash("Remote version of rule"),
            )
            manifest = SyncManifest(
                manifest_id="m2", author="remote_user",
                namespace="default", created_at="now",
                diffs=[diff], base_clock={}, head_clock={},
            )
            engine = TeamSyncEngine(db, author="local_user")
            conflicts = engine.detect_conflicts(manifest)
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0].engram_id, row_id)
            self.assertIn("Local version", conflicts[0].local_content)
            self.assertIn("Remote version", conflicts[0].remote_content)

    def test_remote_wins_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db, episodes=[
                (time.time(), "proj", "fact", "Old local fact", 1.0, "active"),
            ])
            conn = sqlite3.connect(db)
            row_id = conn.execute("SELECT id FROM episodes LIMIT 1").fetchone()[0]
            conn.close()

            engine = TeamSyncEngine(db, author="local")
            diff = MemoryDiff(
                engram_id=row_id, project="proj", kind="fact",
                content="Updated remote fact", utility=1.5,
                status="active", timestamp=time.time(),
                author="remote",
                content_hash=engine._content_hash("Updated remote fact"),
            )
            manifest = SyncManifest(
                manifest_id="m3", author="remote",
                namespace="default", created_at="now",
                diffs=[diff], base_clock={}, head_clock={},
            )
            with _mock_enterprise_license():
                result = engine.apply_manifest(manifest, conflict_strategy="remote_wins")
                self.assertEqual(result["applied"], 1)
                # Verify remote content won
                conn = sqlite3.connect(db)
                text = conn.execute("SELECT text FROM episodes WHERE id=?", (row_id,)).fetchone()[0]
                conn.close()
                self.assertEqual(text, "Updated remote fact")

    def test_local_wins_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db, episodes=[
                (time.time(), "proj", "fact", "Local fact preserved", 1.0, "active"),
            ])
            conn = sqlite3.connect(db)
            row_id = conn.execute("SELECT id FROM episodes LIMIT 1").fetchone()[0]
            conn.close()

            engine = TeamSyncEngine(db, author="local")
            diff = MemoryDiff(
                engram_id=row_id, project="proj", kind="fact",
                content="Remote wants to overwrite", utility=1.5,
                status="active", timestamp=time.time(),
                author="remote",
                content_hash=engine._content_hash("Remote wants to overwrite"),
            )
            manifest = SyncManifest(
                manifest_id="m4", author="remote",
                namespace="default", created_at="now",
                diffs=[diff], base_clock={}, head_clock={},
            )
            with _mock_enterprise_license():
                result = engine.apply_manifest(manifest, conflict_strategy="local_wins")
                self.assertEqual(result["skipped"], 1)
                self.assertEqual(result["applied"], 0)
                # Verify local content preserved
                conn = sqlite3.connect(db)
                text = conn.execute("SELECT text FROM episodes WHERE id=?", (row_id,)).fetchone()[0]
                conn.close()
                self.assertEqual(text, "Local fact preserved")


class TestSyncAuditLog(unittest.TestCase):
    """Audit logging for sync operations."""

    def test_export_creates_audit_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            with _mock_enterprise_license():
                engine = TeamSyncEngine(db, author="auditor")
                engine.export_diffs()
                history = engine.get_sync_history()
                self.assertGreaterEqual(len(history), 1)
                self.assertEqual(history[0]["operation"], "export")
                self.assertEqual(history[0]["author"], "auditor")

    def test_import_creates_audit_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            manifest = SyncManifest(
                manifest_id="audit-test", author="remote",
                namespace="default", created_at="now",
                diffs=[], base_clock={}, head_clock={},
            )
            with _mock_enterprise_license():
                engine = TeamSyncEngine(db, author="local")
                engine.apply_manifest(manifest)
                history = engine.get_sync_history()
                import_entries = [h for h in history if h["operation"] == "import"]
                self.assertGreaterEqual(len(import_entries), 1)

    def test_history_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            with _mock_enterprise_license():
                engine = TeamSyncEngine(db, author="bulk")
                for i in range(5):
                    engine.export_diffs()
                history = engine.get_sync_history(limit=3)
                self.assertEqual(len(history), 3)


class TestContentHash(unittest.TestCase):
    """Content hashing for conflict detection."""

    def test_same_content_same_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            engine = TeamSyncEngine(db, author="x")
            h1 = engine._content_hash("identical content")
            h2 = engine._content_hash("identical content")
            self.assertEqual(h1, h2)

    def test_different_content_different_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            engine = TeamSyncEngine(db, author="x")
            h1 = engine._content_hash("content A")
            h2 = engine._content_hash("content B")
            self.assertNotEqual(h1, h2)

    def test_hash_length_is_16(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            _create_test_db(db)
            engine = TeamSyncEngine(db, author="x")
            h = engine._content_hash("test")
            self.assertEqual(len(h), 16)


if __name__ == "__main__":
    unittest.main()

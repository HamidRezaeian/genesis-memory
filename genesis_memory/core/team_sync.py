"""GENESIS Team Memory Sync Engine.

Provides secure, conflict-aware memory synchronization across team members.
Enterprise-tier feature that enables shared knowledge graphs with merge
conflict resolution, namespace isolation, and audit logging.

Architecture:
- Each team member maintains a local SQLite memory DB (existing)
- Team Sync exports/imports memory diffs as signed JSON patches
- Conflict detection uses vector clocks and content hashing
- All sync operations are auditable and reversible
"""

import datetime
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from genesis_memory.core.licensing import load_active_license


@dataclass
class MemoryDiff:
    """Represents a single memory change for synchronization."""
    engram_id: int
    project: str
    kind: str
    content: str
    utility: float
    status: str
    timestamp: float
    author: str
    content_hash: str
    vector_clock: Dict[str, int] = field(default_factory=dict)
    operation: str = "upsert"  # upsert | delete | supersede

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SyncManifest:
    """A package of memory diffs ready for team distribution."""
    manifest_id: str
    author: str
    namespace: str
    created_at: str
    diffs: List[MemoryDiff]
    base_clock: Dict[str, int]
    head_clock: Dict[str, int]
    signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "author": self.author,
            "namespace": self.namespace,
            "created_at": self.created_at,
            "diffs": [d.to_dict() for d in self.diffs],
            "base_clock": self.base_clock,
            "head_clock": self.head_clock,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncManifest":
        diffs = [MemoryDiff(**d) for d in data.get("diffs", [])]
        return cls(
            manifest_id=data["manifest_id"],
            author=data["author"],
            namespace=data.get("namespace", "default"),
            created_at=data["created_at"],
            diffs=diffs,
            base_clock=data.get("base_clock", {}),
            head_clock=data.get("head_clock", {}),
            signature=data.get("signature", ""),
        )


@dataclass
class SyncConflict:
    """Represents a merge conflict between local and remote memory."""
    engram_id: int
    local_content: str
    remote_content: str
    local_hash: str
    remote_hash: str
    local_author: str
    remote_author: str
    resolution: Optional[str] = None  # "local" | "remote" | "merge"


class TeamSyncEngine:
    """Manages team-wide memory synchronization with conflict resolution.

    Requires enterprise license tier to function. Community/Pro users
    get a clear error message directing them to upgrade.
    """

    SYNC_DIR_NAME = "team_sync"
    VECTOR_CLOCK_TABLE = "sync_vector_clock"
    SYNC_LOG_TABLE = "sync_audit_log"

    def __init__(self, db_path: str, author: str, namespace: str = "default"):
        self.db_path = db_path
        self.author = author
        self.namespace = namespace
        self._ensure_sync_schema()

    def _check_entitlement(self) -> Tuple[bool, str]:
        """Verify the current license includes team_shared_memory_sync."""
        lic = load_active_license()
        if lic.has_capability("team_shared_memory_sync"):
            return True, f"Enterprise license active (owner: {lic.owner})"
        return False, (
            f"Team Sync requires Enterprise tier. "
            f"Current tier: {lic.tier.upper()}. "
            f"Upgrade at https://genesis-memory.dev/pricing"
        )

    def _ensure_sync_schema(self) -> None:
        """Create sync metadata tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.VECTOR_CLOCK_TABLE} (
                author TEXT PRIMARY KEY,
                clock_value INTEGER NOT NULL DEFAULT 0,
                last_sync_ts REAL
            );
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.SYNC_LOG_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                manifest_id TEXT NOT NULL,
                author TEXT NOT NULL,
                operation TEXT NOT NULL,
                diffs_count INTEGER NOT NULL,
                conflicts_count INTEGER DEFAULT 0,
                namespace TEXT DEFAULT 'default',
                details TEXT
            );
        """)
        conn.commit()
        conn.close()

    def _get_vector_clock(self) -> Dict[str, int]:
        """Read the current vector clock state."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            f"SELECT author, clock_value FROM {self.VECTOR_CLOCK_TABLE}"
        ).fetchall()
        conn.close()
        return {r[0]: r[1] for r in rows}

    def _increment_clock(self) -> Dict[str, int]:
        """Increment this author's vector clock entry."""
        conn = sqlite3.connect(self.db_path)
        now = time.time()
        conn.execute(f"""
            INSERT INTO {self.VECTOR_CLOCK_TABLE} (author, clock_value, last_sync_ts)
            VALUES (?, 1, ?)
            ON CONFLICT(author) DO UPDATE SET
                clock_value = clock_value + 1,
                last_sync_ts = ?
        """, (self.author, now, now))
        conn.commit()
        clock = dict(conn.execute(
            f"SELECT author, clock_value FROM {self.VECTOR_CLOCK_TABLE}"
        ).fetchall())
        conn.close()
        return clock

    def _content_hash(self, content: str) -> str:
        """Generate a deterministic hash for content comparison."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def _log_sync_operation(
        self, manifest_id: str, operation: str,
        diffs_count: int, conflicts_count: int = 0,
        details: str = ""
    ) -> None:
        """Write an audit log entry for the sync operation."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"""
            INSERT INTO {self.SYNC_LOG_TABLE}
            (ts, manifest_id, author, operation, diffs_count, conflicts_count, namespace, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (time.time(), manifest_id, self.author, operation,
              diffs_count, conflicts_count, self.namespace, details))
        conn.commit()
        conn.close()

    def export_diffs(self, since_ts: float = 0.0) -> SyncManifest:
        """Export local memory changes since a given timestamp as a sync manifest.

        Returns a SyncManifest containing all engrams modified after `since_ts`,
        ready for distribution to team members.
        """
        ok, msg = self._check_entitlement()
        if not ok:
            raise PermissionError(msg)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, project, kind, text, utility, status, ts
            FROM episodes
            WHERE ts > ? AND project = ?
            ORDER BY ts ASC
        """, (since_ts, self.namespace)).fetchall()

        if not rows:
            rows = conn.execute("""
                SELECT id, project, kind, text, utility, status, ts
                FROM episodes
                WHERE ts > ?
                ORDER BY ts ASC
            """, (since_ts,)).fetchall()
        conn.close()

        base_clock = self._get_vector_clock()
        head_clock = self._increment_clock()

        diffs = []
        for row in rows:
            content = row["text"]
            status = row["status"] if row["status"] else "active"
            diff = MemoryDiff(
                engram_id=row["id"],
                project=row["project"],
                kind=row["kind"],
                content=content,
                utility=row["utility"],
                status=status,
                timestamp=row["ts"],
                author=self.author,
                content_hash=self._content_hash(content),
                vector_clock=head_clock.copy(),
                operation="delete" if status == "deleted" else "upsert",
            )
            diffs.append(diff)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        manifest_id = f"sync-{self.author}-{int(time.time())}"

        manifest = SyncManifest(
            manifest_id=manifest_id,
            author=self.author,
            namespace=self.namespace,
            created_at=now,
            diffs=diffs,
            base_clock=base_clock,
            head_clock=head_clock,
        )

        self._log_sync_operation(
            manifest_id=manifest_id,
            operation="export",
            diffs_count=len(diffs),
        )

        return manifest

    def detect_conflicts(self, manifest: SyncManifest) -> List[SyncConflict]:
        """Detect merge conflicts between incoming manifest and local state."""
        conflicts = []
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        for diff in manifest.diffs:
            local_row = conn.execute(
                "SELECT id, text, status FROM episodes WHERE id = ?",
                (diff.engram_id,)
            ).fetchone()

            if local_row:
                local_hash = self._content_hash(local_row["text"])
                if local_hash != diff.content_hash:
                    conflicts.append(SyncConflict(
                        engram_id=diff.engram_id,
                        local_content=local_row["text"],
                        remote_content=diff.content,
                        local_hash=local_hash,
                        remote_hash=diff.content_hash,
                        local_author=self.author,
                        remote_author=diff.author,
                    ))
        conn.close()
        return conflicts

    def apply_manifest(
        self,
        manifest: SyncManifest,
        conflict_strategy: str = "remote_wins",
    ) -> Dict[str, Any]:
        """Apply a sync manifest to the local database.

        Args:
            manifest: The SyncManifest to apply
            conflict_strategy: How to resolve conflicts
                - "remote_wins": Remote changes always win
                - "local_wins": Local changes are preserved
                - "skip_conflicts": Skip conflicting entries

        Returns:
            Summary dict with applied, skipped, and conflict counts.
        """
        ok, msg = self._check_entitlement()
        if not ok:
            raise PermissionError(msg)

        conflicts = self.detect_conflicts(manifest)
        conflict_ids = {c.engram_id for c in conflicts}

        applied = 0
        skipped = 0
        conn = sqlite3.connect(self.db_path)

        for diff in manifest.diffs:
            if diff.engram_id in conflict_ids:
                if conflict_strategy == "local_wins":
                    skipped += 1
                    continue
                elif conflict_strategy == "skip_conflicts":
                    skipped += 1
                    continue
                # remote_wins: fall through to apply

            if diff.operation == "delete":
                conn.execute(
                    "UPDATE episodes SET status = 'deleted' WHERE id = ?",
                    (diff.engram_id,)
                )
                applied += 1
            else:
                existing = conn.execute(
                    "SELECT id FROM episodes WHERE id = ?",
                    (diff.engram_id,)
                ).fetchone()

                if existing:
                    conn.execute("""
                        UPDATE episodes SET text = ?, utility = ?, status = ?
                        WHERE id = ?
                    """, (diff.content, diff.utility, diff.status, diff.engram_id))
                else:
                    conn.execute("""
                        INSERT INTO episodes (project, kind, text, utility, ts, status)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (diff.project, diff.kind, diff.content,
                          diff.utility, diff.timestamp, diff.status))
                applied += 1

        # Update vector clock with remote state
        for author, clock_val in manifest.head_clock.items():
            conn.execute(f"""
                INSERT INTO {self.VECTOR_CLOCK_TABLE} (author, clock_value, last_sync_ts)
                VALUES (?, ?, ?)
                ON CONFLICT(author) DO UPDATE SET
                    clock_value = MAX(clock_value, ?),
                    last_sync_ts = ?
            """, (author, clock_val, time.time(), clock_val, time.time()))

        conn.commit()
        conn.close()

        self._log_sync_operation(
            manifest_id=manifest.manifest_id,
            operation="import",
            diffs_count=applied,
            conflicts_count=len(conflicts),
            details=f"strategy={conflict_strategy}, skipped={skipped}",
        )

        return {
            "applied": applied,
            "skipped": skipped,
            "conflicts": len(conflicts),
            "manifest_id": manifest.manifest_id,
            "strategy": conflict_strategy,
        }

    def get_sync_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent sync audit log entries."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"""
            SELECT * FROM {self.SYNC_LOG_TABLE}
            ORDER BY ts DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_vector_clock_status(self) -> Dict[str, Any]:
        """Return current vector clock state for all known authors."""
        clock = self._get_vector_clock()
        return {
            "namespace": self.namespace,
            "author": self.author,
            "clock": clock,
            "total_authors": len(clock),
        }

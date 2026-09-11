"""GENESIS Lossless Spool Engine — Pointer-not-Payload Spooling.

Captures massive command stdout/stderr to local spool files (~/.genesis/spool/<id>.log)
using atomic write-then-rename, provides byte-identical dereferencing via MCP/CLI,
enforces retention GC (TTL + size caps), and instruments the critical fetch-rate metric.

Privacy boundary (R2): every byte is passed through the Zero-Trust Privacy Shield
(structural key patterns + Shannon entropy) *before* it is written, so credentials
never touch disk. Non-secret output is preserved byte-for-byte. ``GENESIS_SPOOL_RAW=1``
opts out for deliberate forensic captures.
"""

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from genesis_memory.core.privacy_shield import redact_bytes

logger = logging.getLogger("genesis.spool")

DEFAULT_SPOOL_DIR = Path.home() / ".genesis" / "spool"
DEFAULT_TTL_DAYS = 7.0
DEFAULT_MAX_DIR_BYTES = 500 * 1024 * 1024  # 500 MB

# IDs are engine-generated sha256[:8]; nothing else is ever a valid id (R3).
SPOOL_ID_RE = re.compile(r"^[0-9a-f]{8}$")


class SpoolEngine:
    """Atomic, lossless command output spooler with retention GC and fetch-rate metrics."""

    def __init__(
        self,
        spool_dir: Optional[Union[str, Path]] = None,
        ttl_days: float = DEFAULT_TTL_DAYS,
        max_bytes: int = DEFAULT_MAX_DIR_BYTES,
        store: Optional[Any] = None,
    ) -> None:
        self.spool_dir = Path(
            spool_dir or os.environ.get("GENESIS_SPOOL_DIR", str(DEFAULT_SPOOL_DIR))
        ).expanduser().resolve()
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_days = ttl_days
        self.max_bytes = max_bytes
        self.store = store

        # Internal in-memory counters (synced with store if available)
        self.spools_created = 0
        self.spools_dereferenced = 0
        self.spooled_bytes = 0
        self.dereferenced_bytes = 0
        self.spools_full_reads = 0
        self.secrets_blocked = 0
        self._sync_counters_from_store()

    def _sync_counters_from_store(self) -> None:
        if self.store and hasattr(self.store, "db"):
            try:
                self.store.db.execute(
                    "CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, count INTEGER DEFAULT 0)"
                )
                for cname in ("spools_created", "spools_dereferenced", "spooled_bytes", "dereferenced_bytes", "spools_full_reads"):
                    self.store.db.execute("INSERT OR IGNORE INTO counters(name, count) VALUES (?, 0)", (cname,))
                self.store.db.commit()
                rows = self.store.db.execute(
                    "SELECT name, count FROM counters WHERE name IN "
                    "('spools_created', 'spools_dereferenced', 'spooled_bytes', 'dereferenced_bytes', 'spools_full_reads')"
                ).fetchall()
                for n, c in rows:
                    if n == "spools_created":
                        self.spools_created = max(self.spools_created, c)
                    elif n == "spools_dereferenced":
                        self.spools_dereferenced = max(self.spools_dereferenced, c)
                    elif n == "spooled_bytes":
                        self.spooled_bytes = max(self.spooled_bytes, c)
                    elif n == "dereferenced_bytes":
                        self.dereferenced_bytes = max(self.dereferenced_bytes, c)
                    elif n == "spools_full_reads":
                        self.spools_full_reads = max(self.spools_full_reads, c)
            except Exception as exc:
                logger.debug("Failed to sync spool counters from store: %s", exc)

    def _inc_counter(self, name: str, amount: int = 1) -> None:
        if name == "spools_created":
            self.spools_created += amount
        elif name == "spools_dereferenced":
            self.spools_dereferenced += amount
        elif name == "spooled_bytes":
            self.spooled_bytes += amount
        elif name == "dereferenced_bytes":
            self.dereferenced_bytes += amount
        elif name == "spools_full_reads":
            self.spools_full_reads += amount
        elif name == "secrets_blocked":
            self.secrets_blocked += amount

        if self.store and hasattr(self.store, "db"):
            try:
                self.store.db.execute(
                    "INSERT INTO counters(name, count) VALUES (?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET count = count + ?",
                    (name, amount, amount),
                )
                self.store.db.commit()
            except Exception as exc:
                logger.debug("Failed to persist spool counter %s: %s", name, exc)

    def write_spool(
        self,
        content: Union[str, bytes],
        command: str = "",
        exit_code: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Path]:
        """Atomically writes content to ~/.genesis/spool/<id>.log.

        Secrets are redacted *before* the bytes touch disk (Zero-Trust Privacy
        Shield: structural patterns + Shannon entropy). Non-secret bytes are
        preserved exactly. Set ``GENESIS_SPOOL_RAW=1`` to opt out for forensic
        captures. Returns (spool_id, log_path).
        """
        # Normalize content to raw bytes
        if isinstance(content, str):
            raw_bytes = content.encode("utf-8")
        else:
            raw_bytes = content

        shield_report: Optional[Dict[str, Any]] = None
        if os.environ.get("GENESIS_SPOOL_RAW", "") not in ("1", "true", "yes"):
            raw_bytes, rep = redact_bytes(raw_bytes)
            if not rep.clean:
                shield_report = rep.to_dict()
                self._inc_counter("secrets_blocked", rep.redactions)

        # Safe decoding with fallback for metadata calculation
        try:
            text_str = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text_str = raw_bytes.decode("utf-8", errors="replace")

        # Deterministic, collision-resistant 8-hex identifier
        hasher = hashlib.sha256()
        hasher.update(raw_bytes)
        hasher.update(str(time.time_ns()).encode("utf-8"))
        spool_id = hasher.hexdigest()[:8]

        log_path = self.spool_dir / f"{spool_id}.log"
        meta_path = self.spool_dir / f"{spool_id}.meta.json"
        temp_log = self.spool_dir / f"{spool_id}.tmp.{os.getpid()}"
        temp_meta = self.spool_dir / f"{spool_id}.meta.tmp.{os.getpid()}"

        # 1. Atomic write-then-rename for log payload
        with open(temp_log, "wb") as f:
            f.write(raw_bytes)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_log, log_path)

        # 2. Atomic metadata recording
        meta = {
            "id": spool_id,
            "ts": time.time(),
            "command": command,
            "exit_code": exit_code,
            "byte_size": len(raw_bytes),
            "lines_count": len(text_str.splitlines()),
            "extra": metadata or {},
        }
        if shield_report:
            meta["privacy_shield"] = shield_report
        with open(temp_meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_meta, meta_path)

        self._inc_counter("spools_created", 1)
        self._inc_counter("spooled_bytes", len(raw_bytes))
        return spool_id, log_path

    def _validated_spool_paths(self, spool_id: Any) -> Tuple[Path, Path]:
        """Validate id format and contain resolved paths (R3).

        Policy: only engine-generated 8-hex ids; no separators, absolute
        paths, or symlinks (rejected even when pointing inside); resolved
        files must sit directly in the spool directory. Violations raise
        ValueError with a bounded, non-sensitive message (the MCP layer
        surfaces it without tracebacks).
        """
        if not isinstance(spool_id, str) or not SPOOL_ID_RE.match(spool_id):
            raise ValueError("invalid spool id")
        log_path = self.spool_dir / f"{spool_id}.log"
        meta_path = self.spool_dir / f"{spool_id}.meta.json"
        for p in (log_path, meta_path):
            if p.is_symlink():
                raise ValueError("invalid spool id")
            try:
                resolved = p.resolve()
            except OSError:
                raise ValueError("invalid spool id")
            if resolved.parent != self.spool_dir:
                raise ValueError("invalid spool id")
        return log_path, meta_path

    def read_spool_raw_bytes(self, spool_id: str) -> bytes:
        """Reads raw binary content of a spool file byte-for-byte."""
        log_path, _ = self._validated_spool_paths(spool_id)
        if not log_path.exists():
            raise FileNotFoundError(f"Spool file {spool_id} not found in {self.spool_dir}")

        self._inc_counter("spools_dereferenced", 1)
        with open(log_path, "rb") as f:
            return f.read()

    def read_spool(
        self,
        spool_id: str,
        grep: Optional[str] = None,
        offset: int = 0,
        max_lines: Optional[int] = 40,
        full: Union[bool, str] = False,
        reason: Optional[str] = None,
        page: int = 0,
    ) -> Dict[str, Any]:
        """Reads spool content with optional regex/keyword grep filtering, slicing, and 500-line gate.

        For large logs (>500 lines) without grep or offset, returns an actionable refusal
        hint with discovered anchors unless full='chunked' and reason is provided.
        """
        log_path, meta_path = self._validated_spool_paths(spool_id)

        if not log_path.exists():
            raise FileNotFoundError(f"Spool file {spool_id} not found at {log_path}")

        raw_bytes = log_path.read_bytes()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_bytes.decode("utf-8", errors="replace")

        lines = text.splitlines()
        total_lines = len(lines)

        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Actionable 500-line gate (Fable/OpenCode Consensus)
        is_chunked = (full == "chunked" or full is True)
        if total_lines > 500 and not grep and offset == 0 and not is_chunked:
            # Extract line anchors for failure/error lines
            anchors: List[Dict[str, Any]] = []
            for idx, line in enumerate(lines, 1):
                l_str = line.strip()
                if (
                    l_str.startswith("FAILED ")
                    or l_str.startswith("ERROR ")
                    or "AssertionError" in l_str
                    or l_str.startswith("___ ")
                ):
                    anchors.append({"line": idx, "label": l_str[:100]})
                    if len(anchors) >= 8:
                        break

            return {
                "refused": True,
                "id": spool_id,
                "total_lines": total_lines,
                "byte_size": len(raw_bytes),
                "anchors": anchors,
                "suggested": [
                    {"grep": "FAILED|AssertionError"},
                    {"offset": anchors[0]["line"] if anchors else 1, "lines": 40},
                ],
                "escape": {
                    "full": "chunked",
                    "reason": "Explain why grep or line offset is insufficient",
                    "page": 0,
                },
                "hint": (
                    f"Spool has {total_lines} lines. Reading the entire log wastes context. "
                    "Use offset from line anchors or grep. If you must inspect sequentially, "
                    "provide full='chunked' and a short reason."
                ),
            }

        # Track unique dereference and increment counters
        self._inc_counter("spools_dereferenced", 1)
        if self.store and hasattr(self.store, "db"):
            try:
                self.store.db.execute(
                    "CREATE TABLE IF NOT EXISTS spool_derefs (spool_id TEXT PRIMARY KEY, deref_count INTEGER, first_seen REAL)"
                )
                self.store.db.execute(
                    "INSERT INTO spool_derefs (spool_id, deref_count, first_seen) VALUES (?, 1, ?) "
                    "ON CONFLICT(spool_id) DO UPDATE SET deref_count = deref_count + 1",
                    (spool_id, time.time()),
                )
                self.store.db.commit()
            except Exception:
                pass

        if is_chunked:
            self._inc_counter("spools_full_reads", 1)
            chunk_size = 400
            start = page * chunk_size
            end = start + chunk_size
            selected = lines[start:end]
            returned_bytes = len("\n".join(selected).encode("utf-8"))
            self._inc_counter("dereferenced_bytes", returned_bytes)
            total_pages = max(1, (total_lines + chunk_size - 1) // chunk_size)
            next_page = (page + 1) if (page + 1) < total_pages else None
            return {
                "id": spool_id,
                "content": "\n".join(selected),
                "total_lines": total_lines,
                "returned_lines": len(selected),
                "offset": start,
                "page": page,
                "total_pages": total_pages,
                "next_page": next_page,
                "remaining_lines": max(0, total_lines - end),
                "command": meta.get("command", ""),
                "exit_code": meta.get("exit_code"),
                "byte_size": len(raw_bytes),
                "reason": reason,
            }

        # Apply grep filter if requested
        if grep:
            pattern = re.compile(grep, re.IGNORECASE)
            matched_lines = [l for l in lines if pattern.search(l)]
        else:
            matched_lines = lines

        matched_total = len(matched_lines)

        # Slice window
        start = max(0, offset)
        limit = min(max_lines if max_lines is not None else 40, 400)
        end = start + limit
        selected = matched_lines[start:end]
        is_truncated = end < matched_total

        returned_bytes = len("\n".join(selected).encode("utf-8"))
        self._inc_counter("dereferenced_bytes", returned_bytes)

        return {
            "id": spool_id,
            "content": "\n".join(selected),
            "total_lines": total_lines,
            "matched_lines": matched_total,
            "returned_lines": len(selected),
            "offset": start,
            "is_truncated": is_truncated,
            "command": meta.get("command", ""),
            "exit_code": meta.get("exit_code"),
            "byte_size": len(raw_bytes),
        }

    def prune(
        self,
        max_age_days: Optional[float] = None,
        max_bytes: Optional[int] = None,
    ) -> int:
        """Enforces TTL expiration and total directory size caps via LRU eviction."""
        ttl = max_age_days if max_age_days is not None else self.ttl_days
        cap = max_bytes if max_bytes is not None else self.max_bytes
        now = time.time()
        ttl_cutoff = now - (ttl * 86400.0)

        deleted_count = 0
        file_entries: List[Tuple[Path, float, int]] = []  # (path, mtime, size)

        # Pass 1: Prune by TTL
        for p in self.spool_dir.iterdir():
            if not p.is_file():
                continue
            try:
                stat = p.stat()
                mtime = stat.st_mtime
                size = stat.st_size
                if mtime < ttl_cutoff:
                    p.unlink(missing_ok=True)
                    deleted_count += 1
                else:
                    file_entries.append((p, mtime, size))
            except Exception as exc:
                logger.debug("Error inspecting spool file %s: %s", p, exc)

        # Pass 2: Prune by size cap (LRU: oldest first)
        current_bytes = sum(size for _, _, size in file_entries)
        if current_bytes > cap:
            # Sort by mtime ascending
            file_entries.sort(key=lambda item: item[1])
            target_bytes = int(cap * 0.80)  # Evict down to 80% watermark
            for p, _, size in file_entries:
                if current_bytes <= target_bytes:
                    break
                try:
                    p.unlink(missing_ok=True)
                    current_bytes -= size
                    deleted_count += 1
                except Exception as exc:
                    logger.debug("Failed to evict spool file %s: %s", p, exc)

        return deleted_count

    def get_telemetry(self) -> Dict[str, Any]:
        """Calculates live storage metrics and the critical fetch-rate ratio."""
        self._sync_counters_from_store()
        total_files = 0
        total_bytes = 0
        try:
            for p in self.spool_dir.glob("*.log"):
                if p.is_file():
                    total_files += 1
                    total_bytes += p.stat().st_size
        except Exception:
            pass

        created = max(0, self.spools_created)
        deref = max(0, self.spools_dereferenced)
        fetch_rate = round((deref / max(1, created)) * 100.0, 2)

        unique_derefs = deref
        if self.store and hasattr(self.store, "db"):
            try:
                unique_derefs = self.store.db.execute("SELECT COUNT(*) FROM spool_derefs").fetchone()[0]
            except Exception:
                pass

        spooled_b = max(0, self.spooled_bytes)
        deref_b = max(0, self.dereferenced_bytes)
        byte_ratio = round((deref_b / max(1, spooled_b)) * 100.0, 2)

        return {
            "spools_created": created,
            "spools_dereferenced": deref,
            "spools_dereferenced_unique": unique_derefs,
            "spools_full_reads": self.spools_full_reads,
            "fetch_rate_pct": fetch_rate,
            "spooled_bytes": spooled_b,
            "dereferenced_bytes": deref_b,
            "byte_deref_ratio_pct": byte_ratio,
            "active_spool_files": total_files,
            "total_spool_bytes": total_bytes,
            "spool_dir": str(self.spool_dir),
        }

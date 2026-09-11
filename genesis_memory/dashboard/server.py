"""GENESIS Mission Control — live telemetry server for the cognitive dashboard.

Standard library only (``http.server`` + SQLite). The dashboard reads the store
through **read-only** connections so a browser tab can never mutate memory; the
few explicit actions (resolve conflict, reinforce, trigger sleep) go through the
daemon's ``Store`` and are POST-only.

Routes
------
GET  /                       dashboard (static/index.html)
GET  /health                 {status, uptime_s}
GET  /api/overview           everything the cockpit needs in one round-trip
GET  /api/stream             Server-Sent Events: overview every N seconds
GET  /api/snapshot           legacy snapshot (daemon.snapshot)
GET  /api/license            active license
GET  /api/engrams?q=&kind=&project=&status=&limit=&offset=
GET  /api/engram/<id>
GET  /api/skills             procedural skills
GET  /api/conflicts?status=  veto queue
GET  /api/thread             unified active work thread
GET  /api/dialogue           cross-client dialogue buffer (latest N)
GET  /api/edges              AST dependency graph
GET  /api/clients            universal client matrix (detection + configured)
GET  /api/spool              spool telemetry + recent spools
GET  /api/ledger             session ledger
GET  /api/proxy_telemetry    proxied from the gateway (:8000) when alive
GET  /api/pricing            proxied from the gateway
GET  /api/sleep              generate read-only sleep report
GET  /llms.txt, /openapi.json
POST /api/shield             {"text": ...} -> redaction preview (nothing stored)
POST /api/conflicts/<id>/resolve {"action": "superseded|kept_both|dismissed", "winner_id"?}
POST /api/reinforce          {"id": int, "outcome": "success|failure", "note"?}
POST /api/sleep/now          {"deep": bool}
"""

from __future__ import annotations

import http.server
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from genesis_memory.core import db as _dbx
from genesis_memory.core import privacy_shield
from genesis_memory.daemon.server import DB_PATH, Store, snapshot, rss_mb
from genesis_memory.sleep import sleep_consolidation

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
HTML_PATH = STATIC_DIR / "index.html"
LLMS_PATH = REPO_ROOT / "llms.txt"
OPENAPI_PATH = REPO_ROOT / "openapi.json"


def _read_contract(name: str) -> bytes:
    """Read a shipped contract file (llms.txt / openapi.json).

    Installed package first (importlib.resources: works on user machines
    with no repo checkout); repo-root fallback for dev checkouts. The rules
    live inside the product — never depend on ambient files.
    """
    try:
        from importlib import resources as _resources
        data = (_resources.files("genesis_memory") / "data" / name).read_bytes()
        if data:
            return bytes(data)
    except Exception:
        pass
    fpath = LLMS_PATH if name == "llms.txt" else OPENAPI_PATH
    return fpath.read_bytes()
DEFAULT_DB = os.environ.get("GENESIS_DAEMON_DB", os.path.expanduser("~/.genesis/memory.db"))
DEFAULT_PORT = int(os.environ.get("GENESIS_DASHBOARD_PORT", "8090"))
SERVER_START_TIME = time.time()
STREAM_INTERVAL_S = float(os.environ.get("GENESIS_DASHBOARD_STREAM_S", "2.0"))
VERSION = "0.6.0"

_ID_RE = re.compile(r"^\d{1,12}$")


# --------------------------------------------------------------------------- repository
class DashboardRepo:
    """Read-only SQL access for the cockpit. Every method tolerates a missing/old schema."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> Optional[sqlite3.Connection]:
        if not os.path.exists(self.db_path):
            return None
        try:
            return _dbx.connect(self.db_path, readonly=True, row_factory=sqlite3.Row)
        except sqlite3.Error:
            return None

    def _rows(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        conn = self._conn()
        if conn is None:
            return []
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def _scalar(self, sql: str, params: Tuple[Any, ...] = (), default: Any = 0) -> Any:
        conn = self._conn()
        if conn is None:
            return default
        try:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row and row[0] is not None else default
        except sqlite3.Error:
            return default
        finally:
            conn.close()

    # -- engrams -------------------------------------------------------------
    def engrams(self, q: str = "", kind: str = "", project: str = "", status: str = "",
                limit: int = 60, offset: int = 0) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        where, params = [], []
        if kind:
            where.append("e.kind = ?"); params.append(kind)
        if project:
            where.append("e.project = ?"); params.append(project)
        if status:
            where.append("COALESCE(e.status,'active') = ?"); params.append(status)
        cols = ("e.id, e.ts, e.project, e.kind, e.text, e.utility, e.accesses, e.updated, "
                "COALESCE(e.status,'active') AS status, e.superseded_by, e.stability, "
                "e.reinforcements, e.last_reinforced, e.model_source")
        if q.strip():
            terms = [t for t in re.findall(r"[\w]{2,}", q.lower())][:10]
            fts = " OR ".join(f'"{t}"*' for t in terms) if terms else '""'
            sql = (f"SELECT {cols}, bm25(episodes_fts) AS score FROM episodes_fts "
                   f"JOIN episodes e ON e.id = episodes_fts.rowid WHERE episodes_fts MATCH ?"
                   + ("".join(" AND " + w for w in where)) +
                   " ORDER BY score ASC LIMIT ? OFFSET ?")
            rows = self._rows(sql, tuple([fts] + params + [limit, offset]))
        else:
            sql = (f"SELECT {cols}, NULL AS score FROM episodes e"
                   + (" WHERE " + " AND ".join(where) if where else "") +
                   " ORDER BY e.utility DESC, e.accesses DESC, e.updated DESC LIMIT ? OFFSET ?")
            rows = self._rows(sql, tuple(params + [limit, offset]))
        for r in rows:
            r["tokens_est"] = max(1, len(r.get("text") or "") // 4)
        total = self._scalar("SELECT COUNT(*) FROM episodes")
        projects = [r["project"] for r in self._rows(
            "SELECT project, COUNT(*) AS n FROM episodes GROUP BY project ORDER BY n DESC LIMIT 30")]
        return {"items": rows, "count": len(rows), "total": total, "offset": offset,
                "limit": limit, "projects": projects}

    def engram(self, eid: int) -> Optional[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM episodes WHERE id = ?", (int(eid),))
        if not rows:
            return None
        row = rows[0]
        row["tokens_est"] = max(1, len(row.get("text") or "") // 4)
        row["conflicts"] = self._rows(
            "SELECT id, ts, new_id, conflicting_id, similarity, status FROM conflicts "
            "WHERE new_id = ? OR conflicting_id = ? ORDER BY ts DESC", (int(eid), int(eid)))
        return row

    # -- graph ---------------------------------------------------------------
    def memory_graph(self, limit: int = 400) -> Dict[str, Any]:
        """Engrams as nodes; links from conflicts, supersession, shared project & lexical overlap."""
        rows = self._rows(
            "SELECT id, project, kind, substr(text,1,140) AS preview, text, utility, accesses, "
            "COALESCE(status,'active') AS status, superseded_by, stability, reinforcements, updated "
            "FROM episodes ORDER BY utility DESC, accesses DESC LIMIT ?", (max(1, min(limit, 2000)),))
        nodes = []
        by_id = {}
        for r in rows:
            n = {"id": r["id"], "project": r["project"], "kind": r["kind"], "preview": r["preview"],
                 "utility": r["utility"], "accesses": r["accesses"], "status": r["status"],
                 "stability": r["stability"], "reinforcements": r["reinforcements"],
                 "updated": r["updated"], "tokens_est": max(1, len(r["text"] or "") // 4)}
            nodes.append(n)
            by_id[r["id"]] = r
        links = []
        seen = set()

        def add(a, b, rel, w=1.0):
            if a == b or a not in by_id or b not in by_id:
                return
            key = (min(a, b), max(a, b), rel)
            if key in seen:
                return
            seen.add(key)
            links.append({"source": a, "target": b, "relation": rel, "weight": w})

        for r in rows:
            if r["superseded_by"]:
                add(r["id"], r["superseded_by"], "superseded_by", 2.0)
        for c in self._rows("SELECT new_id, conflicting_id, similarity, status FROM conflicts"):
            add(c["new_id"], c["conflicting_id"], "conflict" if c["status"] == "pending" else "resolved",
                float(c["similarity"] or 1.0))
        # Lexical synapses: share >=2 informative (5+ char) terms, bounded via inverted index.
        index: Dict[str, List[int]] = {}
        terms_of: Dict[int, set] = {}
        for r in rows:
            toks = {t for t in re.findall(r"[a-z][a-z0-9_]{4,}", (r["text"] or "").lower())}
            terms_of[r["id"]] = toks
            for t in toks:
                index.setdefault(t, []).append(r["id"])
        for t, ids in index.items():
            if 2 <= len(ids) <= 12:
                for i in range(len(ids)):
                    for j in range(i + 1, len(ids)):
                        a, b = ids[i], ids[j]
                        shared = len(terms_of[a] & terms_of[b])
                        if shared >= 2:
                            add(a, b, "lexical", min(3.0, shared / 2.0))
        edges = self._rows("SELECT source, target, relation FROM edges WHERE status = 'active' OR status IS NULL LIMIT 600")
        return {"nodes": nodes, "links": links, "ast_edges": edges,
                "counts": {"nodes": len(nodes), "links": len(links), "ast_edges": len(edges)}}

    # -- misc tables ----------------------------------------------------------
    def skills(self) -> List[Dict[str, Any]]:
        rows = self._rows("SELECT id, name, trigger_patterns, preconditions, action_recipe, invariants, "
                          "confidence, success_count, status, created_at, updated_at FROM skills "
                          "ORDER BY success_count DESC, confidence DESC")
        for r in rows:
            try:
                r["trigger_patterns"] = json.loads(r["trigger_patterns"]) if r.get("trigger_patterns") else []
            except Exception:
                r["trigger_patterns"] = [p.strip() for p in str(r.get("trigger_patterns") or "").split(",") if p.strip()]
        return rows

    def conflicts(self, status: str = "") -> List[Dict[str, Any]]:
        sql = ("SELECT c.id, c.ts, c.new_id, c.conflicting_id, c.similarity, c.status, c.updated, "
               "n.text AS new_text, n.kind AS new_kind, n.status AS new_status, "
               "o.text AS old_text, o.kind AS old_kind, o.status AS old_status "
               "FROM conflicts c LEFT JOIN episodes n ON n.id = c.new_id "
               "LEFT JOIN episodes o ON o.id = c.conflicting_id")
        params: Tuple[Any, ...] = ()
        if status:
            sql += " WHERE c.status = ?"
            params = (status,)
        return self._rows(sql + " ORDER BY c.ts DESC LIMIT 200", params)

    def thread(self) -> Dict[str, Any]:
        rows = self._rows("SELECT updated_at, client, session_id, topic, summary, recent_files, pending_focus "
                          "FROM active_thread WHERE id = 1")
        if not rows:
            return {"present": False}
        t = rows[0]
        t["present"] = True
        t["age_s"] = round(time.time() - float(t.get("updated_at") or 0), 1)
        return t

    def dialogue(self, limit: int = 12) -> List[Dict[str, Any]]:
        return self._rows("SELECT session_id, client, updated_at, user_prompt, assistant_summary, salient_terms "
                          "FROM dialogue_buffer ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 100)),))

    def counters(self) -> Dict[str, int]:
        return {r["name"]: r["count"] for r in self._rows("SELECT name, count FROM counters")}

    def kinds(self) -> Dict[str, int]:
        return {r["kind"]: r["n"] for r in self._rows("SELECT kind, COUNT(*) AS n FROM episodes GROUP BY kind")}

    def statuses(self) -> Dict[str, int]:
        return {r["s"]: r["n"] for r in self._rows(
            "SELECT COALESCE(status,'active') AS s, COUNT(*) AS n FROM episodes GROUP BY s")}

    def timeline(self, buckets: int = 48) -> List[Dict[str, Any]]:
        """Engram creation histogram over the last 48 hours (hourly)."""
        now = time.time()
        span = 3600.0
        start = now - buckets * span
        rows = self._rows("SELECT ts, kind FROM episodes WHERE ts >= ?", (start,))
        hist = [{"t": start + i * span, "fact": 0, "decision": 0, "outcome": 0} for i in range(buckets)]
        for r in rows:
            idx = int((float(r["ts"]) - start) // span)
            if 0 <= idx < buckets:
                k = r["kind"] if r["kind"] in ("fact", "decision", "outcome") else "fact"
                hist[idx][k] += 1
        return hist

    def totals(self) -> Dict[str, Any]:
        total_chars = self._scalar("SELECT COALESCE(SUM(LENGTH(text)),0) FROM episodes")
        size = None
        if os.path.exists(self.db_path):
            try:
                size = os.path.getsize(self.db_path)
                wal = self.db_path + "-wal"
                if os.path.exists(wal):
                    size += os.path.getsize(wal)
            except OSError:
                size = None
        return {
            "episodes_total": self._scalar("SELECT COUNT(*) FROM episodes"),
            "episodes_active": self._scalar("SELECT COUNT(*) FROM episodes WHERE status = 'active' OR status IS NULL"),
            "solidified": self._scalar("SELECT COUNT(*) FROM episodes WHERE status = 'solidified'"),
            "superseded": self._scalar("SELECT COUNT(*) FROM episodes WHERE status = 'superseded'"),
            "invalidated": self._scalar("SELECT COUNT(*) FROM episodes WHERE status = 'invalidated'"),
            "skills": self._scalar("SELECT COUNT(*) FROM skills WHERE status = 'active'"),
            "conflicts_pending": self._scalar("SELECT COUNT(*) FROM conflicts WHERE status = 'pending'"),
            "edges_active": self._scalar("SELECT COUNT(*) FROM edges WHERE status = 'active' OR status IS NULL"),
            "total_chars": total_chars,
            "total_tokens_est": total_chars // 4,
            "db_bytes": size,
            "schema_version": self._scalar("PRAGMA user_version", default=0),
            "journal_mode": str(self._scalar("PRAGMA journal_mode", default="?")),
        }


# --------------------------------------------------------------------------- collectors
def collect_spool(db_path: str) -> Dict[str, Any]:
    try:
        from genesis_memory.proxy.spool import SpoolEngine
        engine = SpoolEngine()
        tel = engine.get_telemetry()
        recent = []
        for meta in sorted(engine.spool_dir.glob("*.meta.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]:
            try:
                d = json.loads(meta.read_text(encoding="utf-8"))
                recent.append({"id": d.get("id"), "ts": d.get("ts"), "command": (d.get("command") or "")[:120],
                               "exit_code": d.get("exit_code"), "byte_size": d.get("byte_size"),
                               "lines_count": d.get("lines_count"),
                               "shield": (d.get("privacy_shield") or {}).get("redactions", 0)})
            except Exception:
                continue
        tel["recent"] = recent
        tel["spool_dir"] = str(engine.spool_dir)
        return tel
    except Exception as exc:
        return {"error": str(exc), "recent": []}


def collect_clients() -> List[Dict[str, Any]]:
    try:
        from genesis_memory.cli.client_registry import ClientRegistry
        return [c.to_dict() for c in ClientRegistry.discover_all()]
    except Exception as exc:
        return [{"error": str(exc)}]


def collect_license() -> Dict[str, Any]:
    try:
        from genesis_memory.core.licensing import load_active_license
        return load_active_license().to_dict()
    except Exception:
        return {"tier": "community", "is_valid": True}


def _proxy_get(path: str, timeout: float = 1.5) -> Optional[Dict[str, Any]]:
    port = int(os.environ.get("GENESIS_PROXY_PORT", "8000"))
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"User-Agent": "GenesisDashboard/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def collect_proxy() -> Dict[str, Any]:
    data = _proxy_get("/v1/telemetry")
    if data is None:
        return {"connected": False, "mode": "offline", "requests_total": 0,
                "tokens": {"prompt_stripped_est": 0, "total": 0},
                "bypass": {"bypass_rate_pct": 0.0, "bypasses_total": 0, "reasons": {}},
                "performance": {"p50_ttft_ms": 0.0, "p95_ttft_ms": 0.0, "mean_ttft_ms": 0.0},
                "upstream": {"status_codes": {}}, "cost": {}}
    data["connected"] = True
    return data


def read_ledger(db_path: str, limit: int = 200) -> List[Dict[str, Any]]:
    ledger_file = os.path.join(os.path.dirname(os.path.abspath(db_path)), "ledger.jsonl")
    sessions: List[Dict[str, Any]] = []
    if os.path.exists(ledger_file):
        with open(ledger_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        sessions.append(json.loads(line))
                    except Exception:
                        pass
    return sessions[-limit:]


def build_overview(db_path: str) -> Dict[str, Any]:
    repo = DashboardRepo(db_path)
    totals = repo.totals()
    counters = repo.counters()
    proxy = collect_proxy()
    spool = collect_spool(db_path)
    stripped = int(((proxy.get("tokens") or {}).get("prompt_stripped_est") or 0))
    spooled_bytes = int(spool.get("spooled_bytes") or 0)
    deref_bytes = int(spool.get("dereferenced_bytes") or 0)
    spool_tokens_saved = max(0, (spooled_bytes - deref_bytes) // 4)
    dollars = float(((proxy.get("cost") or {}).get("dollars_saved_total") or 0.0))
    return {
        "version": VERSION,
        "server_time": time.time(),
        "uptime_s": round(time.time() - SERVER_START_TIME, 1),
        "db_path": db_path,
        "db_present": os.path.exists(db_path),
        "rss_mb": rss_mb(),
        "totals": totals,
        "counters": counters,
        "kinds": repo.kinds(),
        "statuses": repo.statuses(),
        "timeline": repo.timeline(),
        "thread": repo.thread(),
        "dialogue": repo.dialogue(6),
        "skills": repo.skills()[:12],
        "conflicts": repo.conflicts("pending")[:20],
        "spool": spool,
        "proxy": proxy,
        "license": collect_license(),
        "clients": collect_clients(),
        "ledger_tail": read_ledger(db_path, 12),
        "diet": {
            "proxy_tokens_stripped": stripped,
            "spool_tokens_saved_est": spool_tokens_saved,
            "tokens_saved_total_est": stripped + spool_tokens_saved,
            "dollars_saved": round(dollars, 6),
            "secrets_blocked": int(counters.get("secrets_blocked", 0)) + int(spool.get("secrets_blocked") or 0),
        },
    }


def trigger_auto_sleep(db_path: str = DEFAULT_DB) -> Dict[str, Any]:
    """Generate read-only sleep consolidation report automatically."""
    try:
        db_to_use = db_path if os.path.exists(db_path) else DB_PATH
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(db_to_use)), "sleep_reports")
        os.makedirs(reports_dir, exist_ok=True)
        report = sleep_consolidation.build_report(db_to_use)
        latest_file = os.path.join(reports_dir, "auto_sleep_latest.md")
        with open(latest_file, "w", encoding="utf-8") as f:
            f.write(report)
        return {"ok": True, "file": latest_file, "report": report}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def background_auto_sleep_loop(interval_sec: int = 900, stop: Optional[threading.Event] = None) -> None:
    stop = stop or threading.Event()
    while not stop.wait(interval_sec):
        trigger_auto_sleep()


def edges_graph(db_path: str) -> Dict[str, Any]:
    repo = DashboardRepo(db_path)
    rows = repo._rows("SELECT id, source, target, relation, file_hash, status FROM edges "
                      "WHERE status = 'active' OR status IS NULL")
    stdlib_set = {"os", "sys", "time", "json", "re", "math", "sqlite3", "collections", "typing", "pathlib",
                  "threading", "datetime", "hashlib", "io", "subprocess", "copy", "itertools", "functools",
                  "abc", "argparse", "shutil", "urllib", "ctypes", "traceback", "logging", "asyncio",
                  "dataclasses", "inspect", "random", "enum", "uuid", "tempfile", "contextlib", "signal"}
    nodes: Dict[str, Dict[str, Any]] = {}
    edges = []

    def label(nid: str) -> str:
        return nid.replace("\\", "/").split("/")[-1] if ("/" in nid or "\\" in nid) else nid

    def subsystem(nid: str, cat: str) -> str:
        p = nid.replace("\\", "/")
        if cat != "internal":
            return cat
        for key in ("daemon", "hook", "cli", "proxy", "sleep", "dashboard", "core", "eval", "tests"):
            if f"/{key}/" in f"/{p}/" or p.startswith(f"{key}/"):
                return key
        return "internal"

    for r in rows:
        for nid in (r["source"], r["target"]):
            if nid not in nodes:
                cat = "internal" if ("/" in nid or "\\" in nid or nid.startswith("genesis")) else (
                    "stdlib" if nid.split(".")[0] in stdlib_set else "external")
                nodes[nid] = {"id": nid, "label": label(nid), "category": cat, "subsystem": subsystem(nid, cat),
                              "in_degree": 0, "out_degree": 0}
        nodes[r["source"]]["out_degree"] += 1
        nodes[r["target"]]["in_degree"] += 1
        edges.append({"id": r["id"], "source": r["source"], "target": r["target"],
                      "relation": r["relation"] or "depends_on"})
    for n in nodes.values():
        n["degree"] = n["in_degree"] + n["out_degree"]
    return {"count": len(edges), "nodes_count": len(nodes), "nodes": list(nodes.values()), "edges": edges}


# --------------------------------------------------------------------------- HTTP
class TelemetryHandler(http.server.BaseHTTPRequestHandler):
    db_path = DEFAULT_DB
    protocol_version = "HTTP/1.1"
    server_version = "GenesisMissionControl/" + VERSION

    # -- helpers -------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data: Any, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _active_db(self) -> str:
        return self.db_path if os.path.exists(self.db_path) else DB_PATH

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- GET -----------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: C901 - flat router on purpose (stdlib, zero deps)
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        db = self._active_db()
        repo = DashboardRepo(db)
        try:
            if path == "/health":
                return self._json({"status": "ok", "uptime_s": round(time.time() - SERVER_START_TIME, 1),
                                   "version": VERSION})
            if path == "/api/overview":
                return self._json(build_overview(db))
            if path == "/api/stream":
                return self._stream(db)
            if path == "/api/snapshot":
                data = snapshot(db)
                if data.get("uptime_s", 0) <= 0.5:
                    data["uptime_s"] = round(time.time() - SERVER_START_TIME, 1)
                data["license"] = collect_license()
                return self._json(data)
            if path == "/api/license":
                return self._json(collect_license())
            if path == "/api/engrams":
                return self._json(repo.engrams(q=qs.get("q", ""), kind=qs.get("kind", ""),
                                               project=qs.get("project", ""), status=qs.get("status", ""),
                                               limit=int(qs.get("limit", 60)), offset=int(qs.get("offset", 0))))
            m = re.match(r"^/api/engram/(\d{1,12})$", path)
            if m:
                row = repo.engram(int(m.group(1)))
                return self._json(row) if row else self._error(404, "engram not found")
            if path == "/api/graph":
                return self._json(repo.memory_graph(limit=int(qs.get("limit", 400))))
            if path == "/api/skills":
                return self._json({"items": repo.skills()})
            if path == "/api/conflicts":
                return self._json({"items": repo.conflicts(qs.get("status", ""))})
            if path == "/api/thread":
                return self._json(repo.thread())
            if path == "/api/dialogue":
                return self._json({"items": repo.dialogue(int(qs.get("limit", 12)))})
            if path == "/api/timeline":
                return self._json({"buckets": repo.timeline(int(qs.get("buckets", 48)))})
            if path == "/api/edges":
                return self._json(edges_graph(db))
            if path == "/api/clients":
                return self._json({"items": collect_clients()})
            if path == "/api/spool":
                return self._json(collect_spool(db))
            if path == "/api/ledger":
                sessions = read_ledger(db)
                return self._json({"sessions": sessions, "count": len(sessions)})
            if path == "/api/proxy_telemetry":
                return self._json(collect_proxy())
            if path == "/api/pricing":
                data = _proxy_get("/v1/pricing" + ("?" + parsed.query if parsed.query else ""), timeout=3.0)
                if data is None:
                    try:
                        from genesis_memory.proxy.pricing_engine import ModelPricingEngine
                        eng = ModelPricingEngine(auto_fetch=False)
                        q = (qs.get("q") or qs.get("search") or "").lower()
                        models = [{"id": k, **v} for k, v in eng._catalog.items() if q in k.lower()][:200]
                        data = {"total_models": len(eng._catalog), "models": models, "source": eng.source}
                    except Exception as exc:
                        data = {"error": str(exc), "total_models": 0, "models": []}
                return self._json(data)
            if path == "/api/sleep":
                return self._json(trigger_auto_sleep(db))
            if path in ("/llms.txt", "/openapi.json"):
                name = "llms.txt" if path == "/llms.txt" else "openapi.json"
                content = _read_contract(name)
                if path == "/openapi.json":
                    json.loads(content.decode("utf-8"))
                return self._send(200, content, "text/plain; charset=utf-8" if path == "/llms.txt"
                                  else "application/json; charset=utf-8")
            if path == "/" or path.startswith("/dashboard"):
                return self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            if path.startswith("/static/"):
                target = (STATIC_DIR / path[len("/static/"):]).resolve()
                if STATIC_DIR.resolve() in target.parents and target.is_file():
                    ctype = {"css": "text/css", "js": "application/javascript", "svg": "image/svg+xml",
                             "png": "image/png", "woff2": "font/woff2"}.get(target.suffix.lstrip("."), "application/octet-stream")
                    return self._send(200, target.read_bytes(), ctype)
            return self._error(404, "not found")
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as exc:
            return self._error(500, f"{type(exc).__name__}: {exc}")

    def _stream(self, db: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        interval = STREAM_INTERVAL_S
        try:
            while True:
                payload = json.dumps(build_overview(db), ensure_ascii=False, default=str)
                self.wfile.write(f"event: overview\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    # -- POST ----------------------------------------------------------------
    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_json_body()
        db = self._active_db()
        try:
            if path == "/api/shield":
                text = str(body.get("text", ""))[:20000]
                clean, report = privacy_shield.redact(text)
                tokens = re.findall(r"[A-Za-z0-9_\-+/=\.]{8,}", text)[:200]
                entropies = [{"token_len": len(t), "entropy_bits": round(privacy_shield.shannon_entropy(t), 3),
                              "flagged": privacy_shield.is_high_entropy_token(t)} for t in tokens]
                return self._json({"clean": clean, "report": report.to_dict(),
                                   "findings": privacy_shield.explain(text), "entropies": entropies,
                                   "stored": False})
            m = re.match(r"^/api/conflicts/(\d{1,12})/resolve$", path)
            if m:
                action = str(body.get("action", "dismissed"))
                if action not in ("superseded", "kept_both", "dismissed"):
                    return self._error(400, "action must be superseded|kept_both|dismissed")
                winner = body.get("winner_id")
                store = Store(db)
                try:
                    res = store.resolve_conflict(int(m.group(1)), action=action,
                                                 winner_id=int(winner) if winner is not None else None)
                finally:
                    store.db.close()
                return self._json(res)
            if path == "/api/reinforce":
                eid = body.get("id")
                if eid is None or not _ID_RE.match(str(eid)):
                    return self._error(400, "id required")
                outcome = "failure" if str(body.get("outcome", "success")) == "failure" else "success"
                store = Store(db)
                try:
                    res = store.reinforce(int(eid), outcome=outcome, note=str(body.get("note", ""))[:500])
                finally:
                    store.db.close()
                return self._json(res)
            if path == "/api/sleep/now":
                store = Store(db)
                try:
                    res = store.sleep_now(deep=bool(body.get("deep", False)))
                finally:
                    store.db.close()
                return self._json(res)
            return self._error(404, "not found")
        except ValueError as exc:
            return self._error(400, str(exc))
        except Exception as exc:
            return self._error(500, f"{type(exc).__name__}: {exc}")

    def log_message(self, format: str, *args: Any) -> None:  # silence request spam
        pass


class DashboardServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(port: int = DEFAULT_PORT, db_path: Optional[str] = None,
                host: str = "127.0.0.1") -> DashboardServer:
    if db_path:
        TelemetryHandler.db_path = db_path
    return DashboardServer((host, port), TelemetryHandler)


def run_server(port: int = DEFAULT_PORT, db_path: Optional[str] = None, auto_sleep: bool = True,
               open_browser: bool = False) -> None:
    server = make_server(port, db_path)
    stop = threading.Event()
    if auto_sleep:
        threading.Thread(target=background_auto_sleep_loop, args=(900, stop), daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"\U0001F6F0  GENESIS Mission Control  ->  {url}")
    print(f"\U0001F4CA Live DB: {TelemetryHandler.db_path}")
    if auto_sleep:
        print("\U0001F319 Auto-sleep consolidation worker active (15m interval)")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Mission Control.")
    finally:
        stop.set()
        server.server_close()


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="genesis-dashboard", description="GENESIS Mission Control telemetry server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to bind (default: {DEFAULT_PORT})")
    parser.add_argument("--db", type=str, default=DEFAULT_DB, help="Path to SQLite memory.db")
    parser.add_argument("--no-sleep", action="store_true", help="Disable the background auto-sleep worker")
    parser.add_argument("--open", action="store_true", help="Open the dashboard in your browser")
    args = parser.parse_args(argv)
    run_server(port=args.port, db_path=args.db, auto_sleep=not args.no_sleep, open_browser=args.open)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
GENESIS MCP Cognitive Daemon v0.4 — episodic project memory over stdio MCP.
Stdlib only (no numpy/torch/orm — hard ban: RSS budget <100MB). SQLite FTS5 retrieval
with utility scoring (recency + accesses + curator utility), audited eviction, RSS self-cap.
DB: $GENESIS_DAEMON_DB or ./genesis_memory.db next to this file. Schema versioned via
PRAGMA user_version + MIGRATIONS (never loses user data on upgrade).
Ops CLI: --export/--import (JSONL backup, data sovereignty), --snapshot (dashboard feed).
Logs: rotating file log next to DB (1MB x3) or $GENESIS_DAEMON_LOG ("off" disables).
Protocol: newline-delimited JSON-RPC on stdio (MCP): initialize, tools/list, tools/call, ping.
Tools (exactly 4): remember, recall, forget, status.
Token estimates are len(chars)//4 heuristics, ALWAYS labeled estimate (never tokenizer counts).
"""

import ctypes
import json
import os
import re
import sqlite3
import sys
import time

# Ensure package root is always in sys.path when invoked directly as a standalone script
_pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

# NOTE (RSS discipline): argparse/logging import ONLY inside main()/setup_logging().
# The stdio server path must stay import-lean; every top-level import costs resident MB.

EPISODES_MAX = int(os.environ.get("GENESIS_EPISODES_MAX", "10000"))
SNIPPET_WORDS = 40
RSS_ALERT_MB = 90.0

# v0.2 query builder (fixes EXP108 finding: sentence-queries vs FTS5 AND-semantics
# returned 0 hits in 48/48 live recalls). Content-term extraction + OR + prefix.
QUERY_MAX_TERMS = 10


def build_fts_query(text):
    """Natural sentence -> FTS5 OR-of-prefixes over extracted terms.

    Extracts Unicode word tokens (length >= 2), deduplicates, and caps at QUERY_MAX_TERMS.
    Each term becomes a prefix query (`"term"*`). SQLite FTS5 BM25 handles ranking
    mathematically via term frequency and inverse document frequency (IDF),
    without requiring any hardcoded language dictionaries or stopword lists.
    """
    seen, terms = set(), []
    for tok in re.findall(r"[\w]{2,}", text.lower()):
        if tok not in seen:
            seen.add(tok)
            terms.append(f'"{tok}"*')
            if len(terms) >= QUERY_MAX_TERMS:
                break
    return " OR ".join(terms)


STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "use", "using",
    "set", "get", "add", "run", "not", "all", "are", "was", "has", "have", "been",
    "will", "would", "can", "could", "should", "about", "which", "when", "where"
}


SECRET_PATTERNS = [
    # Full PEM blocks first (DOTALL): body must never survive redaction (R2).
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
               re.DOTALL),
    # Unclosed marker fallback (fail-closed): a BEGIN without END still guards
    # everything after it — trailing benign text loss is accepted over a leak.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*", re.DOTALL),
    re.compile(r"\b(sk-[a-zA-Z0-9_-]{20,})\b"),
    re.compile(r"\b(AIza[0-9A-Za-z-_]{30,})\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{30,})\b"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"\bBearer\s+[a-zA-Z0-9_\-\.]{25,}\b"),
]


def scan_secrets(text):
    """Check text for API keys, tokens, and private keys. Pure regex (zero tokens/cost)."""
    if not text or not isinstance(text, str):
        return False
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            return True
    return False


def redact_secrets(text):
    """Check text for API keys, tokens, and private keys and redact them."""
    if not text or not isinstance(text, str):
        return ""
    result = text
    for pat in SECRET_PATTERNS:
        result = pat.sub("[REDACTED_API_KEY]", result)
    return result


DB_PATH = os.environ.get(
    "GENESIS_DAEMON_DB",
    os.path.expanduser("~/.genesis/memory.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes(
  id INTEGER PRIMARY KEY, ts REAL, project TEXT, kind TEXT, text TEXT,
  utility REAL DEFAULT 1.0, accesses INTEGER DEFAULT 0, updated REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
  text, content='episodes', content_rowid='id', tokenize='porter');
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
  INSERT INTO episodes_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
  INSERT INTO episodes_fts(episodes_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
  INSERT INTO episodes_fts(episodes_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO episodes_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TABLE IF NOT EXISTS counters(
  name TEXT PRIMARY KEY, count INTEGER DEFAULT 0);
"""

# v0.5: versioned schema. Baseline above IS version 1. Future upgrades append
# {new_version: [sql, ...]} here; migrate() applies pending ones in order.
# RULE: migrations only ever ADD (tables/columns/indexes); never drop/alter user data.
SCHEMA_VERSION = 9
MIGRATIONS = {
    1: [],  # baseline (episodes + fts + triggers), recorded for provenance
    2: [
        "CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, count INTEGER DEFAULT 0)",
        "INSERT OR IGNORE INTO counters(name, count) VALUES ('remember', 0), ('recall', 0), ('forget', 0), ('status', 0)",
    ],
    3: [
        "ALTER TABLE episodes ADD COLUMN status TEXT DEFAULT 'active'",
        "ALTER TABLE episodes ADD COLUMN superseded_by INTEGER",
        "CREATE INDEX IF NOT EXISTS idx_episodes_id_status ON episodes(id, status)",
        "INSERT OR IGNORE INTO counters(name, count) VALUES ('invalidate', 0)",
    ],
    4: [
        "CREATE TABLE IF NOT EXISTS conflicts(id INTEGER PRIMARY KEY, ts REAL, new_id INTEGER REFERENCES episodes(id), conflicting_id INTEGER REFERENCES episodes(id), similarity REAL, status TEXT DEFAULT 'pending', updated REAL)",
        "CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status)",
        "INSERT OR IGNORE INTO counters(name, count) VALUES ('conflicts_detected', 0), ('secrets_blocked', 0), ('vetoes_resolved', 0)",
    ],
    5: [
        "CREATE TABLE IF NOT EXISTS edges(id INTEGER PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL, relation TEXT DEFAULT 'depends_on', file_hash TEXT, status TEXT DEFAULT 'active', ts REAL, updated REAL)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_src_tgt_rel ON edges(source, target, relation)",
        "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source, status)",
        "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target, status)",
        "INSERT OR IGNORE INTO counters(name, count) VALUES ('edges_extracted', 0), ('edges_invalidated', 0), ('vetoes_applied', 0), ('vetoes_dismissed', 0)",
    ],
    6: [
        "CREATE TABLE IF NOT EXISTS active_thread(id INTEGER PRIMARY KEY CHECK (id = 1), updated_at REAL, client TEXT, session_id TEXT, topic TEXT, summary TEXT, recent_files TEXT, pending_focus TEXT)",
        "INSERT OR IGNORE INTO counters(name, count) VALUES ('thread_updates', 0)",
    ],
    7: [
        "CREATE TABLE IF NOT EXISTS dialogue_buffer(session_id TEXT PRIMARY KEY, client TEXT, updated_at REAL, user_prompt TEXT, assistant_summary TEXT, salient_terms TEXT)",
        "CREATE INDEX IF NOT EXISTS idx_dialogue_updated ON dialogue_buffer(updated_at)",
        "INSERT OR IGNORE INTO counters(name, count) VALUES ('dialogue_updates', 0)",
    ],
    8: [
        "ALTER TABLE episodes ADD COLUMN stability REAL DEFAULT 7.0",
        "ALTER TABLE episodes ADD COLUMN reinforcements INTEGER DEFAULT 0",
        "ALTER TABLE episodes ADD COLUMN last_reinforced REAL",
        "CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY, name TEXT NOT NULL, trigger_patterns TEXT, preconditions TEXT, action_recipe TEXT NOT NULL, invariants TEXT, confidence REAL DEFAULT 1.0, success_count INTEGER DEFAULT 0, status TEXT DEFAULT 'active', created_at REAL, updated_at REAL)",
        "CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status)",
        "INSERT OR IGNORE INTO counters(name, count) VALUES ('reinforce', 0), ('synthesize_skill', 0), ('skill_recall', 0), ('sleep_cycles', 0)",
    ],
    9: [
        "ALTER TABLE episodes ADD COLUMN model_source TEXT",
        "INSERT OR IGNORE INTO counters(name, count) VALUES ('challenge_rule', 0)",
    ],
}


def migrate(db):
    """Bring an SQLite handle to SCHEMA_VERSION without data loss. Idempotent."""
    cur = db.execute("PRAGMA user_version").fetchone()[0]
    if cur == 0 and db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            " AND name='episodes'").fetchone()[0]:
        cur = 1  # pre-versioning database already at baseline shape
    db.executescript(SCHEMA)
    for ver in range(cur + 1, SCHEMA_VERSION + 1):
        for stmt in MIGRATIONS.get(ver, []):
            try:
                db.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    db.commit()
    return SCHEMA_VERSION


def setup_logging(db_path):
    """Rotating file log next to the DB. Stdout stays pure JSON-RPC (critical)."""
    import logging
    import logging.handlers
    logger = logging.getLogger("genesis_daemon")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    target = os.environ.get("GENESIS_DAEMON_LOG", "")
    if target.lower() == "off":
        logger.addHandler(logging.NullHandler())
        return logger
    path = target or os.path.join(os.path.dirname(os.path.abspath(db_path)), "daemon.log")
    try:
        h = logging.handlers.RotatingFileHandler(path, maxBytes=1048576, backupCount=3,
                                                 encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(h)
    except Exception:
        logger.addHandler(logging.NullHandler())
    return logger

TOOLS = [
    {"name": "remember",
     "description": "Store one episodic project memory (fact, decision, or outcome). Returns id. Can supersede previous memory.",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"},
                                    "kind": {"type": "string", "enum": ["fact", "decision", "outcome"]},
                                    "project": {"type": "string"},
                                    "utility": {"type": "number"},
                                    "supersedes_id": {"type": "integer"}},
                     "required": ["text"]}},
    {"name": "recall",
     "description": "Retrieve relevant active memories (snippets, default 40 words each). Never silently truncates: reports budget use.",
     "inputSchema": {"type": "object",
                     "properties": {"query": {"type": "string"},
                                    "project": {"type": "string"},
                                    "limit": {"type": "integer", "default": 5},
                                    "max_tokens_estimate": {"type": "integer", "default": 800},
                                    "snippet_words": {"type": "integer", "default": 40}},
                     "required": ["query"]}},
    {"name": "forget",
     "description": "Delete one memory by id (human veto / correction). Returns deleted count.",
     "inputSchema": {"type": "object",
                     "properties": {"id": {"type": "integer"}},
                     "required": ["id"]}},
    {"name": "invalidate",
     "description": "Mark a memory as invalidated or superseded without deletion (MESI coherence). Preserves causal audit trace.",
     "inputSchema": {"type": "object",
                     "properties": {"id": {"type": "integer"},
                                    "superseded_by": {"type": "integer"}},
                     "required": ["id"]}},
    {"name": "resolve_conflict",
     "description": "Resolve a staged concurrent conflict from the Sleep Report Veto Queue.",
     "inputSchema": {"type": "object",
                     "properties": {"conflict_id": {"type": "integer"},
                                    "action": {"type": "string", "enum": ["superseded", "kept_both", "dismissed"]},
                                    "winner_id": {"type": "integer"}},
                     "required": ["conflict_id", "action"]}},
    {"name": "get_dependencies",
     "description": "Retrieve AST-extracted code dependencies (graph closure) for a module or file path.",
     "inputSchema": {"type": "object",
                     "properties": {"source": {"type": "string"},
                                    "depth": {"type": "integer", "default": 1}},
                     "required": ["source"]}},
    {"name": "attest_closure",
     "description": "Audit and attest working context completeness against active deterministic AST dependency closure.",
     "inputSchema": {"type": "object",
                     "properties": {"entity": {"type": "string"},
                                    "context_text": {"type": "string"},
                                    "depth": {"type": "integer", "default": 1},
                                    "threshold": {"type": "number", "default": 0.40}},
                     "required": ["entity", "context_text"]}},
    {"name": "genesis_log",
     "description": "Dereference and read full spooled command output by id. Supports grep keyword search and pagination.",
     "inputSchema": {"type": "object",
                     "properties": {"id": {"type": "string", "description": "8-character spool id from [ctx:log/<id>]"},
                                    "grep": {"type": "string", "description": "Optional keyword or regex to filter matching lines"},
                                    "lines": {"type": "integer", "default": 100, "description": "Max lines to return"},
                                    "offset": {"type": "integer", "default": 0, "description": "Line offset for pagination"}},
                     "required": ["id"]}},
    {"name": "status",
     "description": "Daemon health: counts, DB bytes, RSS MB (self-measured), uptime, call stats, active vs superseded.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "thread_update",
     "description": "Update the unified cross-client active working thread so all AI clients share seamless context.",
     "inputSchema": {"type": "object",
                     "properties": {"topic": {"type": "string"},
                                    "summary": {"type": "string"},
                                    "recent_files": {"type": "string"},
                                    "pending_focus": {"type": "string"},
                                    "client": {"type": "string"}},
                     "required": ["topic", "summary"]}},
    {"name": "thread_get",
     "description": "Read the unified cross-client active working thread.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "dialogue_update",
     "description": "Record 1-turn cross-session dialogue exchange into dialogue_buffer (sanitized & bounded).",
     "inputSchema": {"type": "object",
                     "properties": {"session_id": {"type": "string"},
                                    "client": {"type": "string"},
                                    "user_prompt": {"type": "string"},
                                    "assistant_summary": {"type": "string"},
                                    "salient_terms": {"type": "string"}},
                     "required": ["user_prompt", "assistant_summary"]}},
    {"name": "dialogue_get",
     "description": "Retrieve the latest 1-turn cross-session dialogue exchange (TTL 1800s).",
     "inputSchema": {"type": "object",
                     "properties": {"session_id": {"type": "string"},
                                    "max_age_s": {"type": "number", "default": 1800}}}},
    {"name": "cross_client_resolve",
     "description": "Resolve a cross-client follow-up in ONE call: thread, fresh dialogue, episodic recall, then local client storage. Returns provenance-labeled answer or resolved:false (say unknown, never invent).",
     "inputSchema": {"type": "object",
                     "properties": {"question": {"type": "string"},
                                    "max_chars": {"type": "integer", "default": 1200}},
                     "required": ["question"]}},
    {"name": "reinforce",
     "description": "Reinforce (+1) or penalize (-1) a memory engram based on task outcome and feedback (Hebbian learning).",
     "inputSchema": {"type": "object",
                     "properties": {"id": {"type": "integer"},
                                    "outcome": {"type": "string", "enum": ["success", "failure"], "default": "success"},
                                    "note": {"type": "string", "default": ""}},
                     "required": ["id"]}},
    {"name": "synthesize_skill",
     "description": "Persist a deterministic procedural skill / actionable recipe with trigger patterns into procedural memory.",
     "inputSchema": {"type": "object",
                     "properties": {"name": {"type": "string"},
                                    "action_recipe": {"type": "string"},
                                    "trigger_patterns": {"type": "array", "items": {"type": "string"}},
                                    "preconditions": {"type": "string", "default": ""},
                                    "invariants": {"type": "string", "default": ""},
                                    "confidence": {"type": "number", "default": 1.0}},
                     "required": ["name", "action_recipe"]}},
    {"name": "skill_recall",
     "description": "Recall procedural skills and actionable recipes matching a prompt, keyword, or error pattern.",
     "inputSchema": {"type": "object",
                     "properties": {"query": {"type": "string"},
                                    "min_confidence": {"type": "number", "default": 0.5},
                                    "limit": {"type": "integer", "default": 3}},
                     "required": ["query"]}},
    {"name": "sleep_now",
     "description": "Trigger an immediate biomimetic sleep consolidation cycle (Hebbian decay, skill extraction, Active Digest generation).",
     "inputSchema": {"type": "object",
                     "properties": {"deep": {"type": "boolean", "default": False}}}},
    {"name": "challenge_rule",
     "description": "Challenge a solidified golden rule with a better alternative. Registers a conflict for user review instead of silently complying with a possibly outdated rule.",
     "inputSchema": {"type": "object",
                     "properties": {"solidified_id": {"type": "integer", "description": "ID of the solidified engram to challenge"},
                                    "proposed_text": {"type": "string", "description": "The proposed better rule or approach"},
                                    "reason": {"type": "string", "description": "Why the new approach is better"}},
                     "required": ["solidified_id", "proposed_text"]}},
]

# One-tool gateway: the entire surface above behind a single schema, so the
# tool list sent every turn shrinks from ~13 schemas to 1.
# Mode via GENESIS_MCP_TOOL_MODE: "full" (default, current list) or "gateway"
# (only this tool is advertised; all ops still route). Default flips after
# dogfood; see Docs/Product/CONTINUITY_DOGFOOD.md discipline.
GATEWAY_TOOL_NAME = "genesis"
GATEWAY_OPS = ("help", "remember", "recall", "forget", "invalidate",
               "resolve_conflict", "get_dependencies", "attest_closure",
               "genesis_log", "status", "thread_update", "thread_get",
               "dialogue_update", "dialogue_get", "cross_client_resolve",
               "reinforce", "synthesize_skill", "skill_recall", "sleep_now",
               "challenge_rule")
GATEWAY_TOOL = {
    "name": GATEWAY_TOOL_NAME,
    "description": (
        "GENESIS memory gateway: every memory op through one tool, keeping "
        "per-turn context small. Op args pass through unchanged; "
        "{op:'help'} lists op shapes. Ex: {op:'recall', query:'...'}."
    ),
    "inputSchema": {"type": "object",
                    "properties": {"op": {"type": "string",
                                          "enum": list(GATEWAY_OPS),
                                          "description": "Operation to route"}},
                    "required": ["op"],
                    "additionalProperties": True},
}


def visible_tools():
    """Tool list advertised to clients; gateway-only shrinks every-turn bytes."""
    if os.environ.get("GENESIS_MCP_TOOL_MODE", "full").lower() == "gateway":
        return [GATEWAY_TOOL]
    return TOOLS + [GATEWAY_TOOL]



def rss_mb():
    try:
        if sys.platform == "win32":
            from ctypes import wintypes

            class PMem(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD),
                            ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            p, m = ctypes.windll.kernel32.GetCurrentProcess(), PMem()
            m.cb = ctypes.sizeof(PMem)
            fn = ctypes.windll.psapi.GetProcessMemoryInfo
            fn.restype = wintypes.BOOL
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMem), wintypes.DWORD]
            if fn(p, ctypes.byref(m), m.cb):
                return round(m.WorkingSetSize / 1048576.0, 1)
            return None
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        return None


class Store:
    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(path)
        migrate(self.db)  # versioned schema, never loses user data
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA cache_size=-1000")  # ~4MB page cache cap (RSS discipline;
        # tuned down from 16MB: recall stays sub-50ms on 10K-row scale per bench, RSS wins)
        self.calls = {
            "remember": 0, "recall": 0, "forget": 0, "invalidate": 0, "resolve_conflict": 0,
            "get_dependencies": 0, "attest_closure": 0, "status": 0, "hits": 0, "conflicts_detected": 0,
            "secrets_blocked": 0, "vetoes_resolved": 0, "vetoes_applied": 0, "vetoes_dismissed": 0,
            "edges_extracted": 0, "edges_invalidated": 0,
            "faults_triggered": 0, "faults_resolved": 0, "fault_caps_hit": 0, "attestations_passed": 0,
            "spools_dereferenced": 0
        }
        self.t0 = time.time()
        self._sync_base_counters()

    @property
    def schema_version(self):
        return self.db.execute("PRAGMA user_version").fetchone()[0]

    def _sync_base_counters(self):
        try:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, count INTEGER DEFAULT 0)"
            )
            for name in ("remember", "recall", "forget", "invalidate", "resolve_conflict", "get_dependencies",
                         "attest_closure", "status", "hits", "conflicts_detected", "secrets_blocked", "vetoes_resolved",
                         "vetoes_applied", "vetoes_dismissed", "edges_extracted", "edges_invalidated",
                         "faults_triggered", "faults_resolved", "fault_caps_hit", "attestations_passed",
                         "spools_created", "spools_dereferenced", "spooled_bytes", "dereferenced_bytes",
                         "spools_full_reads", "reinforce", "synthesize_skill", "skill_recall", "sleep_cycles",
                         "challenge_rule"):
                self.db.execute("INSERT OR IGNORE INTO counters(name, count) VALUES (?, 0)", (name,))
            # Synchronize baseline ground truth from existing database records
            ep_count = self.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            acc_sum = self.db.execute("SELECT COALESCE(SUM(accesses), 0) FROM episodes").fetchone()[0]
            hit_count = self.db.execute("SELECT COUNT(*) FROM episodes WHERE accesses > 0").fetchone()[0]
            self.db.execute("UPDATE counters SET count = MAX(count, ?) WHERE name = 'remember'", (ep_count,))
            self.db.execute("UPDATE counters SET count = MAX(count, ?) WHERE name = 'recall'", (acc_sum,))
            self.db.execute("UPDATE counters SET count = MAX(count, ?) WHERE name = 'hits'", (hit_count,))
            self.db.commit()
            rows = self.db.execute("SELECT name, count FROM counters").fetchall()
            for n, c in rows:
                self.calls[n] = max(self.calls.get(n, 0), c)

        except Exception:
            pass

    def _inc_counter(self, name, amount=1):
        self.calls[name] = self.calls.get(name, 0) + amount
        try:
            self.db.execute(
                "INSERT INTO counters(name, count) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET count = count + ?",
                (name, amount, amount)
            )
            self.db.commit()
        except Exception:
            pass

    def _get_counters(self):
        try:
            rows = self.db.execute("SELECT name, count FROM counters").fetchall()
            c = {name: cnt for name, cnt in rows}
            for k, v in self.calls.items():
                c[k] = max(c.get(k, 0), v)
            recalls = c.get("recall", 0)
            hits = c.get("hits", 0)
            c["hit_rate"] = round(hits / max(1, recalls), 4) if recalls > 0 else 0.0
            v_resolved = c.get("vetoes_resolved", 0)
            v_dismissed = c.get("vetoes_dismissed", 0)
            c["dismissed_rate"] = round(v_dismissed / max(1, v_resolved), 4) if v_resolved > 0 else 0.0
            return c
        except Exception:
            return dict(self.calls)

    def remember(self, text, kind="fact", project="default", utility=1.0, supersedes_id=None):
        if kind not in ("fact", "decision", "outcome"):
            raise ValueError("kind must be fact|decision|outcome")
        if scan_secrets(text):
            self._inc_counter("secrets_blocked")
            raise ValueError("Secret detected in payload: rejected by security invariant (Rule 31)")
        self._inc_counter("remember")
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO episodes(ts,project,kind,text,utility,accesses,updated,status,superseded_by)"
            " VALUES(?,?,?,?,?,0,?,'active',NULL)", (now, project, kind, text, float(utility), now))
        new_id = cur.lastrowid
        if supersedes_id is not None:
            self.db.execute(
                "UPDATE episodes SET status = 'superseded', superseded_by = ?, updated = ?"
                " WHERE id = ?", (new_id, now, int(supersedes_id)))
        elif kind in ("decision", "fact"):
            # Multi-window conflict detection: check for active collision in the same project
            # Uses stopword filtering + BM25 score threshold (< -2.0) or multi-term non-stopword overlap (>= 2)
            # Prevents staging on single accidental lexical matches (veto fatigue)
            try:
                terms = [tok for tok in re.findall(r"[\w]{3,}", text.lower()) if tok not in STOPWORDS][:6]
                if terms:
                    fts_q = " OR ".join(f'"{t}"*' for t in terms)
                    chk_sql = (
                        "SELECT e.id, e.text, bm25(episodes_fts) FROM episodes_fts JOIN episodes e "
                        "ON e.id = episodes_fts.rowid "
                        "WHERE episodes_fts MATCH ? AND e.id != ? AND e.project = ? "
                        "AND e.kind = ? AND (e.status = 'active' OR e.status IS NULL) "
                        "ORDER BY bm25(episodes_fts) ASC LIMIT 1"
                    )
                    row = self.db.execute(chk_sql, (fts_q, new_id, project, kind)).fetchone()
                    if row:
                        conflicting_id, conf_text, bm_score = row
                        conf_words = set(re.findall(r"[\w]{3,}", conf_text.lower())) - STOPWORDS
                        overlap = [t for t in terms if t in conf_words]
                        if float(bm_score) < -2.0 or len(overlap) >= 2:
                            sim_score = max(round(-float(bm_score), 2), round(len(overlap) / len(terms), 2))
                            self.db.execute(
                                "INSERT INTO conflicts(ts, new_id, conflicting_id, similarity, status, updated) "
                                "VALUES(?, ?, ?, ?, 'pending', ?)",
                                (now, new_id, conflicting_id, sim_score, now)
                            )
                            self._inc_counter("conflicts_detected")
            except Exception:
                pass
        self.db.execute(
            "DELETE FROM episodes WHERE id IN (SELECT id FROM episodes"
            " ORDER BY utility + accesses DESC, updated DESC LIMIT -1 OFFSET ?)",
            (EPISODES_MAX,))
        self.db.commit()
        res = {"id": new_id}
        if supersedes_id is not None:
            res["supersedes_id"] = int(supersedes_id)
        return res

    def resolve_conflict(self, conflict_id, action="dismissed", winner_id=None):
        self._inc_counter("vetoes_resolved")
        now = time.time()
        c_row = self.db.execute(
            "SELECT new_id, conflicting_id FROM conflicts WHERE id = ?", (int(conflict_id),)
        ).fetchone()
        if not c_row:
            raise ValueError(f"Conflict #{conflict_id} not found")
        new_id, old_id = c_row
        if action == "superseded":
            self._inc_counter("vetoes_applied")
            target_winner = int(winner_id) if winner_id is not None else new_id
            target_loser = old_id if target_winner == new_id else new_id
            self.invalidate(target_loser, superseded_by=target_winner)
        else:
            self._inc_counter("vetoes_dismissed")
        self.db.execute(
            "UPDATE conflicts SET status = 'resolved', updated = ? WHERE id = ?",
            (now, int(conflict_id))
        )
        self.db.commit()
        return {"conflict_id": int(conflict_id), "status": "resolved", "action": action, "winner_id": winner_id}

    def challenge_rule(self, solidified_id, proposed_text, reason=""):
        """Challenge a solidified golden rule with a proposed better alternative.

        Instead of silently complying with a potentially outdated solidified rule,
        this registers a formal conflict for user review. The solidified rule is NOT
        automatically replaced — the user decides via resolve_conflict.
        """
        self._inc_counter("challenge_rule")
        now = time.time()

        # 1. Validate target is actually solidified
        row = self.db.execute(
            "SELECT id, text, status, utility, reinforcements FROM episodes WHERE id = ?",
            (int(solidified_id),)
        ).fetchone()
        if not row:
            raise ValueError(f"Episode #{solidified_id} not found")
        eid, old_text, status, old_util, old_reinf = row
        if status != "solidified":
            return {
                "challenged": False,
                "reason": f"Episode #{solidified_id} is '{status}', not 'solidified'. "
                          "Only solidified golden rules can be challenged.",
                "solidified_id": eid,
            }

        # 2. Check for secret content in proposed text
        if scan_secrets(proposed_text):
            self._inc_counter("secrets_blocked")
            raise ValueError("Secret detected in proposed text: rejected by security invariant")

        # 3. Store the proposed alternative as a pending_challenge episode
        challenge_text = proposed_text.strip()
        if reason:
            challenge_text += f" [challenge reason: {reason.strip()}]"
        cur = self.db.execute(
            "INSERT INTO episodes(ts, project, kind, text, utility, accesses, updated, status) "
            "VALUES(?, 'default', 'decision', ?, ?, 0, ?, 'pending_challenge')",
            (now, challenge_text, float(old_util), now)
        )
        new_id = cur.lastrowid

        # 4. Register formal conflict between solidified rule and challenger
        self.db.execute(
            "INSERT INTO conflicts(ts, new_id, conflicting_id, similarity, status, updated) "
            "VALUES(?, ?, ?, ?, 'challenge_pending', ?)",
            (now, new_id, eid, 1.0, now)
        )
        conflict_id = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        self._inc_counter("conflicts_detected")
        self.db.commit()

        return {
            "challenged": True,
            "conflict_id": conflict_id,
            "solidified_rule": {
                "id": eid,
                "text": old_text,
                "reinforcements": old_reinf or 0,
            },
            "proposed_rule": {
                "id": new_id,
                "text": proposed_text.strip(),
                "reason": reason,
            },
            "action_required": (
                f"⚠️ CONFLICT: Solidified rule #{eid} is being challenged. "
                f"Resolve with: resolve_conflict(conflict_id={conflict_id}, "
                f"action='superseded', winner_id=<winning_id>) or "
                f"resolve_conflict(conflict_id={conflict_id}, action='dismissed')"
            ),
        }

    def get_dependencies(self, source, depth=1):
        """Retrieve AST-extracted code dependencies (graph closure) for a module or file path."""
        self._inc_counter("get_dependencies")
        rows = self.db.execute(
            "SELECT target, relation, file_hash, updated FROM edges "
            "WHERE source = ? AND (status = 'active' OR status IS NULL)", (source,)
        ).fetchall()
        return {
            "source": source,
            "dependencies": [{"target": r[0], "relation": r[1], "file_hash": r[2], "updated": r[3]} for r in rows],
            "count": len(rows)
        }

    def attest_closure(self, entity, context_text, depth=1, threshold=0.40):
        """Audit and attest working context completeness against active deterministic AST dependency closure."""
        self._inc_counter("attest_closure")
        from genesis_memory.core.verification_trap import attest_closure as trap_attest
        return trap_attest(self.db, [entity], context_text, threshold=float(threshold), max_depth=int(depth))

    def invalidate(self, mid, superseded_by=None):

        self._inc_counter("invalidate")
        now = time.time()
        st = "superseded" if superseded_by is not None else "invalidated"
        cur = self.db.execute(
            "UPDATE episodes SET status = ?, superseded_by = ?, updated = ? WHERE id = ?",
            (st, int(superseded_by) if superseded_by is not None else None, now, int(mid)))
        self.db.commit()
        return {"id": int(mid), "status": st, "superseded_by": superseded_by, "updated": cur.rowcount}

    def recall(self, query, project=None, limit=5, max_tokens_estimate=800,
               snippet_words=SNIPPET_WORDS, fallback=True):
        # v0.3: BM25 ranking + Hebbian synaptic weight & Ebbinghaus decay
        self._inc_counter("recall")
        fts_q = build_fts_query(query)
        now = time.time()
        scored = []
        rows = []
        if fts_q:
            sql = ("SELECT e.id, e.project, e.kind, e.text, e.utility, e.accesses, e.updated,"
                   " bm25(episodes_fts), COALESCE(e.reinforcements, 0), COALESCE(e.stability, 7.0), e.status"
                   " FROM episodes_fts JOIN episodes e"
                   " ON e.id = episodes_fts.rowid WHERE episodes_fts MATCH ?"
                   " AND (e.status IN ('active', 'solidified') OR e.status IS NULL)")
            args = [fts_q]
            if project:
                sql += " AND e.project = ?"
                args.append(project)
            rows = self.db.execute(sql, args).fetchall()
            from genesis_memory.core.hebbian_engine import compute_retention, compute_synaptic_weight
            for rid, proj, kind, text, util, acc, upd, bm, reinf, stab, st in rows:
                age_s = max(0.0, now - (upd or now))
                ret = compute_retention(age_s, stab)
                w = compute_synaptic_weight(util, ret, acc, reinf)
                score = -float(bm) + 1.5 * w + (0.8 if st == 'solidified' else 0.0)
                scored.append((score, rid, proj, kind, text, util, acc))

        # Working memory fallback: if enabled and (FTS returned 0 hits or query was empty)
        if not scored and fallback:
            cond = "WHERE (status IN ('active', 'solidified') OR status IS NULL)"
            args = []
            if project:
                cond += " AND project = ?"
                args.append(project)
            rows = self.db.execute(
                f"SELECT id, project, kind, text, utility, accesses, updated, "
                f"COALESCE(reinforcements, 0), COALESCE(stability, 7.0), status FROM episodes {cond}"
                " ORDER BY utility DESC, accesses DESC, updated DESC LIMIT 50", args).fetchall()
            from genesis_memory.core.hebbian_engine import compute_retention, compute_synaptic_weight
            for rid, proj, kind, text, util, acc, upd, reinf, stab, st in rows:
                age_s = max(0.0, now - (upd or now))
                ret = compute_retention(age_s, stab)
                w = compute_synaptic_weight(util, ret, acc, reinf)
                score = 1.5 * w + (0.8 if st == 'solidified' else 0.0)
                scored.append((score, rid, proj, kind, text, util, acc))
        scored.sort(reverse=True)
        out, used, ids = [], 0, []
        for _, rid, proj, kind, text, util, acc in scored[: max(1, limit)]:
            snippet = " ".join(text.split()[: max(1, int(snippet_words))])
            est = max(1, len(snippet) // 4)  # heuristic estimate, NOT a tokenizer count
            if used + est > max_tokens_estimate and out:
                break
            used += est
            ids.append(rid)
            out.append({"id": rid, "project": proj, "kind": kind, "text": text, "snippet": snippet,
                        "tokens_estimate": est, "utility": util})
        if ids:
            self._inc_counter("hits")
            self.db.execute(
                f"UPDATE episodes SET accesses = accesses + 1, updated = {now}"
                f" WHERE id IN ({','.join('?' for _ in ids)})", ids)
            self.db.commit()
            try:
                from genesis_memory.core.hebbian_engine import HebbianEngine
                HebbianEngine(self.db).record_co_activation(ids, now=now)
            except Exception:
                pass
        return {"results": out, "tokens_estimate_total": used,
                "candidates_considered": len(rows), "budget_tokens_estimate": max_tokens_estimate}

    def reinforce(self, id, outcome="success", note=""):
        self._inc_counter("reinforce")
        from genesis_memory.core.hebbian_engine import HebbianEngine
        hebbian = HebbianEngine(self.db)
        return hebbian.reinforce_memory(int(id), outcome=outcome, note=note)

    def synthesize_skill(self, name, action_recipe, trigger_patterns=None, preconditions="", invariants="", confidence=1.0, skill_id=None):
        self._inc_counter("synthesize_skill")
        from genesis_memory.core.skill_synthesizer import SkillSynthesizer
        syn = SkillSynthesizer(self.db)
        trigs = trigger_patterns if isinstance(trigger_patterns, list) else [t.strip() for t in (trigger_patterns or "").split(",") if t.strip()]
        return syn.create_or_update_skill(
            name=name,
            action_recipe=action_recipe,
            trigger_patterns=trigs,
            skill_id=skill_id,
            preconditions=preconditions,
            invariants=invariants,
            confidence=float(confidence),
        )

    def skill_recall(self, query, min_confidence=0.5, limit=3):
        self._inc_counter("skill_recall")
        from genesis_memory.core.skill_synthesizer import SkillSynthesizer
        syn = SkillSynthesizer(self.db)
        return {"skills": syn.match_skills(query, min_confidence=float(min_confidence), limit=int(limit))}

    def sleep_now(self, deep=False):
        self._inc_counter("sleep_cycles")
        from genesis_memory.sleep.sleep_daemon import run_sleep_cycle
        return run_sleep_cycle(self.path, deep=bool(deep))

    def forget(self, mid):
        self._inc_counter("forget")
        cur = self.db.execute("DELETE FROM episodes WHERE id = ?", (mid,))
        self.db.commit()
        return {"deleted": cur.rowcount}

    def status(self):
        self._inc_counter("status")
        n_active = self.db.execute(
            "SELECT COUNT(*) FROM episodes WHERE status = 'active' OR status IS NULL").fetchone()[0]
        n_total = self.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        n_superseded = self.db.execute(
            "SELECT COUNT(*) FROM episodes WHERE status = 'superseded'").fetchone()[0]
        n_invalidated = self.db.execute(
            "SELECT COUNT(*) FROM episodes WHERE status = 'invalidated'").fetchone()[0]
        try:
            n_solidified = self.db.execute(
                "SELECT COUNT(*) FROM episodes WHERE status = 'solidified'").fetchone()[0]
        except Exception:
            n_solidified = 0
        try:
            n_dormant = self.db.execute(
                "SELECT COUNT(*) FROM episodes WHERE status = 'dormant'").fetchone()[0]
        except Exception:
            n_dormant = 0
        try:
            n_skills_active = self.db.execute(
                "SELECT COUNT(*) FROM skills WHERE status = 'active'").fetchone()[0]
        except Exception:
            n_skills_active = 0
        try:
            n_conflicts_pending = self.db.execute(
                "SELECT COUNT(*) FROM conflicts WHERE status = 'pending'").fetchone()[0]
        except Exception:
            n_conflicts_pending = 0
        try:
            n_edges_active = self.db.execute(
                "SELECT COUNT(*) FROM edges WHERE status = 'active' OR status IS NULL").fetchone()[0]
        except Exception:
            n_edges_active = 0
        try:
            size = self.db.execute("PRAGMA page_count").fetchone()[0] * \
                self.db.execute("PRAGMA page_size").fetchone()[0]
        except Exception:
            size = None
        rss = rss_mb()
        counters = self._get_counters()
        return {"episodes": n_active, "episodes_total": n_total,
                "superseded": n_superseded, "invalidated": n_invalidated,
                "solidified": n_solidified, "dormant": n_dormant,
                "skills_active": n_skills_active,
                "conflicts_pending": n_conflicts_pending,
                "edges_active": n_edges_active,
                "db_bytes": size, "rss_mb": rss,
                "rss_alert": (rss is not None and rss > RSS_ALERT_MB),
                "uptime_s": round(time.time() - self.t0, 1),
                "calls": counters,
                "hit_rate": counters.get("hit_rate", 0.0),
                "episodes_max": EPISODES_MAX}

    def read_spool(self, id, grep=None, offset=0, lines=100, full=False, reason=None, page=0):
        from genesis_memory.proxy.spool import SpoolEngine
        engine = SpoolEngine(store=self)
        return engine.read_spool(
            id, grep=grep, offset=offset, max_lines=lines, full=full, reason=reason, page=page
        )

    def set_thread(self, topic, summary, recent_files="", pending_focus="", client="unknown", session_id=""):
        self._inc_counter("thread_updates")
        now = time.time()
        if isinstance(recent_files, (list, tuple)):
            recent_files_str = ", ".join(str(f) for f in recent_files)
        else:
            recent_files_str = str(recent_files or "")
        # R2 storage boundary: thread fields are redacted like dialogue fields.
        # No truncation (singleton row is storage-bounded by overwrite).
        topic = redact_secrets(topic)
        summary = redact_secrets(summary)
        recent_files_str = redact_secrets(recent_files_str)
        pending_focus = redact_secrets(pending_focus)

        self.db.execute(
            """INSERT INTO active_thread(id, updated_at, client, session_id, topic, summary, recent_files, pending_focus)
               VALUES(1, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 updated_at=excluded.updated_at,
                 client=excluded.client,
                 session_id=excluded.session_id,
                 topic=excluded.topic,
                 summary=excluded.summary,
                 recent_files=excluded.recent_files,
                 pending_focus=excluded.pending_focus""",
            (now, client, session_id, topic, summary, recent_files_str, pending_focus)
        )
        self.db.commit()
        return {"status": "ok", "updated_at": now}

    def get_thread(self):
        try:
            row = self.db.execute(
                "SELECT updated_at, client, session_id, topic, summary, recent_files, pending_focus FROM active_thread WHERE id = 1"
            ).fetchone()
            if not row:
                return None
            return {
                "updated_at": row[0],
                "client": row[1],
                "session_id": row[2],
                "topic": row[3],
                "summary": row[4],
                "recent_files": row[5],
                "pending_focus": row[6],
            }
        except Exception:
            return None

    def set_dialogue(self, session_id, client, user_prompt, assistant_summary, salient_terms=""):
        self._inc_counter("dialogue_updates")
        now = time.time()
        # Strictly sanitize secrets per OpenCode security/privacy invariant
        clean_prompt = redact_secrets(user_prompt)[:250]
        clean_summary = redact_secrets(assistant_summary)[:250]
        if isinstance(salient_terms, (list, tuple)):
            salient_str = " ".join(str(t) for t in salient_terms)
        else:
            salient_str = str(salient_terms or "")
        salient_str = redact_secrets(salient_str)[:120]

        sid = str(session_id or "default")
        self.db.execute(
            """INSERT INTO dialogue_buffer(session_id, client, updated_at, user_prompt, assistant_summary, salient_terms)
               VALUES(?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 client=excluded.client,
                 updated_at=excluded.updated_at,
                 user_prompt=excluded.user_prompt,
                 assistant_summary=excluded.assistant_summary,
                 salient_terms=excluded.salient_terms""",
            (sid, client or "unknown", now, clean_prompt, clean_summary, salient_str)
        )
        self.db.commit()
        return {"status": "updated", "session_id": sid, "updated_at": now}

    def get_latest_dialogue(self, session_id=None, max_age_s=1800, fallback=True):
        now = time.time()
        try:
            row = None
            if session_id:
                row = self.db.execute(
                    "SELECT session_id, client, updated_at, user_prompt, assistant_summary, salient_terms FROM dialogue_buffer WHERE session_id = ?",
                    (str(session_id),)
                ).fetchone()
            if not row:
                if not fallback:
                    return None
                # Fallback to most recent dialogue across any session/client
                row = self.db.execute(
                    "SELECT session_id, client, updated_at, user_prompt, assistant_summary, salient_terms FROM dialogue_buffer ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            if not row:
                return None
            sid, client, updated_at, prompt, summary, salient = row
            age_s = max(0.0, now - float(updated_at or now))
            is_stale = age_s > max_age_s
            if is_stale and not fallback:
                return None
            return {
                "session_id": sid,
                "client": client,
                "updated_at": updated_at,
                "age_s": age_s,
                "stale": is_stale,
                "user_prompt": prompt,
                "assistant_summary": summary,
                "salient_terms": salient,
            }
        except Exception:
            return None

    def cross_client_resolve(self, question, max_chars=1200):
        """Server-side resolution ladder in ONE call (replaces forensic spelunking).

        Rung 1 thread → Rung 2 latest fresh dialogue turns → Rung 3 episodic
        recall → Rung 4 local OpenCode storage (last resort, best-effort).
        Every section carries its provenance; empty ladder returns
        resolved=False (caller must say it doesn't know — never invent).
        """
        sections = []
        resolved_rung = None

        def _add(rung, label, text):
            nonlocal resolved_rung
            text = (text or "").strip()
            if not text:
                return
            if resolved_rung is None:
                resolved_rung = rung
            sections.append(f"[{label}]\n{text}")

        # Rung 1: active thread.
        try:
            th = self.get_thread() or {}
            t_topic = (th.get("topic") or "").strip()
            t_summary = (th.get("summary") or "").strip()
            if t_topic or t_summary:
                _add(1, "thread",
                     f"Topic: {t_topic[:200]}\nSummary: {t_summary[:300]}".strip())
        except Exception:
            pass

        # Rung 2: latest fresh dialogue turns (up to 3).
        try:
            now = time.time()
            rows = self.db.execute(
                "SELECT session_id, client, updated_at, user_prompt, assistant_summary "
                "FROM dialogue_buffer ORDER BY updated_at DESC LIMIT 3"
            ).fetchall()
            for sid, cli, ts, up, asr in rows:
                try:
                    age = max(0.0, now - float(ts or now))
                except (ValueError, TypeError):
                    continue
                if age >= 1800 or not ((up or "").strip() or (asr or "").strip()):
                    continue
                _add(2, f"dialogue:{sid}",
                     f"User: {(up or '').strip()[:200]}\n"
                     f"Assistant: {(asr or '').strip()[:300]}".strip())
        except Exception:
            pass

        # Rung 3: episodic recall.
        try:
            res = self.recall(question, limit=3) or {}
            for e in (res.get("results", []) or [])[:3]:
                snip = (e.get("snippet") or e.get("text") or "").strip()
                if snip:
                    _add(3, f"episodic:#{e.get('id', '?')}",
                         snip[:300])
        except Exception:
            pass

        # Rung 4: local OpenCode storage, last resort (best-effort).
        try:
            hits = self._search_opencode_storage(question)
            for ts, text in hits:
                _add(4, f"opencode.db@{ts}", text[:400])
        except Exception:
            pass

        answer = "\n\n".join(sections)
        if len(answer) > max_chars:
            answer = answer[:max_chars] + "\n…[truncated]"
        return {
            "resolved": resolved_rung is not None,
            "rung": resolved_rung,
            "answer": answer,
            "question": question,
        }

    def _search_opencode_storage(self, question, limit_rows=200, max_hits=2):
        """Best-effort keyword search over local OpenCode message parts."""
        import re as _re
        kws = [w.lower() for w in _re.findall(r"[\w]{3,}", question or "")]
        if not kws:
            return []
        db = os.path.join(os.path.expanduser("~"), ".local", "share",
                          "opencode", "opencode.db")
        if not os.path.exists(db):
            return []
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT m.time_created, p.data FROM part p "
                "JOIN message m ON m.id = p.message_id "
                "ORDER BY m.time_created DESC LIMIT ?",
                (limit_rows,),
            ).fetchall()
        except Exception:
            return []
        finally:
            try:
                con.close()
            except Exception:
                pass
        hits = []
        for ts, data in rows:
            try:
                d = json.loads(data)
            except Exception:
                continue
            txt = d.get("text", "")
            if not isinstance(txt, str):
                continue
            low = txt.lower()
            if any(k in low for k in kws):
                hits.append((ts, txt.strip()[:600]))
                if len(hits) >= max_hits:
                    break
        return hits


def handle(store, msg):
    mid = msg.get("id")
    method = msg.get("method", "")

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    try:
        if method == "initialize":
            return ok({"protocolVersion": "2024-11-05",
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": "genesis-memory", "version": "0.5.0"}})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": visible_tools()})
        if method == "tools/call":
            p = msg.get("params", {})
            name, args = p.get("name", ""), p.get("arguments", {}) or {}
            if name == GATEWAY_TOOL_NAME:
                # One-tool gateway: route op to the same branches below.
                op = args.get("op", "help") if isinstance(args, dict) else "help"
                if op == "help":
                    return ok({"content": [{"type": "text", "text": json.dumps({
                        "tool": GATEWAY_TOOL_NAME, "ops": list(GATEWAY_OPS),
                        "mode": os.environ.get("GENESIS_MCP_TOOL_MODE", "full"),
                    })}]})
                if op not in GATEWAY_OPS:
                    return err(-32602, f"unknown op: {op}. ops: {', '.join(GATEWAY_OPS)}")
                store.calls[GATEWAY_TOOL_NAME] = store.calls.get(GATEWAY_TOOL_NAME, 0) + 1
                name = op
                args = {k: v for k, v in args.items() if k != "op"}
            tool = next((t for t in TOOLS + [GATEWAY_TOOL] if t["name"] == name), None)
            if tool is None:
                return err(-32602, f"unknown tool: {name}")
            store.calls[name] = store.calls.get(name, 0) + 1
            if name == "remember":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.remember(**args))}]})
            if name == "recall":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.recall(**args))}]})
            if name == "forget":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.forget(args["id"]))}]})
            if name == "invalidate":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.invalidate(**args))}]})
            if name == "resolve_conflict":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.resolve_conflict(**args))}]})
            if name == "get_dependencies":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.get_dependencies(**args))}]})
            if name == "attest_closure":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.attest_closure(**args))}]})
            if name in ("genesis_log", "ctx_log"):
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.read_spool(**args))}]})
            if name == "status":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.status())}]})
            if name == "thread_update":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.set_thread(**args))}]})
            if name == "thread_get":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.get_thread())}]})
            if name == "dialogue_update":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.set_dialogue(**args))}]})
            if name == "dialogue_get":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.get_latest_dialogue(**args))}]})
            if name == "cross_client_resolve":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.cross_client_resolve(**args))}]})
            if name == "reinforce":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.reinforce(**args))}]})
            if name == "synthesize_skill":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.synthesize_skill(**args))}]})
            if name == "skill_recall":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.skill_recall(**args))}]})
            if name == "sleep_now":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.sleep_now(**args))}]})
            if name == "challenge_rule":
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(store.challenge_rule(**args))}]})

        if method.startswith("notifications/"):
            return None
        return err(-32601, f"unknown method: {method}")
    except Exception as e:
        return err(-32603, f"{type(e).__name__}: {e}")


def export_jsonl(db_path, out_path):
    """Dump all episodes to JSONL (data sovereignty / backup). Read-only on DB."""
    store = Store(db_path)
    rows = store.db.execute(
        "SELECT id, ts, project, kind, text, utility, accesses, updated"
        " FROM episodes ORDER BY id").fetchall()
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"id": r[0], "ts": r[1], "project": r[2], "kind": r[3],
                                "text": r[4], "utility": r[5], "accesses": r[6],
                                "updated": r[7]}) + "\n")
    return len(rows)


def import_jsonl(db_path, in_path):
    """Load episodes from JSONL (fresh ids assigned; originals kept in payload)."""
    store = Store(db_path)
    n = 0
    with open(in_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            store.db.execute(
                "INSERT INTO episodes(ts,project,kind,text,utility,accesses,updated)"
                " VALUES(?,?,?,?,?,?,?)",
                (d.get("ts", time.time()), d.get("project", "default"),
                 d.get("kind", "fact"), d.get("text", ""),
                 float(d.get("utility", 1.0)), int(d.get("accesses", 0)),
                 d.get("updated", time.time())))
            n += 1
    store.db.commit()
    return n


def snapshot(db_path):
    """Dashboard feed: health + top memories + counts by kind. Read-only."""
    store = Store(db_path)
    try:
        st = store.status()
        top = store.db.execute(
            "SELECT id, project, kind, substr(text,1,160), utility, accesses FROM episodes"
            " ORDER BY utility DESC, accesses DESC LIMIT 20").fetchall()
        kinds = store.db.execute(
            "SELECT kind, COUNT(*) FROM episodes GROUP BY kind").fetchall()
        total_chars = store.db.execute(
            "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM episodes").fetchone()[0]
        total_tokens_est = total_chars // 4

        st.update({"top_memories": [{"id": r[0], "project": r[1], "kind": r[2], "preview": r[3],
                                     "utility": r[4], "accesses": r[5]} for r in top],
                   "by_kind": {k: n for k, n in kinds},
                   "total_chars": total_chars,
                   "total_tokens_est": total_tokens_est,
                   "schema_version": store.db.execute("PRAGMA user_version").fetchone()[0]})
        return st
    finally:
        try:
            store.db.close()
        except Exception:
            pass



def main():
    import argparse
    ap = argparse.ArgumentParser(prog="genesis_daemon",
                                 description="GENESIS MCP cognitive daemon")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--export", metavar="FILE",
                    help="dump episodes to JSONL and exit (read-only on DB)")
    ap.add_argument("--import", dest="imp", metavar="FILE",
                    help="load episodes from JSONL and exit")
    ap.add_argument("--snapshot", metavar="FILE",
                    help="write dashboard JSON snapshot and exit (read-only)")
    ap.add_argument("--capsule", action="store_true",
                    help="format bounded deterministic subconscious memory capsule (<=150 tokens) and exit")
    ap.add_argument("--recall-json", metavar="QUERY",
                    help="execute recall with query string and exit as JSON")
    ap.add_argument("--set-thread-json", metavar="JSON",
                    help="update the unified cross-client active working thread from JSON and exit")
    ap.add_argument("--get-thread", action="store_true",
                    help="print active working thread as JSON and exit")
    args = ap.parse_args()
    if args.set_thread_json:
        store = Store(args.db)
        data = json.loads(args.set_thread_json)
        res = store.set_thread(**data)
        print(json.dumps(res))
        return
    if args.get_thread:
        store = Store(args.db)
        print(json.dumps(store.get_thread()))
        return
    if args.export:
        print(json.dumps({"exported": export_jsonl(args.db, args.export)}))
        return
    if args.imp:
        print(json.dumps({"imported": import_jsonl(args.db, args.imp)}))
        return
    if args.snapshot:
        with open(args.snapshot, "w", encoding="utf-8") as f:
            json.dump(snapshot(args.db), f, indent=2)
        print(json.dumps({"snapshot": args.snapshot}))
        return
    if args.capsule:
        from genesis_memory.hooks.subconscious_hook import query_subconscious_memories
        memories, telemetry = query_subconscious_memories("")
        if memories:
            header = f"[GENESIS Subconscious Memory | Live Telemetry: {telemetry['engram_count']} engrams • {telemetry['injected_tokens']} tokens • ↓ {telemetry['savings_pct']}% payload vs {telemetry['db_total_tokens']} tok DB]:"
            print(header + "\n" + "\n".join(memories))
        return
    if args.recall_json:
        store = Store(args.db)
        res = store.recall(args.recall_json)
        print(json.dumps(res, ensure_ascii=False))
        return
    store = Store(args.db)
    log = setup_logging(args.db).info
    log(f"genesis-memory daemon up db={args.db} rss={rss_mb()}MB")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": f"parse: {e}"}}) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(store, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

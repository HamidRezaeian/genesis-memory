"""
GENESIS Subconscious Memory Hook (Antigravity PreInvocation Lifecycle Hook).
Universal, language-agnostic cognitive working buffer: runs before the model turn,
injects the active working memory buffer (identity, critical decisions, and associative matches)
directly into the model's working context as an ephemeral message.

Zero hardcoded language rules. Zero dictionaries. Zero if-else heuristics.
Pure mathematical ranking, Unicode tokenization, and recency-anchored working buffer.
Follows EXP108 selective context discipline (<200 tokens budget: ~75 thread + ~35 dialogue + ~90 engrams).
"""

import json
import os
import re
import sqlite3
import sys
import time

try:
    from genesis_memory.daemon.server import redact_secrets
except Exception:
    redact_secrets = None  # degraded: truncate-only (documented in capture)

def find_repo_root():
    """Dynamically discover repo root even when running from global ~/.genesis/ copy."""
    if "GENESIS_ROOT" in os.environ and os.path.exists(os.environ["GENESIS_ROOT"]):
        return os.environ["GENESIS_ROOT"]
    cur = os.path.abspath(os.getcwd())
    while True:
        if os.path.exists(os.path.join(cur, "src", "genesis")) or os.path.exists(os.path.join(cur, ".agents")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    candidate = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if os.path.exists(os.path.join(candidate, "src", "genesis")):
        return candidate
    common_workspace = os.path.expanduser("~/source/repos/GENESIS")
    if os.path.exists(os.path.join(common_workspace, "src", "genesis")):
        return common_workspace
    # Standalone genesis-memory repo fallback: package root carries pyproject.toml
    pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if os.path.isfile(os.path.join(pkg_root, "pyproject.toml")) and os.path.isdir(
        os.path.join(pkg_root, "genesis_memory")
    ):
        return pkg_root
    return None

REPO_ROOT = find_repo_root()

DB_PATH = os.environ.get(
    "GENESIS_DAEMON_DB",
    os.path.expanduser("~/.genesis/memory.db")
)
if not os.path.exists(DB_PATH):
    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genesis_memory.db")
    if os.path.exists(fallback):
        DB_PATH = fallback


# Static answer-style line for direct-model clients (native agents that never
# route through the proxy). Same GENESIS_OUTPUT_DIET flag as the proxy so one
# switch governs both paths. Appended LAST and only if it fits the token
# budget — memory content always outranks style.
def _diet_enabled():
    return os.environ.get("GENESIS_OUTPUT_DIET", "0").lower() in ("1", "true", "yes")


HOOK_DIET_LINE = (
    "• [Answer style]: reply tersely, no filler; keep code, commands, errors, "
    "paths and numbers byte-for-byte."
)


# --- Ambient multi-client layer (Antigravity 3-layer proposal, conditional) ---
# CAPTURE_STALE_S mirrors the plugin query-cache TTL (60s): structural
# anti-churn bound, not a tuned constant.
CAPTURE_STALE_S = 60.0
CAPTURE_PROMPT_CHARS = 250
GIT_DELTA_MAX_FILES = 5
GIT_DELTA_MAX_CHARS = 120
MICRO_DIRECTIVE_LINE = (
    "• [Thread hygiene]: when starting a new phase or switching tasks, "
    "call thread_update first."
)
RECALL_HINT_LINE = (
    "• [Recall rule]: on questions referring to earlier discussion without detail here, call recall first."
)
GROUNDING_LINE = (
    "• [Grounding rule]: quote numbers with their source; "
    "never state a count without where it came from."
)
RESOLUTION_LADDER_LINE = (
    "• [Resolution order]: answer from this capsule first; then dialogue_buffer; "
    "then recall; last resort: local store."
)
CHALLENGE_PROTOCOL_LINE = (
    "• [Challenge rule]: if better than a rule above, "
    "call challenge_rule(solidified_id, proposed_text, reason)."
)
# Fresh-session boost: one-time extra budget when NO fresh dialogue exists
# (new/returning session). Bounded and telemetry-visible.
BOOST_CHARS = 400
DIALOGUE_TTL_S = 1800.0
DIALOGUE_MAX_TURNS = 3


def _detect_client(argv, payload):
    """Best-effort client attribution for hook:{client} session keys."""
    args = list(argv[1:])
    if "--cursor" in args:
        return "cursor"
    if "--claude" in args:
        return "claude"
    if isinstance(payload, dict) and payload.get("transcriptPath"):
        return "antigravity"
    if "--raw" in args or "-r" in args:
        return "opencode"
    return "unknown"


def _parse_git_files(status_stdout, max_files=GIT_DELTA_MAX_FILES):
    """Basenames from `git status --porcelain`, order-preserved, deduped."""
    files = []
    for line in (status_stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        path = line.split("->")[-1].strip().split()[-1].strip('"')
        base = os.path.basename(path.rstrip("/"))
        if base and base not in files:
            files.append(base)
        if len(files) >= max_files:
            break
    return files



def get_latest_user_text(transcript_path):
    """Safely extract the latest user request text from the transcript (bounded tail-read)."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        file_size = os.path.getsize(transcript_path)
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            if file_size > 65536:
                f.seek(file_size - 65536)
                f.readline()  # Discard partial line
            lines = f.readlines()
        for line in reversed(lines):
            try:
                d = json.loads(line)
                if d.get("type") == "USER_INPUT":
                    content = d.get("content", "")
                    m = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
                    return m.group(1).strip() if m else content.strip()
            except Exception:
                continue
    except Exception:
        pass
    return ""


def get_latest_assistant_text(transcript_path, max_chars=2000):
    """Best-effort latest assistant text from a transcript (schema-tolerant).

    Matches lines whose type contains 'ASSIST' (excluding tool/result noise)
    or role == 'assistant', reading content/text/message/output string fields.
    Returns "" when unknown — callers must treat that as absence, not content.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        file_size = os.path.getsize(transcript_path)
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            if file_size > 262144:
                f.seek(file_size - 262144)
                f.readline()  # Discard partial line
            lines = f.readlines()
        for line in reversed(lines):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            dtype = str(d.get("type", ""))
            dsource = str(d.get("source", ""))
            is_assistant = (
                ("ASSIST" in dtype.upper() and "TOOL" not in dtype.upper())
                or str(d.get("role", "")).lower() == "assistant"
                or (dtype.upper() == "PLANNER_RESPONSE" and dsource.upper() == "MODEL")
            )
            if not is_assistant:
                continue
            for key in ("content", "text", "message", "output"):
                val = d.get(key)
                if isinstance(val, str) and val.strip():
                    clean = re.sub(r"<[^>]{1,64}>", "", val).strip()
                    if clean:
                        return clean[:max_chars]
    except Exception:
        pass
    return ""


def query_subconscious_memories(query_text, max_tokens=200, client=None, capture_turn=False, capture_assistant=""):
    """Retrieve working memory buffer + associative matches without language-specific hardcoding.

    Adheres to Baddeley's Working Memory Model:
    1. Working Buffer Anchor: Always primes the immediate active identity/state (highest utility & recency).
    2. Dialogue Continuity Buffer: Recalls 1-turn cross-session dialogue turn within 1800s TTL.
    3. Associative Spreading Activation: Tokenizes Unicode words (>=2 chars), queries FTS5 via BM25,
       letting IDF mathematically suppress non-discriminative terms across any natural language or code.
    4. Output strictly bounded under max_tokens (<200 tokens) to prevent context pollution.
    """
    if not os.path.exists(DB_PATH):
        return [], {
            "engram_count": 0,
            "injected_tokens": 0,
            "db_total_tokens": 0,
            "db_episodes_count": 0,
            "savings_pct": 0.0,
            "snoop_invalidations": 0,
            "faults_triggered": 0,
            "faults_resolved": 0,
            "fault_caps_hit": 0,
            "attestations_passed": 0,
        }

    results = []
    seen_ids = set()
    ambient_captured = False
    boosted = False
    extra_dialogue: list = []

    try:
        conn = sqlite3.connect(DB_PATH, timeout=1.0)
        cur = conn.cursor()

        # Check schema capability (supports v2 legacy and v3 active status)
        cols = [c[1] for c in cur.execute("PRAGMA table_info(episodes)").fetchall()]
        has_status = "status" in cols
        status_filter = "WHERE (status IN ('active', 'solidified') OR status IS NULL)" if has_status else ""
        status_join = "AND (e.status IN ('active', 'solidified') OR e.status IS NULL)" if has_status else ""

        # Check matching procedural skills (SkillSynthesizer)
        skill_entries: list = []
        try:
            from genesis_memory.core.skill_synthesizer import SkillSynthesizer
            synth = SkillSynthesizer(conn)
            matched_skills = synth.match_skills(query_text, min_confidence=0.6, limit=1)
            skill_entries = synth.format_skills_for_prompt(matched_skills, max_chars=180)
        except Exception:
            pass

        # Exclude IDs already pinned in AGENTS.md digest to eliminate token duplication
        agents_md = os.path.join(REPO_ROOT, ".agents", "AGENTS.md")
        if os.path.exists(agents_md):
            try:
                with open(agents_md, "r", encoding="utf-8") as f:
                    ag_text = f.read()
                for match_id in re.findall(r"\[#(\d+)\s+[A-Z]+\]", ag_text):
                    seen_ids.add(int(match_id))
            except Exception:
                pass

        # 1. Working Buffer Anchor: Prime the newest active state not in digest (solidified first)
        anchor_sql = (
            f"SELECT id, kind, text FROM episodes "
            f"{status_filter} "
            f"ORDER BY CASE WHEN status = 'solidified' THEN 0 ELSE 1 END, ts DESC LIMIT 8"
        )
        for rid, kind, text in cur.execute(anchor_sql).fetchall():
            if rid not in seen_ids:
                seen_ids.add(rid)
                results.append((rid, kind, text))
                break

        # 2. Associative Spreading Activation: Unicode tokens + universal prefix subwords
        tokens = list(set(re.findall(r"[\w]{2,}", query_text.lower())))[:8]
        terms = []
        for tok in tokens:
            terms.append(f'"{tok}"*')
            # Mathematical morphological prefix expansion (no language dictionary needed):
            # For words >= 4 chars, add prefixes down to 3 chars so inflected forms match root forms
            for i in range(len(tok) - 1, max(2, len(tok) - 2), -1):
                terms.append(f'"{tok[:i]}"*')

        if terms:
            terms = list(dict.fromkeys(terms))[:12]
            fts_query = " OR ".join(terms)
            try:
                sql = (
                    "SELECT e.id, e.kind, e.text, bm25(episodes_fts) "
                    "FROM episodes_fts JOIN episodes e ON e.id = episodes_fts.rowid "
                    f"WHERE episodes_fts MATCH ? {status_join} "
                    "ORDER BY bm25(episodes_fts) ASC, e.utility DESC LIMIT 2"
                )
                for rid, kind, text, _ in cur.execute(sql, (fts_query,)).fetchall():
                    if rid not in seen_ids:
                        seen_ids.add(rid)
                        results.append((rid, kind, text))
            except Exception:
                pass

        # 3. Supplemental Buffer: if associative yielded nothing new, fill up to 2 items
        if len(results) < 2:
            fill_sql = (
                f"SELECT id, kind, text FROM episodes "
                f"{status_filter} "
                f"ORDER BY utility DESC, ts DESC LIMIT 2"
            )
            for rid, kind, text in cur.execute(fill_sql).fetchall():
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    results.append((rid, kind, text))
                    if len(results) >= 2:
                        break

        # 4. Spine Snooping Bus (L2 Invalidation & Coherence Gate)
        # Verify candidate engrams against indexed SQLite status before prompt formatting.
        # If any engram was invalidated or superseded, prune it immediately to prevent stale injection.
        snoop_invalidations = 0
        if results and has_status:
            cand_ids = [r[0] for r in results]
            placeholders = ",".join("?" for _ in cand_ids)
            snoop_sql = (
                f"SELECT id FROM episodes WHERE id IN ({placeholders}) "
                "AND (status = 'active' OR status IS NULL)"
            )
            valid_ids = {row[0] for row in cur.execute(snoop_sql, cand_ids).fetchall()}
            snoop_invalidations = len(results) - len(valid_ids)
            results = [r for r in results if r[0] in valid_ids]

        # 5. Verification Trap & Graph Closure Attestation (Priority 3 Gate)
        # Audit candidate working memories against deterministic AST graph closure.
        # Demand-pages missing dependencies into working buffer up to MAX_FAULTS_PER_TURN.
        faults_triggered = 0
        faults_resolved = 0
        fault_caps_hit = 0
        attestations_passed = 0
        try:
            from genesis_memory.core.verification_trap import AttestationTrap
            trap = AttestationTrap(conn, fault_cap=2, threshold=0.40)
            trap_res = trap.trap_and_resolve(results, query_text)
            results = trap_res["memories"]
            faults_triggered = trap_res["faults_triggered"]
            faults_resolved = trap_res["faults_resolved"]
            fault_caps_hit = trap_res["fault_caps_hit"]
            attestations_passed = trap_res["attestations_passed"]
        except Exception:
            pass

        # Check active working thread for cross-client seamless continuity
        thread_entry = None
        try:
            cur.execute("SELECT updated_at, client, topic, summary, recent_files, pending_focus FROM active_thread WHERE id = 1")
            t_row = cur.fetchone()
            if t_row and t_row[2]:
                updated_at, t_client, t_topic, t_summary, t_files, t_focus = t_row
                ts = time.time()
                if isinstance(updated_at, (int, float)):
                    ts = float(updated_at)
                elif updated_at:
                    try:
                        ts = float(updated_at)
                    except (ValueError, TypeError):
                        try:
                            from datetime import datetime
                            clean_dt = str(updated_at).replace("Z", "").split(".")[0]
                            ts = datetime.strptime(clean_dt, "%Y-%m-%d %H:%M:%S").timestamp()
                        except Exception:
                            ts = time.time()
                age_s = max(0.0, time.time() - ts)

                # 4-Signal Multi-Signal Validator (Rule 17 Grounding, OpenCode Certified)
                # Signal 1: Host clock age_s
                # Signal 2: Recent files mtime via stat
                mtime_old = True
                if t_files:
                    for fn in re.split(r"[,;\s]+", t_files):
                        fn = fn.strip()
                        if fn and os.path.exists(fn):
                            try:
                                if (time.time() - os.path.getmtime(fn)) < 3600:
                                    mtime_old = False
                                    break
                            except Exception:
                                pass

                # Signal 3: Proxy health / PID alive
                proxy_alive = False
                try:
                    import socket
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(0.04)
                        proxy_alive = (s.connect_ex(("127.0.0.1", 8000)) == 0)
                except Exception:
                    proxy_alive = False

                # Signal 4: Git working tree cleanliness (+ file list for delta line)
                git_active = False
                git_files: list = []
                if REPO_ROOT:
                    try:
                        import subprocess
                        res = subprocess.run(
                            ["git", "status", "--porcelain"],
                            cwd=REPO_ROOT,
                            capture_output=True,
                            text=True,
                            timeout=0.5,
                        )
                        if res.returncode == 0 and res.stdout.strip():
                            git_active = True
                            git_files = _parse_git_files(res.stdout)
                    except Exception:
                        pass

                # Rule 17 Empirical Expiry:
                # Expired if age >= 7200 (2h bound) AND files haven't changed AND proxy dead
                # PID alive extends past-1800 with Stale tag, but never past 7200 without thread_update
                is_expired = (age_s >= 7200) or (age_s >= 3600 and mtime_old and not proxy_alive)

                if not is_expired:
                    if age_s < 120:
                        freshness = "just now"
                    elif age_s < 3600:
                        freshness = f"{int(age_s // 60)}m ago"
                    else:
                        freshness = f"{int(age_s // 3600)}h ago"

                    status_tag = "Live" if (age_s < 1800 or (not mtime_old and proxy_alive)) else "Stale"

                    # Strict 80-token cap (~300 chars max) to prevent engram starvation
                    topic_str = (t_topic[:75] + "...") if len(t_topic) > 75 else t_topic
                    summary_str = (t_summary[:120] + "...") if len(t_summary) > 120 else t_summary
                    parts = [
                        f"• [Active Work Thread ({status_tag} • updated {freshness} from {t_client})]:",
                        f"  - Topic: {topic_str}",
                        f"  - Summary: {summary_str}",
                    ]
                    if t_focus:
                        focus_str = (t_focus[:60] + "...") if len(t_focus) > 60 else t_focus
                        parts.append(f"  - Focus: {focus_str}")
                    if git_files:
                        delta = ", ".join(git_files)[:GIT_DELTA_MAX_CHARS]
                        parts.append(f"  - Delta: {delta}")
                    thread_entry = "\n".join(parts)
        except Exception:
            pass

        # Check dialogue_buffer for conversational continuity (cross-session &
        # cross-client): up to DIALOGUE_MAX_TURNS fresh turns, latest full,
        # older ones as one-liners. No fresh turn => fresh-session boost.
        dialogue_entries: list = []
        boosted = False
        try:
            cur.execute(
                "SELECT client, updated_at, user_prompt, assistant_summary, salient_terms "
                "FROM dialogue_buffer ORDER BY updated_at DESC LIMIT %d" % DIALOGUE_MAX_TURNS
            )
            now_ts = time.time()
            fresh_rows = []
            for d_row in cur.fetchall():
                try:
                    d_age = max(0.0, now_ts - float(d_row[1] or 0))
                except (ValueError, TypeError):
                    continue
                if d_age < DIALOGUE_TTL_S and (d_row[2] or d_row[3]):
                    fresh_rows.append((d_age,) + tuple(d_row))
            if not fresh_rows:
                boosted = True
            for pos, (d_age_s, d_client, _ts, d_user, d_summary, _terms) in enumerate(fresh_rows):
                if d_age_s < 60:
                    d_freshness = "just now"
                elif d_age_s < 3600:
                    d_freshness = f"{int(d_age_s // 60)}m ago"
                else:
                    d_freshness = f"{int(d_age_s // 3600)}h ago"
                if pos == 0:
                    user_snippet = (d_user[:80] + "...") if len(d_user) > 80 else d_user
                    summary_snippet = (d_summary[:120] + "...") if len(d_summary) > 120 else d_summary
                    dialogue_entries.append("\n".join([
                        f"• [Last Dialogue Turn ({d_freshness} via {d_client or 'client'})]:",
                        f"  - User: \"{user_snippet}\"",
                        f"  - Assistant: \"{summary_snippet}\"",
                    ]))
                else:
                    u = (d_user[:60] + "...") if len(d_user) > 60 else d_user
                    s = (d_summary[:60] + "...") if len(d_summary) > 60 else d_summary
                    dialogue_entries.append(
                        f"• [Earlier Turn ({d_freshness} via {d_client or 'client'})]: "
                        f"User: \"{u}\" -> Assistant: \"{s}\""
                    )
        except Exception:
            pass
        dialogue_entry = dialogue_entries[0] if dialogue_entries else None
        extra_dialogue = dialogue_entries[1:]

        # Layer 1 Ambient Turn Capture (conditional, write-LAST): share this
        # turn cross-client ONLY when no proxy-fresh row exists. Proxy traffic
        # already writes via set_dialogue; the hook fills only the direct-model
        # gap. Skip-on-busy via a dedicated short-timeout connection so reads
        # and user latency are never held by a contended WAL.
        ambient_captured = False
        if capture_turn and client and isinstance(query_text, str) and query_text.strip():
            try:
                cur.execute(
                    "SELECT session_id, updated_at FROM dialogue_buffer "
                    "ORDER BY updated_at DESC LIMIT 1"
                )
                latest = cur.fetchone()
                fresh_proxy = False
                if latest:
                    try:
                        fresh_proxy = (
                            (time.time() - float(latest[1] or 0)) < CAPTURE_STALE_S
                            and not str(latest[0] or "").startswith("hook:")
                        )
                    except (ValueError, TypeError):
                        pass
                if not fresh_proxy:
                    # Per-turn rows (not single-row overwrite): rapid follow-ups
                    # must not clobber each other; readers take latest-N.
                    # TTL prune keeps growth bounded (hook rows only — proxy
                    # and user sessions are never touched here).
                    now_ts = time.time()
                    hook_session = f"hook:{client}:{int(now_ts * 1000)}"
                    prompt = query_text.strip()[:CAPTURE_PROMPT_CHARS]
                    clean = redact_secrets(prompt)[:CAPTURE_PROMPT_CHARS] if redact_secrets else prompt
                    raw_reply = capture_assistant.strip()[:CAPTURE_PROMPT_CHARS] if isinstance(capture_assistant, str) else ""
                    clean_reply = redact_secrets(raw_reply)[:CAPTURE_PROMPT_CHARS] if redact_secrets else raw_reply
                    wconn = sqlite3.connect(DB_PATH, timeout=0.05)
                    try:
                        wconn.execute(
                            "CREATE TABLE IF NOT EXISTS dialogue_buffer("
                            "session_id TEXT PRIMARY KEY, client TEXT, updated_at REAL, "
                            "user_prompt TEXT, assistant_summary TEXT, salient_terms TEXT)"
                        )
                        wconn.execute(
                            "INSERT INTO dialogue_buffer(session_id, client, updated_at, "
                            "user_prompt, assistant_summary, salient_terms) "
                            "VALUES(?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(session_id) DO UPDATE SET client=excluded.client, "
                            "updated_at=excluded.updated_at, user_prompt=excluded.user_prompt, "
                            "assistant_summary=excluded.assistant_summary, "
                            "salient_terms=excluded.salient_terms",
                            (hook_session, client, now_ts, clean, clean_reply, ""),
                        )
                        wconn.execute(
                            "DELETE FROM dialogue_buffer WHERE session_id LIKE 'hook:%' "
                            "AND updated_at < ?",
                            (now_ts - DIALOGUE_TTL_S,),
                        )
                        wconn.commit()
                        ambient_captured = True
                    finally:
                        try:
                            wconn.close()
                        except Exception:
                            pass
            except Exception:
                pass

        # Compute live DB totals for dynamic telemetry
        cur.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(text)), 0) FROM episodes")
        db_count, db_chars = cur.fetchone()
    except Exception:
        thread_entry = None
        dialogue_entry = None
        db_count, db_chars = 0, 0
        snoop_invalidations = 0
        faults_triggered = 0
        faults_resolved = 0
        fault_caps_hit = 0
        attestations_passed = 0
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    # Format bounded output (<200 tokens, +boost when no fresh dialogue)
    budget_chars = max_tokens * 4 + (BOOST_CHARS if boosted else 0)
    formatted = []
    total_chars = 0
    if thread_entry:
        formatted.append(thread_entry)
        total_chars += len(thread_entry)

    if dialogue_entry:
        if total_chars + len(dialogue_entry) <= budget_chars:
            formatted.append(dialogue_entry)
            total_chars += len(dialogue_entry)
    for older in extra_dialogue:
        if total_chars + len(older) <= budget_chars:
            formatted.append(older)
            total_chars += len(older)

    for s_line in skill_entries:
        if total_chars + len(s_line) <= budget_chars:
            formatted.append(s_line)
            total_chars += len(s_line)

    for rid, kind, text in results:
        # Standardized 40 words per snippet matching Store.recall invariant
        snippet = " ".join(text.split()[:40])
        if total_chars + len(snippet) > budget_chars and formatted:
            break
        formatted.append(f"• [Memory #{rid} - {kind}]: {snippet}")
        total_chars += len(snippet)

    # Layer 3 micro-directive: one line, budget-checked. Placed BEFORE the
    # diet line so diet stays the last capsule entry (diet contract).
    micro_applied = False
    if total_chars + len(MICRO_DIRECTIVE_LINE) <= budget_chars:
        formatted.append(MICRO_DIRECTIVE_LINE)
        total_chars += len(MICRO_DIRECTIVE_LINE)
        micro_applied = True

    # Recall-hint directive: teaches pull-before-answer on referential gaps.
    recall_hint_applied = False
    if total_chars + len(RECALL_HINT_LINE) <= budget_chars:
        formatted.append(RECALL_HINT_LINE)
        total_chars += len(RECALL_HINT_LINE)
        recall_hint_applied = True

    grounding_applied = False
    if total_chars + len(GROUNDING_LINE) <= budget_chars:
        formatted.append(GROUNDING_LINE)
        total_chars += len(GROUNDING_LINE)
        grounding_applied = True

    resolution_applied = False
    if total_chars + len(RESOLUTION_LADDER_LINE) <= budget_chars:
        formatted.append(RESOLUTION_LADDER_LINE)
        total_chars += len(RESOLUTION_LADDER_LINE)
        resolution_applied = True

    challenge_protocol_applied = False
    if total_chars + len(CHALLENGE_PROTOCOL_LINE) <= budget_chars:
        formatted.append(CHALLENGE_PROTOCOL_LINE)
        total_chars += len(CHALLENGE_PROTOCOL_LINE)
        challenge_protocol_applied = True

    diet_applied = False
    if _diet_enabled() and total_chars + len(HOOK_DIET_LINE) <= budget_chars:
        formatted.append(HOOK_DIET_LINE)
        total_chars += len(HOOK_DIET_LINE)
        diet_applied = True

    injected_tok = max(1, total_chars // 4)
    db_tok = max(1, db_chars // 4)
    savings_pct = round((1.0 - (injected_tok / max(injected_tok, db_tok))) * 100, 1)

    telemetry = {
        "engram_count": len(results),
        "diet_applied": diet_applied,
        "micro_applied": micro_applied,
        "recall_hint_applied": recall_hint_applied,
        "grounding_applied": grounding_applied,
        "resolution_applied": resolution_applied,
        "challenge_protocol_applied": challenge_protocol_applied,
        "ambient_captured": ambient_captured,
        "boosted": boosted,
        "dialogue_turns": 1 + len(extra_dialogue) if dialogue_entry else len(extra_dialogue),
        "injected_tokens": injected_tok,
        "db_total_tokens": db_tok,
        "db_episodes_count": db_count,
        "savings_pct": savings_pct,
        "snoop_invalidations": snoop_invalidations,
        "faults_triggered": faults_triggered,
        "faults_resolved": faults_resolved,
        "fault_caps_hit": fault_caps_hit,
        "attestations_passed": attestations_passed,
    }

    return formatted, telemetry




def main():
    raw_mode = any(arg in sys.argv for arg in ("--raw", "--cursor", "--claude", "-r"))
    payload = {}
    if not raw_mode:
        try:
            if not sys.stdin.isatty():
                payload = json.load(sys.stdin)
        except Exception:
            pass

    transcript_path = payload.get("transcriptPath", "")
    user_text = get_latest_user_text(transcript_path)
    if not user_text and len(sys.argv) > 1:
        # Filter out flags
        words = [a for a in sys.argv[1:] if not a.startswith("-")]
        if words:
            user_text = " ".join(words)

    client = _detect_client(sys.argv, payload)
    # Assistant side, freshest-first: OpenCode plugin hands the just-finished
    # reply via env (cleared after each spawn, never stale); otherwise fall
    # back to transcript parsing (Antigravity); otherwise unknown ("").
    assistant_text = os.environ.get("GENESIS_LAST_ASSISTANT_TEXT", "") or ""
    if not assistant_text.strip():
        assistant_text = get_latest_assistant_text(transcript_path)
    memories, telemetry = query_subconscious_memories(
        user_text, client=client, capture_turn=True,
        capture_assistant=assistant_text,
    )

    if memories:
        header = (
            "[GENESIS Subconscious Memory • PINNED]:\n"
            "Ground truth of workspace state; on conflict between local chat assumptions and this block for referential queries, prefer this block and cite it."
        )
        msg = header + "\n" + "\n".join(memories)

        if raw_mode:
            print(msg)
            return

        response = {
            "injectSteps": [
                {
                    "ephemeralMessage": msg
                }
            ]
        }
    else:
        if raw_mode:
            return
        response = {"injectSteps": []}

    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()


"""Universal transcript watcher (passive cross-client capture).

Why this exists: per-client push integrations (plugins/hooks/MCP calls) only
work when the model cooperates. A trivial turn like ``+2`` never triggers a
tool call, so the other clients never learn about it. The watcher flips the
direction: the daemon *pulls* new turns from local transcript stores on disk
(read-only, never modifies client files) and upserts them into
``dialogue_buffer`` through the same bounded ``set_dialogue`` path every
other writer uses.

Design rules (from the +2 post-mortem):
- Generic engine. No client names anywhere in code identifiers, dispatch,
  cursor keys or stats — sources are data rows (``id`` + ``kind`` + ``path``
  + optional ``params``). Adding a source of a known format = adding one row,
  zero code. A brand-new on-disk format needs one parser function + one
  registry line; the engine (``scan_once``) never changes.
- Format knowledge lives ONLY in parser functions and registry rows.
  (Unavoidable: a message/part SQLite store and a steps-table protobuf store
  share nothing parseable. ``kind`` names the FORMAT, never the product.)
- Read-only on client files (``mode=ro`` URI, no writes, skip locked DBs).
- Bounded output: one ``dialogue_buffer`` row per observed turn, each row
  already truncated by ``set_dialogue`` (250/250/120 chars). Watcher rows
  are prefixed ``watch:`` and pruned after ``WATCH_TTL_S``.
- Content never logged: logs carry file names, counts and lengths only.

Configuration (all optional, all data — no code changes):
- ``GENESIS_WATCHER_SOURCES_JSON``: full source list override (JSON array of
  rows: {"id", "kind", "path", "enabled", "params"}).
- ``GENESIS_WATCHER_STEPS_DIRS``: pathsep-separated dirs of per-conversation
  ``*.db`` step tables (kind ``steps_dir``).
- ``GENESIS_WATCHER_CHAT_DBS``: pathsep-separated SQLite chat stores
  (kind ``chat_parts_db``). Captured only when ``GENESIS_WATCHER_CHAT_DB_ENABLE=1``.
- ``GENESIS_WATCHER_JSONL``: glob of JSONL transcripts (kind ``jsonl_file``).
"""

import glob
import json
import logging
import os
import re
import sqlite3
import threading
import time

log = logging.getLogger("genesis_watcher")

WATCH_PREFIX = "watch:"
WATCH_TTL_S = float(os.environ.get("GENESIS_WATCHER_TTL_S", "3600"))
SCAN_INTERVAL_S = float(os.environ.get("GENESIS_WATCHER_INTERVAL_S", "10"))
SCAN_LIMIT = int(os.environ.get("GENESIS_WATCHER_LIMIT", "50"))
# Conversations untouched for longer than this are skipped: the watcher is a
# continuity feed, not a history importer. Reopening a chat refreshes mtime.
SOURCE_MAX_AGE_S = float(os.environ.get("GENESIS_WATCHER_MAX_AGE_S", "86400"))

# Printable human-text runs (Latin + Persian/Arabic blocks). Used to pull
# readable text out of protobuf-ish blobs without knowing their schema.
_RUN_RE = re.compile(
    r"[\w\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF+\-*/=]"
    r"[\w\s\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF"
    r".,!?;:'\"()\[\]\-+/=<>@#%&\u060C\u061F\u061B]{0,300}"
)
_SHORT_OK_RE = re.compile(r"[\d\u0600-\u06FF?!+\-*/=\u061F]")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HASH_RE = re.compile(r"^[A-Za-z0-9+/=_-]{20,}$")
# Stray length-byte artifacts glued to the front of the real text
# (protobuf field-length bytes decoded as latin-1: "H" + prompt,
# "S Q" + prompt, "7" + prompt). Only fires directly before Persian text,
# so legitimate prefixes ("v2 ...", "Version ...") are untouched.
_LEAD_ARTIFACT_RES = (
    re.compile(r"^(?:[A-Za-z]\s)*[A-Za-z0-9](?=[\u0600-\u06FF])"),
)


def _clean_text(raw):
    """Best-effort cleanup of one extracted candidate. Returns '' if noise."""
    if not raw:
        return ""
    text = str(raw).strip()
    for _ in range(3):  # layered artifacts ("S " then "Q" then text)
        for rx in _LEAD_ARTIFACT_RES:
            text = rx.sub("", text).strip()
    if text.endswith('"') and text.count('"') % 2 == 1:
        text = text[:-1].strip()
    if not (1 <= len(text) <= 250):
        return ""
    if " " not in text and len(text) < 3 and not _SHORT_OK_RE.search(text):
        return ""
    squashed = text.replace(" ", "").replace("-", "")
    if _UUID_RE.match(text) or _UUID_RE.match(squashed):
        return ""
    if " " not in text and _HASH_RE.match(squashed):
        return ""
    if " " not in text and len(text) > 60:
        return ""
    return text


# Tool-call syntax that sometimes lands in user-channel blobs (browser/tool
# runner echoes). Human turns never look like this.
_TOOL_NOISE_RES = (
    re.compile(r"[A-Za-z_]{2,}\([^)]*\)"),   # execute_url(...), run_command(
    re.compile(r"[a-z]+://"),                 # http://, https://
    re.compile(r'":"'),                       # embedded JSON
    re.compile(r"call_\d"),                   # call_4351685
    re.compile(r"\.[A-Za-z0-9]{1,4}\)"),      # catalog ref: Rules-01-08.md)
    re.compile(r"[A-Za-z_]+\([A-Z]:"),        # catalog opcode: write_file(C:
    re.compile(r"[A-Z][A-Z0-9_]{3,}="),       # env assignment: PYTHONIOENCODING=
    re.compile(r"\b[a-z_]{3,}\([^)]{0,4}$"),  # dangling opcode: command(, write_file(c:
)
# Single punctuation or control chars — protobuf field markers, not human text.
_SINGLE_JUNK_RE = re.compile(r"^[*#\-\x00-\x1f\x7f]$")
# Agent identity markers embedded in response blobs (e.g. bot-<uuid>).
# Noise in every client that emits them; harmless where absent.
_AGENT_ID_RE = re.compile(r"bot-[0-9a-fA-F-]{8,}")


def _is_tool_noise(text):
    if text.count(".") > 2 and not re.search(r"[\u0600-\u06FF]", text):
        return True
    if " " not in text and not re.search(r"[\u0600-\u06FF]", text):
        # Bare tokens: hostnames, versions, drive labels — never a message.
        if "." in text or text.endswith(":"):
            return True
    return any(rx.search(text) for rx in _TOOL_NOISE_RES)


_PERSIAN_RE = re.compile(r"[\u0600-\u06FF]")
_MATH_RE = re.compile(r"\d.*[+\-*/=]|[+\-*/=].*\d")
# A ?/! at the END is intent (a real question); mid-string is often accident.
_SENT_END_RE = re.compile(r"[?!\u061F]+$")
_SENT_ANY_RE = re.compile(r"[?!\u061F]")
_SINGLE_TOKEN_RE = re.compile(r"^[^\s?!.,:;+\-*/=()]+$")
# User-typed text never contains control characters; byte-soup accidents do.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def score_candidate(text):
    """Message-likeness score for blob-extracted strings.

    User-channel blobs embed the whole agent context (tool catalogs, file
    trees, page titles), so shape — not position — identifies the message:
    natural language (Persian strongly), questions, math. Tool syntax and
    bare path fragments score out.
    """
    if _is_tool_noise(text):
        return -100
    if len(text) > 140:
        return -100
    if _CONTROL_RE.search(text):
        return -100
    if "\\" in text or text.lower().endswith((".exe", ".md", ".py", ".json", ".log")):
        return -100
    score = 0
    if _PERSIAN_RE.search(text):
        score += 10
    if _SENT_END_RE.search(text):
        score += 3
    elif _SENT_ANY_RE.search(text):
        score += 1
    words = text.split()
    if 2 <= len(words) <= 60:
        score += 2
    if _MATH_RE.search(text):
        score += 2
    if _SINGLE_TOKEN_RE.match(text) and len(text) > 2:
        score -= 50
    return score + 1


def extract_text_runs(blob):
    """Pull distinct human-readable strings out of arbitrary bytes.

    Generic: works on UTF-8, protobuf payloads, JSON fragments — anything
    with embedded text. Returns longest-first unique candidates.
    """
    if not blob:
        return []
    if isinstance(blob, str):
        text = blob
    else:
        try:
            text = bytes(blob).decode("utf-8", errors="ignore")
        except Exception:
            return []
    seen = set()
    out = []
    for match in _RUN_RE.findall(text):
        cleaned = _clean_text(match)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out  # blob order; callers rank by score


def pick_user_text(candidates):
    """Highest message-likeness wins; ties go shorter, then later in blob."""
    best = ""
    best_key = None
    for pos, cand in enumerate(candidates):
        key = (score_candidate(cand), -len(cand), pos)
        if best_key is None or key > best_key:
            best_key = key
            best = cand
    return best if best_key is not None and best_key[0] > 0 else ""


def _open_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)


# --------------------------------------------------------------------------
# Source parsers. Each yields (conv_id, cursor, user_text, assistant_text).
# Cursor is an opaque per-conversation offset string; the engine persists it.
# --------------------------------------------------------------------------

def parse_chat_parts(db_path, after_ms=0, limit=SCAN_LIMIT):
    """Precise parser for a chat-parts SQLite store (known schema).

    Format: ``message`` + ``part`` tables; the message row's JSON ``data``
    carries the role, text parts carry ``{"type": "text", "text": ...}``.
    Returns (turns, new_cursor_ms) — always a tuple.
    """
    try:
        after_ms = int(after_ms)
    except (TypeError, ValueError):
        after_ms = 0
    try:
        con = _open_ro(db_path)
    except Exception:
        return [], after_ms
    try:
        try:
            rows = con.execute(
                "SELECT m.session_id, m.data, p.data, p.time_created "
                "FROM part p JOIN message m ON m.id = p.message_id "
                "WHERE p.time_created > ? ORDER BY p.time_created ASC LIMIT ?",
                (int(after_ms), int(limit)),
            ).fetchall()
        except Exception:
            return []
        turns = []
        pending_user = None
        pending_conv = None
        max_ts = int(after_ms)
        for session_id, mdata, pdata, ts in rows:
            try:
                max_ts = max(max_ts, int(ts or 0))
            except (TypeError, ValueError):
                pass
            try:
                role = (json.loads(mdata or "{}") or {}).get("role", "")
            except Exception:
                role = ""
            try:
                part = json.loads(pdata or "{}") or {}
            except Exception:
                continue
            if part.get("type") != "text" or not isinstance(part.get("text"), str):
                continue
            text = _clean_text(part["text"])
            if not text:
                continue
            if role == "user":
                pending_user = text
                pending_conv = session_id
            elif role == "assistant" and pending_user:
                turns.append((str(pending_conv or session_id), str(ts), pending_user, text))
                pending_user = None
                pending_conv = None
        if pending_user:
            turns.append((str(pending_conv or "unknown"), f"pending-{max_ts}", pending_user, ""))
        return turns, max_ts
    finally:
        try:
            con.close()
        except Exception:
            pass


def _pick_assistant_text(candidates):
    """Pick the assistant's reply from protobuf-extracted candidates.

    Rule 1: candidates starting with `*` (0x2A = protobuf field 5,
    length-delimited string = the response text field). Strip the marker,
    drop agent IDs/hashes, concatenate fragments in order.
    Rule 2: short responses are split from their `*` marker by the length
    byte — fall back to the first short clean candidate AFTER a long
    (>20 char) metadata candidate (session ID / hash / agent ID).
    Rule 3: fallback to the first clean candidate.
    """
    parts = []
    for cand in candidates:
        if not cand.startswith("*") or len(cand) < 2:
            continue
        text = cand[1:]
        if _AGENT_ID_RE.search(text):
            continue
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
            continue
        if _is_tool_noise(text):
            continue
        if len(text) > 140:
            continue
        parts.append(text)
    if parts:
        if len(parts[0]) <= 3:
            return parts[0]  # complete short answer (e.g. "۴", "5")
        return " ".join(parts)[:280]
    seen_long = False
    for cand in candidates:
        if _is_tool_noise(cand):
            continue
        if _SINGLE_JUNK_RE.match(cand):
            continue
        if len(cand) > 140:
            continue
        if len(cand) > 20:
            seen_long = True
            continue
        if seen_long and len(cand) <= 10:
            return cand
    for cand in candidates:
        if _is_tool_noise(cand):
            continue
        if _SINGLE_JUNK_RE.match(cand):
            continue
        if len(cand) > 140:
            continue
        return cand
    return ""


def parse_steps_db(db_path, after_idx=-1, user_step=14, assistant_step=15):
    """Best-effort parser for one conversation-steps SQLite DB.

    Format: a ``steps`` table ``(idx, step_type, step_payload)`` where one
    step type carries the user's message and another the assistant's
    response (binary payloads, e.g. protobuf). The concrete type codes are
    parameters — supplied per source row via ``params`` — not engine logic.
    Returns (turns, new_max_idx) — always a tuple.
    """
    try:
        after_idx = int(after_idx)
    except (TypeError, ValueError):
        after_idx = -1
    try:
        con = _open_ro(db_path)
    except Exception:
        return [], after_idx
    try:
        try:
            rows = con.execute(
                "SELECT idx, step_type, step_payload FROM steps "
                "WHERE step_type IN (?, ?) AND idx > ? "
                "ORDER BY idx ASC LIMIT ?",
                (int(user_step), int(assistant_step), after_idx, SCAN_LIMIT),
            ).fetchall()
        except Exception:
            return [], after_idx
        conv = os.path.splitext(os.path.basename(db_path))[0]
        turns = []
        pending_user = None
        pending_cursor = None
        max_idx = after_idx
        for idx, step_type, payload in rows:
            try:
                max_idx = max(max_idx, int(idx))
            except (TypeError, ValueError):
                pass
            if step_type == user_step:
                best = pick_user_text(extract_text_runs(payload))
                if best:
                    pending_user = best
                    pending_cursor = str(idx)
            elif step_type == assistant_step and pending_user:
                assistant = _pick_assistant_text(extract_text_runs(payload))
                turns.append((conv, pending_cursor, pending_user, assistant))
                pending_user = None
                pending_cursor = None
        # Unpaired user message (response not yet in DB)
        if pending_user:
            turns.append((conv, pending_cursor, pending_user, ""))
        return turns, max_idx
    finally:
        try:
            con.close()
        except Exception:
            pass


def parse_jsonl(path, after_offset=0, limit=SCAN_LIMIT):
    """Generic JSONL transcript parser (role/content shapes)."""
    turns = []
    try:
        size = os.path.getsize(path)
    except OSError:
        return turns, after_offset
    if after_offset >= size:
        return turns, after_offset
    pending_user = None
    offset = int(after_offset)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            fh.seek(offset)
            for _ in range(limit * 4):
                line = fh.readline()
                if not line:
                    break
                offset = fh.tell()
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                role = str(obj.get("role", "")).lower()
                content = obj.get("content", "")
                if isinstance(content, list):
                    parts = [str(b.get("text", "")) for b in content
                             if isinstance(b, dict) and b.get("type") == "text"]
                    content = " ".join(parts)
                text = _clean_text(content)
                if not text:
                    continue
                if role == "user":
                    pending_user = text
                elif role == "assistant" and pending_user:
                    turns.append((os.path.basename(path), str(offset), pending_user, text))
                    pending_user = None
                    if len(turns) >= limit:
                        break
            else:
                pass
    except OSError:
        return turns, after_offset
    if pending_user:
        turns.append((os.path.basename(path), str(offset), pending_user, ""))
    return turns, offset


# --------------------------------------------------------------------------
# Source registry (data, not logic). A source row is:
#   {"id", "kind", "path", "enabled", "params"}
# ``kind`` names the on-disk FORMAT and selects the handler from
# KIND_HANDLERS below. ``id`` is an opaque label (cursor keys, session ids,
# stats) — never branched on. ``params`` overrides parser defaults
# (e.g. {"user_step": 14, "assistant_step": 15}).
# --------------------------------------------------------------------------

# Convenience discovery defaults ONLY — override or extend via environment.
# The engine never sees product names; it sees rows with format kinds.
# (One brand string survives here as a default filesystem path, not logic.)
_DEFAULT_STEPS_DIRS = [
    d for d in (
        os.environ.get("GENESIS_WATCHER_STEPS_DIRS", "").split(os.pathsep)
        if os.environ.get("GENESIS_WATCHER_STEPS_DIRS")
        else [os.path.join(os.path.expanduser("~"), ".gemini",
                           "antigravity-ide", "conversations")]
    ) if d
]
_DEFAULT_CHAT_DBS = [
    p for p in (
        os.environ.get("GENESIS_WATCHER_CHAT_DBS", "").split(os.pathsep)
        if os.environ.get("GENESIS_WATCHER_CHAT_DBS")
        else [os.path.join(os.path.expanduser("~"), ".local", "share",
                           "opencode", "opencode.db")]
    ) if p
]


def default_sources():
    """Build the default source list. Full override via
    GENESIS_WATCHER_SOURCES_JSON (JSON array of rows)."""
    try:
        override = json.loads(os.environ.get("GENESIS_WATCHER_SOURCES_JSON", "") or "null")
    except Exception:
        override = None
    if isinstance(override, list):
        rows = []
        for i, row in enumerate(override):
            if not isinstance(row, dict) or not row.get("kind") or not row.get("path"):
                continue
            rows.append({
                "id": str(row.get("id") or "%s-%d" % (row["kind"], i)),
                "kind": str(row["kind"]),
                "path": str(row["path"]),
                "enabled": bool(row.get("enabled", True)),
                "params": dict(row.get("params") or {}),
            })
        return rows
    sources = [
        {"id": f"steps-{i}", "kind": "steps_dir", "path": d,
         "enabled": True, "params": {}}
        for i, d in enumerate(_DEFAULT_STEPS_DIRS)
    ]
    # A push plugin may already capture this store's turns; the watcher copy
    # stays opt-in to avoid double rows. Flip with GENESIS_WATCHER_CHAT_DB_ENABLE=1.
    chat_enabled = os.environ.get("GENESIS_WATCHER_CHAT_DB_ENABLE", "0") == "1"
    sources += [
        {"id": f"chatdb-{i}", "kind": "chat_parts_db", "path": p,
         "enabled": chat_enabled, "params": {}}
        for i, p in enumerate(_DEFAULT_CHAT_DBS)
    ]
    jsonl_glob = os.environ.get("GENESIS_WATCHER_JSONL", "")
    sources += [
        {"id": f"jsonl-{i}", "kind": "jsonl_file", "path": p,
         "enabled": True, "params": {}}
        for i, p in enumerate(glob.glob(jsonl_glob) if jsonl_glob else [])
    ]
    return sources


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

def _get_cursor(store, source_key):
    try:
        row = store.db.execute(
            "SELECT cursor FROM watcher_state WHERE source = ?", (source_key,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _set_cursor(store, source_key, cursor):
    try:
        store.db.execute(
            "INSERT INTO watcher_state(source, cursor, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at",
            (source_key, str(cursor), time.time()))
        store.db.commit()
    except Exception:
        pass


def _ingest_turns(store, source, turns):
    ingested = 0
    sid = source.get("id") or "unknown"
    for conv_id, cursor, user_text, assistant_text in turns:
        session_id = f"{WATCH_PREFIX}{sid}:{conv_id}:{cursor}"
        try:
            store.set_dialogue(
                session_id=session_id,
                client="watcher",
                user_prompt=user_text,
                assistant_summary=assistant_text or "",
                salient_terms="",
            )
            ingested += 1
        except Exception:
            continue
    return ingested


def _scan_chat_parts_db(store, source, stats):
    """Handler for kind ``chat_parts_db``: single SQLite chat store."""
    if not os.path.exists(source["path"]):
        return
    stats["scanned"] += 1
    sid = source.get("id") or "chatdb"
    cur = _get_cursor(store, sid) or 0
    try:
        cur = int(cur)
    except (TypeError, ValueError):
        cur = 0
    turns, new_cur = parse_chat_parts(source["path"], after_ms=cur)
    n = _ingest_turns(store, source, turns)
    try:
        new_cur = int(new_cur)
    except (TypeError, ValueError):
        new_cur = cur
    if new_cur > cur:
        _set_cursor(store, sid, new_cur)
    stats["ingested"] += n
    stats["sources"][sid] = {"turns": len(turns), "ingested": n}


def _scan_steps_dir(store, source, stats):
    """Handler for kind ``steps_dir``: dir of per-conversation steps DBs."""
    conv_dir = source["path"]
    if not os.path.isdir(conv_dir):
        return
    stats["scanned"] += 1
    sid = source.get("id") or "steps"
    params = source.get("params") or {}
    try:
        user_step = int(params.get("user_step", 14))
        assistant_step = int(params.get("assistant_step", 15))
    except (TypeError, ValueError):
        user_step, assistant_step = 14, 15
    total = ingested = 0
    now = time.time()
    recent = sorted(glob.glob(os.path.join(conv_dir, "*.db")),
                    key=os.path.getmtime, reverse=True)[:20]
    # Insert oldest-first so updated_at recency matches wall-clock
    # recency and latest-N queries return the true latest turns.
    for db_path in reversed(recent):
        try:
            if now - os.path.getmtime(db_path) > SOURCE_MAX_AGE_S:
                continue
        except OSError:
            continue
        key = f"{sid}:{os.path.basename(db_path)}"
        cur = _get_cursor(store, key)
        try:
            after = int(cur) if cur is not None else -1
        except (TypeError, ValueError):
            after = -1
        turns, max_idx = parse_steps_db(
            db_path, after_idx=after,
            user_step=user_step, assistant_step=assistant_step)
        total += len(turns)
        ingested += _ingest_turns(store, source, turns)
        try:
            max_idx = int(max_idx)
        except (TypeError, ValueError):
            continue
        if max_idx > after:
            _set_cursor(store, key, max_idx)
    stats["ingested"] += ingested
    stats["sources"][sid] = {"turns": total, "ingested": ingested}


def _scan_jsonl_file(store, source, stats):
    """Handler for kind ``jsonl_file``: single JSONL transcript."""
    if not os.path.exists(source["path"]):
        return
    stats["scanned"] += 1
    sid = source.get("id") or "jsonl"
    key = f"{sid}:{source['path']}"
    cur = _get_cursor(store, key) or 0
    try:
        off = int(cur)
    except (TypeError, ValueError):
        off = 0
    turns, new_off = parse_jsonl(source["path"], after_offset=off)
    n = _ingest_turns(store, source, turns)
    try:
        new_off = int(new_off)
    except (TypeError, ValueError):
        new_off = off
    if new_off > off:
        _set_cursor(store, key, new_off)
    stats["ingested"] += n
    stats["sources"][sid] = {"turns": len(turns), "ingested": n}


# Format registry: kind -> handler. New FORMAT = one function + one line
# here. New SOURCE of a known format = one data row, zero code.
KIND_HANDLERS = {
    "chat_parts_db": _scan_chat_parts_db,
    "steps_dir": _scan_steps_dir,
    "jsonl_file": _scan_jsonl_file,
}


def scan_once(store, sources=None):
    """Single passive sweep over all enabled sources. Returns stats dict."""
    sources = sources if sources is not None else default_sources()
    stats = {"scanned": 0, "ingested": 0, "sources": {}}
    for position, source in enumerate(sources):
        if not source.get("enabled", True):
            continue
        kind = source.get("kind")
        if not source.get("id"):
            source["id"] = f"{kind or 'source'}-{position}"
        try:
            handler = KIND_HANDLERS.get(kind)
            if handler is None:
                log.warning("watcher unknown kind id=%s kind=%s",
                            source.get("id"), kind)
                continue
            handler(store, source, stats)
        except Exception as exc:
            log.warning("watcher source failed id=%s err=%s",
                        source.get("id"), type(exc).__name__)
    try:
        cutoff = time.time() - WATCH_TTL_S
        store.db.execute(
            "DELETE FROM dialogue_buffer WHERE session_id LIKE 'watch:%' AND updated_at < ?",
            (cutoff,))
        store.db.commit()
    except Exception:
        pass
    try:
        store._inc_counter("watcher_scans")
        if stats["ingested"]:
            store._inc_counter("watcher_ingested", stats["ingested"])
    except Exception:
        pass
    # Lengths-only telemetry; content never logged.
    log.info("watcher scan scanned=%d ingested=%d", stats["scanned"], stats["ingested"])
    return stats


_THREAD = None
_STOP = threading.Event()


def _loop(store, interval_s):
    while not _STOP.wait(interval_s):
        try:
            scan_once(store)
        except Exception as exc:
            log.warning("watcher loop err=%s", type(exc).__name__)


def start_watcher(store, interval_s=SCAN_INTERVAL_S):
    """Start the background watcher thread (idempotent)."""
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return _THREAD
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, args=(store, interval_s),
                               name="genesis-watcher", daemon=True)
    _THREAD.start()
    return _THREAD


def stop_watcher():
    _STOP.set()

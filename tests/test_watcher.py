"""Tests for the universal transcript watcher (passive cross-client capture).

Covers the +2 post-mortem contract:
- Generic engine: new client = new source row, no new logic.
- Read-only on client files; bounded rows via set_dialogue caps.
- Cursor dedup: second scan ingests nothing new.
- TTL prune keeps watcher rows bounded.
"""

import json
import os
import sqlite3
import time

import pytest

from genesis_memory.daemon.server import Store
from genesis_memory.daemon import watcher
from genesis_memory.daemon.watcher import (
    KIND_HANDLERS,
    _pick_assistant_text,
    default_sources,
    extract_text_runs,
    parse_chat_parts,
    parse_jsonl,
    parse_steps_db,
    scan_once,
)


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "memory.db")
    st = Store(db_path)
    yield st
    st.db.close()


def _make_steps_db(path, user_texts, assistant_texts=None,
                   user_step=14, assistant_step=15):
    """Synthetic conversation-steps DB: user steps + assistant steps + noise."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE steps(idx INTEGER PRIMARY KEY, step_type INTEGER, "
        "step_payload BLOB, render_info BLOB)")
    if assistant_texts is None:
        assistant_texts = [""] * len(user_texts)
    # protobuf-ish framing around the text (length byte + junk)
    idx = 0
    for i, text in enumerate(user_texts):
        payload = b"\x08\x0f \x03*\xc1\x02" + text.encode("utf-8") + b"\xa8\x01\x01"
        con.execute("INSERT INTO steps(idx, step_type, step_payload) VALUES(?, ?, ?)",
                    (idx, user_step, payload))
        idx += 1
        # noise step (tool output containing '+2' must be ignored)
        con.execute("INSERT INTO steps(idx, step_type, step_payload) VALUES(?, 21, ?)",
                    (idx, b"diff --git a/x\n+++ b/x\n@@ +2,7 @@"))
        idx += 1
        # assistant response
        if i < len(assistant_texts) and assistant_texts[i]:
            resp_payload = b"\x08\x0f" + assistant_texts[i].encode("utf-8") + b"\xa8\x01\x01"
            con.execute("INSERT INTO steps(idx, step_type, step_payload) VALUES(?, ?, ?)",
                        (idx, assistant_step, resp_payload))
            idx += 1
    con.commit()
    con.close()


def test_extract_text_runs_filters_ids_and_hashes():
    blob = (b"\x08\x0f" + "6ebf74c3-e1d4-4777-aa7d-184ad3df3e50".encode()
            + b"\x10\x06" + "1+1=?".encode() + b"\xa8\x01")
    cands = extract_text_runs(blob)
    assert "1+1=?" in cands
    assert not any("6ebf74c3" in c for c in cands)


def test_parse_steps_db_pairs_user_and_assistant(tmp_path):
    db = str(tmp_path / "conv.db")
    _make_steps_db(db, ["1+1=?", "+2"], ["2", "4"])
    turns, _ = parse_steps_db(db)
    assert [(t[2], t[3]) for t in turns] == [("1+1=?", "2"), ("+2", "4")]
    # cursor scoping: only new steps
    turns2, _ = parse_steps_db(db, after_idx=0)
    assert [(t[2], t[3]) for t in turns2] == [("+2", "4")]


def test_step_type_codes_are_row_params(tmp_path):
    """Step codes are NOT hardcoded: a store using 1/2 parses via params."""
    db = str(tmp_path / "conv.db")
    _make_steps_db(db, ["1+1=?"], ["2"], user_step=1, assistant_step=2)
    assert parse_steps_db(db) == ([], -1)  # defaults see nothing
    turns, _ = parse_steps_db(db, user_step=1, assistant_step=2)
    assert [(t[2], t[3]) for t in turns] == [("1+1=?", "2")]


def test_parse_steps_db_unpaired_user(tmp_path):
    """Unpaired user message (no assistant yet) still captured."""
    db = str(tmp_path / "conv.db")
    _make_steps_db(db, ["hello?"], [""])
    turns, _ = parse_steps_db(db)
    assert turns == [("conv", "0", "hello?", "")]


def test_parse_steps_db_only_user_channel(tmp_path):
    """Legacy: no assistant steps at all → last user message captured."""
    db = str(tmp_path / "conv.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE steps(idx INTEGER PRIMARY KEY, step_type INTEGER, "
                "step_payload BLOB, render_info BLOB)")
    for i, text in enumerate(["1+1=?", "+2"]):
        payload = b"\x08\x0f \x03*\xc1\x02" + text.encode("utf-8") + b"\xa8\x01\x01"
        con.execute("INSERT INTO steps(idx, step_type, step_payload) VALUES(?, 14, ?)",
                    (i * 2, payload))
    con.commit()
    con.close()
    turns, _ = parse_steps_db(db)
    # Only last unpaired user message survives (first overwrites pending)
    assert turns[0][2] == "+2"


def test_pick_assistant_star_prefix_short():
    """`*`-prefixed short fragment (protobuf field 5) is the answer."""
    cands = ["2", "0", "B!\n\tsessionID", "h" * 26, "*۴", "*2(bot-1234", "M2"]
    assert _pick_assistant_text(cands) == "۴"


def test_pick_assistant_star_prefix_join():
    """Multiple `*`-fragments concatenate into the full response."""
    cands = ["2", "B!\n\tsessionID", "h" * 26,
             "*بله، لازم است.", "*\n\nدلایل:", "*اعتبار پروژه"]
    assert _pick_assistant_text(cands) == "بله، لازم است. \n\nدلایل: اعتبار پروژه"


def test_pick_assistant_short_after_metadata():
    """Lone `*` marker split from answer → first short after metadata."""
    cands = ["*", "2", "0", "B!\n\tsessionID", "h" * 23,
             "52(bot-5285a675-xxx", "5", "M2"]
    assert _pick_assistant_text(cands) == "5"


def test_pick_assistant_fallback_first_clean():
    """No markers, no metadata → first clean candidate."""
    assert _pick_assistant_text(["2"]) == "2"
    assert _pick_assistant_text([]) == ""


def _make_chat_parts_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE message(id TEXT PRIMARY KEY, session_id TEXT, "
                "time_created INTEGER, time_updated INTEGER, data TEXT)")
    con.execute("CREATE TABLE part(id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
                "time_created INTEGER, time_updated INTEGER, data TEXT)")
    con.execute("INSERT INTO message VALUES('m1','s1',1000,1000,?)",
                (json.dumps({"role": "user"}),))
    con.execute("INSERT INTO part VALUES('p1','m1','s1',1000,1000,?)",
                (json.dumps({"type": "text", "text": "1+1=?"}),))
    con.execute("INSERT INTO message VALUES('m2','s1',2000,2000,?)",
                (json.dumps({"role": "assistant"}),))
    con.execute("INSERT INTO part VALUES('p2','m2','s1',2000,2000,?)",
                (json.dumps({"type": "text", "text": "2"}),))
    con.commit()
    con.close()


def test_parse_chat_parts_pairs_turns(tmp_path):
    db = str(tmp_path / "chatparts.db")
    _make_chat_parts_db(db)
    turns, cursor = parse_chat_parts(db)
    assert turns == [("s1", "2000", "1+1=?", "2")]
    assert cursor == 2000
    turns2, _ = parse_chat_parts(db, after_ms=cursor)
    assert turns2 == []


def test_scan_once_ingests_and_dedups(store, tmp_path):
    db = str(tmp_path / "conv.db")
    _make_steps_db(db, ["1+1=?", "+2"], ["2", "4"])
    sources = [{"id": "steps-e2e", "kind": "steps_dir",
                "path": str(tmp_path), "enabled": True}]
    stats = scan_once(store, sources)
    assert stats["ingested"] == 2
    rows = store.db.execute(
        "SELECT session_id, user_prompt, assistant_summary FROM dialogue_buffer "
        "WHERE session_id LIKE 'watch:%' ORDER BY session_id").fetchall()
    assert len(rows) == 2
    assert all(sid.startswith("watch:steps-e2e:") for sid, _, _ in rows)
    prompts = sorted(p for _, p, _ in rows)
    assert prompts == ["+2", "1+1=?"]
    summaries = {p: s for _, p, s in rows}
    assert summaries["1+1=?"] == "2"
    assert summaries["+2"] == "4"
    # second sweep: cursor dedup, nothing new
    stats2 = scan_once(store, sources)
    assert stats2["ingested"] == 0


def test_scan_once_bounds_lengths(store, tmp_path):
    db = str(tmp_path / "conv.db")
    _make_steps_db(db, ["x" * 5000], ["ok"])
    sources = [{"id": "steps-e2e", "kind": "steps_dir",
                "path": str(tmp_path), "enabled": True}]
    scan_once(store, sources)
    row = store.db.execute(
        "SELECT user_prompt FROM dialogue_buffer WHERE session_id LIKE 'watch:%'").fetchone()
    assert row is not None
    assert len(row[0]) <= 250  # set_dialogue cap


def test_new_client_is_one_source_row(store, tmp_path):
    """Generic claim: a new JSONL client works with zero new parser code."""
    jl = tmp_path / "newclient.jsonl"
    jl.write_text(
        json.dumps({"role": "user", "content": "deploy bee?"}) + "\n"
        + json.dumps({"role": "assistant", "content": "done"}) + "\n",
        encoding="utf-8")
    sources = [{"id": "jsonl-new", "kind": "jsonl_file",
                "path": str(jl), "enabled": True}]
    stats = scan_once(store, sources)
    assert stats["ingested"] == 1
    row = store.db.execute(
        "SELECT user_prompt, assistant_summary FROM dialogue_buffer "
        "WHERE session_id LIKE 'watch:%'").fetchone()
    assert row == ("deploy bee?", "done")


def test_registry_has_no_brand_names():
    """Contract: format kinds never name products. New product ≠ new kind."""
    brands = ("antigravity", "opencode", "gemini", "cursor", "windsurf",
              "claude", "copilot", "aider")
    for kind in KIND_HANDLERS:
        assert not any(b in kind.lower() for b in brands), kind


def test_unknown_kind_skipped_safely(store, tmp_path):
    sources = [{"id": "mystery", "kind": "does_not_exist",
                "path": str(tmp_path), "enabled": True}]
    stats = scan_once(store, sources)
    assert stats["ingested"] == 0
    assert stats["scanned"] == 0


def test_new_format_is_one_registry_line(store, tmp_path):
    """Contract: a new FORMAT needs one handler + one registry line —
    scan_once itself is never edited (proven by registering at runtime)."""
    marker = tmp_path / "ping.txt"
    marker.write_text("hello?", encoding="utf-8")

    def _scan_ping(store, source, stats):
        from genesis_memory.daemon.watcher import _get_cursor, _set_cursor, _ingest_turns
        if _get_cursor(store, "ping-test") is not None:
            return
        stats["scanned"] += 1
        n = _ingest_turns(store, source, [("c", "0", "hello?", "hi")])
        _set_cursor(store, "ping-test", "done")
        stats["ingested"] += n

    KIND_HANDLERS["ping_test_kind"] = _scan_ping
    try:
        sources = [{"id": "ping", "kind": "ping_test_kind",
                    "path": str(marker), "enabled": True}]
        assert scan_once(store, sources)["ingested"] == 1
        assert scan_once(store, sources)["ingested"] == 0  # cursor holds
    finally:
        del KIND_HANDLERS["ping_test_kind"]


def test_watcher_rows_pruned_after_ttl(store, tmp_path, monkeypatch):
    db = str(tmp_path / "conv.db")
    _make_steps_db(db, ["hello?"])
    sources = [{"id": "steps-e2e", "kind": "steps_dir",
                "path": str(tmp_path), "enabled": True}]
    scan_once(store, sources)
    assert store.db.execute(
        "SELECT COUNT(*) FROM dialogue_buffer WHERE session_id LIKE 'watch:%'").fetchone()[0] == 1
    monkeypatch.setattr(watcher, "WATCH_TTL_S", -1)  # everything already expired
    stats = scan_once(store, sources)
    assert stats["ingested"] == 0  # cursor still holds; only prune runs
    assert store.db.execute(
        "SELECT COUNT(*) FROM dialogue_buffer WHERE session_id LIKE 'watch:%'").fetchone()[0] == 0


def test_tool_noise_never_ingested(store, tmp_path):
    """Browser/tool echoes in user-channel blobs must not become turns."""
    db = str(tmp_path / "conv.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE steps(idx INTEGER PRIMARY KEY, step_type INTEGER, "
                "step_payload BLOB, render_info BLOB)")
    noise = ('execute_url(it-tools.tech) "execute_url(opensource.google.com) '
             "(execute_url(summerofcode.withgoogle.com)").encode()
    con.execute("INSERT INTO steps VALUES(0, 14, ?, NULL)", (noise,))
    con.execute("INSERT INTO steps VALUES(2, 14, ?, NULL)", ("1+1=?".encode(),))
    con.execute("INSERT INTO steps VALUES(3, 15, ?, NULL)", ("2".encode(),))
    con.commit()
    con.close()
    sources = [{"id": "steps-e2e", "kind": "steps_dir",
                "path": str(tmp_path), "enabled": True}]
    stats = scan_once(store, sources)
    assert stats["ingested"] == 1
    row = store.db.execute(
        "SELECT user_prompt, assistant_summary FROM dialogue_buffer "
        "WHERE session_id LIKE 'watch:%'").fetchone()
    assert row == ("1+1=?", "2")


def test_stale_conversations_skipped(store, tmp_path, monkeypatch):
    """History import is out of scope: untouched chats are not backfilled."""
    db = str(tmp_path / "conv.db")
    _make_steps_db(db, ["old question?"], ["old answer"])
    old = time.time() - 10 * 86400
    os.utime(db, (old, old))
    monkeypatch.setattr(watcher, "SOURCE_MAX_AGE_S", 86400)
    sources = [{"id": "steps-e2e", "kind": "steps_dir",
                "path": str(tmp_path), "enabled": True}]
    stats = scan_once(store, sources)
    assert stats["ingested"] == 0


def test_catalog_and_byte_artifacts_lose_to_message():
    """Tool-catalog fragments and length-byte prefixes never win scoring."""
    from genesis_memory.daemon.watcher import pick_user_text, _clean_text
    assert _clean_text("S Qچرا؟") == "چرا؟"
    assert _clean_text("7مگه؟") == "مگه؟"
    winner = pick_user_text([
        "Rules-17-21.md)\nbwrite_file(c:",
        'env:PYTHONIOENCODING="utf-8";)\ncommand(',
        "+2",
    ])
    assert winner == "+2"

"""Tests for GENESIS Cross-Session and Cross-Client Dialogue Buffer (Schema v7).

Verifies:
1. Schema v7 migration and dialogue_buffer table integrity.
2. Store.set_dialogue & Store.get_latest_dialogue with secrets redaction (scan_secrets).
3. TTL enforcement (1800s window) and session isolation.
4. Entity-aware Dialogue Synthesizer (<140 chars / ~35 tokens).
5. Subconscious memory hook injection of [Last Dialogue Turn] within 200-token budget.
"""

import os
import sqlite3
import tempfile
import time
import pytest

from genesis_memory.daemon.server import Store, SCHEMA_VERSION
from genesis_memory.proxy.dialogue_synthesizer import (
    synthesize_dialogue_summary,
    extract_salient_entities,
    extract_core_takeaway,
)
from genesis_memory.proxy.proxy_server import GenesisProxyServer
from genesis_memory.hooks import subconscious_hook as hook


@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "test_memory.db")
    store = Store(db_path)
    yield store, db_path
    store.db.close()


def test_schema_v7_dialogue_buffer_table(temp_db):
    """Test 1: Schema v7 creates dialogue_buffer table idempotently."""
    store, db_path = temp_db
    assert store.schema_version >= 7

    cur = store.db.cursor()
    cur.execute("PRAGMA table_info(dialogue_buffer)")
    cols = {row[1]: row[2] for row in cur.fetchall()}
    expected_cols = {
        "session_id": "TEXT",
        "client": "TEXT",
        "updated_at": "REAL",
        "user_prompt": "TEXT",
        "assistant_summary": "TEXT",
        "salient_terms": "TEXT",
    }
    for col, col_type in expected_cols.items():
        assert col in cols, f"Missing column {col} in dialogue_buffer"


def test_store_dialogue_persist_and_secrets_redaction(temp_db):
    """Test 2: Store.set_dialogue redacts secrets and persists cleanly."""
    store, _ = temp_db

    # Insert turn with an OpenAI API key and GitHub token
    secret_prompt = "Here is my secret sk-proj-1234567890abcdef1234567890 and ghp_abcdefghijklmnopqrstuvwxyz123456"
    assistant_resp = "یک‌خطی: کلید شما شناسایی و محافظت شد در genesis_daemon.py با AuthError"

    res = store.set_dialogue(
        session_id="sess-001",
        client="opencode",
        user_prompt=secret_prompt,
        assistant_summary=assistant_resp,
        salient_terms=["genesis_daemon.py", "AuthError"],
    )
    assert res.get("status") == "updated"

    # Verify retrieval
    dialogue = store.get_latest_dialogue(session_id="sess-001")
    assert dialogue is not None
    assert dialogue["client"] == "opencode"
    assert "sk-proj-" not in dialogue["user_prompt"]
    assert "[REDACTED_API_KEY]" in dialogue["user_prompt"]
    assert "ghp_" not in dialogue["user_prompt"]
    assert "AuthError" in dialogue["salient_terms"]


def test_dialogue_ttl_enforcement(temp_db):
    """Test 3: Dialogue turns older than 1800s expire under TTL."""
    store, _ = temp_db

    old_ts = time.time() - 1900  # 1900 seconds ago (>1800s TTL)
    cur = store.db.cursor()
    cur.execute(
        """
        INSERT INTO dialogue_buffer (session_id, client, updated_at, user_prompt, assistant_summary, salient_terms)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("sess-old", "cursor", old_ts, "Old prompt", "Old summary", "[]"),
    )
    store.db.commit()

    # Without fallback: expired turn returns None
    assert store.get_latest_dialogue(session_id="sess-old", max_age_s=1800, fallback=False) is None

    # With fallback: returns row marked as stale
    stale = store.get_latest_dialogue(session_id="sess-old", max_age_s=1800, fallback=True)
    assert stale is not None
    assert stale["stale"] is True


def test_entity_aware_synthesizer():
    """Test 4: Synthesizer extracts entities and core takeaway within 140 chars."""
    verbose_text = """
    من بررسی کردم و به این نتیجه رسیدم:
    مشکل در فایل src/genesis/proxy/proxy_server.py بود که هنگام پرتاب ConnectionResetError
    کرش می‌کرد. خط ۱۲۴ دچار ناهماهنگی شده بود.

    یک‌خطی: نشت استریم با هندل کردن ConnectionResetError در proxy_server.py برطرف شد.
    """
    summary, salient = synthesize_dialogue_summary(verbose_text, max_chars=140)

    assert len(summary) <= 140
    assert "proxy_server.py" in summary or "proxy_server.py" in salient
    assert "ConnectionResetError" in summary or "ConnectionResetError" in salient
    assert "برطرف شد" in summary or "نشت استریم" in summary


def test_subconscious_hook_dialogue_injection(temp_db, monkeypatch):
    """Test 5: subconscious_memory_hook injects [Last Dialogue Turn] under budget."""
    store, db_path = temp_db

    # Seed an active thread
    store.set_thread(
        topic="Cross-Session Memory Continuity",
        summary="Unified dialogue buffer across clients",
        recent_files=["proxy_server.py"],
        pending_focus="Run verification suite",
        client="antigravity",
    )

    # Seed a dialogue turn
    store.set_dialogue(
        session_id="sess-active",
        client="opencode",
        user_prompt="خوب حالا بریم سراغ پیاده سازیش؟",
        assistant_summary="یک‌خطی: شروع پیاده‌سازی dialogue_buffer و اتصال به proxy_server",
        salient_terms=["dialogue_buffer", "proxy_server.py"],
    )

    # Point hook to temp DB
    monkeypatch.setattr(hook, "DB_PATH", db_path)

    formatted, telemetry = hook.query_subconscious_memories("این چیزی که گفت چی بود؟", max_tokens=200)

    full_output = "\n".join(formatted)
    assert "• [Active Work Thread" in full_output
    assert "• [Last Dialogue Turn" in full_output
    assert "خوب حالا بریم سراغ پیاده سازیش؟" in full_output
    assert "dialogue_buffer" in full_output
    assert telemetry["injected_tokens"] <= 200


def test_salient_terms_secrets_redaction(temp_db):
    """Test 6: Salient terms strictly redact secrets per OpenCode v7.1 audit."""
    store, _ = temp_db

    secret_key = "sk-proj-999888777666555444333222111"
    res = store.set_dialogue(
        session_id="sess-leak-check",
        client="opencode",
        user_prompt="Testing salient terms protection",
        assistant_summary="Summary text",
        salient_terms=["safe_term", secret_key],
    )
    assert res.get("status") == "updated"

    d = store.get_latest_dialogue(session_id="sess-leak-check")
    assert d is not None
    assert secret_key not in d["salient_terms"]
    assert "[REDACTED_API_KEY]" in d["salient_terms"]
    assert "safe_term" in d["salient_terms"]


def test_multi_session_isolation_sess_a_b(temp_db):
    """Test 7: Sessions A and B remain isolated without cross-session clobbering."""
    store, _ = temp_db

    # Turn in Session A
    store.set_dialogue(
        session_id="sess-A",
        client="opencode",
        user_prompt="Prompt from Session A",
        assistant_summary="Summary A",
        salient_terms=["module_a.py"],
    )

    time.sleep(0.01)

    # Turn in Session B
    store.set_dialogue(
        session_id="sess-B",
        client="cursor",
        user_prompt="Prompt from Session B",
        assistant_summary="Summary B",
        salient_terms=["module_b.py"],
    )

    # Both sessions exist independently
    row_a = store.get_latest_dialogue(session_id="sess-A")
    row_b = store.get_latest_dialogue(session_id="sess-B")
    assert row_a is not None and row_b is not None
    assert row_a["user_prompt"] == "Prompt from Session A"
    assert row_b["user_prompt"] == "Prompt from Session B"
    assert row_a["client"] == "opencode"
    assert row_b["client"] == "cursor"

    # Global latest without session_id returns the most recent (sess-B)
    latest_global = store.get_latest_dialogue(session_id=None)
    assert latest_global["session_id"] == "sess-B"


def _hook_rows(store, client):
    """All per-turn hook rows for a client, newest first."""
    cur = store.db.cursor()
    cur.execute(
        "SELECT session_id, user_prompt, assistant_summary FROM dialogue_buffer "
        "WHERE session_id LIKE ? ORDER BY updated_at DESC",
        (f"hook:{client}:%",),
    )
    return cur.fetchall()


def test_hook_ambient_capture_writes_when_stale(temp_db, monkeypatch):
    """Layer 1: direct-model turn with no proxy-fresh row is shared cross-client."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    formatted, telemetry = hook.query_subconscious_memories(
        "What did we decide about retry policy?",
        client="opencode", capture_turn=True, max_tokens=200,
    )
    assert telemetry["ambient_captured"] is True
    rows = _hook_rows(store, "opencode")
    assert len(rows) == 1
    assert "retry policy" in rows[0][1]
    assert rows[0][0].startswith("hook:opencode:")


def test_hook_ambient_capture_skips_proxy_fresh(temp_db, monkeypatch):
    """Layer 1: a proxy-fresh row (<60s, non-hook session) suppresses the write."""
    import time
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    store.set_dialogue(session_id="default", client="proxy",
                       user_prompt="Proxy turn just now",
                       assistant_summary="ack", salient_terms=[])
    formatted, telemetry = hook.query_subconscious_memories(
        "Direct turn while proxy is fresh",
        client="opencode", capture_turn=True, max_tokens=200,
    )
    assert telemetry["ambient_captured"] is False
    assert _hook_rows(store, "opencode") == []


def test_hook_ambient_capture_writes_when_proxy_stale(temp_db, monkeypatch):
    """Layer 1: a proxy row older than 60s no longer suppresses capture."""
    import sqlite3
    import time
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    store.set_dialogue(session_id="default", client="proxy",
                       user_prompt="Old proxy turn",
                       assistant_summary="ack", salient_terms=[])
    con = sqlite3.connect(db_path)
    con.execute("UPDATE dialogue_buffer SET updated_at = ?",
                (time.time() - 120.0,))
    con.commit()
    con.close()
    _, telemetry = hook.query_subconscious_memories(
        "Direct turn after stale proxy",
        client="antigravity", capture_turn=True, max_tokens=200,
    )
    assert telemetry["ambient_captured"] is True
    rows = _hook_rows(store, "antigravity")
    assert len(rows) == 1 and "stale proxy" in rows[0][1]


def test_hook_ambient_capture_redacts_secrets(temp_db, monkeypatch):
    """Layer 1: captured prompts pass the shared secrets redaction."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    _, telemetry = hook.query_subconscious_memories(
        "Deploy with sk-abcdefghijklmnopqrst1234 now",
        client="opencode", capture_turn=True, max_tokens=200,
    )
    assert telemetry["ambient_captured"] is True
    rows = _hook_rows(store, "opencode")
    assert len(rows) == 1
    assert "sk-abcdefghijklmnopqrst1234" not in rows[0][1]
    assert "Deploy with" in rows[0][1]


def test_hook_ambient_capture_fail_open_on_lock(temp_db, monkeypatch):
    """Layer 1 (C1): a locked WAL never breaks the read path or raises."""
    import sqlite3
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    locker = sqlite3.connect(db_path, timeout=5.0)
    locker.execute("BEGIN EXCLUSIVE")
    locker.execute("CREATE TABLE IF NOT EXISTS _lock_probe(x)")
    try:
        formatted, telemetry = hook.query_subconscious_memories(
            "Hello under lock?", client="opencode", capture_turn=True,
            max_tokens=200,
        )
    finally:
        locker.rollback()
        locker.close()
    assert isinstance(formatted, list)
    assert telemetry["ambient_captured"] is False


def test_hook_no_capture_by_default(temp_db, monkeypatch):
    """Layer 1: plain queries (daemon --capsule path) never write."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    hook.query_subconscious_memories("Just reading", max_tokens=200)
    assert store.get_latest_dialogue(session_id="hook:unknown") is None
    cur = store.db.cursor()
    cur.execute("SELECT COUNT(*) FROM dialogue_buffer")
    assert cur.fetchone()[0] == 0


def test_detect_client_cases():
    assert hook._detect_client(["hook.py", "--cursor"], {}) == "cursor"
    assert hook._detect_client(["hook.py", "--claude"], {}) == "claude"
    assert hook._detect_client(["hook.py"], {"transcriptPath": "/tmp/t.jsonl"}) == "antigravity"
    assert hook._detect_client(["hook.py", "hello", "--raw"], {}) == "opencode"
    assert hook._detect_client(["hook.py"], {}) == "unknown"


def test_parse_git_files_cases():
    assert hook._parse_git_files(" M src/a.py\n?? new.txt\n") == ["a.py", "new.txt"]
    assert hook._parse_git_files('R  old.py -> new.py\n') == ["new.py"]
    assert hook._parse_git_files("") == []
    assert hook._parse_git_files(None) == []
    assert len(hook._parse_git_files("\n".join(f" M f{i}.py" for i in range(20)))) == 5


def test_micro_directive_present_and_budgeted(temp_db, monkeypatch):
    """Layer 3: one-line thread_update reminder inside the token budget."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    formatted, telemetry = hook.query_subconscious_memories("status?", max_tokens=200)
    assert telemetry["micro_applied"] is True
    assert any("thread_update" in line for line in formatted)
    assert telemetry["injected_tokens"] <= 200


def test_hook_delta_line_absent_off_repo(temp_db, monkeypatch, tmp_path):
    """Layer 2: without a git tree the hook stays silent (no crash, no line)."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    monkeypatch.setattr(hook, "REPO_ROOT", str(tmp_path))
    formatted, _ = hook.query_subconscious_memories("status?", max_tokens=200)
    assert not any("Delta:" in line for line in formatted)


def test_hook_renders_three_fresh_turns(temp_db, monkeypatch):
    """Continuity+ (3): latest full + up to two one-liners, all fresh."""
    import sqlite3
    import time
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    now = time.time()
    con = sqlite3.connect(db_path)
    rows = [
        ("s-old", "opencode", now - 100, "First question here", "First answer here", ""),
        ("s-mid", "opencode", now - 50, "Second question here", "Second answer here", ""),
        ("s-new", "opencode", now - 5, "Third question here", "Third answer here", ""),
    ]
    con.executemany(
        "INSERT INTO dialogue_buffer(session_id, client, updated_at, user_prompt, "
        "assistant_summary, salient_terms) VALUES(?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    formatted, telemetry = hook.query_subconscious_memories("And then?", max_tokens=300)
    assert telemetry["dialogue_turns"] == 3
    assert telemetry["boosted"] is False
    text = "\n".join(formatted)
    assert "Third question here" in text
    assert "Earlier Turn" in text
    assert "Second question here" in text


def test_hook_skips_stale_middle_turn(temp_db, monkeypatch):
    """Continuity+ (3): a stale middle turn is skipped, fresh ones render."""
    import sqlite3
    import time
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    now = time.time()
    con = sqlite3.connect(db_path)
    con.executemany(
        "INSERT INTO dialogue_buffer(session_id, client, updated_at, user_prompt, "
        "assistant_summary, salient_terms) VALUES(?, ?, ?, ?, ?, ?)",
        [("s-old", "opencode", now - 5000, "Ancient question here", "Ancient answer", ""),
         ("s-new", "opencode", now - 5, "Fresh question here", "Fresh answer", "")])
    con.commit()
    con.close()
    formatted, telemetry = hook.query_subconscious_memories("And then?", max_tokens=300)
    text = "\n".join(formatted)
    assert "Fresh question here" in text
    assert "Ancient question here" not in text
    assert telemetry["dialogue_turns"] == 1


def test_hook_fresh_session_boost(temp_db, monkeypatch):
    """Continuity+ (2): no fresh dialogue => one-time boost flag + room."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    _, telemetry = hook.query_subconscious_memories("Hello new session", max_tokens=200)
    assert telemetry["boosted"] is True
    assert telemetry["dialogue_turns"] == 0


def test_hook_recall_hint_present_and_budgeted(temp_db, monkeypatch):
    """Continuity+ (1): behavioral recall line, no hardcoded trigger words."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    formatted, telemetry = hook.query_subconscious_memories("status?", max_tokens=200)
    assert telemetry["recall_hint_applied"] is True
    hint = next(line for line in formatted if "Recall rule" in line)
    for word in ("this", "that", "it", "همین", "اون"):
        assert word not in hint.split("referring")[0]  # no trigger-word lists
    assert telemetry["injected_tokens"] <= 200


def _seed_thread(store):
    store.set_thread(
        topic="Diet flag plumbing",
        summary="Hook diet line for direct-model clients",
        recent_files=[], pending_focus="", client="ci",
    )


def test_hook_diet_off_by_default(temp_db, monkeypatch):
    """Hook diet is flag-gated OFF unless GENESIS_OUTPUT_DIET=1."""
    store, db_path = temp_db
    _seed_thread(store)
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    monkeypatch.delenv("GENESIS_OUTPUT_DIET", raising=False)
    formatted, telemetry = hook.query_subconscious_memories("status?", max_tokens=200)
    assert telemetry["diet_applied"] is False
    assert not any("Answer style" in line for line in formatted)


def test_hook_diet_appended_last_within_budget(temp_db, monkeypatch):
    """Enabled diet lands LAST, inside the token budget, and is counted."""
    store, db_path = temp_db
    _seed_thread(store)
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    monkeypatch.setenv("GENESIS_OUTPUT_DIET", "1")
    formatted, telemetry = hook.query_subconscious_memories("status?", max_tokens=200)
    assert telemetry["diet_applied"] is True
    assert formatted[-1].startswith("• [Answer style]")
    assert "byte-for-byte" in formatted[-1]
    assert telemetry["injected_tokens"] <= 200


def test_hook_diet_yields_to_memory_on_tiny_budget(temp_db, monkeypatch):
    """Memory outranks style: diet drops when it does not fit."""
    store, db_path = temp_db
    _seed_thread(store)
    # Fresh dialogue => no boost, so the tiny caller budget strictly applies.
    store.set_dialogue(session_id="s-keep", client="opencode",
                       user_prompt="Keep me", assistant_summary="ack",
                       salient_terms=[])
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    monkeypatch.setenv("GENESIS_OUTPUT_DIET", "1")
    formatted, telemetry = hook.query_subconscious_memories("status?", max_tokens=5)
    assert telemetry["boosted"] is False
    assert telemetry["diet_applied"] is False
    assert not any("Answer style" in line for line in formatted)
    # Thread (memory) still present: content beats style
    assert any("Active Work Thread" in line for line in formatted)


def _write_transcript(path, lines):
    import json
    with open(path, "w", encoding="utf-8") as f:
        for obj in lines:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def test_assistant_text_extraction_shapes(tmp_path):
    tr = str(tmp_path / "t.jsonl")
    _write_transcript(tr, [
        {"type": "USER_INPUT", "content": "status?"},
        {"type": "ASSISTANT_TOOL_CALL", "content": "{\"tool\": \"x\"}"},
        {"role": "assistant", "content": "First answer 11"},
        {"type": "ASSISTANT_MESSAGE", "content": "Latest answer 34 <b>bold</b>"},
    ])
    got = hook.get_latest_assistant_text(tr)
    assert got == "Latest answer 34 bold"
    assert hook.get_latest_assistant_text(str(tmp_path / "nope.jsonl")) == ""


def test_capture_stores_assistant_side(temp_db, monkeypatch):
    """Assistant replies are captured redacted: follow-ups can resolve them."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    _, telemetry = hook.query_subconscious_memories(
        "What is the status?", client="opencode", capture_turn=True,
        capture_assistant="34 conflicts sit in the resolve queue",
        max_tokens=200,
    )
    assert telemetry["ambient_captured"] is True
    rows = _hook_rows(store, "opencode")
    assert len(rows) == 1
    assert "34 conflicts" in rows[0][2]


def test_cross_client_which_34_regression(temp_db, monkeypatch):
    """The failed dogfood case: tab A answer mentions a count, tab B asks
    'which 34?' — the new tab's capsule must carry the 34 context."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    # Tab A (opencode): status turn incl. assistant answer with the count.
    hook.query_subconscious_memories(
        "How is the project?", client="opencode", capture_turn=True,
        capture_assistant="Health ok, 34 conflicts sit in the resolve queue",
        max_tokens=200,
    )
    # Tab B (antigravity): bare referential follow-up.
    formatted, _ = hook.query_subconscious_memories(
        "کدوم 34 تا؟", client="antigravity", capture_turn=True,
        capture_assistant="", max_tokens=200,
    )
    assert "34 conflicts" in "\n".join(formatted)


def test_grounding_line_present_and_budgeted(temp_db, monkeypatch):
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    formatted, telemetry = hook.query_subconscious_memories("status?", max_tokens=200)
    assert telemetry["grounding_applied"] is True
    assert any("Grounding rule" in line for line in formatted)
    assert any("where it came from" in line for line in formatted)
    assert telemetry["injected_tokens"] <= 200


def test_resolution_ladder_universal_not_client_specific(temp_db, monkeypatch):
    """Universal rule: same ladder for every client, no client names inside."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    formatted, telemetry = hook.query_subconscious_memories("status?", max_tokens=200)
    assert telemetry["resolution_applied"] is True
    line = next(line for line in formatted if "Resolution order" in line)
    assert "capsule first" in line and "last resort" in line
    for name in ("opencode", "antigravity", "cursor", "claude", "codex"):
        assert name not in line.lower()
    assert telemetry["injected_tokens"] <= 200


def test_plugin_assistant_handoff_invariants():
    """Template must track per-ID longest text, hand off via env, and clear
    after use — logging lengths only, never content."""
    p = hook_repo_root() / "genesis_memory" / "cli" / "client_registry.py"
    if not p.exists():
        p = hook_repo_root() / "src" / "genesis" / "cli" / "client_registry.py"
    if not p.exists():
        p = hook_repo_root().parent / "src" / "genesis" / "cli" / "client_registry.py"
    src = p.read_text(encoding="utf-8")
    assert "GENESIS_LAST_ASSISTANT_TEXT" in src
    assert "seenMessageTexts" in src and "mostRecentMessageId" in src
    assert "MAX_ASSISTANT_CHARS" in src
    # Clearing after spawn: no stale handoff across turns.
    assert 'process.env.GENESIS_LAST_ASSISTANT_TEXT = ""' in src
    # Lengths only in logs.
    assert "assistant captured (" in src and "content never logged" in src
    # Cache-hit must not swallow a pending assistant handoff.
    assert "forceSpawn" in src
    assert "getCapsule(currentTurnQuery, forceSpawn)" in src


def hook_repo_root():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent


def test_hook_main_prefers_env_assistant(temp_db, monkeypatch, tmp_path, capsys):
    """main(): OpenCode env handoff lands in the row (transcript only matters
    for non-raw Antigravity invocations, which never reach this branch)."""
    import io
    import sys
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    monkeypatch.setattr(sys, "argv", ["hook.py", "status", "--raw"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setenv("GENESIS_LAST_ASSISTANT_TEXT", "Env reply forty two")
    hook.main()
    monkeypatch.delenv("GENESIS_LAST_ASSISTANT_TEXT", raising=False)
    rows = _hook_rows(store, "opencode")
    assert len(rows) == 1
    assert "forty two" in rows[0][2]


def test_hook_main_transcript_branch(temp_db, monkeypatch, tmp_path, capsys):
    """main(): Antigravity shape (stdin transcript, no --raw) captures the
    transcript assistant side under hook:antigravity."""
    import io
    import json
    import sys
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    tr = tmp_path / "tr.jsonl"
    tr.write_text(
        json.dumps({"type": "USER_INPUT", "content": "do the thing"}) + "\n"
        + json.dumps({"type": "ASSISTANT_MESSAGE",
                      "content": "Transcript reply nine"}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["hook.py"])
    monkeypatch.setattr(sys, "stdin",
                         io.StringIO(json.dumps({"transcriptPath": str(tr)})))
    monkeypatch.delenv("GENESIS_LAST_ASSISTANT_TEXT", raising=False)
    hook.main()
    rows = _hook_rows(store, "antigravity")
    assert len(rows) == 1
    assert "Transcript reply nine" in rows[0][2]


def test_hook_rapid_turns_do_not_clobber(temp_db, monkeypatch):
    """Per-turn rows: three rapid turns keep all three prompts retrievable."""
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    for i, text in enumerate(("first turn alpha", "second turn beta", "third turn gamma")):
        _, telemetry = hook.query_subconscious_memories(
            text, client="opencode", capture_turn=True, max_tokens=200)
        assert telemetry["ambient_captured"] is True
    rows = _hook_rows(store, "opencode")
    assert len(rows) == 3
    prompts = " ".join(r[1] for r in rows)
    assert "alpha" in prompts and "beta" in prompts and "gamma" in prompts
    # Newest first.
    assert "gamma" in rows[0][1]


def test_hook_prune_keeps_proxy_rows(temp_db, monkeypatch):
    """TTL prune removes only stale hook rows; proxy/user rows are untouched."""
    import sqlite3
    import time
    store, db_path = temp_db
    monkeypatch.setattr(hook, "DB_PATH", db_path)
    store.set_dialogue(session_id="default", client="proxy",
                       user_prompt="Proxy history", assistant_summary="ack",
                       salient_terms=[])
    con = sqlite3.connect(db_path)
    # Age the proxy row past the 60s capture-suppression window (but it must
    # still survive: prune only touches hook:% rows).
    con.execute("UPDATE dialogue_buffer SET updated_at = ? WHERE session_id = 'default'",
                (time.time() - 120.0,))
    con.execute(
        "INSERT INTO dialogue_buffer(session_id, client, updated_at, user_prompt, "
        "assistant_summary, salient_terms) VALUES(?, ?, ?, ?, ?, ?)",
        ("hook:opencode:1", "opencode", time.time() - 5000,
         "Ancient hook turn", "", ""))
    con.commit()
    con.close()
    _, telemetry = hook.query_subconscious_memories(
        "Fresh turn now", client="opencode", capture_turn=True, max_tokens=200)
    assert telemetry["ambient_captured"] is True
    cur = store.db.cursor()
    cur.execute("SELECT session_id FROM dialogue_buffer")
    sids = {r[0] for r in cur.fetchall()}
    assert not any(s == "hook:opencode:1" for s in sids)
    assert "default" in sids
    assert sum(1 for s in sids if s.startswith("hook:opencode:")) == 1

"""
Tests for GENESIS Respectful Challenge Mechanism.

Tests the challenge_rule MCP tool, schema v9 migration (model_source column),
and challenge protocol injection in the subconscious hook.
"""

import json
import os
import sqlite3
import time
import pytest

from genesis_memory.daemon.server import Store, handle, SCHEMA_VERSION


@pytest.fixture
def store(tmp_path):
    """Fresh Store with clean database."""
    db_path = str(tmp_path / "test_challenge.db")
    return Store(db_path)


def _make_solidified(store, text="Use double backslash for Windows paths"):
    """Helper: create an episode and force it to solidified status."""
    result = store.remember(text, kind="decision", utility=3.0)
    eid = result["id"]
    store.db.execute(
        "UPDATE episodes SET status = 'solidified', reinforcements = 10, "
        "stability = 30.0 WHERE id = ?", (eid,)
    )
    store.db.commit()
    return eid


# =====================================================================
# 1. Schema v9 Migration
# =====================================================================

def test_schema_version_is_9(store):
    """Schema should be upgraded to v9 with model_source column."""
    assert SCHEMA_VERSION == 9
    ver = store.db.execute("PRAGMA user_version").fetchone()[0]
    assert ver == 9

    # model_source column should exist
    cols = [c[1] for c in store.db.execute("PRAGMA table_info(episodes)").fetchall()]
    assert "model_source" in cols


def test_challenge_rule_counter_exists(store):
    """challenge_rule counter should be initialized."""
    row = store.db.execute(
        "SELECT count FROM counters WHERE name = 'challenge_rule'"
    ).fetchone()
    assert row is not None
    assert row[0] == 0


# =====================================================================
# 2. challenge_rule Tool
# =====================================================================

def test_challenge_rule_basic_flow(store):
    """Basic challenge flow: solidified rule -> challenge -> conflict registered."""
    solid_id = _make_solidified(store)

    result = store.challenge_rule(
        solidified_id=solid_id,
        proposed_text="Use pathlib.Path for cross-platform path handling",
        reason="pathlib is more Pythonic and handles OS differences automatically"
    )

    assert result["challenged"] is True
    assert result["conflict_id"] > 0
    assert result["solidified_rule"]["id"] == solid_id
    assert "pathlib" in result["proposed_rule"]["text"]
    assert "action_required" in result

    # Verify conflict record in DB
    conflict = store.db.execute(
        "SELECT status FROM conflicts WHERE id = ?", (result["conflict_id"],)
    ).fetchone()
    assert conflict[0] == "challenge_pending"

    # Verify proposed episode has pending_challenge status
    proposed = store.db.execute(
        "SELECT status FROM episodes WHERE id = ?", (result["proposed_rule"]["id"],)
    ).fetchone()
    assert proposed[0] == "pending_challenge"


def test_challenge_rule_rejects_non_solidified(store):
    """Cannot challenge a rule that isn't solidified."""
    result = store.remember("Some active memory", kind="fact")
    eid = result["id"]

    result = store.challenge_rule(
        solidified_id=eid,
        proposed_text="Better approach"
    )

    assert result["challenged"] is False
    assert "not 'solidified'" in result["reason"]


def test_challenge_rule_rejects_nonexistent(store):
    """Raises error for nonexistent episode ID."""
    with pytest.raises(ValueError, match="not found"):
        store.challenge_rule(solidified_id=99999, proposed_text="test")


def test_challenge_rule_rejects_secrets(store):
    """Proposed text with API keys should be rejected."""
    solid_id = _make_solidified(store)

    with pytest.raises(ValueError, match="Secret detected"):
        store.challenge_rule(
            solidified_id=solid_id,
            proposed_text="Use API key sk-abc123xyz456def789ghi012jkl345mno678"
        )


def test_challenge_then_resolve_supersede(store):
    """Full flow: challenge -> resolve with supersede -> old rule superseded."""
    solid_id = _make_solidified(store)

    challenge = store.challenge_rule(
        solidified_id=solid_id,
        proposed_text="Use pathlib.Path instead",
        reason="Modern Python"
    )
    conflict_id = challenge["conflict_id"]
    new_id = challenge["proposed_rule"]["id"]

    # Resolve: new rule wins
    resolve = store.resolve_conflict(
        conflict_id=conflict_id,
        action="superseded",
        winner_id=new_id
    )
    assert resolve["status"] == "resolved"
    assert resolve["action"] == "superseded"

    # Old rule should be superseded
    old = store.db.execute(
        "SELECT status, superseded_by FROM episodes WHERE id = ?", (solid_id,)
    ).fetchone()
    assert old[0] == "superseded"
    assert old[1] == new_id


def test_challenge_then_resolve_dismissed(store):
    """Challenge dismissed: old rule stays solidified, new one stays pending."""
    solid_id = _make_solidified(store)

    challenge = store.challenge_rule(
        solidified_id=solid_id,
        proposed_text="Different approach"
    )
    conflict_id = challenge["conflict_id"]

    resolve = store.resolve_conflict(
        conflict_id=conflict_id,
        action="dismissed"
    )
    assert resolve["action"] == "dismissed"

    # Old rule should still be solidified
    old = store.db.execute(
        "SELECT status FROM episodes WHERE id = ?", (solid_id,)
    ).fetchone()
    assert old[0] == "solidified"


def test_challenge_rule_increments_counter(store):
    """challenge_rule counter should increment."""
    solid_id = _make_solidified(store)

    store.challenge_rule(solidified_id=solid_id, proposed_text="Better way")

    row = store.db.execute(
        "SELECT count FROM counters WHERE name = 'challenge_rule'"
    ).fetchone()
    assert row[0] >= 1


# =====================================================================
# 3. MCP Gateway Integration
# =====================================================================

def test_challenge_rule_via_mcp_gateway(store):
    """challenge_rule should be callable via the genesis gateway."""
    solid_id = _make_solidified(store)

    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "genesis",
            "arguments": {
                "op": "challenge_rule",
                "solidified_id": solid_id,
                "proposed_text": "Use pathlib",
                "reason": "Better"
            }
        }
    }
    resp = handle(store, msg)
    assert "error" not in resp
    data = json.loads(resp["result"]["content"][0]["text"])
    assert data["challenged"] is True


def test_challenge_rule_via_direct_tool(store):
    """challenge_rule should be callable as a direct tool."""
    solid_id = _make_solidified(store)

    msg = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "challenge_rule",
            "arguments": {
                "solidified_id": solid_id,
                "proposed_text": "Use os.path.join",
                "reason": "Simpler"
            }
        }
    }
    resp = handle(store, msg)
    assert "error" not in resp
    data = json.loads(resp["result"]["content"][0]["text"])
    assert data["challenged"] is True


def test_challenge_rule_in_tools_list(store):
    """challenge_rule should appear in the tools/list response."""
    msg = {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
    resp = handle(store, msg)
    tool_names = [t["name"] for t in resp["result"]["tools"]]
    assert "challenge_rule" in tool_names


# =====================================================================
# 4. Subconscious Hook Challenge Protocol
# =====================================================================

def test_challenge_protocol_line_injected():
    """The challenge protocol directive should be injected into the hook output."""
    from genesis_memory.hooks.subconscious_hook import CHALLENGE_PROTOCOL_LINE
    assert "challenge_rule" in CHALLENGE_PROTOCOL_LINE
    assert "solidified_id" in CHALLENGE_PROTOCOL_LINE


# =====================================================================
# 5. Subconscious Hook Standing Spool Discipline
# =====================================================================

def test_spool_discipline_line_content():
    """The standing spool order must name the mechanism and stay tiny."""
    from genesis_memory.hooks.subconscious_hook import SPOOL_DISCIPLINE_LINE
    assert "genesis run" in SPOOL_DISCIPLINE_LINE
    # One line, pointer-short: the <200-token capsule is nearly full.
    assert "\n" not in SPOOL_DISCIPLINE_LINE
    assert len(SPOOL_DISCIPLINE_LINE) <= 64


def test_spool_discipline_line_injected_first_turn(tmp_path, monkeypatch):
    """A fresh install (empty DB, first turn) must still carry the spool order."""
    from genesis_memory.hooks import subconscious_hook as hook
    db_path = str(tmp_path / "test_spool_fresh.db")
    Store(db_path)
    monkeypatch.setattr(hook, "DB_PATH", db_path)

    formatted, telemetry = hook.query_subconscious_memories("run the tests", max_tokens=200)

    full_output = "\n".join(formatted)
    assert "• [Spool]:" in full_output
    assert telemetry["spool_rule_applied"] is True
    assert telemetry["injected_tokens"] <= 200

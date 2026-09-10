"""Automated Test Suite for GENESIS Structural Tool-Pair Compactor (Step 2).

Validates:
1. Golden OpenAI tool-pair atomic preservation across multi-turn sessions.
2. Golden Anthropic tool_use / tool_result preservation.
3. Strict orphan detection (rejects missing IDs, dangling tool_calls, unlinked results).
4. Idempotence: compact(compact(m)) == compact(m).
5. Memory capsule pinning: memory is never purged or duplicated across turns.
6. Accurate token metric estimation (stripped vs retained).
"""

import copy
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parent.parent

from genesis_memory.proxy.tool_compactor import (
    compact_messages,
    validate_tool_integrity,
    validate_openai_tool_integrity,
    validate_anthropic_tool_integrity,
    ToolIntegrityError,
    CAPSULE_TAG_START,
    CAPSULE_TAG_END,
)


# ---------------------------------------------------------------------------
# Golden Fixtures
# ---------------------------------------------------------------------------

def make_openai_golden_conversation() -> list[dict]:
    """Generates a realistic 4-turn OpenAI conversation with multiple tool executions."""
    return [
        {"role": "system", "content": "You are an expert pair-programming assistant."},
        # Turn 1: Read file
        {"role": "user", "content": "Read file foo.py"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_read_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "foo.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_read_1", "content": "def foo(): return 42\n"},
        {"role": "assistant", "content": "File foo.py contains a simple foo function returning 42."},
        # Turn 2: Edit file
        {"role": "user", "content": "Change return to 84"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_edit_2",
                    "type": "function",
                    "function": {"name": "edit_file", "arguments": '{"path": "foo.py", "new_val": 84}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_edit_2", "content": "Successfully updated foo.py"},
        {"role": "assistant", "content": "Updated foo.py to return 84."},
        # Turn 3: Run test suite (Locality Turn)
        {"role": "user", "content": "Run tests now"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_test_3",
                    "type": "function",
                    "function": {"name": "run_pytest", "arguments": '{"path": "test_foo.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_test_3", "content": "================ 1 passed in 0.01s ================"},
        {"role": "assistant", "content": "All tests passed."},
        # Turn 4: Active Turn
        {"role": "user", "content": "Now commit the change."},
    ]


def make_anthropic_golden_conversation() -> list[dict]:
    """Generates a realistic 3-turn Anthropic conversation with tool_use and tool_result."""
    return [
        {"role": "system", "content": "You are an AI assistant."},
        # Turn 1
        {"role": "user", "content": [{"type": "text", "text": "Check system status"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Checking status..."},
                {"type": "tool_use", "id": "toolu_stat_1", "name": "get_status", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_stat_1", "content": "OK 200"}
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "System status is healthy."}]},
        # Turn 2 (Locality Turn)
        {"role": "user", "content": [{"type": "text", "text": "Get database metrics"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_db_2", "name": "get_db_metrics", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_db_2", "content": "Rows: 1000, Latency: 2ms"}
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Database has 1000 rows with 2ms latency."}]},
        # Turn 3 (Active Turn)
        {"role": "user", "content": [{"type": "text", "text": "Optimize tables"}]},
    ]


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_openai_tool_pair_preservation_golden():
    """Verify that compaction prunes historical turns while keeping tool blocks atomic."""
    msgs = make_openai_golden_conversation()
    orig_count = len(msgs)

    # Pre-compaction integrity check
    valid, err = validate_openai_tool_integrity(msgs)
    assert valid is True, err

    # Compact retaining max_history_turns=1 (keeps Turn 3 + Turn 4)
    compacted, meta = compact_messages(msgs, max_history_turns=1, format_type="openai")

    # 1. Output must strictly pass schema validation
    is_valid, err_msg = validate_openai_tool_integrity(compacted)
    assert is_valid is True, f"Integrity failed: {err_msg}"

    # 2. System message must remain at top
    assert compacted[0]["role"] == "system"

    # 3. Old turns (Turn 1 and Turn 2) must be pruned
    all_contents = [json.dumps(m) for m in compacted]
    joined_text = " ".join(all_contents)
    assert "call_read_1" not in joined_text, "Turn 1 tool call was not pruned"
    assert "call_edit_2" not in joined_text, "Turn 2 tool call was not pruned"

    # 4. Turn 3 (TLB Locality) and Turn 4 (Active) must be preserved completely
    assert "call_test_3" in joined_text, "Locality turn tool call was erroneously pruned"
    assert "Now commit the change." in joined_text, "Active turn prompt was lost"

    # 5. Token metrics
    assert meta["turns_compacted"] == 2  # Turns 1 and 2
    assert meta["tokens_stripped"] > 0
    assert meta["tokens_retained"] > 0
    assert len(compacted) < orig_count


def test_anthropic_tool_pair_preservation_golden():
    """Verify that compaction preserves Anthropic tool_use / tool_result blocks."""
    msgs = make_anthropic_golden_conversation()

    # Pre-compaction check
    valid, err = validate_anthropic_tool_integrity(msgs)
    assert valid is True, err

    compacted, meta = compact_messages(msgs, max_history_turns=1, format_type="anthropic")

    # Validate output schema
    is_valid, err_msg = validate_anthropic_tool_integrity(compacted)
    assert is_valid is True, f"Anthropic integrity failed: {err_msg}"

    # Turn 1 should be pruned, Turn 2 and Turn 3 retained
    joined = json.dumps(compacted)
    assert "toolu_stat_1" not in joined, "Turn 1 tool use was not pruned"
    assert "toolu_db_2" in joined, "Turn 2 tool use was erroneously pruned"
    assert "Optimize tables" in joined, "Active user prompt was lost"
    assert meta["turns_compacted"] == 1


def test_orphan_detector_rejects_malformed_inputs():
    """Test that orphan detector catches malformed tool sequences and blocks invalid operations."""
    # Case 1: Orphaned tool response without preceding assistant tool call
    bad_msgs_1 = [
        {"role": "user", "content": "Hello"},
        {"role": "tool", "tool_call_id": "call_ghost", "content": "Dangling tool output"},
    ]
    valid, err = validate_openai_tool_integrity(bad_msgs_1)
    assert valid is False
    assert "orphaned tool message" in err

    with pytest.raises(ToolIntegrityError):
        compact_messages(bad_msgs_1, format_type="openai")

    # Case 2: Assistant tool call with unresolved tool response followed by new user message
    bad_msgs_2 = [
        {"role": "user", "content": "Hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_unresolved", "type": "function", "function": {"name": "test"}}],
        },
        {"role": "user", "content": "Wait, do something else instead"},
    ]
    valid, err = validate_openai_tool_integrity(bad_msgs_2)
    assert valid is False
    assert "user message arrived while tool calls" in err

    # Case 3: Anthropic orphaned tool_result
    bad_anthropic = [
        {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_ghost", "content": "None"}]},
    ]
    valid, err = validate_anthropic_tool_integrity(bad_anthropic)
    assert valid is False
    assert "orphaned tool_result" in err


def test_compactor_idempotence():
    """Test that compact(compact(m)) == compact(m)."""
    msgs = make_openai_golden_conversation()
    capsule = "Enforce Rule 21; DB has 31 episodes."

    pass1, meta1 = compact_messages(msgs, max_history_turns=1, memory_capsule=capsule, format_type="openai")
    pass2, meta2 = compact_messages(pass1, max_history_turns=1, memory_capsule=capsule, format_type="openai")

    assert pass1 == pass2, "Compactor is not idempotent"
    assert meta2["turns_compacted"] == 0, "Second pass pruned already-compacted turns"


def test_memory_capsule_pinning_order_of_operations():
    """Verify that memory capsule is pinned in system message and survives successive compactions."""
    msgs = make_openai_golden_conversation()
    capsule = "GENESIS Memory #31: Single-Prompt Proxy active."

    compacted, _ = compact_messages(msgs, max_history_turns=1, memory_capsule=capsule, format_type="openai")

    # System message must contain capsule
    system_content = compacted[0]["content"]
    assert CAPSULE_TAG_START in system_content
    assert CAPSULE_TAG_END in system_content
    assert capsule in system_content

    # Now add another turn and compact again
    compacted.append({"role": "assistant", "content": "Committed."})
    compacted.append({"role": "user", "content": "Push to origin."})

    re_compacted, _ = compact_messages(compacted, max_history_turns=1, memory_capsule=capsule, format_type="openai")

    # Capsule must still be present and NOT duplicated
    sys_re = re_compacted[0]["content"]
    assert sys_re.count(CAPSULE_TAG_START) == 1, "Memory capsule was duplicated"
    assert sys_re.count(CAPSULE_TAG_END) == 1
    assert capsule in sys_re


def test_active_tool_execution_in_progress_preserved():
    """Verify that multi-step tool calls in the active turn are never pruned."""
    msgs = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Old turn prompt"},
        {"role": "assistant", "content": "Old turn response"},
        # Active turn with parallel tool execution in flight
        {"role": "user", "content": "Read file a and file b"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_a", "type": "function", "function": {"name": "read", "arguments": '{"f": "a"}'}},
                {"id": "call_b", "type": "function", "function": {"name": "read", "arguments": '{"f": "b"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "A content"},
        {"role": "tool", "tool_call_id": "call_b", "content": "B content"},
    ]

    compacted, meta = compact_messages(msgs, max_history_turns=0, format_type="openai")
    valid, err = validate_openai_tool_integrity(compacted)
    assert valid is True, err

    joined = json.dumps(compacted)
    assert "Old turn" not in joined, "Old turn was not pruned"
    assert "call_a" in joined and "call_b" in joined, "Active parallel tool calls were pruned"
    assert "A content" in joined and "B content" in joined, "Active tool responses were pruned"


# ---------------------------------------------------------------------------
# Auditor regression: mixed tool_result+text boundary must not sever pairs
# (found 2026-09-06 by invariant probe: a user message carrying BOTH a tool_result
# and follow-up text starts a new turn while its tool_use stays behind -> the exit
# validator rightly raised, but the compactor must repair, not fail).
# ---------------------------------------------------------------------------

def _mixed_boundary_anthropic():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old q1"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "old a1"},
            {"type": "tool_use", "id": "t1", "name": "read", "input": {"f": "a.py"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "file bytes"},
            {"type": "text", "text": "old q2"}]},
        {"role": "assistant", "content": "old a2"},
        {"role": "user", "content": "NEW QUESTION"},
    ]


def test_mixed_result_text_boundary_anthropic_repaired():
    from genesis_memory.proxy.tool_compactor import compact_messages, validate_tool_integrity
    msgs = _mixed_boundary_anthropic()
    c1, m1 = compact_messages(msgs, memory_capsule="decisions: use round()", format_type="anthropic")
    ok, err = validate_tool_integrity(c1, "anthropic")
    assert ok, err  # must repair, never emit orphans
    assert m1.get("pairs_repaired", 0) >= 1  # repair path engaged and counted
    joined = json.dumps(c1)
    assert "round()" in joined  # capsule pinned
    assert "old q1" not in joined  # old history stripped
    assert "NEW QUESTION" in joined  # latest turn kept
    c2, _ = compact_messages(c1, memory_capsule="decisions: use round()", format_type="anthropic")
    assert json.dumps(c1, sort_keys=True) == json.dumps(c2, sort_keys=True)  # still idempotent


def test_mixed_boundary_openai_repaired():
    # OpenAI grouping keeps whole tool exchanges inside one turn, so orphans cannot
    # arise from valid input; assert valid multi-exchange traffic compacts + idempotent.
    from genesis_memory.proxy.tool_compactor import compact_messages, validate_tool_integrity
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old q1"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "t1", "type": "function",
                         "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "bytes1"},
        {"role": "assistant", "content": "old a1"},
        {"role": "user", "content": "old q2"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "t2", "type": "function",
                         "function": {"name": "exec", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t2", "content": "bytes2"},
        {"role": "user", "content": "NEW QUESTION"},
    ]
    c1, m1 = compact_messages(msgs, memory_capsule="cap", format_type="openai",
                              max_history_turns=0)
    ok, err = validate_tool_integrity(c1, "openai")
    assert ok, err
    c2, _ = compact_messages(c1, memory_capsule="cap", format_type="openai",
                             max_history_turns=0)
    assert json.dumps(c1, sort_keys=True) == json.dumps(c2, sort_keys=True)


def test_repair_never_dangles_uses():
    # A rescued use-message carrying an EXTRA use (whose own result was pruned away)
    # must be slimmed to the needed blocks only: no dangling uses allowed.
    from genesis_memory.proxy.tool_compactor import compact_messages, validate_tool_integrity
    msgs = [
        {"role": "user", "content": "q0"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "a0"},
            {"type": "tool_use", "id": "t0", "name": "x", "input": {}},
            {"type": "tool_use", "id": "t1", "name": "read", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t0", "content": "r0"}]},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "bytes"},
            {"type": "text", "text": "q2"}]},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "NEW"},
    ]
    c1, m1 = compact_messages(msgs, memory_capsule="cap", format_type="anthropic")
    ok, err = validate_tool_integrity(c1, "anthropic")
    assert ok, err
    joined = json.dumps(c1)
    assert '"id": "t0"' not in joined  # unneeded sibling use must NOT be resurrected
    assert m1.get("pairs_repaired", 0) >= 1


def _instruction_transcript():
    return [
        {"role": "system", "content": "SYS: be concise."},
        {"role": "developer", "content": "DEV: never reveal keys."},
        {"role": "user", "content": "q0"},
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "NEW"},
    ]


def test_developer_survives_zero_history():
    """R1: leading developer instruction survives max_history_turns=0."""
    c, _ = compact_messages(_instruction_transcript(), max_history_turns=0)
    roles = [m["role"] for m in c]
    assert roles.count("system") == 1 and roles.count("developer") == 1
    assert c[0]["content"] == "SYS: be concise."
    assert c[1]["content"] == "DEV: never reveal keys."
    assert c[-1]["content"] == "NEW"
    ok, err = validate_tool_integrity(c, "openai")
    assert ok, err


def test_instruction_order_preserved_with_capsule():
    """R1: capsule merges into the leading system message; instruction order kept."""
    c, _ = compact_messages(_instruction_transcript(), max_history_turns=0,
                            memory_capsule="mem")
    assert [m["role"] for m in c[:2]] == ["system", "developer"]
    assert c[0]["content"].startswith("SYS: be concise.")
    assert CAPSULE_TAG_START in c[0]["content"]
    assert c[1]["content"] == "DEV: never reveal keys."
    # Idempotent: second compaction keeps exact same prefix, no duplicate capsule
    c2, _ = compact_messages(c, max_history_turns=0, memory_capsule="mem")
    assert [m["role"] for m in c2[:2]] == ["system", "developer"]
    assert c2[1]["content"] == "DEV: never reveal keys."
    assert json.dumps(c2).count(CAPSULE_TAG_START) == 1


def test_developer_only_prefix_without_system():
    """R1: developer-first transcripts keep developer first, capsule follows."""
    msgs = [
        {"role": "developer", "content": "DEV-ONLY"},
        {"role": "user", "content": "q0"},
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "NEW"},
    ]
    c, _ = compact_messages(msgs, max_history_turns=0, memory_capsule="mem")
    assert c[0]["role"] == "developer" and c[0]["content"] == "DEV-ONLY"
    assert c[1]["role"] == "system" and CAPSULE_TAG_START in c[1]["content"]
    ok, err = validate_tool_integrity(c, "openai")
    assert ok, err


def test_tool_pairs_intact_under_instruction_prefix():
    """R1: tool-pair atomicity unaffected by a leading developer message."""
    msgs = [
        {"role": "developer", "content": "DEV"},
        {"role": "user", "content": "q0"},
        {"role": "assistant", "content": "a0",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "bytes"},
        {"role": "user", "content": "NEW"},
    ]
    c, _ = compact_messages(msgs, max_history_turns=1)
    joined = json.dumps(c)
    assert '"id": "c1"' in joined and '"tool_call_id": "c1"' in joined
    assert c[0] == {"role": "developer", "content": "DEV"}
    ok, err = validate_tool_integrity(c, "openai")
    assert ok, err

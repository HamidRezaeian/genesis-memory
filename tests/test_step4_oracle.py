"""R5 mutation gates for the independent retained-transcript oracle.

Each test proves the oracle FAILS on a corrupted transcript class that the
old grade-only-received loop passed vacuously. The oracle must also stay
silent (zero mismatches) on a faithful retained transcript.
Run: python -m pytest tests/test_step4_oracle.py -q
"""
import copy
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

from genesis_memory.eval.step4_harness import (
    audit_transcript_fidelity,
    expected_retained_transcript,
    strip_capsule_block,
)
from genesis_memory.proxy.tool_compactor import CAPSULE_TAG_START, CAPSULE_TAG_END


def _history():
    return [
        {"role": "user", "content": "Read store.py"},
        {"role": "assistant", "content": "Reading.",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read", "arguments": '{"path": "a.py"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "FILEBYTES"},
        {"role": "user", "content": "Now fix it"},
    ]


def _received_faithful():
    capsule_sys = {"role": "system",
                   "content": f"{CAPSULE_TAG_START}\nmem\n{CAPSULE_TAG_END}"}
    return [capsule_sys,
            {"role": "user", "content": "Now fix it"}]


def test_faithful_transcript_zero_mismatches():
    mism, comps, sem, _rw = audit_transcript_fidelity(_history(), _received_faithful())
    assert mism == 0
    assert comps > 0  # nonzero comparisons required (void-guard)


def test_empty_received_fails_with_comparisons():
    mism, comps, _sem2, _rw2 = audit_transcript_fidelity(_history(), [])
    assert mism > 0 and comps > 0


def test_empty_both_sides_voids():
    mism, comps, _sem2, _rw2 = audit_transcript_fidelity([], [])
    assert comps == 0  # caller must VOID, never PASS


def test_dropped_required_message_fails():
    hist = [
        {"role": "user", "content": "go"},
    ]
    recv = [
        {"role": "user", "content": "WRONG"},
    ]
    mism, comps, _sem2, _rw2 = audit_transcript_fidelity(hist, recv)
    assert mism > 0 and comps > 0


def test_modified_result_content_fails():
    recv = copy.deepcopy(_received_faithful())
    # Simulate a history where the retained slice holds a tool result, then corrupt it.
    hist = [
        {"role": "assistant", "content": "r",
         "tool_calls": [{"id": "c9", "type": "function",
                         "function": {"name": "run", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c9", "content": "1 failed"},
        {"role": "user", "content": "next"},
    ]
    recv2 = [
        {"role": "tool", "tool_call_id": "c9", "content": "1 passed"},
        {"role": "user", "content": "next"},
    ]
    mism, comps, _sem2, _rw2 = audit_transcript_fidelity(hist, recv2)
    assert mism > 0 and comps > 0
    _ = recv  # faithful fixture above covers the positive path


def test_reordered_messages_fail():
    hist = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    recv = [
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "b"},  # orphaned earlier turn, wrong order
        {"role": "user", "content": "c"},
    ]
    mism, comps, _sem2, _rw2 = audit_transcript_fidelity(hist, recv)
    assert mism > 0


def test_tool_id_change_fails():
    hist = [
        {"role": "user", "content": "q0"},
        {"role": "assistant", "content": "r",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read", "arguments": "{}"}}]},
    ]
    recv = [
        {"role": "user", "content": "q0"},
        {"role": "assistant", "content": "r",
         "tool_calls": [{"id": "cX", "type": "function",
                         "function": {"name": "read", "arguments": "{}"}}]},
    ]
    mism, comps, _sem2, _rw2 = audit_transcript_fidelity(hist, recv)
    assert mism > 0


def test_byte_vs_semantic_split():
    hist = [
        {"role": "user", "content": "q0"},
        {"role": "assistant", "content": "r",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read", "arguments": '{"path": "a.py"}'}}]},
    ]
    recv = copy.deepcopy(hist)
    recv[1]["tool_calls"][0]["function"]["arguments"] = '{"path":"a.py"}'  # same JSON, other bytes
    mism, comps, sem, _rw = audit_transcript_fidelity(hist, recv)
    assert mism > 0  # byte gate is binding
    assert sem >= 1  # ...but semantic equivalence is recorded separately


def test_altered_system_prefix_fails():
    hist = [
        {"role": "system", "content": "SYS RULES"},
        {"role": "user", "content": "go"},
    ]
    recv = [
        {"role": "system", "content": "SYS RULES TAMPERED"},
        {"role": "user", "content": "go"},
    ]
    mism, comps, _sem2, _rw2 = audit_transcript_fidelity(hist, recv)
    assert mism > 0


def test_capsule_carrier_permitted_but_checked():
    hist = [{"role": "user", "content": "go"}]
    recv = [{"role": "system",
             "content": f"prefix text\n{CAPSULE_TAG_START}\nmem\n{CAPSULE_TAG_END}"},
            {"role": "user", "content": "go"}]
    # Nonempty stripped system with no sent counterpart → mismatch (not a pure carrier)
    mism, comps, _sem2, _rw2 = audit_transcript_fidelity(hist, recv)
    assert mism > 0 and comps > 0


def test_expected_slice_spec():
    prefix, retained = expected_retained_transcript(_history())
    assert prefix == []
    assert [m["role"] for m in retained] == ["user"]
    assert retained[0]["content"] == "Now fix it"
    assert strip_capsule_block("a\n" + CAPSULE_TAG_START + "\nm\n" + CAPSULE_TAG_END + "\n\nb") == "a\n\nb"


def test_certified_rewrite_exempt_with_provenance():
    hist = [{"role": "user", "content": "What does this package do?"}]
    recv = [
        {"role": "system",
         "content": f"{CAPSULE_TAG_START}\nfiles=[store.py]\n{CAPSULE_TAG_END}"},
        {"role": "user", "content": "What does this package do? store.py"},
    ]
    mism, comps, sem, rw = audit_transcript_fidelity(hist, recv)
    assert mism == 0 and rw == 1 and comps > 0


def test_rewrite_exemption_requires_capsule_grounding():
    hist = [{"role": "user", "content": "What does this package do?"}]
    recv = [
        {"role": "system",
         "content": f"{CAPSULE_TAG_START}\nfiles=[other.py]\n{CAPSULE_TAG_END}"},
        {"role": "user", "content": "What does this package do? evil.py"},
    ]
    mism, comps, sem, rw = audit_transcript_fidelity(hist, recv)
    assert mism > 0 and rw == 0  # ungrounded appended text is corruption


def test_rewrite_exemption_requires_prefix():
    hist = [{"role": "user", "content": "What does this package do?"}]
    recv = [
        {"role": "system",
         "content": f"{CAPSULE_TAG_START}\nfiles=[store.py]\n{CAPSULE_TAG_END}"},
        {"role": "user", "content": "Something else entirely store.py"},
    ]
    mism, comps, sem, rw = audit_transcript_fidelity(hist, recv)
    assert mism > 0 and rw == 0

"""
Tests for GENESIS Hebbian Plasticity, Skill Synthesis, and Autonomous Sleep Consolidation.
"""

import json
import os
from pathlib import Path
import sqlite3
import time
import pytest

from genesis_memory.core.hebbian_engine import (
    HebbianEngine,
    compute_stability,
    compute_retention,
    compute_synaptic_weight,
    classify_state,
)
from genesis_memory.core.skill_synthesizer import SkillSynthesizer, extract_keywords
from genesis_memory.daemon.server import Store, handle
from genesis_memory.sleep.sleep_daemon import run_sleep_cycle, SleepDaemon


# =====================================================================
# 1. Mathematical and Algorithmic Unit Tests
# =====================================================================

def test_hebbian_math_stability_and_retention():
    """Stability increases monotonically with accesses and reinforcements."""
    s0 = compute_stability(accesses=0, reinforcements=0, base_stability_days=7.0)
    assert s0 == 7.0

    s1 = compute_stability(accesses=5, reinforcements=2, base_stability_days=7.0)
    assert s1 > s0

    # Retention at t=0 is 1.0
    r0 = compute_retention(elapsed_seconds=0.0, stability_days=s1)
    assert pytest.approx(r0, rel=1e-3) == 1.0

    # Retention decays over time
    r_week = compute_retention(elapsed_seconds=7 * 86400.0, stability_days=s1)
    assert 0.0 < r_week < 1.0


def test_hebbian_synaptic_weight_and_classification():
    """Synaptic weight properly dictates dynamic state transitions."""
    w_low = compute_synaptic_weight(utility=0.2, retention=0.1, accesses=0, reinforcements=0)
    assert classify_state(w_low, age_days=10.0, accesses=0) == "prune_candidate"

    w_active = compute_synaptic_weight(utility=1.0, retention=0.8, accesses=1, reinforcements=0)
    assert classify_state(w_active, age_days=1.0, accesses=1) == "active"

    w_solid = compute_synaptic_weight(utility=1.5, retention=1.0, accesses=10, reinforcements=3)
    assert w_solid >= 1.6
    assert classify_state(w_solid, age_days=5.0, accesses=10) == "solidified"


# =====================================================================
# 2. Hebbian Engine Database Operations
# =====================================================================

def test_hebbian_reinforcement_and_decay(tmp_path: Path):
    db_file = tmp_path / "test_hebbian.db"
    store = Store(str(db_file))

    # Add a decision memory
    m = store.remember(
        text="Always use pytest tests/ in Windows root to avoid picking up scratch fixtures",
        kind="decision",
        utility=1.0,
    )
    mid = m["id"]

    hebbian = HebbianEngine(store.db)

    # Reinforce memory with success
    res = hebbian.reinforce_memory(mid, outcome="success", note="test passed")
    assert res["id"] == mid
    assert res["reinforcements"] == 1
    assert res["utility"] > 1.0
    assert res["stability_days"] > 7.0

    # Simulate spaced reinforcement up to solidified status
    for _ in range(3):
        res = hebbian.reinforce_memory(mid, outcome="success")
    assert res["status"] == "solidified"

    # Add a weak, unaccessed memory and simulate decay
    m_weak = store.remember(text="Transient scratch note about temp file", kind="outcome", utility=0.4)
    wid = m_weak["id"]

    # Fast-forward 30 days into the future for decay
    future_time = time.time() + 30 * 86400.0
    decay_stats = hebbian.decay_all(now=future_time)
    assert decay_stats["total_processed"] >= 2
    assert decay_stats["solidified"] >= 1  # Solidified invariant survived!

    # Check weak memory state became dormant or prune_candidate
    row = store.db.execute("SELECT status FROM episodes WHERE id = ?", (wid,)).fetchone()
    assert row[0] in ("dormant", "prune_candidate")

    store.db.close()


def test_hebbian_co_activation_wiring(tmp_path: Path):
    db_file = tmp_path / "test_coact.db"
    store = Store(str(db_file))

    m1 = store.remember("Component A architecture pattern")["id"]
    m2 = store.remember("Component B interface adapter")["id"]

    hebbian = HebbianEngine(store.db)
    edges_created = hebbian.record_co_activation([m1, m2])
    assert edges_created == 1

    # Verify edge exists in SQLite
    edge = store.db.execute(
        "SELECT file_hash, relation FROM edges WHERE source = ? AND target = ?",
        (f"ep:{m1}", f"ep:{m2}")
    ).fetchone()
    assert edge is not None
    assert edge[1] == "co_activated"

    # Reinforce co-activation
    hebbian.record_co_activation([m1, m2])
    edge2 = store.db.execute(
        "SELECT file_hash FROM edges WHERE source = ? AND target = ?",
        (f"ep:{m1}", f"ep:{m2}")
    ).fetchone()
    assert float(edge2[0]) > float(edge[0])

    store.db.close()


# =====================================================================
# 3. Procedural Skill Synthesis Tests
# =====================================================================

def test_skill_synthesizer_lifecycle(tmp_path: Path):
    db_file = tmp_path / "test_skills.db"
    conn = sqlite3.connect(str(db_file))
    synth = SkillSynthesizer(conn)

    # 1. Create skill
    skill = synth.create_or_update_skill(
        name="Windows Pytest Directory Selection",
        action_recipe="Run 'pytest tests/' instead of bare 'pytest'",
        trigger_patterns=["pytest", "collecting", "windows"],
        preconditions="Windows OS and test runner execution",
        invariants="Never run bare pytest from root",
        confidence=0.95,
    )
    assert skill["id"].startswith("skill_")
    assert skill["confidence"] == 0.95

    # 2. Match skill
    matches = synth.match_skills("How should we run pytest on windows?")
    assert len(matches) >= 1
    assert matches[0]["name"] == "Windows Pytest Directory Selection"
    assert "pytest tests/" in matches[0]["action_recipe"]

    # 3. Record success
    ok = synth.record_success(skill["id"])
    assert ok is True
    row = conn.execute("SELECT success_count, confidence FROM skills WHERE id = ?", (skill["id"],)).fetchone()
    assert row[0] == 1
    assert row[1] >= 0.95

    # 4. Format for prompt
    formatted = synth.format_skills_for_prompt(matches)
    assert len(formatted) == 1
    assert "• [Active Skill:" in formatted[0]

    conn.close()


def test_auto_discover_skills_from_episodes(tmp_path: Path):
    db_file = tmp_path / "test_auto_skills.db"
    store = Store(str(db_file))

    # Insert two related decisions on sqlite locking
    store.remember("SQLite concurrent write lock error resolution: use WAL mode and busy timeout", kind="decision")
    store.remember("SQLite concurrent operations must acquire busy_timeout before write transaction", kind="decision")

    synth = SkillSynthesizer(store.db)
    discovered = synth.discover_skills_from_episodes(min_cluster_size=2)
    assert len(discovered) >= 1
    assert any("sqlite" in s["name"].lower() or "concurrent" in s["name"].lower() for s in discovered)

    store.db.close()


# =====================================================================
# 4. Autonomous Sleep Daemon Tests
# =====================================================================

def test_run_sleep_cycle_end_to_end(tmp_path: Path):
    db_file = tmp_path / "test_sleep.db"
    store = Store(str(db_file))

    # Populate some episodes
    e1 = store.remember("Architectural invariant: memory DB must stay under 100MB RSS", kind="decision", utility=2.0)["id"]
    store.reinforce(e1, outcome="success")

    # Run sleep cycle
    res = run_sleep_cycle(str(db_file), repo_root=str(tmp_path))
    assert res["ok"] is True
    assert "decay" in res
    assert os.path.exists(res["report_file"])

    # Read generated sleep report
    report_text = Path(res["report_file"]).read_text(encoding="utf-8")
    assert "# 🌙 GENESIS Sleep Consolidation Report" in report_text
    assert "NREM Synaptic Decay" in report_text

    # Verify sleep_cycles counter incremented
    st = store.status()
    assert st["calls"].get("sleep_cycles", 0) >= 1

    store.db.close()


def test_sleep_daemon_idle_detection(tmp_path: Path):
    db_file = tmp_path / "test_daemon_idle.db"
    store = Store(str(db_file))
    store.remember("Sample memory to set timestamp", kind="fact")

    daemon = SleepDaemon(
        db_path=str(db_file),
        idle_threshold_s=0.5,  # 500ms for test speed
        check_interval_s=0.1,
    )

    # Immediately after write: not idle yet
    res_immediate = daemon.step(now=time.time())
    assert res_immediate is None

    # After 1.0s: idle threshold met -> runs sleep
    future_now = time.time() + 1.0
    res_sleep = daemon.step(now=future_now)
    assert res_sleep is not None
    assert res_sleep["ok"] is True

    # Immediate next step without new activity: does not trigger again
    res_noop = daemon.step(now=future_now + 0.1)
    assert res_noop is None

    store.db.close()


# =====================================================================
# 5. MCP Tools & Gateway End-to-End Tests
# =====================================================================

def test_mcp_tools_and_gateway(tmp_path: Path):
    db_file = tmp_path / "test_mcp_cognitive.db"
    store = Store(str(db_file))

    # 1. remember
    msg_rem = {
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "remember",
            "arguments": {
                "text": "Always keep MCP tools strictly conforming to inputSchema",
                "kind": "decision",
                "utility": 1.2
            }
        }
    }
    resp_rem = handle(store, msg_rem)
    assert resp_rem["result"]["content"][0]["text"] is not None
    rem_data = json.loads(resp_rem["result"]["content"][0]["text"])
    eid = rem_data["id"]

    # 2. reinforce
    msg_reinf = {
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "reinforce",
            "arguments": {
                "id": eid,
                "outcome": "success",
                "note": "verified in test"
            }
        }
    }
    resp_reinf = handle(store, msg_reinf)
    reinf_data = json.loads(resp_reinf["result"]["content"][0]["text"])
    assert reinf_data["reinforcements"] == 1
    assert reinf_data["utility"] > 1.2

    # 3. synthesize_skill
    msg_skill = {
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "synthesize_skill",
            "arguments": {
                "name": "MCP Input Validation",
                "action_recipe": "Validate against tool inputSchema before dispatch",
                "trigger_patterns": ["mcp", "schema", "validation"],
                "confidence": 0.9
            }
        }
    }
    resp_skill = handle(store, msg_skill)
    skill_data = json.loads(resp_skill["result"]["content"][0]["text"])
    assert skill_data["name"] == "MCP Input Validation"

    # 4. skill_recall
    msg_sk_recall = {
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "skill_recall",
            "arguments": {
                "query": "How to handle mcp validation?"
            }
        }
    }
    resp_sk_recall = handle(store, msg_sk_recall)
    sk_recall_data = json.loads(resp_sk_recall["result"]["content"][0]["text"])
    assert len(sk_recall_data["skills"]) >= 1

    # 5. gateway route for sleep_now
    msg_sleep = {
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "genesis",
            "arguments": {
                "op": "sleep_now",
                "deep": False
            }
        }
    }
    resp_sleep = handle(store, msg_sleep)
    sleep_data = json.loads(resp_sleep["result"]["content"][0]["text"])
    assert sleep_data["ok"] is True

    # 6. status reflects solidified and skills
    st = store.status()
    assert st["skills_active"] >= 1
    assert "solidified" in st
    assert "dormant" in st

    store.db.close()

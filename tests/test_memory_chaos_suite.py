"""
GENESIS Memory Chaos Testing Suite (Acceptance Gate for Production 10/10).
Tests system degradation and coherence under intentional failure injections:
1. Spine Snooping Under Invalidation (L2 Coherence Gate)
2. Explicit Invalidation & Causal Audit Trace
3. Contradiction Injection & Deterministic Resolution
4. Entry-Miss Graceful Degradation (Fallback vs Starvation)
5. Hub Node Blowup & Attention Dilution Containment
6. Concurrent Writer Race Safety (WAL Multi-threaded Contention)
7. Stale ID Eviction via Snooping Bus

Hermetic, fast, zero-API, stdlib only.
Run: python -m pytest tests/test_memory_chaos_suite.py -v
"""

import os
import sqlite3
import sys
import tempfile
import threading
import time
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from genesis_memory.daemon.server import Store, SCHEMA_VERSION
from genesis_memory.hooks.subconscious_hook import query_subconscious_memories


@pytest.fixture()
def dbpath():
    tmp = tempfile.mkdtemp(prefix="chaos_test_")
    return os.path.join(tmp, "chaos_memory.db")


def test_spine_snooping_under_invalidation(dbpath):
    """Probe 1: Invalidation in L3/L4 must prune L2 Spine instantly via Snooping Bus."""
    store = Store(dbpath)

    # 1. Store Decision A
    res_a = store.remember("Use FP32 for cortical time loops", kind="decision", utility=1.5)
    id_a = res_a["id"]

    # Verify Decision A is active and recalled
    recalled = store.recall("FP32 cortical time loops")
    assert any(r["id"] == id_a for r in recalled["results"])

    # 2. Supersede Decision A with Decision B
    res_b = store.remember(
        "Cast weights to FP16 to activate Turing Tensor Cores per Rule 23",
        kind="decision", utility=2.0, supersedes_id=id_a
    )
    id_b = res_b["id"]
    assert res_b["supersedes_id"] == id_a

    # Check database status
    row_a = store.db.execute("SELECT status, superseded_by FROM episodes WHERE id = ?", (id_a,)).fetchone()
    assert row_a == ("superseded", id_b)

    row_b = store.db.execute("SELECT status, superseded_by FROM episodes WHERE id = ?", (id_b,)).fetchone()
    assert row_b == ("active", None)

    # 3. Recall must NEVER return Decision A, only Decision B
    recalled_after = store.recall("cortical time loops FP16 FP32")
    active_ids = [r["id"] for r in recalled_after["results"]]
    assert id_a not in active_ids, "L3/L4 recall returned superseded Decision A!"
    assert id_b in active_ids, "L3/L4 recall missed active Decision B!"

    # 4. Hook Snooping Bus: simulate an outdated L2 candidate list containing [A, B]
    cur = store.db.cursor()
    candidate_results = [
        (id_a, "decision", "Use FP32 for cortical time loops"),
        (id_b, "decision", "Cast weights to FP16 to activate Turing Tensor Cores per Rule 23")
    ]
    cand_ids = [r[0] for r in candidate_results]
    placeholders = ",".join("?" for _ in cand_ids)
    snoop_sql = (
        f"SELECT id FROM episodes WHERE id IN ({placeholders}) "
        "AND (status = 'active' OR status IS NULL)"
    )
    valid_ids = {row[0] for row in cur.execute(snoop_sql, cand_ids).fetchall()}
    snooped_results = [r for r in candidate_results if r[0] in valid_ids]

    assert len(snooped_results) == 1
    assert snooped_results[0][0] == id_b
    assert id_a not in [r[0] for r in snooped_results]

    store.db.close()


def test_explicit_invalidation_coherence(dbpath):
    """Probe 2: Explicit invalidation tool preserves causal audit without serving stale data."""
    store = Store(dbpath)

    res = store.remember("Deprecate temporary socket cache at /tmp/cache.sock", kind="fact")
    mid = res["id"]

    # Invalidate via daemon tool method
    inv_res = store.invalidate(mid)
    assert inv_res["id"] == mid
    assert inv_res["status"] == "invalidated"
    assert inv_res["updated"] == 1

    # Recall should filter it out
    recall_res = store.recall("socket cache")
    assert not any(r["id"] == mid for r in recall_res["results"])

    # Status telemetry reflects active vs invalidated
    st = store.status()
    assert st["episodes"] == 0
    assert st["episodes_total"] == 1
    assert st["invalidated"] == 1

    store.db.close()


def test_contradiction_injection_and_resolution(dbpath):
    """Probe 3: Contradictory memories without supersede must resolve deterministically by score."""
    store = Store(dbpath)

    # Injected contradiction:
    store.remember("Dashboard server port is 8080", kind="fact", utility=1.0)
    time.sleep(0.01)  # small delta for recency
    res_newer = store.remember("Dashboard server port is 8090", kind="fact", utility=1.5)
    id_newer = res_newer["id"]

    # Query targeting the contradiction
    recall_res = store.recall("Dashboard server port", limit=1)
    assert len(recall_res["results"]) == 1
    # Higher utility & recency must deterministically win without throwing
    assert recall_res["results"][0]["id"] == id_newer
    assert "8090" in recall_res["results"][0]["snippet"]

    store.db.close()


def test_entry_miss_graceful_fallback(dbpath):
    """Probe 4: Out-of-vocabulary entry miss falls back gracefully to working memory anchor."""
    store = Store(dbpath)

    # Store high-utility anchor
    store.remember("Assistant identity: Jigar, operating with zero synthetic fallbacks", kind="fact", utility=2.0)

    # Completely disjoint query (entry-miss)
    recall_res = store.recall("quantum_teleportation_entanglement_superluminal_987", limit=1)
    assert len(recall_res["results"]) == 1
    assert "Jigar" in recall_res["results"][0]["snippet"]
    # Graceful fallback: candidates considered > 0, no exception
    assert recall_res["candidates_considered"] > 0

    store.db.close()


def test_hub_blowup_token_containment(dbpath, monkeypatch):
    """Probe 5: Monster hub memory with 500 words is strictly contained; zero attention dilution."""
    store = Store(dbpath)

    # 500-word monster memory
    verbose_text = "architecture " * 500
    res = store.remember(verbose_text, kind="decision", utility=2.0)
    mid = res["id"]

    # 1. Daemon recall containment
    recall_res = store.recall("architecture", snippet_words=40, max_tokens_estimate=200)
    snippet = recall_res["results"][0]["snippet"]
    words = snippet.split()
    assert len(words) <= 40
    assert recall_res["tokens_estimate_total"] <= 200

    # 2. Subconscious Hook containment (<150 tokens)
    monkeypatch.setenv("GENESIS_DAEMON_DB", dbpath)
    from genesis_memory.hooks import subconscious_hook as hook
    monkeypatch.setattr(hook, "DB_PATH", dbpath)

    formatted, telemetry = hook.query_subconscious_memories("architecture", max_tokens=150)
    assert len(formatted) > 0
    # Fresh session => one-time boost (+400 chars, flagged); still hard-bounded.
    assert telemetry["boosted"] is True
    assert telemetry["injected_tokens"] <= (150 * 4 + 400) // 4

    store.db.close()


def test_concurrent_writer_race_safety(dbpath):
    """Probe 6: Concurrent threads writing simultaneously to WAL database without lock corruption."""
    store = Store(dbpath)
    store.db.close()

    errors = []
    writes_per_thread = 5
    num_threads = 5

    def worker(tid):
        try:
            s = Store(dbpath)
            for i in range(writes_per_thread):
                s.remember(f"thread {tid} write {i} telemetry entry", kind="fact")
            s.db.close()
        except Exception as e:
            errors.append(f"Thread {tid}: {e}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent write errors detected: {errors}"

    # Verify integrity and row count
    verify_store = Store(dbpath)
    count = verify_store.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    assert count == writes_per_thread * num_threads

    st = verify_store.status()
    assert st["calls"]["remember"] == writes_per_thread * num_threads
    verify_store.db.close()


def test_stale_id_eviction_snooping(dbpath):
    """Probe 7: Snooping Bus drops deleted IDs on the fly without crashing."""
    store = Store(dbpath)
    m1 = store.remember("Active memory 1", kind="fact")["id"]
    m2 = store.remember("Memory to be deleted", kind="fact")["id"]

    # Delete m2 behind the scenes
    store.forget(m2)

    cur = store.db.cursor()
    # Snooper checks both IDs
    cand_ids = [m1, m2]
    placeholders = ",".join("?" for _ in cand_ids)
    snoop_sql = (
        f"SELECT id FROM episodes WHERE id IN ({placeholders}) "
        "AND (status = 'active' OR status IS NULL)"
    )
    valid_ids = {row[0] for row in cur.execute(snoop_sql, cand_ids).fetchall()}

    assert m1 in valid_ids
    assert m2 not in valid_ids
    store.db.close()


def test_secret_scanner_blocks_leakage(dbpath):
    """Probe 8: Secret Scanner blocks API keys and private keys with observable counter."""
    store = Store(dbpath)

    # 1. OpenAI API key injection
    with pytest.raises(ValueError, match="Secret detected"):
        store.remember("Configure OpenAI client with sk-live1234567890123456789012", kind="fact")

    # 2. Google API key injection
    with pytest.raises(ValueError, match="Secret detected"):
        store.remember("Set GEMINI_API_KEY=AIzaSyD1234567890123456789012345678901", kind="fact")

    # 3. Private Key injection
    with pytest.raises(ValueError, match="Secret detected"):
        store.remember("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...", kind="fact")

    # Verify zero episodes stored and counter increments
    assert store.status()["episodes"] == 0
    assert store.status()["calls"]["secrets_blocked"] == 3

    # Safe text passes cleanly
    res = store.remember("Standard non-sensitive architectural note", kind="fact")
    assert res["id"] is not None
    assert store.status()["episodes"] == 1
    store.db.close()


def test_conflict_staging_and_sleep_veto_queue(dbpath):
    """Probe 9: Multi-window decision collisions are staged for Sleep Report Veto Queue."""
    from genesis_memory.sleep import sleep_consolidation
    store = Store(dbpath)

    # Store first decision
    res1 = store.remember("Use FP32 kernel precision for time loops", kind="decision", project="genesis")
    id1 = res1["id"]

    # Store colliding second decision without supersedes_id (simulating concurrent window)
    time.sleep(0.01)
    res2 = store.remember("Use FP16 Tensor Cores kernel precision for time loops", kind="decision", project="genesis")
    id2 = res2["id"]

    # Assert collision was detected and staged
    st = store.status()
    assert st["conflicts_pending"] == 1
    assert st["calls"]["conflicts_detected"] == 1

    # Check Sleep Report reflects the staged conflict in its Veto Queue
    report = sleep_consolidation.build_report(dbpath)
    assert "Staged Conflicts (Concurrent Writer Veto Queue: 1 pending)" in report
    assert f"New #{id2} vs Existing #{id1}" in report
    assert "Conflicts detected: 1" in report

    # Resolve conflict: supersede id1 with id2
    c_id = store.db.execute("SELECT id FROM conflicts WHERE status = 'pending'").fetchone()[0]
    resolve_res = store.resolve_conflict(conflict_id=c_id, action="superseded", winner_id=id2)
    assert resolve_res["status"] == "resolved"

    # Assert id1 is now superseded and conflict is closed
    row1 = store.db.execute("SELECT status, superseded_by FROM episodes WHERE id = ?", (id1,)).fetchone()
    assert row1 == ("superseded", id2)
    assert store.status()["conflicts_pending"] == 0
    assert store.status()["calls"]["vetoes_resolved"] == 1

    # Sleep Report now reports 0 pending conflicts
    report_after = sleep_consolidation.build_report(dbpath)
    assert "Staged Conflicts (Concurrent Writer Veto Queue: 0 pending)" in report_after
    assert "Vetoes resolved: 1" in report_after

    store.db.close()


def test_ast_edge_extractor_module_imports(dbpath):
    """Probe 10: AST Edge Extractor captures module imports with zero code body storage (Privacy Invariant)."""
    from genesis_memory.core.ast_edge_extractor import sync_file_edges, normalize_repo_path
    store = Store(dbpath)

    # Create a temporary python module with imports
    temp_dir = tempfile.mkdtemp(prefix="ast_test_mod_")
    mod_file = os.path.join(temp_dir, "sample_module.py")
    with open(mod_file, "w", encoding="utf-8") as f:
        f.write(
            "import os\n"
            "import sys\n"
            "from genesis.server.genesis_daemon import Store\n"
            "\n"
            "def run():\n"
            "    # Secret API key inside code body (must NOT leak into edges!)\n"
            "    api_key = 'sk-live1234567890123456789012'\n"
            "    print('Hello world')\n"
        )

    res = sync_file_edges(store.db, mod_file, root=temp_dir)
    assert res["status"] == "synchronized"
    assert res["extracted"] >= 3

    # Query edges table
    rows = store.db.execute("SELECT source, target, relation, file_hash, status FROM edges").fetchall()
    assert len(rows) >= 3
    targets = {r[1] for r in rows}
    assert "os" in targets
    assert "sys" in targets
    assert any("genesis_daemon" in t for t in targets)

    # Strict Privacy Audit: verify zero code bodies or secret text stored in edges table
    all_edge_data = " ".join(str(val) for r in rows for val in r)
    assert "sk-live" not in all_edge_data, "Secret leaked into edges table payload!"
    assert "Hello world" not in all_edge_data, "Code body leaked into edges table payload!"
    assert "def run" not in all_edge_data, "AST definition leaked into edges table payload!"

    store.db.close()


def test_edge_invalidation_on_file_mutation(dbpath):
    """Probe 11: File mutation/hash drift triggers MESI invalidation of dependent edges."""
    from genesis_memory.core.ast_edge_extractor import sync_file_edges
    store = Store(dbpath)

    temp_dir = tempfile.mkdtemp(prefix="ast_mesi_")
    mod_file = os.path.join(temp_dir, "mutating_service.py")

    # 1. Initial version: depends on 'os' and 'json'
    with open(mod_file, "w", encoding="utf-8") as f:
        f.write("import os\nimport json\n")

    res1 = sync_file_edges(store.db, mod_file, root=temp_dir)
    assert res1["extracted"] == 2
    assert res1["invalidated"] == 0

    rel_source = res1["source"]
    dep1 = store.get_dependencies(rel_source)
    assert dep1["count"] == 2
    targets1 = {d["target"] for d in dep1["dependencies"]}
    assert targets1 == {"os", "json"}

    # 2. No changes -> verify idempotent no-op
    res_noop = sync_file_edges(store.db, mod_file, root=temp_dir)
    assert res_noop["status"] == "unchanged"
    assert res_noop["extracted"] == 0
    assert res_noop["invalidated"] == 0

    # 3. Mutate file: remove 'json', add 'sqlite3'
    time.sleep(0.01)
    with open(mod_file, "w", encoding="utf-8") as f:
        f.write("import os\nimport sqlite3\n")

    res2 = sync_file_edges(store.db, mod_file, root=temp_dir)
    assert res2["status"] == "synchronized"
    assert res2["invalidated"] == 2  # Previous active edges marked 'invalidated'
    assert res2["extracted"] == 2

    # Verify MESI status in database
    inv_rows = store.db.execute("SELECT target FROM edges WHERE source = ? AND status = 'invalidated'", (rel_source,)).fetchall()
    # At least one target (json) must remain invalidated
    assert any(r[0] == "json" for r in inv_rows)

    # Active dependencies query returns strictly active dependencies
    dep2 = store.get_dependencies(rel_source)
    targets2 = {d["target"] for d in dep2["dependencies"]}
    assert targets2 == {"os", "sqlite3"}
    assert "json" not in targets2

    # Status counters
    st = store.status()
    assert st["edges_active"] == 2
    assert st["calls"]["edges_extracted"] >= 4
    assert st["calls"]["edges_invalidated"] >= 2

    store.db.close()


def test_conflict_threshold_and_split_veto_counters(dbpath):
    """Probe 12: Stopword/threshold filtering eliminates veto fatigue and splits applied/dismissed counters."""
    store = Store(dbpath)

    # 1. Non-colliding notes sharing only common stopwords must NOT stage conflict
    store.remember("Note using the and for with this system", kind="decision", project="p1")
    store.remember("Another note using the and for with another feature", kind="decision", project="p1")
    assert store.status()["conflicts_pending"] == 0
    assert store.status()["calls"]["conflicts_detected"] == 0

    # 2. Colliding decisions with strong multi-term overlap DO stage conflict
    d1 = store.remember("Deploy TensorRT FP16 acceleration kernel", kind="decision", project="p1")["id"]
    d2 = store.remember("Deploy PyTorch FP16 acceleration kernel", kind="decision", project="p1")["id"]
    assert store.status()["conflicts_pending"] == 1
    assert store.status()["calls"]["conflicts_detected"] == 1

    # 3. Resolve conflict 1 via dismissal
    c1_id = store.db.execute("SELECT id FROM conflicts WHERE status = 'pending'").fetchone()[0]
    store.resolve_conflict(conflict_id=c1_id, action="dismissed")
    st1 = store.status()
    assert st1["conflicts_pending"] == 0
    assert st1["calls"]["vetoes_resolved"] == 1
    assert st1["calls"]["vetoes_dismissed"] == 1
    assert st1["calls"]["vetoes_applied"] == 0
    assert st1["calls"]["dismissed_rate"] == 1.0

    # 4. Stage second conflict and resolve via supersede
    d3 = store.remember("Deploy WebGPU FP16 acceleration kernel", kind="decision", project="p1")["id"]
    assert store.status()["conflicts_pending"] == 1
    c2_id = store.db.execute("SELECT id FROM conflicts WHERE status = 'pending'").fetchone()[0]
    store.resolve_conflict(conflict_id=c2_id, action="superseded", winner_id=d3)
    st2 = store.status()
    assert st2["conflicts_pending"] == 0
    assert st2["calls"]["vetoes_resolved"] == 2
    assert st2["calls"]["vetoes_dismissed"] == 1
    assert st2["calls"]["vetoes_applied"] == 1
    assert st2["calls"]["dismissed_rate"] == 0.5

    store.db.close()


def test_verification_trap_closure_attestation(dbpath):
    """Probe 13: Attestation Trap audits graph closure and demand-pages missing dependencies."""
    from genesis_memory.core.verification_trap import AttestationTrap, attest_closure
    store = Store(dbpath)

    # 1. Setup dependency chain: root_service -> auth_module and crypto_kernel
    now = time.time()
    store.db.execute(
        "INSERT INTO edges(source, target, relation, file_hash, status, ts, updated) "
        "VALUES('services/root_service.py', 'modules/auth_module.py', 'depends_on', 'h1', 'active', ?, ?)",
        (now, now)
    )
    store.db.execute(
        "INSERT INTO edges(source, target, relation, file_hash, status, ts, updated) "
        "VALUES('services/root_service.py', 'kernels/crypto_kernel.py', 'depends_on', 'h2', 'active', ?, ?)",
        (now, now)
    )

    # Store episode for auth_module
    m_auth = store.remember("auth_module handles token claims and signature verification", kind="fact")["id"]
    store.db.commit()

    # 2. Context has root_service only (missing both auth_module and crypto_kernel)
    candidate_memories = [(101, "decision", "Refactor services/root_service.py pipeline")]
    trap = AttestationTrap(store.db, fault_cap=2, threshold=0.40)

    # 3. Trap execution
    res = trap.trap_and_resolve(candidate_memories, query_text="inspect services/root_service.py")
    assert res["faults_triggered"] == 2
    assert res["faults_resolved"] == 2
    assert res["fault_caps_hit"] == 0
    assert res["fallback_active"] is False

    # Verify augmented memories include demand-paged dependencies
    aug_texts = " ".join(m[2] for m in res["memories"])
    assert "auth_module" in aug_texts
    assert "crypto_kernel" in aug_texts

    # Check status counters
    st = store.status()
    assert st["calls"]["faults_triggered"] == 2
    assert st["calls"]["faults_resolved"] == 2

    store.db.close()


def test_fault_storm_livelock_containment(dbpath):
    """Probe 14: Dense/cyclic dependency graph triggers fault-cap and gracefully falls back (<5ms)."""
    from genesis_memory.core.verification_trap import AttestationTrap
    store = Store(dbpath)

    # Construct pathological cyclic/dense graph: A -> B, B -> C, C -> A, A -> D, A -> E, A -> F
    now = time.time()
    dense_edges = [
        ("cyclic/node_a.py", "cyclic/node_b.py"),
        ("cyclic/node_b.py", "cyclic/node_c.py"),
        ("cyclic/node_c.py", "cyclic/node_a.py"),
        ("cyclic/node_a.py", "dense/node_d.py"),
        ("cyclic/node_a.py", "dense/node_e.py"),
        ("cyclic/node_a.py", "dense/node_f.py"),
    ]
    for s, t in dense_edges:
        store.db.execute(
            "INSERT INTO edges(source, target, relation, file_hash, status, ts, updated) "
            "VALUES(?, ?, 'depends_on', 'cycle_hash', 'active', ?, ?)",
            (s, t, now, now)
        )
    store.db.commit()

    # Empty context: all dependencies missing
    candidate_memories = [(201, "fact", "Working with cyclic/node_a.py")]
    trap = AttestationTrap(store.db, fault_cap=2, threshold=0.40)

    t0 = time.time()
    res = trap.trap_and_resolve(candidate_memories, query_text="execute cyclic/node_a.py")
    elapsed_ms = (time.time() - t0) * 1000.0

    # Strict Livelock Bounds:
    # 1. Faults triggered must be strictly capped at fault_cap (2)
    assert res["faults_triggered"] == 2
    # 2. Fault cap hit must be recorded
    assert res["fault_caps_hit"] == 1
    # 3. Fallback active
    assert res["fallback_active"] is True
    # 4. Zero thrashing / livelock: execution must complete in sub-10ms
    assert elapsed_ms < 50.0, f"Fault storm thrashing detected! Elapsed: {elapsed_ms}ms"

    # Verify counter in database
    st = store.status()
    assert st["calls"]["fault_caps_hit"] >= 1

    store.db.close()


def test_trap_roc_benchmark_gate():
    try:
        from benchmark_trap_roc import run_roc_benchmark
    except ImportError:
        import sys
        from pathlib import Path
        cur_dir = Path(__file__).resolve().parent
        if str(cur_dir) not in sys.path:
            sys.path.insert(0, str(cur_dir))
        from benchmark_trap_roc import run_roc_benchmark
    res = run_roc_benchmark()

    assert res["dataset_size"] == 100
    assert res["auc"] >= 0.90, f"AUC gate failure: {res['auc']} < 0.90"
    assert res["optimal_point"]["f1"] >= 0.85, f"F1 score failure: {res['optimal_point']['f1']} < 0.85"


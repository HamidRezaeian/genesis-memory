"""
GENESIS Verification Trap ROC Evaluation Microbenchmark (Priority 3).
Calibrates and audits AttestationTrap precision/recall on synthetic workload with ground-truth.
Sweeps detection threshold theta across [0.05, 0.95], computes confusion matrices,
Sensitivity (TPR), 1-Specificity (FPR), Precision, F1-Score, and Area Under the ROC Curve (AUC).
Stdlib only. Zero external libraries.
"""

import math
import os
import sqlite3
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from genesis_memory.core.verification_trap import attest_closure, score_attestation, DEFAULT_ATTESTATION_THRESHOLD
from genesis_memory.daemon.server import Store


def create_synthetic_ground_truth_dataset():
    """
    Generate 100 ground-truth synthetic test cases:
    - 50 Positive cases (y=1): Closure has missing dependencies (Trap MUST fire fault).
    - 50 Negative cases (y=0): Closure is complete in context (Trap MUST pass).
    """
    dataset = []

    # Modules and their ground-truth dependency closures
    components = [
        ("cortical_engine", ["tensor_ops", "synapse_weights", "memory_bus"]),
        ("mcts_planner", ["puct_selection", "tree_policy", "rollout_cache"]),
        ("substrate_gpu", ["cuda_stream", "vram_allocator", "fp16_kernel"]),
        ("sensor_grid", ["spatial_encoder", "retina_slice", "neighbor_stencil"]),
        ("plasticity_stdp", ["trace_decay", "reward_modulator", "eligibility_trace"]),
        ("genesis_daemon", ["subconscious_hook", "sqlite_fts", "snooping_bus"]),
        ("telemetry_feed", ["snapshot_builder", "rss_monitor", "hit_counter"]),
        ("audio_encoder", ["fft_spectrum", "gammatone_filter", "cochlea_buffer"]),
        ("causal_tracer", ["intervene_engine", "counterfactual_map", "graph_dag"]),
        ("compact_ram", ["zero_hole_remap", "page_compactor", "cgroup_limiter"]),
    ]

    # Generate 50 Positive (Missing dependencies, including borderline partial token matches)
    for i in range(50):
        comp_idx = i % len(components)
        root, deps = components[comp_idx]
        omitted_dep = deps[i % len(deps)]
        included_deps = [d for d in deps if d != omitted_dep]
        
        ctx_parts = [f"Module {root} initialized in production environment."]
        for inc in included_deps:
            ctx_parts.append(f"Linked component {inc} active.")
        
        # In some cases, inject distractor partial word of omitted_dep to test discrimination
        if i % 2 == 0 and "_" in omitted_dep:
            sub = omitted_dep.split("_")[0]
            ctx_parts.append(f"Generic reference to {sub} noticed.")

        context_text = " ".join(ctx_parts)
        dataset.append({
            "id": f"pos_{i}",
            "root": root,
            "all_deps": deps,
            "omitted_dep": omitted_dep,
            "context_text": context_text,
            "ground_truth": 1  # Fault expected
        })

    # Generate 50 Negative (Complete closures, with varying lexical phrasing)
    for i in range(50):
        comp_idx = i % len(components)
        root, deps = components[comp_idx]
        ctx_parts = [f"Module {root} execution pipeline with full dependency closure:"]
        for d in deps:
            if i % 3 == 0:
                ctx_parts.append(f"Imported and initialized {d} successfully.")
            elif i % 3 == 1:
                ctx_parts.append(f"Active binding {d} verified in context.")
            else:
                ctx_parts.append(f"Component {d} loaded and attested in memory buffer.")
        
        context_text = " ".join(ctx_parts)
        dataset.append({
            "id": f"neg_{i}",
            "root": root,
            "all_deps": deps,
            "omitted_dep": None,
            "context_text": context_text,
            "ground_truth": 0  # Pass expected (no fault)
        })

    return dataset



def setup_mock_edges_db(dataset):
    """Populate temporary SQLite database with edges for dataset."""
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE edges(id INTEGER PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL, "
        "relation TEXT DEFAULT 'depends_on', file_hash TEXT, status TEXT DEFAULT 'active', ts REAL, updated REAL)"
    )
    db.execute("CREATE UNIQUE INDEX idx_edges ON edges(source, target, relation)")

    now = time.time()
    for item in dataset:
        root = item["root"]
        for dep in item["all_deps"]:
            db.execute(
                "INSERT OR IGNORE INTO edges(source, target, relation, file_hash, status, ts, updated) "
                "VALUES(?, ?, 'depends_on', 'mock_hash', 'active', ?, ?)",
                (root, dep, now, now)
            )
    db.commit()
    return db


def run_roc_benchmark():
    """
    Run complete ROC analysis over synthetic ground-truth dataset.
    Returns evaluation metrics, ROC points, optimal threshold, and AUC.
    """
    dataset = create_synthetic_ground_truth_dataset()
    db = setup_mock_edges_db(dataset)

    # Sweep threshold theta in [0.05, 0.95] in 19 steps
    thetas = [round(0.05 + 0.05 * i, 2) for i in range(19)]
    roc_points = []

    for th in thetas:
        tp, fp, tn, fn = 0, 0, 0, 0
        for sample in dataset:
            root = sample["root"]
            ctx = sample["context_text"]
            y_true = sample["ground_truth"]

            audit = attest_closure(db, [root], ctx, threshold=th, max_depth=1)
            # If not attested -> fault declared (y_pred = 1)
            # If attested -> pass (y_pred = 0)
            y_pred = 1 if not audit["attested"] else 0

            if y_true == 1 and y_pred == 1:
                tp += 1
            elif y_true == 0 and y_pred == 1:
                fp += 1
            elif y_true == 0 and y_pred == 0:
                tn += 1
            elif y_true == 1 and y_pred == 0:
                fn += 1

        tpr = tp / max(1, (tp + fn))
        fpr = fp / max(1, (fp + tn))
        precision = tp / max(1, (tp + fp))
        f1 = (2 * precision * tpr) / max(1e-6, (precision + tpr))

        roc_points.append({
            "theta": th,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "tpr": round(tpr, 4),
            "fpr": round(fpr, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4)
        })

    # Compute AUC using trapezoidal rule
    # Ensure (0,0) and (1,1) endpoints are sorted by FPR ascending
    pts = sorted(roc_points, key=lambda p: (p["fpr"], p["tpr"]))
    curve = [(0.0, 0.0)] + [(p["fpr"], p["tpr"]) for p in pts] + [(1.0, 1.0)]
    # Deduplicate curve points
    dedup_curve = []
    for pt in curve:
        if not dedup_curve or pt != dedup_curve[-1]:
            dedup_curve.append(pt)

    auc = 0.0
    for i in range(1, len(dedup_curve)):
        x0, y0 = dedup_curve[i - 1]
        x1, y1 = dedup_curve[i]
        auc += (x1 - x0) * (y0 + y1) / 2.0

    auc = round(min(1.0, max(0.0, auc)), 4)

    # Find optimal threshold (highest F1 score)
    best_pt = max(roc_points, key=lambda p: p["f1"])

    return {
        "dataset_size": len(dataset),
        "positives": sum(1 for s in dataset if s["ground_truth"] == 1),
        "negatives": sum(1 for s in dataset if s["ground_truth"] == 0),
        "auc": auc,
        "optimal_theta": best_pt["theta"],
        "optimal_point": best_pt,
        "roc_points": roc_points
    }


def print_roc_report(results):
    print("=" * 78)
    print(" GENESIS VERIFICATION TRAP ROC EVALUATION REPORT")
    print(f" Dataset: {results['dataset_size']} samples ({results['positives']} Positives, {results['negatives']} Negatives)")
    print(f" Area Under ROC Curve (AUC): {results['auc']} (Target Gate: >= 0.90)")
    print(f" Optimal Operating Threshold: theta* = {results['optimal_theta']}")
    opt = results['optimal_point']
    print(f" Optimal Metrics: TPR={opt['tpr']} | FPR={opt['fpr']} | Precision={opt['precision']} | F1={opt['f1']}")
    print("=" * 78)
    print(f"{'Theta':>6} | {'TPR (Sens)':>10} | {'FPR (1-Spec)':>12} | {'Precision':>10} | {'F1-Score':>9} | {'TP':>4} {'FP':>4} {'TN':>4} {'FN':>4}")
    print("-" * 78)
    for p in results["roc_points"]:
        star = " *" if p["theta"] == results["optimal_theta"] else ""
        print(f"{p['theta']:>6.2f} | {p['tpr']:>10.4f} | {p['fpr']:>12.4f} | {p['precision']:>10.4f} | {p['f1']:>9.4f} | {p['tp']:>4} {p['fp']:>4} {p['tn']:>4} {p['fn']:>4}{star}")
    print("=" * 78)


if __name__ == "__main__":
    t0 = time.time()
    res = run_roc_benchmark()
    elapsed = round(time.time() - t0, 3)
    print_roc_report(res)
    print(f"Microbench Execution Time: {elapsed}s (Budget: < 1.0s)")
    assert res["auc"] >= 0.90, f"AUC regression: {res['auc']} < 0.90"
    print("Status: CERTIFIED ROC AUC GATE PASSED")

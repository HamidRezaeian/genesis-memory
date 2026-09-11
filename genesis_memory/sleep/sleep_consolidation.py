"""
GENESIS sleep consolidation (v0.1) — nightly/end-of-session digest + veto list.
STRICTLY READ-ONLY on the database (never deletes/merges: human vetoes via the
daemon `forget` tool). Groups episodes by (project, kind), surfaces top-accessed
memories, and PROPOSES forget candidates (zero-access + old + low utility) with ids.
Usage: python -m genesis.server.sleep_consolidation --db PATH [--out report.md]
       [--stale-days 30] [--top 10]
Stdlib only (RSS discipline inherited).
"""

import argparse
import os
import sqlite3

from genesis_memory.core import db as _dbx
import time


def build_report(db_path, stale_days=30, top_n=10):
    db = _dbx.connect(db_path, readonly=True)
    try:
        ver = db.execute("PRAGMA user_version").fetchone()[0]
    except Exception:
        ver = -1
    now = time.time()
    total = db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    by_kind = db.execute(
        "SELECT project, kind, COUNT(*) FROM episodes GROUP BY project, kind"
        " ORDER BY COUNT(*) DESC").fetchall()
    tops = db.execute(
        "SELECT id, project, kind, substr(text,1,200), utility, accesses FROM episodes"
        " ORDER BY utility DESC, accesses DESC LIMIT ?", (top_n,)).fetchall()
    cutoff = now - stale_days * 86400.0
    cands = db.execute(
        "SELECT id, project, kind, substr(text,1,160), updated FROM episodes"
        " WHERE accesses = 0 AND updated < ? AND utility <= 1.0"
        " ORDER BY updated ASC LIMIT 50", (cutoff,)).fetchall()
    tok_est = db.execute("SELECT SUM(LENGTH(text)) FROM episodes").fetchone()[0] or 0
    # Check for staged conflicts (concurrent writer collision queue)
    has_conflicts_table = db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='conflicts'"
    ).fetchone()[0] > 0
    conflicts = []
    if has_conflicts_table:
        conflicts = db.execute(
            "SELECT c.id, c.new_id, c.conflicting_id, c.similarity, "
            "e1.text, e2.text, e1.kind, e1.project "
            "FROM conflicts c "
            "JOIN episodes e1 ON e1.id = c.new_id "
            "JOIN episodes e2 ON e2.id = c.conflicting_id "
            "WHERE c.status = 'pending' ORDER BY c.ts DESC LIMIT 50"
        ).fetchall()

    # Query observable telemetry counters
    counters = {}
    has_counters = db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='counters'"
    ).fetchone()[0] > 0
    if has_counters:
        for name, cnt in db.execute("SELECT name, count FROM counters").fetchall():
            counters[name] = cnt

    lines = [
        "# GENESIS Sleep Report (read-only digest, human veto required)",
        f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}",
        f"- DB: {db_path} (schema v{ver})",
        f"- Episodes: {total} (~{tok_est // 4} tokens heuristic estimate, full text)",
        "",
        "## By project/kind",
    ]
    for proj, kind, n in by_kind:
        lines.append(f"- {proj} / {kind}: {n}")
    lines += ["", f"## Top {top_n} by utility (never auto-deleted)"]
    for i, p, k, t, u, a in tops:
        lines.append(f"- [#{i}] [{p}/{k}] u={u} acc={a}: {t}")
    lines += ["",
              f"## Staged Conflicts (Concurrent Writer Veto Queue: {len(conflicts)} pending)",
              "Multi-window or concurrent writes that touched similar domains without explicit superseding.",
              "Resolve each conflict via `resolve_conflict` or `invalidate`:"]
    for cid, nid, oid, sim, t_new, t_old, k, p in conflicts:
        lines.append(f"- [ ] Conflict #{cid} [{p}/{k}] (sim={sim}): New #{nid} vs Existing #{oid}")
        lines.append(f"  - New: {t_new[:120]}...")
        lines.append(f"  - Existing: {t_old[:120]}...")
        lines.append(f"  - Action: call `resolve_conflict(conflict_id={cid}, action='superseded', winner_id={nid})` or `resolve_conflict(conflict_id={cid}, action='dismissed')`")
    if not conflicts:
        lines.append("- (none: zero unresolved concurrent conflicts detected)")
    lines += ["",
              f"## Proposed forgets ({len(cands)}: zero-access, older than {stale_days}d, utility<=1.0)",
              "Nothing was deleted. Veto or approve each id via the daemon `forget` tool."]
    for i, p, k, t, u in cands:
        lines.append(f"- [ ] forget #{i} [{p}/{k}]: {t}")
    if not cands:
        lines.append("- (none: every memory was accessed or is fresh)")
    lines += ["",
              "## Observable System Counters",
              f"- Conflicts detected: {counters.get('conflicts_detected', 0)}",
              f"- Vetoes resolved: {counters.get('vetoes_resolved', 0)}",
              f"- Secrets blocked: {counters.get('secrets_blocked', 0)}",
              f"- Total recalls: {counters.get('recall', 0)} (Hits: {counters.get('hits', 0)})",
              "",
              "## How to act",
              "- Approve a forget: call `forget` with its id.",
              "- Resolve a conflict: call `resolve_conflict` or `invalidate`.",
              "- Keep everything: do nothing; this report is advisory only."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sleep_consolidation")
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--stale-days", type=float, default=30.0)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args(argv)
    report = build_report(a.db, a.stale_days, a.top)
    out = a.out or f"sleep_report_{time.strftime('%Y%m%d_%H%M%S')}.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"sleep report: {len(report)} chars -> {out} (DB untouched)")


if __name__ == "__main__":
    main()

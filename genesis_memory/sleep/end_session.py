"""
End-of-session pipeline (v0.1): Sleep Report + persistent user ledger entry.
- Runs the read-only sleep consolidation on the user DB.
- Saves the report under <dbdir>/sleep_reports/.
- Appends ONE JSONL row to <dbdir>/ledger.jsonl with OBSERVED stats only
  (episode count always; proxy token/spent fields summed ONLY from immutable
  per-request receipts newer than per-instance watermarks — offline keeps
  them null, never fabricated; counterfactual saved/baseline never ledgered).
Usage: python -m genesis_memory.sleep.end_session [--db PATH] [--note "text"]
       [--proxy-url http://127.0.0.1:8000]
Stdlib only.
"""

import argparse
import json
import os
import sys
import time

from genesis_memory.core import db as _dbx
from genesis_memory.sleep import sleep_consolidation as SC


"""
End-of-session pipeline (v0.1): Sleep Report + persistent user ledger entry.
- Runs the read-only sleep consolidation on the user DB.
- Saves the report under <dbdir>/sleep_reports/.
- Appends ONE JSONL row to <dbdir>/ledger.jsonl with OBSERVED stats only.
  Proxy usage comes from IMMUTABLE per-request receipts (rate snapshot frozen
  at request time): session rows sum only receipts newer than the per-instance
  watermark, so rate changes, restarts, resets, second proxies, and concurrent
  runs can never fabricate deltas. Offline/unreachable keeps usage null.
  Counterfactual dollars (saved/baseline) are NEVER ledgered as observed.
Usage: python -m genesis_memory.sleep.end_session [--db PATH] [--note "text"]
       [--proxy-url http://127.0.0.1:8000]
Stdlib only.
"""

import argparse
import json
import os
import sys
import time

# sleep_consolidation SC already imported above

LOCK_STALE_S = 60.0
RECEIPT_LIMIT = 100


def _num(x, cast):
    try:
        v = cast(x)
        return v
    except (TypeError, ValueError):
        return cast(0)


def _http_get_json(url, timeout_s=1.5):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            d = json.load(r)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def fetch_proxy_state(base_url, timeout_s=1.5):
    """Pull receipts (bounded) + telemetry totals. Returns
    (receipts|None, telemetry|None). receipts None = unsupported/offline."""
    base = base_url.rstrip("/")
    receipts = _http_get_json(f"{base}/v1/receipts?limit={RECEIPT_LIMIT}", timeout_s)
    telemetry = _http_get_json(f"{base}/v1/telemetry", timeout_s)
    if receipts is not None and not isinstance(receipts.get("receipts"), list):
        receipts = None
    return receipts, telemetry


def read_receipt_watermarks(ledger_path):
    """Per-instance last-seen receipt ids. Legacy rows lack them (ignored)."""
    marks = {}
    try:
        with open(ledger_path, encoding="utf-8") as f:
            lines = f.read().strip().split("\n")
    except OSError:
        return marks
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        wm = row.get("receipt_watermarks")
        if isinstance(wm, dict):
            for inst, rid in wm.items():
                if isinstance(rid, str):
                    marks[inst] = rid
    return marks


def acquire_ledger_lock(ddir, timeout_s=10.0):
    """O_EXCL lockfile held across watermark-read→append (exactly-once).
    Stale (>60s) locks are reclaimed. Returns lock path or None."""
    import errno
    lock_path = os.path.join(ddir, "ledger.lock")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "time": time.time()}, f)
            return lock_path
        except OSError as e:
            if e.errno != errno.EEXIST:
                return None
            try:
                with open(lock_path, encoding="utf-8") as f:
                    age = time.time() - float(json.load(f).get("time", 0))
                if age > LOCK_STALE_S:
                    os.unlink(lock_path)
                    continue
            except (OSError, ValueError):
                try:
                    os.unlink(lock_path)
                except OSError:
                    pass
                continue
            time.sleep(0.1)
    return None


def release_ledger_lock(lock_path):
    try:
        os.unlink(lock_path)
    except OSError:
        pass


def aggregate_receipts(receipts, watermarks):
    """Sum only receipts newer than each instance's watermark.

    Returns (sums, new_marks, models, coverage). Coverage is 'full' when every
    instance's watermark is still inside the bounded buffer (nothing could have
    rotated away unseen); otherwise 'partial'. New instances start 'first'.
    """
    by_inst = {}
    for r in receipts:
        if not isinstance(r, dict):
            continue
        inst = r.get("instance_id") or "legacy"
        by_inst.setdefault(inst, []).append(r)
    sums = {"prompt": 0, "cached": 0, "completion": 0, "spent": 0.0, "count": 0}
    new_marks = dict(watermarks)
    models = []
    coverage = "full"
    for inst, rows in by_inst.items():
        rows.sort(key=lambda r: (str(r.get("id", "")), float(r.get("ts") or 0)))
        mark = watermarks.get(inst)
        if mark is None:
            fresh, cov = rows, "first"
        elif mark in [str(r.get("id")) for r in rows]:
            fresh = [r for r in rows if str(r.get("id")) > mark]
            cov = "full"
        else:
            fresh, cov = rows, "partial"  # watermark rotated out of buffer
        if cov != "full" and coverage == "full" and mark is not None:
            coverage = "partial"
        for r in fresh:
            tok = r.get("tokens", {}) or {}
            sums["prompt"] += _num(tok.get("prompt"), int)
            sums["cached"] += _num(tok.get("cached"), int)
            sums["completion"] += _num(tok.get("completion"), int)
            sums["spent"] += _num((r.get("cost_usd", {}) or {}).get("actual"), float)
            sums["count"] += 1
            name = ((r.get("model", {}) or {}).get("priced_as")
                    or (r.get("model", {}) or {}).get("requested"))
            if name and name not in models:
                models.append(name)
        if rows:
            new_marks[inst] = str(rows[-1].get("id"))
    sums["spent"] = round(sums["spent"], 6)
    return sums, new_marks, models, coverage


def default_db():
    return os.environ.get(
        "GENESIS_DAEMON_DB",
        os.path.join(os.path.expanduser("~"), ".genesis", "memory.db"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="end_session")
    ap.add_argument("--db", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--proxy-url", default="",
                    help="Proxy base URL for usage pull "
                         "(default: http://127.0.0.1:$GENESIS_PROXY_PORT)")
    a = ap.parse_args(argv)
    db = a.db or default_db()
    if not os.path.exists(db):
        print(json.dumps({"ok": False, "error": f"db not found: {db}"}))
        return 1
    ddir = os.path.dirname(os.path.abspath(db))
    rdir = os.path.join(ddir, "sleep_reports")
    os.makedirs(rdir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(rdir, f"sleep_{stamp}.md")
    report = SC.build_report(db)
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    
    # Auto-refresh AGENTS.md active digest from SQLite
    try:
        from pathlib import Path
        from genesis_memory.sleep import digest_generator
        repo_root = Path(__file__).resolve().parents[2]
        target_agents_md = repo_root / ".agents" / "AGENTS.md"
        if target_agents_md.exists():
            digest_generator.update_agents_md(target_agents_md, db_path=Path(db))
    except Exception as e:
        print(f"[WARN] Failed to auto-update AGENTS.md digest: {e}", file=sys.stderr)

    import sqlite3
    conn = _dbx.connect(db, readonly=True)
    episodes = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    conn.close()
    ledger_path = os.path.join(ddir, "ledger.jsonl")
    # R4 receipts bridge: lock → re-read watermarks → pull → aggregate new
    # receipts only → append. Offline keeps usage null (never fabricated).
    # Counterfactual dollars (saved/baseline) are never ledgered as observed.
    proxy_url = a.proxy_url or "http://127.0.0.1:%s" % os.environ.get("GENESIS_PROXY_PORT", "8000")
    tokens_in = tokens_out = usd_spent = None
    usd_saved = None
    usd_note = None
    proxy_info = {"connected": False}
    new_marks = {}
    receipt_count = 0
    lock_path = acquire_ledger_lock(ddir)
    try:
        marks = read_receipt_watermarks(ledger_path)
        receipts_doc, telemetry = fetch_proxy_state(proxy_url)
        if receipts_doc is not None:
            sums, new_marks, models, coverage = aggregate_receipts(
                receipts_doc.get("receipts", []), marks)
            tokens_in = sums["prompt"]
            tokens_out = sums["completion"]
            usd_spent = sums["spent"]
            usd_note = ("counterfactual dollars are live-tracked as inferred, "
                        "never ledgered as observed")
            tel = telemetry or {}
            proxy_info = {
                "connected": True,
                "instances": sorted(new_marks.keys()),
                "models": models,
                "coverage": coverage,
                "requests_total": _num(tel.get("requests_total"), int),
            }
            receipt_count = sums["count"]
        row = {"ts": time.time(), "date": time.strftime("%Y-%m-%d %H:%M:%S"),
               "event": "session_end", "episodes": episodes, "report": out,
               "tokens_in": tokens_in, "tokens_out": tokens_out,
               "usd_saved": usd_saved, "usd_saved_note": usd_note,
               "usd_spent": usd_spent, "usd_baseline": None,
               "proxy": proxy_info, "receipt_watermarks": new_marks,
               "receipt_count": receipt_count,
               "note": a.note}
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    finally:
        if lock_path:
            release_ledger_lock(lock_path)
    print(json.dumps({"ok": True, "episodes": episodes, "report": out,
                      "ledger": ledger_path, "proxy": proxy_info}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

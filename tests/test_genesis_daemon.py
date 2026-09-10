"""CI gates for the GENESIS MCP daemon product core (fast, hermetic, no API).
- RSS cap (<100MB, alert path exercised)
- Migration idempotence + legacy upgrade without data loss
- Export/import roundtrip fidelity
- Retrieval bench gate (locked 50-note/20-query set, bar 14/20)
- Sleep report read-only guarantee
Run: python -m pytest tests/test_genesis_daemon.py -q  (target < 120s)
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = os.path.join(REPO, "genesis_memory", "daemon", "server.py")


def rpc_call(proc, method, params=None, seq=[0]):
    seq[0] += 1
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": seq[0], "method": method,
                                 "params": params or {}}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


@pytest.fixture()
def dbpath():
    tmp = tempfile.mkdtemp(prefix="gdaemon_test_")
    return os.path.join(tmp, "m.db")


@pytest.fixture()
def live(dbpath):
    env = dict(os.environ, GENESIS_DAEMON_DB=dbpath, GENESIS_DAEMON_LOG="off")
    p = subprocess.Popen([sys.executable, DAEMON], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, env=env, bufsize=1)
    yield p
    try:
        p.kill()
    except Exception:
        pass


def tool(live, name, args):
    r = rpc_call(live, "tools/call", {"name": name, "arguments": args})
    return json.loads(r["result"]["content"][0]["text"])


def test_mcp_handshake_and_tools(live):
    r = rpc_call(live, "initialize")
    assert r["result"]["serverInfo"]["name"] == "genesis-memory"
    assert r["result"]["serverInfo"]["version"] == "0.5.0"
    names = sorted(t["name"] for t in rpc_call(live, "tools/list")["result"]["tools"])
    assert names == [
        "attest_closure",
        "cross_client_resolve",
        "dialogue_get",
        "dialogue_update",
        "forget",
        "genesis",
        "genesis_log",
        "get_dependencies",
        "invalidate",
        "recall",
        "remember",
        "resolve_conflict",
        "status",
        "thread_get",
        "thread_update",
    ]
    assert rpc_call(live, "ping")["result"] == {}




def test_rss_cap_gate(live):
    st = tool(live, "status", {})
    assert st["rss_mb"] is not None, "RSS self-measure must work (CI gate needs it)"
    assert st["rss_mb"] < 100, st
    assert st["rss_alert"] is False


def test_rss_steady_state_under_load(live):
    for i in range(50):
        tool(live, "remember", {"text": f"load note {i} about billing eviction",
                                "kind": "fact", "project": "load"})
    for i in range(100):
        tool(live, "recall", {"query": "billing eviction load test", "project": "load"})
    st = tool(live, "status", {})
    # Steady-state bound (measured ~24-25MB; arena retention, plateau verified).
    # Binding product budget is <100MB; this tighter gate catches regressions early.
    assert st["rss_mb"] < 30, st


def test_remember_recall_forget_roundtrip(live):
    mid = tool(live, "remember", {"text": "CI probe: eviction prefers empty slots",
                                  "kind": "fact", "project": "ci"})["id"]
    res = tool(live, "recall", {"query": "eviction empty slots", "project": "ci"})
    assert any(r["id"] == mid for r in res["results"])
    assert all(len(r["snippet"].split()) <= 40 for r in res["results"])
    assert tool(live, "forget", {"id": mid})["deleted"] == 1
    assert tool(live, "status", {})["episodes"] == 0


def test_migration_idempotent_and_legacy_safe(dbpath):
    from genesis_memory.daemon.server import Store, SCHEMA_VERSION
    # legacy shape: pre-versioning table, user_version 0, one user row
    db = sqlite3.connect(dbpath)
    db.execute("CREATE TABLE episodes(id INTEGER PRIMARY KEY, ts REAL, project TEXT,"
               " kind TEXT, text TEXT, utility REAL DEFAULT 1.0, accesses INTEGER DEFAULT 0,"
               " updated REAL)")
    db.execute("INSERT INTO episodes VALUES(1, 0.0, 'p', 'fact', 'legacy row', 1.0, 0, 0.0)")
    db.commit()
    db.close()
    s1 = Store(dbpath)  # must upgrade without losing the row
    assert s1.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert s1.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1
    s1.db.close()
    s2 = Store(dbpath)  # second open: idempotent no-op
    assert s2.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1
    s2.db.close()


def test_export_import_roundtrip(dbpath, tmp_path):
    from genesis_memory.daemon.server import export_jsonl, import_jsonl, Store
    s = Store(dbpath)
    for i in range(5):
        s.db.execute("INSERT INTO episodes(ts,project,kind,text,utility,accesses,updated)"
                     " VALUES(?,?,?,?,?,?,?)", (float(i), "p", "fact", f"row {i}", 1.0, i, 0.0))
    s.db.commit()
    s.db.close()
    dump = str(tmp_path / "b.jsonl")
    assert export_jsonl(dbpath, dump) == 5
    db2 = os.path.join(str(tmp_path), "m2.db")
    assert import_jsonl(db2, dump) == 5
    rows = sqlite3.connect(db2).execute(
        "SELECT text, accesses FROM episodes ORDER BY id").fetchall()
    assert rows == [(f"row {i}", i) for i in range(5)]


def test_retrieval_bench_gate():
    try:
        import daemon_retrieval_bench as B
    except ImportError:
        pytest.skip("daemon_retrieval_bench fixture not vendored in standalone package")
    assert len(B.NOTES) == 50 and len(B.QUERIES) == 20
    # in-process Store path (same recall code the MCP path serves)
    from genesis_memory.daemon.server import Store
    tmp = tempfile.mkdtemp(prefix="gbench_")
    s = Store(os.path.join(tmp, "m.db"))
    kinds = ["fact", "decision", "outcome"]
    now = time.time()
    for i, n in enumerate(B.NOTES):
        s.db.execute("INSERT INTO episodes(ts,project,kind,text,utility,accesses,updated)"
                     " VALUES(?,?,?,?,?,?,?)", (now, "bench", kinds[i % 3], n, 1.0, 0, now))
    s.db.commit()
    hits = 0
    for q, gold in B.QUERIES:
        got = [r["id"] for r in s.recall(q, project="bench", limit=3)["results"]]
        hits += gold in got
    assert hits >= B.BAR, f"retrieval gate: {hits}/20 < bar {B.BAR}"
    s.db.close()


def test_sleep_report_readonly(dbpath, tmp_path):
    from genesis_memory.sleep import sleep_consolidation as SC
    from genesis_memory.daemon.server import Store
    s = Store(dbpath)
    s.db.execute("INSERT INTO episodes(ts,project,kind,text,utility,accesses,updated)"
                 " VALUES(?,?,?,?,?,?,?)", (0.0, "p", "fact", "old unread note", 1.0, 0, 0.0))
    s.db.commit()
    before = open(dbpath, "rb").read()
    out = str(tmp_path / "sleep.md")
    SC.main(["--db", dbpath, "--out", out, "--stale-days", "-1"])
    assert open(dbpath, "rb").read() == before, "sleep run modified the DB"
    txt = open(out, encoding="utf-8").read()
    assert "Proposed forgets" in txt and "veto" in txt.lower()


def test_end_session_pipeline(tmp_path, monkeypatch):
    from genesis_memory.sleep import end_session as ES
    db = os.path.join(str(tmp_path), "memory.db")
    rc = ES.main(["--db", db, "--note", "ci run"])
    assert rc == 1  # missing DB fails honestly, does not create junk
    import sqlite3
    sqlite3.connect(db).execute(
        "CREATE TABLE episodes(id INTEGER PRIMARY KEY, ts REAL, project TEXT,"
        " kind TEXT, text TEXT, utility REAL DEFAULT 1.0, accesses INTEGER DEFAULT 0,"
        " updated REAL)").close()
    # Dead proxy URL: usage fields stay null (never fabricated, offline discipline)
    rc = ES.main(["--db", db, "--note", "ci run",
                  "--proxy-url", "http://127.0.0.1:9"])
    assert rc == 0
    ddir = os.path.dirname(os.path.abspath(db))
    rows = open(os.path.join(ddir, "ledger.jsonl"), encoding="utf-8").read().strip().split("\n")
    last = json.loads(rows[-1])
    assert last["event"] == "session_end" and last["episodes"] == 0
    assert last["tokens_in"] is None and last["usd_saved"] is None  # never fabricated
    assert last["proxy"]["connected"] is False
    assert os.path.exists(last["report"])


def _fake_proxy_server(state):
    """Mutable fake proxy: /v1/receipts + /v1/telemetry. stdlib only."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            parts = urlparse(self.path)
            if parts.path == "/v1/receipts":
                items = state.get("receipts", [])
                body = json.dumps({"count": len(items), "receipts": items}).encode()
            elif parts.path == "/v1/telemetry":
                body = json.dumps({"requests_total": state.get("requests_total", 0)}).encode()
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _receipt(inst, rid, prompt, cached, completion, spent, model="ci-model"):
    return {
        "id": rid, "ts": 1700000000.0, "instance_id": inst,
        "session_id": "s", "client": "ci", "streaming": False,
        "model": {"requested": model, "priced_as": model, "name": model,
                  "static_fallback": True},
        "stop_reason": "stop", "tool_calls": 0, "upstream_id": None,
        "tokens": {"prompt": prompt, "cached": cached,
                   "uncached": prompt - cached, "completion": completion},
        "cost_usd": {"actual": spent, "basis": "catalog x provider usage",
                     "rates_per_m": {"in": 1.0, "cached": 0.5, "out": 2.0}},
        "verified_savings_usd": 0.0,
        "resume": {"after_request": rid, "session_id": "s"},
    }


def _mkdb(path):
    import sqlite3
    sqlite3.connect(path).execute(
        "CREATE TABLE episodes(id INTEGER PRIMARY KEY, ts REAL, project TEXT,"
        " kind TEXT, text TEXT, utility REAL DEFAULT 1.0, accesses INTEGER DEFAULT 0,"
        " updated REAL)").close()


def _es_last(db):
    from genesis_memory.sleep import end_session as ES
    ddir = os.path.dirname(os.path.abspath(db))
    rows = open(os.path.join(ddir, "ledger.jsonl"), encoding="utf-8").read().strip().split("\n")
    return json.loads(rows[-1])


def test_end_session_receipts_exactly_once(tmp_path):
    """R4: immutable receipts summed once; second run over same state records
    zeros (watermark advanced) — concurrent/duplicate runs can't double-count."""
    from genesis_memory.sleep import end_session as ES
    db = os.path.join(str(tmp_path), "memory.db")
    _mkdb(db)
    state = {"receipts": [
        _receipt("inst-A", "rcpt_01", 1000, 200, 100, 0.5),
        _receipt("inst-A", "rcpt_02", 500, 0, 50, 0.3),
    ], "requests_total": 2}
    srv = _fake_proxy_server(state)
    url = f"http://127.0.0.1:{srv.server_port}"
    assert ES.main(["--db", db, "--proxy-url", url]) == 0
    r1 = _es_last(db)
    assert (r1["tokens_in"], r1["tokens_out"], r1["usd_spent"]) == (1500, 150, 0.8)
    assert r1["usd_saved"] is None  # counterfactuals never ledgered
    assert r1["proxy"]["connected"] is True
    assert r1["receipt_watermarks"] == {"inst-A": "rcpt_02"}
    # Duplicate run, identical proxy state → zeros, same watermark
    assert ES.main(["--db", db, "--proxy-url", url]) == 0
    r2 = _es_last(db)
    assert (r2["tokens_in"], r2["tokens_out"], r2["usd_spent"]) == (0, 0, 0.0)
    assert r2["receipt_watermarks"] == {"inst-A": "rcpt_02"}
    srv.shutdown()


def test_end_session_rate_change_without_requests_is_zero(tmp_path):
    """R4: repricing with no new receipts produces zero deltas — phantom
    dollars are impossible because dollars ride inside immutable receipts."""
    from genesis_memory.sleep import end_session as ES
    db = os.path.join(str(tmp_path), "memory.db")
    _mkdb(db)
    state = {"receipts": [_receipt("inst-A", "rcpt_01", 1000, 0, 100, 2.0)],
             "requests_total": 1}
    srv = _fake_proxy_server(state)
    url = f"http://127.0.0.1:{srv.server_port}"
    assert ES.main(["--db", db, "--proxy-url", url]) == 0
    # Catalog reprices the SAME receipt in place; no new requests happen.
    state["receipts"][0]["cost_usd"]["actual"] = 4.0
    assert ES.main(["--db", db, "--proxy-url", url]) == 0
    r = _es_last(db)
    assert (r["tokens_in"], r["tokens_out"], r["usd_spent"]) == (0, 0, 0.0)
    srv.shutdown()


def test_end_session_two_proxies_and_restart(tmp_path):
    """R4: second proxy instance gets its own chain (no cross-subtraction);
    a restarted instance (lower counters, new id) counts fully, never negative."""
    from genesis_memory.sleep import end_session as ES
    db = os.path.join(str(tmp_path), "memory.db")
    _mkdb(db)
    state = {"receipts": [_receipt("inst-A", "rcpt_01", 1000, 0, 100, 1.0)],
             "requests_total": 1}
    srv = _fake_proxy_server(state)
    url = f"http://127.0.0.1:{srv.server_port}"
    assert ES.main(["--db", db, "--proxy-url", url]) == 0
    # Second proxy appears with its own (smaller) counters → fresh chain
    state["receipts"].append(_receipt("inst-B", "rcpt_01", 400, 0, 40, 0.2))
    state["requests_total"] = 2
    assert ES.main(["--db", db, "--proxy-url", url]) == 0
    r = _es_last(db)
    assert (r["tokens_in"], r["tokens_out"], r["usd_spent"]) == (400, 40, 0.2)
    assert r["receipt_watermarks"] == {"inst-A": "rcpt_01", "inst-B": "rcpt_01"}
    assert all(v >= 0 for v in (r["tokens_in"], r["tokens_out"], r["usd_spent"]))
    srv.shutdown()


def test_persistent_counters_and_hit_rate(dbpath):
    from genesis_memory.daemon.server import Store
    s1 = Store(dbpath)
    s1.remember("note about billing caching", kind="fact", project="ci")
    s1.remember("note about database indexing", kind="fact", project="ci")
    res1 = s1.recall("billing caching", project="ci")
    assert len(res1["results"]) > 0
    st1 = s1.status()
    assert st1["calls"]["remember"] >= 2
    assert st1["calls"]["recall"] >= 1
    assert st1["calls"]["hits"] >= 1
    assert st1["hit_rate"] > 0.0
    s1.db.close()

    # Reopen in fresh Store instance to assert persistence
    s2 = Store(dbpath)
    st2 = s2.status()
    assert st2["calls"]["remember"] >= 2
    assert st2["calls"]["recall"] >= 1
    assert st2["calls"]["hits"] >= 1
    assert st2["hit_rate"] > 0.0
    s2.db.close()


def _call(store, name, args, mid=1):
    from genesis_memory.daemon.server import handle
    r = handle(store, {"id": mid, "method": "tools/call",
                       "params": {"name": name, "arguments": args}})
    assert "error" not in r, r
    return json.loads(r["result"]["content"][0]["text"])


def _stor(tmp_path):
    from genesis_memory.daemon.server import Store
    return Store(str(tmp_path / "gw.db"))


def test_gateway_lists_single_tool_in_gateway_mode(tmp_path, monkeypatch):
    from genesis_memory.daemon.server import handle, visible_tools
    s = _stor(tmp_path)
    monkeypatch.setenv("GENESIS_MCP_TOOL_MODE", "gateway")
    assert [t["name"] for t in visible_tools()] == ["genesis"]
    r = handle(s, {"id": 1, "method": "tools/list", "params": {}})
    assert [t["name"] for t in r["result"]["tools"]] == ["genesis"]
    s.db.close()
    # List-size win, measured in chars (estimate, chars//4 convention).
    import json as _json
    full = _json.dumps(__import__("genesis_memory.daemon.server",
                                  fromlist=["TOOLS"]).TOOLS)
    from genesis_memory.daemon.server import GATEWAY_TOOL
    solo = _json.dumps([GATEWAY_TOOL])
    assert len(solo) < 0.15 * len(full)


def test_gateway_help_and_unknown_op(tmp_path):
    from genesis_memory.daemon.server import GATEWAY_OPS
    s = _stor(tmp_path)
    out = _call(s, "genesis", {"op": "help"})
    assert set(out["ops"]) == set(GATEWAY_OPS)
    from genesis_memory.daemon.server import handle
    r = handle(s, {"id": 2, "method": "tools/call",
                   "params": {"name": "genesis", "arguments": {"op": "nope"}}})
    assert r["error"]["code"] == -32602
    assert "ops" in r["error"]["message"]
    s.db.close()


def test_gateway_roundtrips_equal_direct(tmp_path):
    s = _stor(tmp_path)
    assert _call(s, "genesis", {"op": "remember", "text": "gateway note alpha",
                                "kind": "fact"})["id"] >= 1
    assert _call(s, "genesis", {"op": "thread_update", "topic": "T",
                                "summary": "S"})["status"] == "ok"
    assert _call(s, "genesis", {"op": "dialogue_update", "session_id": "s1",
                                "client": "ci",
                                "user_prompt": "hi?", "assistant_summary": "yo"})["status"] in (
        "ok", "updated")
    assert "gateway note alpha" in json.dumps(
        _call(s, "genesis", {"op": "recall", "query": "gateway note"}))
    assert _call(s, "genesis", {"op": "thread_get"})["topic"] == "T"
    assert _call(s, "genesis", {"op": "status"})["calls"]["remember"] >= 1
    assert _call(s, "genesis", {"op": "dialogue_get"})["user_prompt"] == "hi?"
    assert _call(s, "genesis", {"op": "forget", "id": 1})["deleted"] >= 0
    assert "genesis" in _call(s, "genesis", {"op": "status"})["calls"]
    s.db.close()


def test_gateway_help_lists_resolve(tmp_path):
    s = _stor(tmp_path)
    out = _call(s, "genesis", {"op": "help"})
    assert "cross_client_resolve" in out["ops"]
    s.db.close()


def test_cross_client_resolve_ladder_and_provenance(tmp_path):
    s = _stor(tmp_path)
    s.set_thread(topic="Apple tree pots", summary="Dwarf M9 pots",
                 client="ci")
    s.set_dialogue(session_id="s1", client="ci",
                   user_prompt="How to prune apple?",
                   assistant_summary="Prune in winter", salient_terms=[])
    s.remember("Apple harvest comes in autumn", kind="fact", project="ci")
    out = _call(s, "genesis", {"op": "cross_client_resolve",
                               "question": "When to prune apple?"})
    assert out["resolved"] is True
    assert out["rung"] == 1  # thread present => first rung resolves first
    labels = " ".join(out["answer"].splitlines())
    assert "thread" in labels and "dialogue:" in labels
    s.db.close()


def test_cross_client_resolve_empty_is_honest(tmp_path):
    s = _stor(tmp_path)
    out = _call(s, "cross_client_resolve",
                {"question": "zxqwv kqjvb moot?"})
    assert out["resolved"] is False
    assert out["answer"] == ""
    s.db.close()


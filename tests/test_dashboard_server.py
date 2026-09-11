"""Mission Control dashboard server: every API route, SSE stream, and POST action."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from genesis_memory.daemon.server import Store
from genesis_memory.dashboard import server as ds


@pytest.fixture()
def seeded_db(tmp_path):
    db = str(tmp_path / "m.db")
    s = Store(db)
    for i in range(4):
        s.remember(f"decision {i}: use WAL mode with busy timeout for sqlite concurrency", kind="decision", project="genesis")
    s.remember("fact: the proxy listens on port 8000 and compacts tool outputs", kind="fact", project="proxy")
    s.synthesize_skill("run tests", "genesis run -- pytest tests/", trigger_patterns=["test", "pytest"])
    s.set_thread("Dashboard rewrite", "Building mission control", recent_files=["server.py"], client="copilot")
    s.set_dialogue("sess-1", "cursor", "how do I run tests?", "use genesis run -- pytest")
    s.db.close()
    return db


@pytest.fixture()
def live(seeded_db):
    srv = ds.make_server(0, seeded_db)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base
    srv.shutdown()
    srv.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, json.loads(r.read())


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_health_and_html(live):
    code, data = _get(live, "/health")
    assert code == 200 and data["status"] == "ok" and data["version"]
    with urllib.request.urlopen(live + "/", timeout=5) as r:
        html = r.read().decode("utf-8")
        assert r.status == 200 and "<html" in html.lower() and "genesis" in html.lower()
        assert "text/html" in r.headers["Content-Type"]


def test_overview_is_single_round_trip(live):
    code, ov = _get(live, "/api/overview")
    assert code == 200
    for key in ("totals", "counters", "kinds", "statuses", "timeline", "thread", "dialogue", "skills",
                "conflicts", "spool", "proxy", "license", "clients", "diet", "rss_mb", "version"):
        assert key in ov, key
    assert ov["totals"]["episodes_total"] == 5
    assert ov["totals"]["journal_mode"] == "wal"
    assert ov["totals"]["conflicts_pending"] >= 1  # near-duplicate decisions were staged
    assert len(ov["timeline"]) == 48
    assert ov["thread"]["present"] and ov["thread"]["topic"] == "Dashboard rewrite"
    assert ov["skills"][0]["name"] == "run tests"
    assert isinstance(ov["clients"], list) and len(ov["clients"]) >= 20
    assert ov["proxy"]["connected"] in (True, False)
    assert set(ov["diet"]) >= {"tokens_saved_total_est", "dollars_saved", "secrets_blocked"}


def test_engram_search_and_detail(live):
    code, res = _get(live, "/api/engrams?q=sqlite%20concurrency&limit=10")
    assert code == 200 and res["count"] >= 1 and res["total"] == 5
    assert all("tokens_est" in it for it in res["items"])
    assert res["items"][0]["score"] is not None
    code, res = _get(live, "/api/engrams?kind=fact")
    assert code == 200 and res["count"] == 1 and res["items"][0]["project"] == "proxy"
    code, one = _get(live, f"/api/engram/{res['items'][0]['id']}")
    assert code == 200 and one["kind"] == "fact" and "conflicts" in one
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(live, "/api/engram/999999")
    assert exc.value.code == 404


def test_memory_graph_shape(live):
    code, g = _get(live, "/api/graph")
    assert code == 200
    assert g["counts"]["nodes"] == 5
    assert any(l["relation"] in ("conflict", "lexical") for l in g["links"])
    ids = {n["id"] for n in g["nodes"]}
    for l in g["links"]:
        assert l["source"] in ids and l["target"] in ids


def test_secondary_collections(live):
    assert _get(live, "/api/skills")[1]["items"][0]["trigger_patterns"] == ["test", "pytest"]
    conflicts = _get(live, "/api/conflicts?status=pending")[1]["items"]
    assert conflicts and {"new_text", "old_text"} <= set(conflicts[0])
    assert _get(live, "/api/thread")[1]["present"] is True
    assert _get(live, "/api/dialogue")[1]["items"][0]["client"] == "cursor"
    assert _get(live, "/api/clients")[1]["items"]
    assert "spools_created" in _get(live, "/api/spool")[1]
    assert _get(live, "/api/edges")[1]["nodes_count"] == 0
    assert _get(live, "/api/ledger")[1]["count"] == 0
    assert "connected" in _get(live, "/api/proxy_telemetry")[1]
    pricing = _get(live, "/api/pricing?q=gpt-4o")[1]
    assert pricing["total_models"] >= 400 and pricing["models"]
    snap = _get(live, "/api/snapshot")[1]
    assert "episodes" in snap and "license" in snap
    assert _get(live, "/api/license")[1]["tier"]


def test_shield_preview_never_stores(live, seeded_db):
    secret = "sk-abcdefghijklmnopqrst1234"
    code, res = _post(live, "/api/shield", {"text": f"key {secret} ok"})
    assert code == 200 and secret not in res["clean"] and res["stored"] is False
    assert res["report"]["redactions"] == 1 and res["findings"][0]["detector"] == "openai"
    assert res["entropies"] and {"entropy_bits", "flagged"} <= set(res["entropies"][0])
    s = Store(seeded_db)
    assert s.db.execute("SELECT COUNT(*) FROM episodes WHERE text LIKE '%sk-%'").fetchone()[0] == 0
    s.db.close()


def test_resolve_conflict_and_reinforce_and_sleep(live):
    conflicts = _get(live, "/api/conflicts?status=pending")[1]["items"]
    cid = conflicts[0]["id"]
    code, res = _post(live, f"/api/conflicts/{cid}/resolve", {"action": "dismissed"})
    assert code == 200 and res["status"] == "resolved"
    assert all(c["id"] != cid for c in _get(live, "/api/conflicts?status=pending")[1]["items"])
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(live, f"/api/conflicts/{cid}/resolve", {"action": "nuke"})
    assert exc.value.code == 400
    eid = _get(live, "/api/engrams?kind=fact")[1]["items"][0]["id"]
    code, res = _post(live, "/api/reinforce", {"id": eid, "outcome": "success"})
    assert code == 200
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(live, "/api/reinforce", {"outcome": "success"})
    assert exc.value.code == 400
    code, res = _post(live, "/api/sleep/now", {"deep": False})
    assert code == 200 and isinstance(res, dict)


def test_sse_stream_emits_overview_events(live, monkeypatch):
    monkeypatch.setattr(ds, "STREAM_INTERVAL_S", 0.05)
    req = urllib.request.Request(live + "/api/stream")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.headers["Content-Type"].startswith("text/event-stream")
        first = r.readline().decode()
        assert first.strip() == "event: overview"
        data = r.readline().decode()
        assert data.startswith("data: ")
        payload = json.loads(data[len("data: "):])
        assert payload["totals"]["episodes_total"] == 5


def test_unknown_routes_404(live):
    for path in ("/api/nothing", "/static/../server.py", "/static/missing.css"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(live + path, timeout=5)
        assert exc.value.code == 404, path


def test_repo_tolerates_missing_db(tmp_path):
    repo = ds.DashboardRepo(str(tmp_path / "nope.db"))
    assert repo.engrams()["items"] == []
    assert repo.totals()["episodes_total"] == 0
    assert repo.thread() == {"present": False}
    ov = ds.build_overview(str(tmp_path / "nope.db"))
    assert ov["db_present"] is False and ov["totals"]["episodes_total"] == 0


def test_cli_dashboard_subcommand_parses(monkeypatch):
    from genesis_memory.cli.run import main as cli_main
    called = {}
    monkeypatch.setattr(ds, "run_server", lambda **kw: called.update(kw))
    assert cli_main(["dashboard", "--port", "0", "--no-sleep"]) == 0
    assert called["port"] == 0 and called["auto_sleep"] is False


def test_contract_files_ship_inside_package():
    """llms.txt/openapi.json must live inside the installed product (no repo needed)."""
    from importlib import resources as _resources
    from pathlib import Path
    for name in ("llms.txt", "openapi.json"):
        data = (_resources.files("genesis_memory") / "data" / name).read_bytes()
        assert len(data) > 500, name
        # Single source of truth: packaged copy byte-identical to repo root.
        assert data == (Path(ds.__file__).resolve().parents[2] / name).read_bytes()


def test_contract_routes_served_live(live):
    """Deck serves the packaged contract: /llms.txt 200 text, /openapi.json 200 valid JSON."""
    with urllib.request.urlopen(live + "/llms.txt", timeout=5) as r:
        assert r.status == 200
        assert "text/plain" in r.headers["Content-Type"]
        assert len(r.read()) > 500
    code, res = _get(live, "/openapi.json")
    assert code == 200 and isinstance(res, dict)

"""
GENESIS Memory Deck Live Telemetry Server.
Standard library only (HTTP/1.1 with CORS).
Feeds real-time SQLite episodic memory telemetry to dashboard.html.
"""

import http.server
import json
import os
import sys
import time
from pathlib import Path

# Package root (standalone repo layout)
REPO_ROOT = Path(__file__).resolve().parents[2]

import threading
from genesis_memory.daemon.server import snapshot, DB_PATH
from genesis_memory.sleep import sleep_consolidation

HTML_PATH = Path(__file__).resolve().parent / "static" / "index.html"
if not HTML_PATH.exists():
    HTML_PATH = Path(__file__).resolve().parent / "dashboard.html"
LLMS_PATH = REPO_ROOT / "llms.txt"
OPENAPI_PATH = REPO_ROOT / "openapi.json"
DEFAULT_DB = os.environ.get("GENESIS_DAEMON_DB", os.path.expanduser("~/.genesis/memory.db"))
SERVER_START_TIME = time.time()


def trigger_auto_sleep(db_path=DEFAULT_DB):
    """Generate read-only sleep consolidation report automatically."""
    try:
        db_to_use = db_path if os.path.exists(db_path) else DB_PATH
        db_dir = os.path.dirname(os.path.abspath(db_to_use))
        reports_dir = os.path.join(db_dir, "sleep_reports")
        os.makedirs(reports_dir, exist_ok=True)
        report = sleep_consolidation.build_report(db_to_use)
        latest_file = os.path.join(reports_dir, "auto_sleep_latest.md")
        with open(latest_file, "w", encoding="utf-8") as f:
            f.write(report)
        return {"ok": True, "file": latest_file, "report": report}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def background_auto_sleep_loop(interval_sec=900):
    """Background auto-sleep worker executing every 15 minutes."""
    while True:
        time.sleep(interval_sec)
        trigger_auto_sleep()


class TelemetryHandler(http.server.BaseHTTPRequestHandler):
    db_path = DEFAULT_DB

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok", "uptime_s": round(time.time() - SERVER_START_TIME, 1)}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/api/snapshot"):
            try:
                db_to_use = self.db_path if os.path.exists(self.db_path) else DB_PATH
                data = snapshot(db_to_use)
                if data.get("uptime_s", 0) <= 0.5:
                    data["uptime_s"] = round(time.time() - SERVER_START_TIME, 1)
                body = json.dumps(data, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                err_body = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(err_body)
            return

        if self.path.startswith("/api/ledger"):
            try:
                db_dir = os.path.dirname(os.path.abspath(self.db_path))
                ledger_file = os.path.join(db_dir, "ledger.jsonl")
                sessions = []
                if os.path.exists(ledger_file):
                    with open(ledger_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    sessions.append(json.loads(line))
                                except Exception:
                                    pass
                body = json.dumps({"sessions": sessions, "count": len(sessions)}, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        if self.path.startswith("/api/sleep"):
            try:
                res = trigger_auto_sleep(self.db_path)
                body = json.dumps(res, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        if self.path.startswith("/api/edges"):
            try:
                import sqlite3
                db_to_use = self.db_path if os.path.exists(self.db_path) else DB_PATH
                conn = sqlite3.connect(db_to_use)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                rows = cur.execute(
                    "SELECT id, source, target, relation, file_hash, status FROM edges WHERE status = 'active' OR status IS NULL"
                ).fetchall()
                conn.close()

                stdlib_set = {
                    "os", "sys", "time", "json", "re", "math", "sqlite3", "collections",
                    "typing", "pathlib", "threading", "datetime", "hashlib", "io",
                    "subprocess", "copy", "itertools", "functools", "abc", "argparse",
                    "shutil", "urllib", "ctypes", "traceback", "logging", "asyncio",
                    "dataclasses", "inspect", "random", "enum", "uuid", "tempfile"
                }

                nodes_map = {}
                edges_list = []

                def get_short_label(nid: str) -> str:
                    if "/" in nid or "\\" in nid:
                        return nid.replace("\\", "/").split("/")[-1]
                    return nid

                def get_subsystem(nid: str, cat: str) -> str:
                    p = nid.replace("\\", "/")
                    if cat == "stdlib":
                        return "stdlib"
                    if cat == "external":
                        return "external"
                    if p.startswith("src/genesis/server") or "daemon" in p or "hook" in p:
                        return "core"
                    if p.startswith("src/genesis/cli") or "cli" in p:
                        return "cli"
                    if p.startswith("tests/"):
                        return "tests"
                    if p.startswith("src/legacy_probes"):
                        return "probes"
                    if p.startswith("src/biophysical"):
                        return "biophysical"
                    return "internal_other"

                for r in rows:
                    src = r["source"]
                    tgt = r["target"]
                    rel = r["relation"] or "depends_on"

                    for node_id in (src, tgt):
                        if node_id not in nodes_map:
                            if node_id.startswith("src/") or node_id.startswith("genesis") or "/" in node_id or "\\" in node_id:
                                category = "internal"
                            elif node_id in stdlib_set:
                                category = "stdlib"
                            else:
                                category = "external"
                            subsystem = get_subsystem(node_id, category)
                            nodes_map[node_id] = {
                                "id": node_id,
                                "label": get_short_label(node_id),
                                "short_label": get_short_label(node_id),
                                "full_path": node_id,
                                "category": category,
                                "subsystem": subsystem,
                                "in_degree": 0,
                                "out_degree": 0,
                                "in_edges": [],
                                "out_edges": [],
                            }

                    nodes_map[src]["out_degree"] += 1
                    nodes_map[src]["out_edges"].append(tgt)
                    nodes_map[tgt]["in_degree"] += 1
                    nodes_map[tgt]["in_edges"].append(src)

                    edges_list.append({
                        "id": r["id"],
                        "source": src,
                        "target": tgt,
                        "relation": rel
                    })

                nodes_list = list(nodes_map.values())
                for n in nodes_list:
                    n["degree"] = n["in_degree"] + n["out_degree"]

                body = json.dumps({
                    "count": len(edges_list),
                    "nodes_count": len(nodes_list),
                    "nodes": nodes_list,
                    "edges": edges_list
                }, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
        if self.path.startswith("/api/proxy_telemetry"):
            try:
                import urllib.request
                proxy_port = int(os.environ.get("GENESIS_PROXY_PORT", "8000"))
                proxy_url = f"http://127.0.0.1:{proxy_port}/v1/telemetry"
                req = urllib.request.Request(proxy_url, headers={"User-Agent": "GenesisDashboard/1.0"})
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    proxy_data = json.loads(resp.read().decode("utf-8"))
                    proxy_data["connected"] = True
            except Exception as exc:
                proxy_data = {
                    "connected": False,
                    "mode": "offline",
                    "error": str(exc),
                    "requests_total": 0,
                    "tokens": {"prompt_stripped_est": 0, "total": 0},
                    "bypass": {"bypass_rate_pct": 0.0, "bypasses_total": 0, "reasons": {}},
                    "performance": {"p50_ttft_ms": 0.0, "p95_ttft_ms": 0.0, "mean_ttft_ms": 0.0},
                    "upstream": {"status_codes": {}},
                }
            body = json.dumps(proxy_data, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/api/pricing"):
            try:
                import urllib.request
                proxy_port = int(os.environ.get("GENESIS_PROXY_PORT", "8000"))
                query = self.path[len("/api/pricing"):]
                proxy_url = f"http://127.0.0.1:{proxy_port}/v1/pricing{query}"
                req = urllib.request.Request(proxy_url, headers={"User-Agent": "GenesisDashboard/1.0"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                data = {"error": str(exc), "total_models": 0, "models": []}
            body = json.dumps(data, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Agent-readable index + machine spec (static repo files)
        if self.path == "/llms.txt" or self.path == "/openapi.json":
            fpath = LLMS_PATH if self.path == "/llms.txt" else OPENAPI_PATH
            ctype = ("text/plain; charset=utf-8" if self.path == "/llms.txt"
                     else "application/json; charset=utf-8")
            try:
                with open(fpath, "rb") as f:
                    content = f.read()
                if self.path == "/openapi.json":
                    json.loads(content.decode("utf-8"))  # fail 500, never serve invalid spec
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # Serve HTML dashboard
        if self.path == "/" or self.path.startswith("/dashboard"):
            try:
                with open(HTML_PATH, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(f"Dashboard file not found: {e}".encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        # Silence routine request logging to avoid terminal spam
        pass


def run_server(port=8090, db_path=None):
    if db_path:
        TelemetryHandler.db_path = db_path
    # Launch automatic sleep consolidation background worker
    auto_sleep_thread = threading.Thread(target=background_auto_sleep_loop, args=(900,), daemon=True)
    auto_sleep_thread.start()
    
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), TelemetryHandler)
    print(f"🚀 [GENESIS MEMORY DECK] Live Telemetry Server active at http://127.0.0.1:{port}/")
    print(f"📊 Live DB: {TelemetryHandler.db_path}")
    print("🌙 [AUTO-SLEEP] Background consolidation worker active (15m interval)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping telemetry server.")
        server.server_close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GENESIS Memory Deck Live Telemetry Server")
    parser.add_argument("--port", type=int, default=8090, help="Port to bind (default: 8090)")
    parser.add_argument("--db", type=str, default=DEFAULT_DB, help="Path to SQLite memory.db")
    args = parser.parse_args()
    run_server(port=args.port, db_path=args.db)

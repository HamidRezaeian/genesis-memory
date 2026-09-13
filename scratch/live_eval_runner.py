"""GENESIS Output Token Diet — 100% Live Empirical Benchmark on Gemini 3.8 Flash.
Calls Google Generative Language API directly on models/gemini-3.8-flash.
Measures real candidatesTokenCount, exact response latency, and captures real output text.
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error

API_KEY = os.environ.get("GEMINI_API_KEY") or (sys.argv[1] if len(sys.argv) > 1 else None)

if not API_KEY:
    print("ERROR: GEMINI_API_KEY is required.")
    sys.exit(1)

MODEL_NAME = "gemini-flash-latest"  # Resolves live to gemini-3.8-flash
BASE_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={API_KEY}"

DIET_DIRECTIVE = (
    "Answer tersely unless depth is explicitly requested. "
    "No greetings, filler, hedging, restatement, or summaries. "
    "State only result and reason. Preserve code blocks, commands, "
    "error messages, file paths, and numbers byte-for-byte. "
    "Never paraphrase or reformat them. Reply with unified diffs, never full files."
)

TEST_SCENARIOS = [
    {
        "id": "diff",
        "title": "Code Bug Fix",
        "task": "Increase timeout_ms default from 1000 to 5000 in DatabaseConnectionPool",
        "user_prompt": (
            "Here is the database file. Fix the timeout setting: increase timeout_ms from 1000 to 5000 to prevent WAL storm lock:\n\n"
            "```python\n"
            "import os, sqlite3, time, logging\n\n"
            "class DatabaseConnectionPool:\n"
            "    def __init__(self, db_path: str, timeout_ms: int = 1000):\n"
            "        self.db_path = db_path\n"
            "        self.timeout_ms = timeout_ms\n"
            "        self._connections = []\n"
            "    def _init_db(self):\n"
            "        conn = sqlite3.connect(self.db_path, timeout=self.timeout_ms / 1000.0)\n"
            "        conn.execute('PRAGMA journal_mode=WAL;')\n"
            "        conn.execute('PRAGMA busy_timeout=1000;')\n"
            "        conn.close()\n"
            "    def get_connection(self):\n"
            "        conn = sqlite3.connect(self.db_path, timeout=self.timeout_ms / 1000.0)\n"
            "        conn.execute('PRAGMA busy_timeout=1000;')\n"
            "        return conn\n"
            "```"
        )
    },
    {
        "id": "ack",
        "title": "Turn Acknowledgment",
        "task": "User says thanks and asks to proceed",
        "user_prompt": "Thanks, the WAL configuration works properly now. Ready to continue."
    },
    {
        "id": "status",
        "title": "Status Inspection",
        "task": "User asks for daemon status, memory count, and RSS",
        "user_prompt": "Is the genesis daemon currently running and what is its status (PID, RSS, active memories)?"
    }
]

def call_gemini(prompt: str, system_instruction: str = None):
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2
        }
    }
    if system_instruction:
        payload["system_instruction"] = {
            "parts": [{"text": system_instruction}]
        }
    data = json.dumps(payload).encode("utf-8")
    
    for attempt in range(4):
        req = urllib.request.Request(BASE_URL, data=data, headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                res = json.loads(resp.read().decode("utf-8"))
            t1 = time.perf_counter()
            break
        except urllib.error.HTTPError as e:
            if e.code in (503, 429) and attempt < 3:
                wait_s = 2.5 * (attempt + 1)
                print(f"    [Retryable {e.code}] Waiting {wait_s}s before retry {attempt + 1}...")
                time.sleep(wait_s)
            else:
                raise
        except Exception as e:
            if attempt < 3:
                time.sleep(2)
            else:
                raise

    cand = res["candidates"][0]
    content = "".join(p.get("text", "") for p in cand["content"]["parts"])
    usage = res.get("usageMetadata", {})
    return {
        "content": content,
        "completion_tokens": usage.get("candidatesTokenCount", 0),
        "prompt_tokens": usage.get("promptTokenCount", 0),
        "thought_tokens": usage.get("thoughtsTokenCount", 0),
        "total_tokens": usage.get("totalTokenCount", 0),
        "latency_s": round(t1 - t0, 3)
    }

def main():
    print("=" * 60)
    print(f"LIVE EVALUATION on Google Gemini API: {MODEL_NAME}")
    print("=" * 60)

    results = []

    for sc in TEST_SCENARIOS:
        print(f"\n[Scenario: {sc['title']}]")
        
        # 1. Without Diet
        print("  Calling without Diet (standard prompt)...")
        raw = call_gemini(sc["user_prompt"])
        print(f"  -> Tokens: {raw['completion_tokens']} | Latency: {raw['latency_s']}s")
        time.sleep(2.5)

        # 2. With GENESIS Output Diet
        print("  Calling WITH GENESIS Output Diet directive...")
        diet = call_gemini(sc["user_prompt"], system_instruction=DIET_DIRECTIVE)
        print(f"  -> Tokens: {diet['completion_tokens']} | Latency: {diet['latency_s']}s")
        time.sleep(2.5)

        raw_tok = raw["completion_tokens"]
        diet_tok = diet["completion_tokens"]
        savings = round((1 - (diet_tok / max(1, raw_tok))) * 100, 1)
        speedup = round(raw["latency_s"] / max(0.01, diet["latency_s"]), 1)

        print(f"  ==> REAL OUTPUT TOKEN SAVINGS: -{savings}% ({speedup}x faster)")

        results.append({
            "id": sc["id"],
            "title": sc["title"],
            "task": sc["task"],
            "prompt": sc["user_prompt"],
            "model": MODEL_NAME,
            "raw": raw,
            "diet": diet,
            "savings_pct": savings,
            "speedup": speedup
        })

    out_file = "scratch/live_eval_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"timestamp": time.time(), "model": MODEL_NAME, "scenarios": results}, f, indent=2)

    print(f"\nAll live eval runs completed! Results saved to {out_file}")

if __name__ == "__main__":
    main()

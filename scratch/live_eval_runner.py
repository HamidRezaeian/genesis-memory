"""GENESIS Output Token Diet — illustrative live comparison on Gemini 3.8 Flash.
Calls Google Generative Language API directly on models/gemini-3.8-flash.

Honesty contract (see README benchmark section):
- Identical user prompt on both arms; the diet arm adds ONLY the static diet
  directive (the product mechanism). No cherry-picking: every repeat is logged.
- Savings are reported on completion tokens AND on totals (incl. thought
  tokens) — the directive can inflate reasoning, and that must be visible.
- Latency is reported as a neutral raw/diet ratio per repeat, never as a
  "speedup" claim. Use --repeats N (default 3) and read mean ± stdev.
"""
import argparse
import os
import statistics
import sys
import json
import time
import urllib.request
import urllib.error

parser = argparse.ArgumentParser(description="Illustrative live diet comparison (symmetric prompts).")
parser.add_argument("--repeats", type=int, default=3,
                    help="Repeated raw/diet pairs per scenario (default: 3)")
parser.add_argument("--api-key", default=None,
                    help="Gemini API key (or GEMINI_API_KEY env, or positional argv[1])")
parser.add_argument("maybe_key", nargs="?",
                    help="Positional API key (back-compat with the old CLI)")
args = parser.parse_args()

API_KEY = os.environ.get("GEMINI_API_KEY") or args.api_key or args.maybe_key
REPEATS = max(1, args.repeats)

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

def _mean_sd(xs):
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
    return round(m, 1), round(sd, 1)


def main():
    print("=" * 60)
    print(f"ILLUSTRATIVE LIVE COMPARISON on Google Gemini API: {MODEL_NAME}")
    print(f"Identical prompts both arms; repeats per scenario: {REPEATS}")
    print("=" * 60)

    results = []

    for sc in TEST_SCENARIOS:
        print(f"\n[Scenario: {sc['title']}]")

        raw_runs, diet_runs = [], []
        for rep in range(REPEATS):
            print(f"  Repeat {rep + 1}/{REPEATS}: raw...", flush=True)
            raw_runs.append(call_gemini(sc["user_prompt"]))
            time.sleep(2.5)
            print(f"  Repeat {rep + 1}/{REPEATS}: diet...", flush=True)
            diet_runs.append(call_gemini(sc["user_prompt"], system_instruction=DIET_DIRECTIVE))
            time.sleep(2.5)

        raw_comp = [r["completion_tokens"] for r in raw_runs]
        diet_comp = [r["completion_tokens"] for r in diet_runs]
        raw_tot = [r["total_tokens"] for r in raw_runs]
        diet_tot = [r["total_tokens"] for r in diet_runs]
        lat_ratio = [r["latency_s"] / max(0.01, d["latency_s"])
                     for r, d in zip(raw_runs, diet_runs)]

        comp_m, comp_sd = _mean_sd([round((1 - (d / max(1, r))) * 100, 1)
                                    for r, d in zip(raw_comp, diet_comp)])
        tot_m, tot_sd = _mean_sd([round((1 - (d / max(1, r))) * 100, 1)
                                  for r, d in zip(raw_tot, diet_tot)])
        lat_m, lat_sd = _mean_sd(lat_ratio)

        print(f"  ==> completion-token savings: {comp_m}% ±{comp_sd} "
              f"| total-token savings (incl. thoughts): {tot_m}% ±{tot_sd} "
              f"| latency ratio raw/diet: {lat_m} ±{lat_sd} (neutral, not a speed claim)")

        results.append({
            "id": sc["id"],
            "title": sc["title"],
            "task": sc["task"],
            "prompt": sc["user_prompt"],
            "model": MODEL_NAME,
            "repeats": REPEATS,
            "raw": raw_runs[0],
            "diet": diet_runs[0],
            "raw_runs": raw_runs,
            "diet_runs": diet_runs,
            "savings_pct": comp_m,
            "savings_pct_stdev": comp_sd,
            "total_savings_pct": tot_m,
            "total_savings_pct_stdev": tot_sd,
            "latency_ratio": lat_m,
            "latency_ratio_stdev": lat_sd,
        })

    out_file = "scratch/live_eval_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"timestamp": time.time(), "model": MODEL_NAME,
                   "repeats": REPEATS, "scenarios": results}, f, indent=2)

    print(f"\nAll live eval runs completed! Results saved to {out_file}")


if __name__ == "__main__":
    main()

"""
GENESIS Autonomous Sleep Consolidation Daemon.

Multi-phase biomimetic memory consolidation:
1. Inactivity Detection:
   - Monitors DB and interaction recency; triggers consolidation when idle.
2. Phase 1: NREM Consolidation:
   - Applies Ebbinghaus decay across episodic memory (HebbianEngine).
   - Downscales dormant engrams, protects solidified invariants.
   - Cleans up stale dialogue turns and temporary hook rows.
3. Phase 2: REM Consolidation:
   - Discovers recurring problem-solution patterns and synthesizes candidate skills (SkillSynthesizer).
   - Resolves low-hanging conflict queues.
4. Phase 3: Crystallization:
   - Renders top solidified decisions and active skills into AGENTS.md.
   - Generates persistent Markdown sleep reports.
   - Appends audited stats to ledger.jsonl.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Any, Dict, Optional

from genesis_memory.core.hebbian_engine import HebbianEngine
from genesis_memory.core.skill_synthesizer import SkillSynthesizer
from genesis_memory.sleep import digest_generator

logger = logging.getLogger("genesis.sleep_daemon")


def default_db_path() -> str:
    return os.environ.get(
        "GENESIS_DAEMON_DB",
        os.path.expanduser("~/.genesis/memory.db")
    )


def run_sleep_cycle(
    db_path: Optional[str] = None,
    deep: bool = False,
    stale_days: float = 30.0,
    repo_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Runs a complete 3-phase biomimetic sleep consolidation cycle."""
    db_file = db_path or default_db_path()
    if not os.path.exists(db_file):
        return {"ok": False, "error": f"Database not found: {db_file}"}

    t0 = time.time()
    conn = sqlite3.connect(db_file)
    try:
        # Phase 1: NREM Consolidation
        hebbian = HebbianEngine(conn)
        decay_stats = hebbian.decay_all(now=t0)

        # Dialogue buffer cleanup: prune hook turns older than 1800s
        pruned_dialogue = 0
        try:
            cur = conn.execute(
                "DELETE FROM dialogue_buffer WHERE session_id LIKE 'hook:%' AND updated_at < ?",
                (t0 - 1800.0,)
            )
            pruned_dialogue = cur.rowcount
            conn.commit()
        except Exception:
            pass

        # Phase 2: REM Consolidation
        skill_synth = SkillSynthesizer(conn)
        new_skills = skill_synth.discover_skills_from_episodes(min_cluster_size=2)

        # Count active skills
        skills_count = conn.execute(
            "SELECT COUNT(*) FROM skills WHERE status = 'active'"
        ).fetchone()[0]

        # Phase 3: Crystallization & Active Digest Generation
        digest_updated = False
        target_root = repo_root or os.getcwd()
        agents_md = Path(target_root) / ".agents" / "AGENTS.md"
        if agents_md.exists():
            try:
                digest_updated = digest_generator.update_agents_md(
                    agents_md, db_path=Path(db_file), max_items=8
                )
            except Exception as e:
                logger.warning("Failed to update AGENTS.md digest: %s", e)

        # Increment sleep cycle counter
        try:
            conn.execute(
                "INSERT INTO counters(name, count) VALUES ('sleep_cycles', 1) "
                "ON CONFLICT(name) DO UPDATE SET count = count + 1"
            )
            conn.commit()
        except Exception:
            pass

        # Generate sleep report on disk
        ddir = os.path.dirname(os.path.abspath(db_file))
        rdir = os.path.join(ddir, "sleep_reports")
        os.makedirs(rdir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(t0))
        report_file = os.path.join(rdir, f"sleep_{stamp}.md")

        report_lines = [
            "# 🌙 GENESIS Sleep Consolidation Report",
            f"- **Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t0))}",
            f"- **Database:** `{db_file}`",
            f"- **Duration:** {round(time.time() - t0, 3)}s",
            "",
            "## 1. NREM Synaptic Decay & Pruning",
            f"- Total episodes processed: {decay_stats.get('total_processed', 0)}",
            f"- Solidified (Invariants): {decay_stats.get('solidified', 0)}",
            f"- Active: {decay_stats.get('active', 0)}",
            f"- Dormant: {decay_stats.get('dormant', 0)}",
            f"- Prune candidates: {decay_stats.get('prune_candidate', 0)}",
            f"- Pruned transient dialogue turns: {pruned_dialogue}",
            "",
            "## 2. REM Skill Synthesis",
            f"- Newly synthesized skills: {len(new_skills)}",
            f"- Total active skills in procedural substrate: {skills_count}",
        ]
        if new_skills:
            for s in new_skills:
                report_lines.append(f"  - **{s['name']}**: `{s['action_recipe'][:80]}...`")

        report_lines.extend([
            "",
            "## 3. Crystallization & System State",
            f"- AGENTS.md active digest refreshed: {digest_updated}",
            f"- Report saved to: `{report_file}`",
        ])

        with open(report_file, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines) + "\n")

        # Append summary to persistent ledger.jsonl
        ledger_path = os.path.join(ddir, "ledger.jsonl")
        ledger_row = {
            "ts": t0,
            "date": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
            "event": "sleep_consolidation",
            "decay": decay_stats,
            "new_skills_count": len(new_skills),
            "skills_total": skills_count,
            "report": report_file,
            "duration_s": round(time.time() - t0, 3),
        }
        try:
            with open(ledger_path, "a", encoding="utf-8") as lf:
                lf.write(json.dumps(ledger_row) + "\n")
        except Exception:
            pass

        return {
            "ok": True,
            "timestamp": t0,
            "decay": decay_stats,
            "new_skills": [s["name"] for s in new_skills],
            "skills_total": skills_count,
            "digest_updated": digest_updated,
            "report_file": report_file,
            "duration_s": round(time.time() - t0, 3),
        }
    finally:
        conn.close()


class SleepDaemon:
    """Continuous background worker that triggers sleep cycles upon user inactivity."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        idle_threshold_s: float = 300.0,
        check_interval_s: float = 30.0,
        repo_root: Optional[str] = None,
    ):
        self.db_path = db_path or default_db_path()
        self.idle_threshold_s = idle_threshold_s
        self.check_interval_s = check_interval_s
        self.repo_root = repo_root
        self._stop_event = threading.Event()
        self.last_sleep_ts = 0.0

    def get_last_activity_time(self) -> float:
        """Determines the most recent modification time in SQLite episodes or dialogue buffer."""
        if not os.path.exists(self.db_path):
            return 0.0
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            t_ep = conn.execute("SELECT MAX(updated) FROM episodes").fetchone()[0] or 0.0
            t_dia = 0.0
            try:
                t_dia = conn.execute("SELECT MAX(updated_at) FROM dialogue_buffer").fetchone()[0] or 0.0
            except Exception:
                pass
            conn.close()
            return max(float(t_ep), float(t_dia))
        except Exception:
            return 0.0

    def step(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Evaluates idle state and runs consolidation if threshold met."""
        t = now if now is not None else time.time()
        last_act = self.get_last_activity_time()
        if last_act == 0.0:
            return None

        idle_seconds = max(0.0, t - last_act)
        time_since_sleep = max(0.0, t - self.last_sleep_ts)

        # Trigger sleep only if:
        # 1. System has been idle >= idle_threshold_s
        # 2. Activity occurred after the last sleep cycle
        # 3. Last sleep cycle was not within the idle threshold window
        if (
            idle_seconds >= self.idle_threshold_s
            and last_act > self.last_sleep_ts
            and time_since_sleep >= self.idle_threshold_s
        ):
            logger.info("Idle threshold reached (%.1fs). Triggering autonomous sleep consolidation.", idle_seconds)
            res = run_sleep_cycle(self.db_path, repo_root=self.repo_root)
            self.last_sleep_ts = t
            return res
        return None

    def run(self):
        logger.info(
            "GENESIS Sleep Daemon started. Monitoring %s (idle_threshold=%.1fs, interval=%.1fs)",
            self.db_path, self.idle_threshold_s, self.check_interval_s
        )
        while not self._stop_event.is_set():
            try:
                self.step()
            except Exception as e:
                logger.error("Error in sleep daemon step: %s", e)
            self._stop_event.wait(self.check_interval_s)
        logger.info("GENESIS Sleep Daemon stopped.")

    def stop(self):
        self._stop_event.set()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="genesis-sleep")
    ap.add_argument("--db", default="", help="Path to memory.db")
    ap.add_argument("--daemon", action="store_true", help="Run continuously in background")
    ap.add_argument("--now", action="store_true", help="Run single consolidation cycle immediately")
    ap.add_argument("--idle-threshold", type=float, default=300.0, help="Idle seconds before sleeping")
    ap.add_argument("--interval", type=float, default=30.0, help="Polling interval in seconds")
    a = ap.parse_args(argv)

    db = a.db or default_db_path()

    if a.now or (not a.daemon):
        print(f"Executing immediate sleep consolidation on {db}...")
        res = run_sleep_cycle(db)
        print(json.dumps(res, indent=2))
        return 0

    daemon = SleepDaemon(
        db_path=db,
        idle_threshold_s=a.idle_threshold,
        check_interval_s=a.interval,
    )
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

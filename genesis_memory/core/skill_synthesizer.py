"""
GENESIS Skill Synthesizer & Procedural Memory Substrate.

Transforms episodic experience into deterministic procedural skills:
1. Pattern Extraction:
   - Scans error-correction cycles and recurring decisions.
   - Extracts invariants and actionable recipes.
2. Fast Subconscious Matching:
   - Evaluates incoming prompt against trigger patterns using Unicode token overlap.
3. Zero-Token Determinism:
   - Replaces multi-turn trial-and-error with 1-turn verified actions.
"""

import json
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple


def extract_keywords(text: str, min_len: int = 3) -> List[str]:
    """Tokenizes text into lowercase alphanumeric keywords, excluding basic noise."""
    if not text:
        return []
    words = re.findall(r"[\w]{%d,}" % min_len, text.lower())
    noise = {
        "the", "and", "for", "with", "this", "that", "from", "into", "use", "using",
        "set", "get", "add", "run", "not", "all", "are", "was", "has", "have", "been",
        "will", "would", "can", "could", "should", "about", "which", "when", "where"
    }
    return [w for w in words if w not in noise]


class SkillSynthesizer:
    """Manages procedural skills lifecycle, automated synthesis, and matching."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self._ensure_table()

    def _ensure_table(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS skills(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                trigger_patterns TEXT,
                preconditions TEXT,
                action_recipe TEXT NOT NULL,
                invariants TEXT,
                confidence REAL DEFAULT 1.0,
                success_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at REAL,
                updated_at REAL
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status)")
        self.db.commit()

    def create_or_update_skill(
        self,
        name: str,
        action_recipe: str,
        trigger_patterns: List[str],
        skill_id: Optional[str] = None,
        preconditions: str = "",
        invariants: str = "",
        confidence: float = 1.0,
        status: str = "active",
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Creates or updates a deterministic procedural skill."""
        t = now if now is not None else time.time()
        sid = skill_id or ("skill_" + re.sub(r"[^\w]+", "_", name.lower()).strip("_"))
        triggers_json = json.dumps(trigger_patterns, ensure_ascii=False)

        self.db.execute(
            "INSERT INTO skills(id, name, trigger_patterns, preconditions, action_recipe, "
            "invariants, confidence, success_count, status, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "name = excluded.name, "
            "trigger_patterns = excluded.trigger_patterns, "
            "preconditions = excluded.preconditions, "
            "action_recipe = excluded.action_recipe, "
            "invariants = excluded.invariants, "
            "confidence = excluded.confidence, "
            "status = excluded.status, "
            "updated_at = excluded.updated_at",
            (sid, name, triggers_json, preconditions, action_recipe, invariants,
             float(confidence), status, t, t)
        )
        self.db.commit()

        return {
            "id": sid,
            "name": name,
            "action_recipe": action_recipe,
            "trigger_patterns": trigger_patterns,
            "confidence": confidence,
            "status": status,
        }

    def match_skills(
        self,
        text: str,
        min_confidence: float = 0.5,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Finds matching active skills for a given query/context."""
        if not text:
            return []

        query_terms = set(extract_keywords(text))
        if not query_terms:
            return []

        rows = self.db.execute(
            "SELECT id, name, trigger_patterns, preconditions, action_recipe, "
            "invariants, confidence, success_count, status FROM skills "
            "WHERE (status = 'active' OR status IS NULL) AND confidence >= ?",
            (float(min_confidence),)
        ).fetchall()

        matched = []
        for sid, name, trig_raw, pre, recipe, inv, conf, succ, st in rows:
            try:
                patterns = json.loads(trig_raw) if trig_raw else []
            except Exception:
                patterns = [p.strip() for p in (trig_raw or "").split(",") if p.strip()]

            score = 0.0
            for pat in patterns:
                pat_lower = pat.lower()
                # Direct substring match
                if pat_lower in text.lower():
                    score += 2.0
                else:
                    pat_terms = set(extract_keywords(pat))
                    if pat_terms and pat_terms.issubset(query_terms):
                        score += 1.5
                    elif pat_terms and (pat_terms & query_terms):
                        score += 0.5 * len(pat_terms & query_terms)

            if score > 0.0:
                matched.append((score, {
                    "id": sid,
                    "name": name,
                    "action_recipe": recipe,
                    "preconditions": pre,
                    "invariants": inv,
                    "confidence": conf,
                    "success_count": succ,
                    "match_score": round(score, 2),
                }))

        matched.sort(key=lambda x: (x[0], x[1]["confidence"], x[1]["success_count"]), reverse=True)
        return [item[1] for item in matched[:limit]]

    def record_success(self, skill_id: str) -> bool:
        """Increments skill success count and boosts confidence."""
        cur = self.db.execute(
            "UPDATE skills SET success_count = success_count + 1, "
            "confidence = MIN(1.0, confidence + 0.05), updated_at = ? WHERE id = ?",
            (time.time(), skill_id)
        )
        self.db.commit()
        return cur.rowcount > 0

    def discover_skills_from_episodes(self, min_cluster_size: int = 2) -> List[Dict[str, Any]]:
        """Analyzes verified decisions and outcomes to auto-synthesize candidate skills during REM sleep."""
        rows = self.db.execute(
            "SELECT id, project, kind, text, utility FROM episodes "
            "WHERE (status = 'active' OR status = 'solidified') "
            "AND kind IN ('decision', 'outcome') "
            "ORDER BY updated DESC LIMIT 100"
        ).fetchall()

        # Group by distinctive action clusters
        clusters: Dict[str, List[Tuple[int, str, str]]] = {}
        for eid, proj, kind, text, util in rows:
            words = extract_keywords(text)
            for w in words:
                if len(w) >= 5:
                    clusters.setdefault(w, []).append((eid, kind, text))

        synthesized = []
        for keyword, group in clusters.items():
            if len(group) >= min_cluster_size:
                # We have multiple related episodes around a key topic
                decisions = [t for _, k, t in group if k == "decision"]
                outcomes = [t for _, k, t in group if k == "outcome"]
                if decisions:
                    sid = f"skill_auto_{keyword}"
                    # Check if already exists
                    exists = self.db.execute("SELECT id FROM skills WHERE id = ?", (sid,)).fetchone()
                    if not exists:
                        recipe = decisions[0][:180]
                        triggers = [keyword]
                        skill = self.create_or_update_skill(
                            name=f"Auto-Synthesized {keyword.capitalize()} Heuristic",
                            action_recipe=recipe,
                            trigger_patterns=triggers,
                            skill_id=sid,
                            confidence=0.75,
                            status="active",
                        )
                        synthesized.append(skill)

        return synthesized

    def format_skills_for_prompt(self, skills: List[Dict[str, Any]], max_chars: int = 300) -> List[str]:
        """Formats matched skills into compact single-line instructions for subconscious injection."""
        lines = []
        used = 0
        for s in skills:
            snippet = s["action_recipe"].strip().replace("\n", " ")
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            line = f"• [Active Skill: {s['name']}]: {snippet}"
            if used + len(line) > max_chars and lines:
                break
            lines.append(line)
            used += len(line)
        return lines

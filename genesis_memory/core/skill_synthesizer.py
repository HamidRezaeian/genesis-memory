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
        """Analyzes verified decisions and outcomes to auto-synthesize candidate skills during REM sleep.

        Zero-hardcoding invariant:
        - Structural episode clustering: Groups episodes by pairwise keyword co-occurrence (Jaccard similarity).
        - Recipe deduplication: Identical or duplicate recipes are consolidated with multiple trigger patterns
          rather than spawning duplicate skill records.
        - Dynamic entropy filtering: Words with high frequency (>60% of corpus) are statistically treated as
          corpus noise rather than discriminative triggers.
        """
        rows = self.db.execute(
            "SELECT id, project, kind, text, utility FROM episodes "
            "WHERE (status = 'active' OR status = 'solidified') "
            "AND kind IN ('decision', 'outcome') "
            "ORDER BY updated DESC LIMIT 100"
        ).fetchall()

        if not rows:
            return []

        # 1. Statistical token profiling (zero hardcoded dictionaries)
        episodes = []
        doc_freq: Dict[str, int] = {}
        for eid, proj, kind, text, util in rows:
            terms = set(extract_keywords(text, min_len=4))
            if terms:
                episodes.append((eid, kind, text, terms))
                for t in terms:
                    doc_freq[t] = doc_freq.get(t, 0) + 1

        n_docs = len(episodes)
        max_df = max(int(n_docs * 0.6), min_cluster_size + 1) if n_docs > 5 else n_docs + 1
        filtered_episodes = []
        for eid, kind, text, terms in episodes:
            distinctive = {t for t in terms if doc_freq.get(t, 0) <= max_df}
            if distinctive:
                filtered_episodes.append((eid, kind, text, distinctive))

        # 2. Cluster episodes by pairwise semantic overlap
        clusters: List[Dict[str, Any]] = []
        for eid, kind, text, terms in filtered_episodes:
            matched_cluster = None
            for c in clusters:
                overlap = terms & c["shared_terms"]
                jaccard = len(overlap) / max(len(terms | c["shared_terms"]), 1)
                if len(overlap) >= 2 or jaccard >= 0.25:
                    c["episodes"].append((eid, kind, text))
                    c["shared_terms"] = overlap
                    c["all_terms"].update(terms)
                    matched_cluster = c
                    break
            if not matched_cluster:
                clusters.append({
                    "episodes": [(eid, kind, text)],
                    "shared_terms": set(terms),
                    "all_terms": set(terms)
                })

        # 3. Synthesize discrete, deduplicated skills
        synthesized = []
        existing_recipes = {
            r[0].strip().lower(): r[1]
            for r in self.db.execute("SELECT action_recipe, id FROM skills").fetchall()
            if r[0]
        }

        for c in clusters:
            if len(c["episodes"]) >= min_cluster_size:
                decisions = [t for _, k, t in c["episodes"] if k == "decision"]
                if not decisions:
                    continue

                recipe = decisions[0][:180].strip()
                recipe_key = recipe.lower()

                triggers = sorted(
                    c["shared_terms"] if c["shared_terms"] else c["all_terms"],
                    key=lambda w: (len(w), doc_freq.get(w, 0)),
                    reverse=True
                )[:5]
                if not triggers:
                    continue

                primary_keyword = triggers[0]
                sid = f"skill_auto_{primary_keyword}"

                if recipe_key in existing_recipes:
                    existing_id = existing_recipes[recipe_key]
                    curr = self.db.execute("SELECT trigger_patterns FROM skills WHERE id = ?", (existing_id,)).fetchone()
                    if curr and curr[0]:
                        try:
                            old_trigs = set(json.loads(curr[0]))
                        except Exception:
                            old_trigs = set()
                        merged = sorted(list(old_trigs | set(triggers)))[:8]
                        self.db.execute(
                            "UPDATE skills SET trigger_patterns = ?, updated_at = ? WHERE id = ?",
                            (json.dumps(merged, ensure_ascii=False), time.time(), existing_id)
                        )
                        self.db.commit()
                    continue

                existing_recipes[recipe_key] = sid
                skill = self.create_or_update_skill(
                    name=f"Auto-Synthesized {primary_keyword.capitalize()} Heuristic",
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

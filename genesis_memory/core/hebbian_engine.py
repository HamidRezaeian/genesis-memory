"""
GENESIS Hebbian Plasticity & Temporal Decay Engine.

Implements biologically-inspired learning dynamics:
1. Spaced Repetition Stability & Ebbinghaus Forgetting Curve:
   - Memories decay over time unless reinforced.
   - Stability increases with spaced access and explicit validation.
2. Synaptic Plasticity:
   - Direct reinforcement (success/failure feedback) scales synaptic weight.
3. Dynamic State Transitions:
   - solidified (invariant, immutable golden rule)
   - active (everyday working memory)
   - dormant (sub-threshold, hidden from subconscious injection unless specifically queried)
   - prune_candidate (marked for human veto or archival during sleep cycle)
4. Co-activation Associative Wiring:
   - Neurons (engrams) that fire together wire together: creates/strengthens
     co_activated edges in SQLite graph.

Zero external dependencies (pure math + stdlib SQLite). Strict RSS discipline.
"""

import math
import sqlite3
import time
from typing import Dict, List, Optional, Tuple


def compute_stability(accesses: int, reinforcements: int, base_stability_days: float = 7.0) -> float:
    """Calculates memory half-life stability in days based on spaced reinforcement.
    
    More recalls and verified outcomes exponentially extend stability.
    """
    acc_boost = 0.4 * math.log(1.0 + max(0, accesses))
    reinf_boost = 0.6 * math.log(1.0 + max(0, reinforcements))
    return round(base_stability_days * (1.0 + acc_boost + reinf_boost), 2)


def compute_retention(elapsed_seconds: float, stability_days: float) -> float:
    """Calculates Ebbinghaus retention probability: R(t) = exp(-dt / S)."""
    if stability_days <= 0:
        return 0.0
    elapsed_days = max(0.0, elapsed_seconds / 86400.0)
    # Exponential decay bounded in [0.0, 1.0]
    return math.exp(-elapsed_days / max(0.1, stability_days))


def compute_synaptic_weight(
    utility: float,
    retention: float,
    accesses: int,
    reinforcements: int,
) -> float:
    """Calculates effective synaptic weight: W = max(0.1, U*R + 0.2*sqrt(A) + 0.5*R)."""
    base = float(utility) * float(retention)
    acc_factor = 0.2 * math.sqrt(max(0, accesses))
    reinf_factor = 0.5 * max(0, reinforcements)
    return round(max(0.1, base + acc_factor + reinf_factor), 3)


def classify_state(
    weight: float,
    age_days: float,
    accesses: int,
    current_status: Optional[str] = "active",
) -> str:
    """Determines memory operational category based on synaptic weight and age.
    
    Transitions:
    - weight >= 1.6: solidified (golden rule / crystallized invariant)
    - 0.5 <= weight < 1.6: active (regular working memory)
    - 0.2 <= weight < 0.5: dormant (recalled only on specific FTS5 match)
    - weight < 0.2 (and age >= 7d): prune_candidate
    """
    if current_status in ("superseded", "invalidated"):
        return current_status

    if weight >= 1.6:
        return "solidified"
    if weight >= 0.5:
        return "active"
    if weight >= 0.2:
        return "dormant"
    if age_days >= 7.0 and accesses <= 1:
        return "prune_candidate"
    return "dormant"


class HebbianEngine:
    """High-performance SQLite-backed Hebbian plasticity and decay controller."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def reinforce_memory(
        self,
        episode_id: int,
        outcome: str = "success",
        note: str = "",
        now: Optional[float] = None,
    ) -> Dict[str, object]:
        """Reinforces or penalizes an episode based on task outcome.
        
        Outcomes:
        - 'success': +1 reinforcement, recalculates stability, boosts utility.
        - 'failure': -1 reinforcement (min 0), slightly reduces utility.
        """
        t = now if now is not None else time.time()
        row = self.db.execute(
            "SELECT id, utility, accesses, COALESCE(reinforcements, 0), "
            "COALESCE(stability, 7.0), updated, status, kind, text FROM episodes WHERE id = ?",
            (int(episode_id),)
        ).fetchone()

        if not row:
            raise ValueError(f"Episode #{episode_id} not found")

        eid, util, acc, reinf, stab, upd, status, kind, text = row

        if outcome == "success":
            new_reinf = reinf + 1
            new_util = min(5.0, util + 0.25)
        elif outcome == "failure":
            new_reinf = max(0, reinf - 1)
            new_util = max(0.2, util - 0.25)
        else:
            new_reinf = reinf
            new_util = util

        new_stab = compute_stability(acc, new_reinf)
        elapsed = max(0.0, t - (upd or t))
        retention = compute_retention(elapsed, new_stab)
        new_weight = compute_synaptic_weight(new_util, retention, acc, new_reinf)
        age_days = (t - (upd or t)) / 86400.0
        new_status = classify_state(new_weight, age_days, acc, status)

        self.db.execute(
            "UPDATE episodes SET utility = ?, reinforcements = ?, stability = ?, "
            "last_reinforced = ?, updated = ?, status = ? WHERE id = ?",
            (new_util, new_reinf, new_stab, t, t, new_status, eid)
        )
        self.db.commit()

        return {
            "id": eid,
            "outcome": outcome,
            "utility": round(new_util, 3),
            "reinforcements": new_reinf,
            "stability_days": new_stab,
            "synaptic_weight": new_weight,
            "status": new_status,
            "note": note,
        }

    def decay_all(self, now: Optional[float] = None) -> Dict[str, int]:
        """Applies Ebbinghaus decay across all non-superseded episodes.
        
        Called during NREM sleep consolidation to adjust weights and states.
        """
        t = now if now is not None else time.time()
        rows = self.db.execute(
            "SELECT id, utility, accesses, COALESCE(reinforcements, 0), "
            "COALESCE(stability, 7.0), updated, status FROM episodes "
            "WHERE status IN ('active', 'dormant', 'solidified') OR status IS NULL"
        ).fetchall()

        transitions = {
            "solidified": 0,
            "active": 0,
            "dormant": 0,
            "prune_candidate": 0,
            "total_processed": len(rows),
        }

        updates = []
        for eid, util, acc, reinf, stab, upd, cur_status in rows:
            elapsed = max(0.0, t - (upd or t))
            ret = compute_retention(elapsed, stab)
            weight = compute_synaptic_weight(util, ret, acc, reinf)
            age_days = elapsed / 86400.0
            next_status = classify_state(weight, age_days, acc, cur_status)

            if next_status in transitions:
                transitions[next_status] += 1

            if next_status != cur_status:
                updates.append((next_status, eid))

        if updates:
            self.db.executemany(
                "UPDATE episodes SET status = ? WHERE id = ?",
                updates
            )
            self.db.commit()

        return transitions

    def record_co_activation(self, episode_ids: List[int], now: Optional[float] = None) -> int:
        """Connects co-recalled memories in the associative edge graph.
        
        Creates or updates edges with relation='co_activated'.
        """
        if len(episode_ids) < 2:
            return 0

        t = now if now is not None else time.time()
        clean_ids = sorted(list(set(int(x) for x in episode_ids)))
        edges_added = 0

        for i in range(len(clean_ids)):
            for j in range(i + 1, len(clean_ids)):
                src = f"ep:{clean_ids[i]}"
                tgt = f"ep:{clean_ids[j]}"
                try:
                    self.db.execute(
                        "INSERT INTO edges(source, target, relation, file_hash, status, ts, updated) "
                        "VALUES(?, ?, 'co_activated', '1.0', 'active', ?, ?) "
                        "ON CONFLICT(source, target, relation) DO UPDATE SET "
                        "updated = excluded.updated, "
                        "file_hash = CAST(COALESCE(CAST(file_hash AS REAL), 1.0) + 0.5 AS TEXT)",
                        (src, tgt, t, t)
                    )
                    edges_added += 1
                except Exception:
                    pass

        if edges_added > 0:
            self.db.commit()

        return edges_added

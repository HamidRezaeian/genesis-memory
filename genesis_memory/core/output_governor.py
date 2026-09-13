"""Output-side token governor: ask for less, cap the trivial, spot the echo.

Input diet can be lossless; output diet is lossy by nature — you cannot
compress what has not been said yet, only ask for less of it. So every rule
here is conservative, user-sovereign (an explicit ask always wins), and
observable (telemetry counters, never silent behavior changes).

Three mechanisms, shared by the proxy gateway and the subconscious hook:

1. wants_detail(text): explicit asks for depth disable terse mode for that turn.
   Data-driven pattern matching with user sovereignty fallback.
2. is_tiny_turn(text): structural & allowlist classification — short acknowledgments
   and greetings that can never need a long answer. Anything unrecognized is a
   standard turn (fail-open by construction).
3. detect_file_echo(): spots model replies that paste back whole files instead
   of diffs (fenced blocks whose lines mostly already appear in the prompt).

Zero hardcoded language dictionaries in code: patterns are loaded dynamically
from an extensible data catalog (or user override) with structural fallbacks.
"""
import json
import os
import re
from pathlib import Path
from typing import FrozenSet, List, Optional, Pattern

# ---------------------------------------------------------------------------
# Dynamic Rule Catalog Loading (Data-driven, zero hardcoding in core code)
# ---------------------------------------------------------------------------
_NORM_MAP = str.maketrans({"ي": "ی", "ك": "ک", "‌": " ", " ": " "})
_DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "output_governor_rules.json"

TINY_TURN_MAX_TOKENS = 256
TINY_TURN_MAX_CHARS = 120

_cached_detail_re: Optional[Pattern] = None
_cached_tiny_set: Optional[FrozenSet[str]] = None


def _load_rules():
    global _cached_detail_re, _cached_tiny_set
    if _cached_detail_re is not None and _cached_tiny_set is not None:
        return _cached_detail_re, _cached_tiny_set

    rules_path = os.environ.get("GENESIS_GOVERNOR_RULES_PATH")
    if not rules_path:
        user_path = os.path.expanduser("~/.genesis/output_governor_rules.json")
        if os.path.exists(user_path):
            rules_path = user_path
        elif _DEFAULT_DATA_PATH.exists():
            rules_path = str(_DEFAULT_DATA_PATH)

    detail_patterns: List[str] = []
    tiny_turns: List[str] = []

    if rules_path and os.path.exists(rules_path):
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                detail_patterns = data.get("detail_patterns", [])
                tiny_turns = data.get("tiny_turns", [])
        except Exception:
            pass

    if detail_patterns:
        _cached_detail_re = re.compile("|".join(detail_patterns), re.IGNORECASE)
    else:
        # Minimal structural fallback if data file is absent
        _cached_detail_re = re.compile(
            r"in\s+detail|step\s*by\s*step|thorough|comprehensive|elaborate",
            re.IGNORECASE,
        )

    norm_tiny = {w.translate(_NORM_MAP).casefold() for w in tiny_turns}
    _cached_tiny_set = frozenset(norm_tiny) if norm_tiny else frozenset(["ok", "thanks"])
    return _cached_detail_re, _cached_tiny_set


# ---------------------------------------------------------------------------
# 1. Explicit-detail detection (user sovereignty over brevity)
# ---------------------------------------------------------------------------
def wants_detail(text) -> bool:
    """True when the user explicitly asks for depth (terse mode must yield)."""
    if not isinstance(text, str) or not text.strip():
        return False
    detail_re, _ = _load_rules()
    lowered = re.sub(r"\s+", " ", text.lower().translate(_NORM_MAP))
    return bool(detail_re.search(lowered) or detail_re.search(text))


# ---------------------------------------------------------------------------
# 2. Tiny-turn classification (allowlist & structural bounds — fail-open)
# ---------------------------------------------------------------------------
def is_tiny_turn(text) -> bool:
    """True only for short acknowledgments/greetings — never for real asks."""
    if not isinstance(text, str):
        return False
    t = text.strip()
    if not t or len(t) > TINY_TURN_MAX_CHARS:
        return False
    if "```" in t:
        return False
    t = re.sub(r"[.!?…؟\s]+$", "", t).strip().casefold()
    t = t.translate(_NORM_MAP)
    t = re.sub(r"\s+", " ", t)

    _, tiny_set = _load_rules()
    return t in tiny_set


# ---------------------------------------------------------------------------
# 3. File-echo detection (full files pasted back instead of diffs)
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_ECHO_MIN_LINES = 15
_ECHO_MIN_RATIO = 0.9


def _norm_lines(s: str):
    return [re.sub(r"\s+", "", ln) for ln in s.splitlines() if ln.strip()]


def detect_file_echo(prompt_text, completion_text,
                     min_lines: int = _ECHO_MIN_LINES,
                     min_ratio: float = _ECHO_MIN_RATIO):
    """Spot assistant replies echoing whole files instead of diffs.

    Returns (is_echo, best_ratio, best_lines). A fenced block counts as an
    echo when it holds >= min_lines and >= min_ratio of its lines already
    appear in the prompt. Pure text comparison — no model calls.
    """
    if not isinstance(prompt_text, str) or not isinstance(completion_text, str):
        return (False, 0.0, 0)
    prompt_lines = set(_norm_lines(prompt_text))
    if not prompt_lines:
        return (False, 0.0, 0)
    best = (False, 0.0, 0)
    for block in _FENCE_RE.findall(completion_text):
        blines = _norm_lines(block)
        if len(blines) < min_lines:
            continue
        hits = sum(1 for ln in blines if ln in prompt_lines)
        ratio = hits / len(blines)
        if ratio > best[1]:
            best = (ratio >= min_ratio, ratio, len(blines))
    return best

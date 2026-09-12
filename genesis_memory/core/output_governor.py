"""Output-side token governor: ask for less, cap the trivial, spot the echo.

Input diet can be lossless; output diet is lossy by nature — you cannot
compress what has not been said yet, only ask for less of it. So every rule
here is conservative, user-sovereign (an explicit ask always wins), and
observable (telemetry counters, never silent behavior changes).

Three mechanisms, shared by the proxy gateway and the subconscious hook:

1. wants_detail(text): explicit asks for depth ("in detail", "step by step",
   "مفصل", ...) disable terse mode for that turn. Pure stdlib regex, EN + FA.
2. is_tiny_turn(text): allowlist ONLY — short acknowledgments and greetings
   ("thanks", "ok", "ممنون", ...) that can never need a long answer. Anything
   unrecognized is a standard turn (fail-open by construction).
3. detect_file_echo(): spots model replies that paste back whole files instead
   of diffs (fenced blocks whose lines mostly already appear in the prompt).

No imports beyond `re`: this module must stay dependency-free so both the
proxy server and the hook can use it without import cycles.
"""
import re

# ---------------------------------------------------------------------------
# 1. Explicit-detail detection (user sovereignty over brevity)
# ---------------------------------------------------------------------------
_DETAIL_EN = (
    r"in\s+detail(?:s)?",
    r"\bthorough(?:ly)?\b",
    r"\bin[-\s]?depth\b",
    r"\bcomprehensive(?:ly)?\b",
    r"\belaborate\b",
    r"\belaboration\b",
    r"\bexhaustive(?:ly)?\b",
    r"step\s*by\s*step",
    r"walk\s+me\s+through",
    r"\blong[-\s]?form\b",
    r"\bverbose\b",
    r"\bin\s+full\b",
    r"every\s+detail",
    r"all\s+the\s+details",
    r"don'?t\s+hold\s+back",
)
_DETAIL_FA = (
    "مفصل", "مبسوط", "جامع", "کامل",
    "جزئیات", "جزییات", "طولانی",
    "قدم به قدم", "قدم‌به‌قدم", "گام به گام", "گام‌به‌گام",
    "ریز به ریز", "ریزبه‌ریز", "تک تک", "تک‌تک", "دانه دانه",
)
_DETAIL_RE = re.compile("|".join(_DETAIL_EN), re.IGNORECASE)
# Persian matching uses word boundaries on normalized text so «جامعه» never
# trips «جامع»: false positives fail toward verbose (user sovereignty).
_DETAIL_FA_RE = re.compile(
    "|".join(r"\b" + re.escape(w) + r"\b" for w in _DETAIL_FA))


def wants_detail(text) -> bool:
    """True when the user explicitly asks for depth (terse mode must yield)."""
    if not isinstance(text, str) or not text.strip():
        return False
    if _DETAIL_RE.search(text):
        return True
    lowered = re.sub(r"\s+", " ", text.lower().translate(_FA_NORM))
    return bool(_DETAIL_FA_RE.search(lowered))


# ---------------------------------------------------------------------------
# 2. Tiny-turn classification (allowlist only — unknown shapes stay standard)
# ---------------------------------------------------------------------------
_TINY_EN = (
    "ok", "okay", "thanks", "thank you", "thx", "noted", "got it", "gotcha",
    "great", "perfect", "awesome", "nice", "cool", "yes", "no", "yep",
    "nope", "sure", "hi", "hello", "hey", "hey there",
    "good morning", "good afternoon", "good evening", "bye", "goodbye",
    "see you",
)
_TINY_FA = (
    "باشه", "اوکی", "ممنون", "مرسی", "دمت گرم", "چشم", "حتما", "حتماً",
    "بله", "نه", "خیر", "سلام", "درود", "خداحافظ", "فعلا", "فعلاً",
    "قربانت", "عالی", "خوبه", "خوب", "اره", "آره",
)
# Persian orthography variants collapse to one form before matching.
_FA_NORM = str.maketrans({"ي": "ی", "ك": "ک", "‌": " ", " ": " "})
_TINY_SINGLE = frozenset(_TINY_EN) | frozenset(w.translate(_FA_NORM) for w in _TINY_FA)

TINY_TURN_MAX_TOKENS = 256
TINY_TURN_MAX_CHARS = 120


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
    t = t.translate(_FA_NORM)
    t = re.sub(r"\s+", " ", t)
    return t in _TINY_SINGLE


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

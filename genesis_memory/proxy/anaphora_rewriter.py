"""GENESIS Anaphora Rewriter — Rewrite-then-Retrieve with 1-Turn TLB.

Guarantees:
1. Low-Specificity Detection: Detects pronoun/anaphora references (it, this bug, that error, etc.).
2. Salient Entity Extraction: Pulls error names, touched files, and function symbols from 1-turn TLB.
3. Query Expansion: Rewrites user query into rich FTS5-compatible search keywords.
4. Graceful Fallback (P3 Guard): Detects unresolvable anaphora to prevent hallucination.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

# English anaphora patterns
RE_ANAPHORA_EN = re.compile(
    r"\b("
    r"it|this|that|these|those|"
    r"this bug|that error|the error|the issue|the bug|the failure|the crash|the exception|"
    r"run it|fix it|do it again|same error|previous error|that file|check it|"
    r"again|why\??|what happened\??|how come\??"
    r")\b",
    re.IGNORECASE,
)

# Persian anaphora terms
PERSIAN_ANAPHORA = [
    "این باگ", "همین باگ", "این ارور", "همین ارور", "دوباره", "همونو", "اونو",
    "این خطا", "همین خطا", "تست قبلی", "فیکسش کن", "اجراش کن", "حلش کن"
]

RE_IDENTIFIERS = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]{2,})\b")
RE_ERRORS = re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:Error|Exception|Fault|Interrupt))\b")
RE_FILES = re.compile(r"\b([a-zA-Z0-9_\-\/\\]+\.(?:py|json|md|c|cpp|h|ts|js|html))\b")

STOPWORDS = {
    "the", "and", "this", "that", "with", "from", "for", "have", "been", "will",
    "would", "could", "should", "about", "what", "which", "then", "into", "some",
    "true", "false", "none", "null", "test", "file", "error", "code", "bugs",
    "mean", "said", "look", "good", "make", "just", "know", "like", "time",
}


def is_technical_symbol(s: str) -> bool:
    """Returns True if string looks like a code symbol, error, or file."""
    if "_" in s or "." in s or "/" in s or "\\" in s:
        return True
    # CamelCase e.g. ZeroDivisionError, FooBar
    if re.search(r"[a-z][A-Z]", s):
        return True
    return False


def contains_technical_symbol(text: str) -> bool:
    """Checks if a user query contains code symbols, paths, or explicit error names."""
    if not text or not isinstance(text, str):
        return False
    if RE_ERRORS.search(text) or RE_FILES.search(text):
        return True
    for tok in text.split():
        cleaned = tok.strip(".,!?:;\"'()[]{}")
        if cleaned and is_technical_symbol(cleaned):
            return True
    return False


class AnaphoraRewriter:
    """Expands ambiguous queries using the 1-Turn Locality Buffer before retrieval."""

    def __init__(self) -> None:
        pass

    def is_anaphoric(self, query: str) -> bool:
        """Determines if a user prompt is ambiguous or relies on past anaphora."""
        q = query.strip()
        q_lower = q.lower()

        # Check Persian anaphora
        if any(p in q for p in PERSIAN_ANAPHORA):
            return True

        # Check English anaphora
        if RE_ANAPHORA_EN.search(q_lower):
            return True

        return False

    def extract_salient_tlb_terms(self, tlb: Dict[str, Any]) -> List[str]:
        """Extracts top salient entities from the 1-turn Locality Buffer."""
        salient: List[str] = []
        seen: Set[str] = set()

        # 1. Error signatures (highest priority)
        errors = tlb.get("errors", [])
        for err in errors:
            if err not in seen:
                salient.append(err)
                seen.add(err)

        # 2. Touched file basenames
        files = tlb.get("touched_files", [])
        for f in files:
            base = f.replace("\\", "/").split("/")[-1]
            if base and base not in seen:
                salient.append(base)
                seen.add(base)

        # 3. Test outcomes
        test_out = tlb.get("test_outcome", "")
        if test_out:
            for err in RE_ERRORS.findall(test_out):
                if err not in seen:
                    salient.append(err)
                    seen.add(err)

        # 4. Context text identifiers (only technical symbols)
        context_text = tlb.get("last_assistant_response", "") + " " + tlb.get("last_tool_output", "")
        for err in RE_ERRORS.findall(context_text):
            if err not in seen:
                salient.append(err)
                seen.add(err)

        for ident in RE_IDENTIFIERS.findall(context_text):
            ident_lower = ident.lower()
            if ident_lower not in STOPWORDS and is_technical_symbol(ident) and ident not in seen:
                salient.append(ident)
                seen.add(ident)
                if len(salient) >= 6:
                    break

        return salient[:5]

    def rewrite_query(
        self,
        query: str,
        tlb: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, bool, bool]:
        """Rewrites an anaphoric query using TLB entities.
        
        Returns:
            (rewritten_query, was_rewritten, needs_clarification)
        """
        if not self.is_anaphoric(query):
            return query, False, False

        if not tlb:
            # Anaphoric prompt with completely empty TLB (P3 Guard: trigger clarification)
            return query, False, True

        salient = self.extract_salient_tlb_terms(tlb)
        if not salient:
            # TLB exists but yielded zero specific identifiers
            return query, False, True

        expanded_terms = " ".join(salient)
        rewritten = f"{query.strip()} {expanded_terms}".strip()
        return rewritten, True, False

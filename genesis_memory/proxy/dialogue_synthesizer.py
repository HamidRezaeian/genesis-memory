"""GENESIS Entity-Aware Dialogue Synthesizer.

Compresses assistant conversational responses into compact, high-density entity summaries
under 140 characters (<35 tokens) for cross-session and cross-client anaphora resolution.

Guarantees:
1. Entity Extraction: Identifies files, errors, and technical symbols (RE_FILES, RE_ERRORS).
2. Core Takeaway Priority: Locates verdict / summary lines ("یک‌خطی:", "خلاصه:", "نظرم:", "Verdict:").
3. Strict Budget: Caps summary length strictly to max_chars (default 140 chars / ~35 tokens).
4. Privacy Invariant: Runs scan_secrets() before output to ensure no secrets or keys are persisted.
"""

import re
from typing import List, Tuple

from genesis_memory.daemon.server import scan_secrets, redact_secrets

RE_ERRORS = re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:Error|Exception|Fault|Interrupt))\b")
RE_FILES = re.compile(r"\b([a-zA-Z0-9_\-\/\\]+\.(?:py|json|md|c|cpp|h|ts|js|html))\b")
RE_IDENTIFIERS = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]{2,})\b")

RE_VERDICT_PREFIX = re.compile(
    r"^(?:یک‌خطی|خلاصه|نتیجه|حکم|حکم داور|نظرم|نظر شفاف|Verdict|Summary|Conclusion|Takeaway)\s*[:：\-]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)

STOPWORDS = {
    "the", "and", "this", "that", "with", "from", "for", "have", "been", "will",
    "would", "could", "should", "about", "what", "which", "then", "into", "some",
    "true", "false", "none", "null", "test", "file", "error", "code", "bugs",
    "mean", "said", "look", "good", "make", "just", "know", "like", "time",
    "text", "type", "line", "lines", "return", "pass", "import", "def",
}


def is_technical_symbol(s: str) -> bool:
    """Returns True if string looks like a code symbol, error, or file."""
    if "_" in s or "." in s or "/" in s or "\\" in s:
        return True
    if re.search(r"[a-z][A-Z]", s):
        return True
    return False


def extract_salient_entities(text: str) -> List[str]:
    """Extracts high-salience technical entities from text."""
    if not text or not isinstance(text, str):
        return []

    entities: List[str] = []
    seen = set()

    # 1. Error types (highest priority)
    for err in RE_ERRORS.findall(text):
        if err not in seen:
            entities.append(err)
            seen.add(err)

    # 2. Files
    for f in RE_FILES.findall(text):
        base = f.replace("\\", "/").split("/")[-1]
        if base and base not in seen:
            entities.append(base)
            seen.add(base)

    # 3. Technical symbols
    for ident in RE_IDENTIFIERS.findall(text):
        ident_lower = ident.lower()
        if ident_lower not in STOPWORDS and is_technical_symbol(ident) and ident not in seen:
            entities.append(ident)
            seen.add(ident)
            if len(entities) >= 6:
                break

    return entities[:5]


def extract_core_takeaway(text: str) -> str:
    """Finds explicit summary or core conclusion sentence."""
    if not text:
        return ""

    # Check for explicit takeaway headers
    m = RE_VERDICT_PREFIX.search(text)
    if m:
        candidate = m.group(1).strip()
        # Take first sentence of the candidate (split on sentence ends, not commas)
        first_s = re.split(r"[.\n!؟?]", candidate)[0].strip()
        if len(first_s) > 10:
            return first_s

    # Fallback: scan lines from the end to find the final conclusion
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        # Ignore markdown headers or pure bullet points without substance
        clean = line.lstrip("#-*•> ").strip()
        if len(clean) >= 15 and not clean.startswith("```"):
            return clean

    return lines[0] if lines else ""


def synthesize_dialogue_summary(assistant_text: str, max_chars: int = 140) -> Tuple[str, List[str]]:
    """Synthesizes an entity-aware compact summary of an assistant turn.

    Returns:
        (summary_string, salient_entities_list)
    """
    if not assistant_text or not isinstance(assistant_text, str):
        return "", []

    entities = extract_salient_entities(assistant_text)
    takeaway = extract_core_takeaway(assistant_text)

    # Assemble compact synthesis
    if takeaway:
        # Strip trailing punctuation for clean appending
        clean_takeaway = takeaway.rstrip(".،؛: \t\n")
        if entities:
            ent_str = ", ".join(entities[:3])
            candidate = f"{clean_takeaway} ({ent_str})"
        else:
            candidate = clean_takeaway
    elif entities:
        candidate = f"Discussion on {', '.join(entities[:4])}"
    else:
        candidate = assistant_text.strip()[:max_chars]

    if len(candidate) > max_chars:
        candidate = candidate[: max_chars - 3].rstrip() + "..."

    # Mandatory security / privacy invariant
    sanitized = redact_secrets(candidate)
    sanitized_entities = [redact_secrets(e) for e in entities if redact_secrets(e).strip()]

    return sanitized, sanitized_entities

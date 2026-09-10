"""Zero-Trust Privacy Shield: secrets never touch disk.

Two independent detectors are combined, both pure-Python and token-free:

1. **Structural patterns** — vendor key prefixes (OpenAI ``sk-``, Google ``AIza``,
   GitHub ``gh*_``, AWS ``AKIA``, Slack ``xox*``, Stripe ``sk_live``, JWTs,
   Bearer headers, PEM private-key blocks, ``KEY=value`` assignments, ...).
2. **Shannon entropy** — any long, unbroken token whose per-character entropy is
   high enough to be a credential (random base64/hex) is redacted even if we
   have never seen its vendor prefix. Ordinary words, paths, and URLs score
   well below the threshold; real secrets score above it.

Every redaction is reported so telemetry can count what was blocked without
ever storing the secret itself. Fail-closed: when in doubt we redact.
"""

from __future__ import annotations

import math
import re

# NOTE: no dataclasses/typing imports here — this module is loaded by the MCP
# daemon, whose resident-memory budget is measured in single MB.

REDACTED = "[REDACTED_SECRET]"
REDACTED_KEY = "[REDACTED_API_KEY]"

# Entropy detector tuning. 4.0 bits/char is the classic threshold for
# base64-looking credentials.
ENTROPY_THRESHOLD_BITS = 4.0
ENTROPY_MIN_LENGTH = 20

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-+/=\.]{%d,}" % ENTROPY_MIN_LENGTH)
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_HAS_LOWER_UPPER_DIGIT = re.compile(r"(?=.*[a-z])(?=.*[A-Z])(?=.*\d)")

# Ordered: PEM blocks first (DOTALL) so key bodies never survive.
STRUCTURAL_PATTERNS = [
    ("pem_block", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    # Unclosed marker fallback (fail-closed): trailing benign text loss is
    # accepted over a leak.
    ("pem_unclosed", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*", re.DOTALL)),
    ("openai", re.compile(r"\b(sk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,})\b")),
    ("anthropic", re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{20,})\b")),
    ("google", re.compile(r"\b(AIza[0-9A-Za-z\-_]{30,})\b")),
    ("github", re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{30,})\b")),
    ("github_pat", re.compile(r"\b(github_pat_[A-Za-z0-9_]{60,})\b")),
    ("aws_access", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b")),
    ("slack", re.compile(r"\b(xox[abprs]-[A-Za-z0-9\-]{10,})\b")),
    ("stripe", re.compile(r"\b([sr]k_(?:live|test)_[A-Za-z0-9]{16,})\b")),
    ("sendgrid", re.compile(r"\b(SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,})\b")),
    ("npm", re.compile(r"\b(npm_[A-Za-z0-9]{30,})\b")),
    ("huggingface", re.compile(r"\b(hf_[A-Za-z0-9]{30,})\b")),
    ("jwt", re.compile(r"\b(eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})\b")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.=]{25,}\b")),
    ("basic_auth_url", re.compile(r"(?<=://)[^\s:/@]{1,64}:[^\s/@]{4,}(?=@)")),
    # KEY=value style assignments and JSON/YAML pairs with a secret-ish key name.
    ("assignment", re.compile(
        r"(?i)\b([A-Z0-9_\-\.]*(?:api[_\-]?key|secret|token|passwd|password|private[_\-]?key|access[_\-]?key|client[_\-]?secret|auth)[A-Z0-9_\-\.]*)"
        r"(\s*[:=]\s*[\"']?)([^\s\"',;]{8,})")),
]


def shannon_entropy(s: str) -> float:
    """Bits of entropy per character (0.0 for empty / single-symbol strings)."""
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = float(len(s))
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


_SEG_OK_RE = re.compile(r"^[A-Za-z0-9_.\-~]+$")


def _wordy_segment(seg):
    """Path segments look like words: few digits, at most one case flip ("Users", "pricing_engine.py")."""
    if len(seg) < 2 or not _SEG_OK_RE.match(seg):
        return False
    digits = sum(ch.isdigit() for ch in seg)
    if digits / len(seg) > 0.3:
        return False
    flips = 0
    prev = None
    for ch in seg:
        if ch.isalpha():
            cur = ch.isupper()
            if prev is not None and cur != prev:
                flips += 1
            prev = cur
    return flips <= 1


def _looks_like_prose_or_path(token: str) -> bool:
    """Cheap negatives: URLs, file paths, dotted identifiers, repeated separators."""
    low = token.lower()
    if low.startswith(("http://", "https://", "www.")):
        return True
    if token.count("/") >= 2 and "+" not in token and "=" not in token:
        segs = [s for s in token.split("/") if s]
        # base64 also contains '/', so only treat as a path when most segments read like words
        if segs and sum(_wordy_segment(s) for s in segs) / len(segs) >= 0.75:
            return True
    if token.count(".") >= 3 and all(_wordy_segment(s) or not s for s in token.split(".")):
        return True
    if token.count("-") >= 4 and token.replace("-", "").isalpha():
        return True  # kebab-case-identifier-words
    if token.count("_") >= 3 and token.replace("_", "").isalpha():
        return True  # snake_case_identifier_words
    return False


def is_high_entropy_token(token: str) -> bool:
    """Decides whether one unbroken token is credential-shaped."""
    if len(token) < ENTROPY_MIN_LENGTH or _looks_like_prose_or_path(token):
        return False
    core = token.strip("=.-_")
    if len(core) < ENTROPY_MIN_LENGTH:
        return False
    if _HEX_RE.match(core):
        # Git SHAs, content digests and spool ids are hex and legitimately live in
        # memory; hex credentials are caught by the KEY=value assignment detector.
        return False
    ent = shannon_entropy(core)
    if ent >= ENTROPY_THRESHOLD_BITS + 0.5:
        return True
    # Mid-band tokens must also mix character classes like real random keys do.
    return ent >= ENTROPY_THRESHOLD_BITS and bool(_HAS_LOWER_UPPER_DIGIT.search(core))


def find_high_entropy_tokens(text):
    """Returns ``(start, end, entropy_bits)`` spans that trip the entropy gate."""
    hits = []
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        if is_high_entropy_token(tok):
            hits.append((m.start(), m.end(), round(shannon_entropy(tok.strip("=.-_")), 3)))
    return hits


class ShieldReport:
    """What the shield did — never contains the secrets themselves."""
    __slots__ = ("redactions", "by_detector", "max_entropy_bits")

    def __init__(self):
        self.redactions = 0
        self.by_detector = {}
        self.max_entropy_bits = 0.0

    def __repr__(self):
        return f"ShieldReport(redactions={self.redactions}, by_detector={self.by_detector})"

    @property
    def clean(self) -> bool:
        return self.redactions == 0

    def _bump(self, detector: str, n: int = 1) -> None:
        self.redactions += n
        self.by_detector[detector] = self.by_detector.get(detector, 0) + n

    def to_dict(self):
        return {"redactions": self.redactions, "by_detector": dict(self.by_detector),
                "max_entropy_bits": self.max_entropy_bits, "clean": self.clean}


def redact(text, *, entropy: bool = True, placeholder: str = REDACTED_KEY):
    """Redacts every detected secret. Returns ``(clean_text, report)``.

    Non-string / empty input returns ``("", report)`` to stay fail-closed for
    callers that forward the result straight to storage.
    """
    report = ShieldReport()
    if not text or not isinstance(text, str):
        return "", report
    out = text
    for name, pat in STRUCTURAL_PATTERNS:
        if name == "assignment":
            def _sub(m):
                report._bump("assignment")
                return f"{m.group(1)}{m.group(2)}{placeholder}"
            out, n = pat.subn(_sub, out)
        else:
            out, n = pat.subn(placeholder, out)
            if n:
                report._bump(name, n)
    if entropy:
        spans = find_high_entropy_tokens(out)
        if spans:
            pieces = []
            last = 0
            for start, end, bits in spans:
                pieces.append(out[last:start])
                pieces.append(placeholder)
                last = end
                report._bump("entropy")
                report.max_entropy_bits = max(report.max_entropy_bits, bits)
            pieces.append(out[last:])
            out = "".join(pieces)
    return out, report


def scan(text, *, entropy: bool = True) -> bool:
    """True when the text contains at least one detectable secret."""
    if not text or not isinstance(text, str):
        return False
    for _name, pat in STRUCTURAL_PATTERNS:
        if pat.search(text):
            return True
    return bool(entropy and find_high_entropy_tokens(text))


def redact_bytes(raw: bytes, *, entropy: bool = True):
    """Byte-preserving redaction for command output headed to disk.

    Uses ``surrogateescape`` so arbitrary (even non-UTF-8) bytes round-trip
    exactly when no secret is present; only the secret spans change.
    """
    text = raw.decode("utf-8", errors="surrogateescape")
    if not scan(text, entropy=entropy):
        return raw, ShieldReport()
    clean, report = redact(text, entropy=entropy)
    return clean.encode("utf-8", errors="surrogateescape"), report


def explain(text: str):
    """Diagnostic view for the dashboard sandbox: which spans trip which detector."""
    findings = []
    for name, pat in STRUCTURAL_PATTERNS:
        for m in pat.finditer(text):
            findings.append({"detector": name, "start": m.start(), "end": m.end(),
                             "length": m.end() - m.start()})
    for start, end, bits in find_high_entropy_tokens(text):
        findings.append({"detector": "entropy", "start": start, "end": end,
                         "length": end - start, "entropy_bits": bits})
    findings.sort(key=lambda f: (int(f["start"]), -int(f["length"])))
    return findings


def token_entropies(tokens):
    return [(t, round(shannon_entropy(t), 3)) for t in tokens]

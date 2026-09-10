"""GENESIS Content Compressor — bulk-aware input shrinking with byte-exact recovery.

Complements structural history stripping (tool_compactor): while the compactor
drops whole turns, this module shrinks the large TEXT blocks that remain
(logs, JSON blobs, pytest output, terminal noise).

Safety contract (fail-closed, every transform):
1. Result must be strictly smaller, else passthrough.
2. All error signatures (RE_ERROR_SIG) and pytest gate signatures (RE_PYTEST)
   present in the original must survive byte-identical in the output.
3. JSON minify is verified by re-parse equality.
4. Giant blocks (>ELIDE_THRESHOLD) keep head+tail; the elided middle must
   contain zero error/pytest signatures, and the FULL original (redacted) is
   persisted to the local recovery store with a handle embedded in the view.
5. Code-looking blocks (fences or >30% indented lines) skip blank-collapse.

Structural bounds (Rule 17): MIN_BLOCK_CHARS=500 — below ~400ch the handle
overhead exceeds any saving; ELIDE_THRESHOLD=4000 with 1500/1000 head/tail.
Recovery: ~/.genesis/recovery, 7d TTL, 50MB cap, LRU to 80% (spool pattern).
Stdlib only.
"""

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from genesis_memory.proxy.structured_distiller import RE_ERROR_SIG, RE_PYTEST
from genesis_memory.daemon.server import redact_secrets

logger = logging.getLogger("genesis.content_compressor")

MIN_BLOCK_CHARS = 500
ELIDE_THRESHOLD = 4000
ELIDE_HEAD = 1500
ELIDE_TAIL = 1000
REPEAT_RUN_MIN = 3
RECOVERY_TAG = "GENESIS-RECOVERY"

DEFAULT_RECOVERY_DIR = Path.home() / ".genesis" / "recovery"
DEFAULT_TTL_DAYS = 7.0
DEFAULT_MAX_BYTES = 50 * 1024 * 1024

RE_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()#][0-9A-Z]")
RECOVERY_ID = re.compile(r"^[0-9a-f]{16}$")


def _signatures(text: str) -> Tuple[set, Optional[str]]:
    errs = set(RE_ERROR_SIG.findall(text))
    m = RE_PYTEST.search(text)
    return errs, (m.group(0) if m else None)


def _signatures_preserved(orig: str, new: str) -> bool:
    o_errs, o_py = _signatures(orig)
    n_errs, n_py = _signatures(new)
    if not o_errs.issubset(n_errs):
        return False
    if o_py is not None and o_py not in new:
        return False
    return True


def _looks_like_code(text: str) -> bool:
    if "```" in text:
        return True
    lines = text.split("\n")
    if not lines:
        return False
    indented = sum(1 for l in lines if l.startswith(("    ", "\t")))
    return (indented / max(1, len(lines))) > 0.30


def json_minify(text: str) -> Optional[str]:
    """Lossless whitespace minify; verified by re-parse equality."""
    s = text.strip()
    if not (s.startswith("{") or s.startswith("[")):
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    try:
        out = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if len(out) >= len(text):
        return None
    try:
        if json.loads(out) != obj:
            return None
    except ValueError:
        return None
    return out


def ansi_strip(text: str) -> Optional[str]:
    out = RE_ANSI.sub("", text)
    if out == text or len(out) >= len(text):
        return None
    return out


def repeat_collapse(text: str) -> Optional[str]:
    """Collapse runs of >=3 identical consecutive non-blank lines."""
    lines = text.split("\n")
    out: List[str] = []
    i = 0
    changed = False
    while i < len(lines):
        j = i + 1
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        run = j - i
        if run >= REPEAT_RUN_MIN and lines[i].strip():
            out.append(lines[i])
            out.append(f"[... x{run} identical lines elided]")
            changed = True
        else:
            out.extend(lines[i:j])
        i = j
    if not changed:
        return None
    return "\n".join(out)


def blank_collapse(text: str) -> Optional[str]:
    if _looks_like_code(text):
        return None
    out = re.sub(r"\n{4,}", "\n\n\n", text)
    if out == text:
        return None
    return out


class RecoveryStore:
    """Content-addressed local originals with TTL + size-cap GC (spool pattern)."""

    def __init__(
        self,
        store_dir: Optional[Path] = None,
        ttl_days: float = DEFAULT_TTL_DAYS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.store_dir = Path(store_dir) if store_dir else DEFAULT_RECOVERY_DIR
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_days = ttl_days
        self.max_bytes = max_bytes
        self._writes = 0

    def store(self, text: str) -> str:
        redacted = redact_secrets(text)
        rid = hashlib.sha256(redacted.encode("utf-8")).hexdigest()[:16]
        path = self.store_dir / f"{rid}.txt"
        if not path.exists():
            tmp = self.store_dir / f"{rid}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(redacted)
            os.replace(tmp, path)
        self._writes += 1
        if self._writes % 10 == 0:
            try:
                self.prune()
            except Exception:
                logger.debug("recovery prune failed", exc_info=True)
        return rid

    def get(self, rid: str) -> Optional[str]:
        if not RECOVERY_ID.match(rid or ""):
            return None
        path = self.store_dir / f"{rid}.txt"
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def prune(self, max_age_days: Optional[float] = None, max_bytes: Optional[int] = None) -> int:
        ttl = self.ttl_days if max_age_days is None else max_age_days
        cap = self.max_bytes if max_bytes is None else max_bytes
        cutoff = time.time() - ttl * 86400
        deleted = 0
        try:
            files = [p for p in self.store_dir.iterdir() if p.suffix == ".txt"]
        except OSError:
            return 0
        for p in files:
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    deleted += 1
            except OSError:
                logger.debug("recovery unlink failed", exc_info=True)
        try:
            files = [p for p in self.store_dir.iterdir() if p.suffix == ".txt"]
            total = sum(p.stat().st_size for p in files)
        except OSError:
            return deleted
        if total > cap:
            files.sort(key=lambda p: p.stat().st_mtime)
            watermark = int(cap * 0.80)
            for p in files:
                if total <= watermark:
                    break
                try:
                    total -= p.stat().st_size
                    p.unlink(missing_ok=True)
                    deleted += 1
                except OSError:
                    logger.debug("recovery evict failed", exc_info=True)
        return deleted

    def stats(self) -> Dict[str, Any]:
        try:
            files = [p for p in self.store_dir.iterdir() if p.suffix == ".txt"]
            return {
                "recovery_files": len(files),
                "recovery_bytes": sum(p.stat().st_size for p in files),
            }
        except OSError:
            return {"recovery_files": 0, "recovery_bytes": 0}


def compress_text(
    text: str,
    recovery: Optional[RecoveryStore] = None,
    min_chars: int = MIN_BLOCK_CHARS,
) -> Tuple[str, Dict[str, Any]]:
    """Shrink one text block. Returns (possibly-unchanged text, meta).

    meta: {compressed: bool, ctype: str, saved_chars: int, recovery_id: str|None}
    Fail-closed: any violated invariant returns the original untouched.
    """
    meta: Dict[str, Any] = {
        "compressed": False,
        "ctype": "passthrough",
        "saved_chars": 0,
        "recovery_id": None,
    }
    if not isinstance(text, str) or len(text) < min_chars:
        return text, meta

    cur = text
    ctypes: List[str] = []

    mini = json_minify(cur)
    if mini is not None and _signatures_preserved(text, mini):
        cur, ctypes = mini, ["json"]

    if not ctypes:
        for fn, name in ((ansi_strip, "ansi"), (repeat_collapse, "repeat"), (blank_collapse, "blank")):
            try:
                nxt = fn(cur)
            except Exception:
                continue
            if nxt is not None and len(nxt) < len(cur) and _signatures_preserved(text, nxt):
                cur, ctypes = nxt, ctypes + [name]

    if len(cur) >= ELIDE_THRESHOLD and recovery is not None:
        head, tail = cur[:ELIDE_HEAD], cur[-ELIDE_TAIL:]
        middle = cur[ELIDE_HEAD: len(cur) - ELIDE_TAIL]
        m_errs, m_py = _signatures(middle)
        # Fail-closed: elided middle must carry zero error/pytest signatures.
        if not m_errs and m_py is None:
            try:
                rid = recovery.store(text)
            except Exception:
                rid = None
            if rid is not None:
                elided = len(middle)
                cur = (
                    f"{head}\n[... GENESIS elided {elided} chars "
                    f"({'+'.join(ctypes) if ctypes else 'raw'}); "
                    f"original {RECOVERY_TAG}:{rid} via GET /v1/recovery/{rid}]\n{tail}"
                )
                ctypes = ctypes + ["elide"]
                meta["recovery_id"] = rid

    if cur == text:
        return text, meta
    if not _signatures_preserved(text, cur):
        return text, meta
    meta.update({
        "compressed": True,
        "ctype": "+".join(ctypes) if ctypes else "shrunk",
        "saved_chars": len(text) - len(cur),
    })
    return cur, meta

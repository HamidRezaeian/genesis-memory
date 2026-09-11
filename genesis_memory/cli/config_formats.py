"""Dependency-free emitters for every config dialect an AI client might speak.

GENESIS refuses to pull in PyYAML/tomli-w just to write a handful of keys, so
this module contains small, deterministic serializers for the nested
dict/list/scalar shapes that MCP and proxy configuration actually use, plus a
marker-block merger for text formats we cannot structurally parse (YAML, TOML,
Lua, Emacs Lisp, dotenv). Marker blocks make re-runs idempotent and reverts
trivial: everything between the markers belongs to GENESIS, nothing else is
ever touched.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

MARKER_BEGIN = ">>> genesis-memory >>>"
MARKER_END = "<<< genesis-memory <<<"

_YAML_SAFE_RE = re.compile(r"^[A-Za-z0-9_./@:+-]+$")
_TOML_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# --------------------------------------------------------------------------- JSON
def to_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- YAML
def _yaml_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if s == "" or not _YAML_SAFE_RE.match(s) or s.lower() in ("true", "false", "null", "yes", "no", "on", "off", "~"):
        return json.dumps(s, ensure_ascii=False)
    if s[0] in "-?:,[]{}#&*!|>'\"%@`" or s.startswith(("0", "+")) and s.replace(".", "").isdigit():
        return json.dumps(s, ensure_ascii=False)
    return s


def to_yaml(data: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(data, dict):
        if not data:
            return pad + "{}\n"
        out: List[str] = []
        for k, v in data.items():
            key = _yaml_scalar(str(k))
            if isinstance(v, dict) and v:
                out.append(f"{pad}{key}:\n{to_yaml(v, indent + 1)}")
            elif isinstance(v, list) and v:
                out.append(f"{pad}{key}:\n{to_yaml(v, indent + 1)}")
            elif isinstance(v, (dict, list)):
                out.append(f"{pad}{key}: {'{}' if isinstance(v, dict) else '[]'}\n")
            else:
                out.append(f"{pad}{key}: {_yaml_scalar(v)}\n")
        return "".join(out)
    if isinstance(data, list):
        if not data:
            return pad + "[]\n"
        out = []
        for item in data:
            if isinstance(item, dict) and item:
                body = to_yaml(item, indent + 1)
                # first key goes on the dash line
                first, rest = body.split("\n", 1)
                out.append(f"{pad}- {first.strip()}\n{rest}" if rest.strip() else f"{pad}- {first.strip()}\n")
            elif isinstance(item, list):
                out.append(f"{pad}-\n{to_yaml(item, indent + 1)}")
            else:
                out.append(f"{pad}- {_yaml_scalar(item)}\n")
        return "".join(out)
    return pad + _yaml_scalar(data) + "\n"


# --------------------------------------------------------------------------- TOML
def _toml_key(k: str) -> str:
    return k if _TOML_BARE_KEY_RE.match(k) else json.dumps(k, ensure_ascii=False)


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_toml_key(str(k))} = {_toml_value(x)}" for k, x in v.items()) + " }"
    if v is None:
        return '""'
    return json.dumps(str(v), ensure_ascii=False)


def to_toml(data: Dict[str, Any], prefix: str = "") -> str:
    """Emits ``[a.b]`` tables for nested dicts and inline values for scalars/lists."""
    scalars: List[str] = []
    tables: List[str] = []
    for k, v in data.items():
        full = f"{prefix}.{_toml_key(str(k))}" if prefix else _toml_key(str(k))
        if isinstance(v, dict):
            tables.append(to_toml(v, full))
        else:
            scalars.append(f"{_toml_key(str(k))} = {_toml_value(v)}")
    out = ""
    if prefix:
        out += f"[{prefix}]\n"
    if scalars:
        out += "\n".join(scalars) + "\n"
    if scalars or (prefix and not tables):
        out += "\n"
    out += "".join(tables)
    return out


# --------------------------------------------------------------------------- dotenv
def to_env(data: Dict[str, Any]) -> str:
    lines = []
    for k, v in data.items():
        key = str(k)
        if not _ENV_KEY_RE.match(key):
            raise ValueError(f"invalid env var name: {key!r}")
        if isinstance(v, bool):
            val = "1" if v else "0"
        elif isinstance(v, (dict, list)):
            val = json.dumps(v, separators=(",", ":"))
        else:
            val = str(v)
        if re.search(r"[\s#\"'$`\\]", val):
            val = json.dumps(val)
        lines.append(f"{key}={val}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- marker blocks
def render_marker_block(body: str, comment: str = "#") -> str:
    body = body.rstrip("\n")
    return f"{comment} {MARKER_BEGIN}\n{body}\n{comment} {MARKER_END}\n"


def merge_marker_block(existing: str, body: str, comment: str = "#") -> str:
    """Replaces the GENESIS marker block in ``existing`` or appends one.

    Never touches a byte outside the markers, so hand-written config,
    comments and ordering are preserved (Non-Hostile Invariant).
    """
    block = render_marker_block(body, comment)
    pattern = re.compile(
        re.escape(comment) + r"[ \t]*" + re.escape(MARKER_BEGIN) + r".*?" +
        re.escape(comment) + r"[ \t]*" + re.escape(MARKER_END) + r"[ \t]*\n?",
        re.DOTALL,
    )
    if pattern.search(existing):
        return pattern.sub(lambda _m: block, existing, count=1)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing.strip():
        existing += "\n"
    return existing + block


def strip_marker_block(existing: str, comment: str = "#") -> str:
    pattern = re.compile(
        r"\n?" + re.escape(comment) + r"[ \t]*" + re.escape(MARKER_BEGIN) + r".*?" +
        re.escape(comment) + r"[ \t]*" + re.escape(MARKER_END) + r"[ \t]*\n?",
        re.DOTALL,
    )
    return pattern.sub("", existing, count=1)


def has_marker_block(existing: Optional[str], comment: str = "#") -> bool:
    return bool(existing) and MARKER_BEGIN in existing and MARKER_END in existing


# --------------------------------------------------------------------------- dispatch
FORMATS = ("json", "yaml", "toml", "env")


def render(data: Any, fmt: str) -> str:
    fmt = fmt.lower()
    if fmt == "json":
        return to_json(data)
    if fmt in ("yaml", "yml"):
        return to_yaml(data)
    if fmt == "toml":
        if not isinstance(data, dict):
            raise ValueError("TOML top level must be a table")
        return to_toml(data)
    if fmt in ("env", "dotenv"):
        if not isinstance(data, dict):
            raise ValueError("env output requires a flat mapping")
        return to_env(data)
    raise ValueError(f"unsupported format: {fmt} (choose from {', '.join(FORMATS)})")

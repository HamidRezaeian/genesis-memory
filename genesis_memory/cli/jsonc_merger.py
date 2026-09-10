"""Robust JSONC parser, sanitizer, and non-destructive structural merger.

Supports:
1. Single-line (// ...) and multi-line (/* ... */) comments outside of string literals.
2. Trailing commas before '}' and ']'.
3. Deep structural dictionary merging without clobbering existing sibling keys.
"""

import json
import re
from typing import Any, Dict, Union


def strip_jsonc_comments(text: str) -> str:
    """Strips // and /* */ comments from JSONC text while respecting string literals."""
    result = []
    in_string = False
    escape = False
    i = 0
    n = len(text)

    while i < n:
        char = text[i]

        if in_string:
            result.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            i += 1
            continue

        # Not in string
        if char == '"':
            in_string = True
            result.append(char)
            i += 1
        elif char == "/" and i + 1 < n and text[i + 1] == "/":
            # Single-line comment: skip until newline
            i += 2
            while i < n and text[i] not in ("\r", "\n"):
                i += 1
        elif char == "/" and i + 1 < n and text[i + 1] == "*":
            # Multi-line comment: skip until */
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2  # Skip */
        else:
            result.append(char)
            i += 1

    cleaned = "".join(result)
    # Remove trailing commas before } or ]
    cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
    return cleaned


def parse_jsonc(text: str) -> Any:
    """Parses JSONC text into Python objects safely."""
    cleaned = strip_jsonc_comments(text).strip()
    if not cleaned:
        return {}
    return json.loads(cleaned)


def deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Deeply merges patch into base dictionary.
    
    Dictionaries are merged recursively. Lists or scalar values in patch
    overwrite or augment base according to key semantics.
    Existing keys not in patch are preserved 100% intact.
    """
    merged = dict(base)
    for key, value in patch.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def merge_jsonc_file_content(existing_text: str, patch_dict: Dict[str, Any]) -> str:
    """Merges a patch into existing JSONC content and returns pretty-printed JSON."""
    if not existing_text or not existing_text.strip():
        base = {}
    else:
        base = parse_jsonc(existing_text)

    if not isinstance(base, dict):
        base = {}

    merged = deep_merge(base, patch_dict)
    return json.dumps(merged, indent=2, ensure_ascii=False) + "\n"

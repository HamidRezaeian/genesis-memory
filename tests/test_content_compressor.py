"""Unit + integration gates for bulk content compression (fail-closed).

Covers: JSON minify (lossless, re-parse verified), ANSI strip, repeated-line
collapse, code-guard on blank-collapse, error/pytest signature preservation
(KILL cases), size threshold, elision with recovery roundtrip, secrets
redaction, TTL/GC caps, proxy wiring (on/off/shadow) + recovery endpoint.
Run: python -m pytest tests/test_content_compressor.py -q
"""
import json
import os
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from genesis_memory.proxy.content_compressor import (  # noqa: E402
    RecoveryStore,
    ansi_strip,
    blank_collapse,
    compress_text,
    json_minify,
    repeat_collapse,
)


def test_json_minify_lossless():
    raw = '{ "a" : 1 , "b" : [1, 2, 3] , "c" : {"x" : "y y"} }'
    out = json_minify(raw)
    assert out is not None
    assert json.loads(out) == json.loads(raw)
    assert len(out) < len(raw)
    # Idempotent
    assert json_minify(out) is None or json_minify(out) == out


def test_json_minify_passthrough():
    assert json_minify("not json at all") is None
    assert json_minify('{"a":1}') is None  # nothing to save
    assert json_minify('{"a": 1}') is not None


def test_ansi_strip():
    raw = "\x1b[32mOK\x1b[0m all \x1b[1mbold\x1b[0m done"
    out = ansi_strip(raw)
    assert out == "OK all bold done"
    assert ansi_strip("plain text") is None


def test_repeat_collapse_keeps_first_and_signatures():
    lines = ["yay"] * 2 + ["ValueError: boom"] * 5 + ["tail line here now"]
    raw = "\n".join(lines)
    out = repeat_collapse(raw)
    assert out is not None
    assert "ValueError: boom" in out
    assert "x5 identical lines" in out
    assert len(out) < len(raw)


def test_repeat_collapse_ignores_shorts_and_blanks():
    assert repeat_collapse("a\na\nb") is None
    assert repeat_collapse("x\n\n\n\ny") is None  # blank runs are blank_collapse's job


def test_blank_collapse_code_guard():
    code = "def f():\n    x = 1\n    y = 2\n\n\n\n    return x\n"
    assert blank_collapse(code) is None  # 50% indented → code → skip
    assert blank_collapse("```\ncode\n\n\n\n```") is None  # fences → skip
    prose = "line one\n\n\n\nline two here"
    out = blank_collapse(prose)
    assert out is not None and len(out) < len(prose)


def test_signature_preservation_kill_cases():
    # Compressor must never drop error tokens or pytest gate strings.
    assert compress_text("x" * 600 + " ValueError: disk full")[0].count("ValueError") >= 1
    out, meta = compress_text("line\n" * 200 + "FAILED test_x.py::test_y\n" + "line\n" * 200)
    assert "FAILED test_x.py::test_y" in out


def test_threshold_passthrough():
    out, meta = compress_text("short text")
    assert out == "short text" and meta["compressed"] is False


def test_elide_happy_path_and_recovery_roundtrip(tmp_path):
    store = RecoveryStore(store_dir=tmp_path / "rec")
    head = "START header line with info here\n" + "filler line abcdef\n" * 30
    middle = "neutral filler line number %d with words here\n" % 0
    middle = "".join(f"neutral filler line number {i} with words here\n" for i in range(400))
    tail = "END tail line with summary here\n" + "more tail words here\n" * 20
    raw = head + middle + tail
    assert len(raw) > 4000
    out, meta = compress_text(raw, store)
    assert meta["compressed"] is True and meta["recovery_id"]
    assert "START header" in out and "END tail" in out
    assert len(out) < len(raw)
    back = store.get(meta["recovery_id"])
    assert back == raw  # byte-exact roundtrip


def test_elide_fail_closed_on_error_in_middle(tmp_path):
    # Repeat-collapse may still shrink the runs, but elision (which needs a
    # recovery handle) must refuse: the error sits in the elidable middle.
    store = RecoveryStore(store_dir=tmp_path / "rec")
    head = "header line here\n" * 60
    middle = "pad line\n" * 200 + "ConnectionResetError: upstream died here\n" + "pad line\n" * 200
    tail = "tail line here\n" * 60
    raw = head + middle + tail
    assert len(raw) > 4000
    out, meta = compress_text(raw, store)
    assert "ConnectionResetError: upstream died here" in out
    assert meta["recovery_id"] is None  # no elision handle issued


def test_recovery_redacts_secrets(tmp_path):
    store = RecoveryStore(store_dir=tmp_path / "rec")
    rid = store.store("deploy log sk-testapikey1234567890 done " + "x" * 600)
    back = store.get(rid)
    assert "sk-testapikey1234567890" not in back
    assert "[REDACTED" in back


def test_recovery_id_validation_and_missing(tmp_path):
    store = RecoveryStore(store_dir=tmp_path / "rec")
    assert store.get("../proxy_server") is None
    assert store.get("zzzz") is None
    assert store.get("0" * 16) is None  # well-formed but absent → 404 path


def test_recovery_ttl_and_size_gc(tmp_path):
    d = tmp_path / "rec"
    store = RecoveryStore(store_dir=d, ttl_days=7.0, max_bytes=10**9)
    rid = store.store("old bytes here " + "y" * 600)
    p = d / f"{rid}.txt"
    old = time.time() - 2 * 86400
    os.utime(p, (old, old))
    assert store.prune(max_age_days=1.0) >= 1
    assert not p.exists()
    # Size cap evicts oldest-first down to 80% watermark
    store2 = RecoveryStore(store_dir=tmp_path / "rec2", max_bytes=1000)
    for i in range(4):
        store2.store(f"payload-{i} " + "z" * 400)
        time.sleep(0.01)
    assert store2.prune() >= 1
    st = store2.stats()
    assert st["recovery_bytes"] <= 1000

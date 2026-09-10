"""R2 privacy gates: storage-boundary redaction for thread/memory paths.

- Synthetic secret fixtures must not survive in thread fields, capsules, or logs.
- Redaction precedes truncation and covers complete credential structures
  (full PEM blocks, unclosed markers fail-closed).
- Debug logs carry operational metadata, never prompt content.
- Spool is deliberately RAW (command evidence) — memory paths are redacted.
Run: python -m pytest tests/test_privacy_redaction.py -q
"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

from genesis_memory.daemon.server import (
    Store,
    redact_secrets,
    scan_secrets,
)
from genesis_memory.proxy.spool import SpoolEngine

SK = "sk-abcdefghijklmnopqrst1234"
GOOGLE = "AIza" + "a" * 30
GHP = "ghp_" + "b" * 30
AKIA = "AKIA" + "C" * 16
BEARER = "Bearer " + "c" * 32
PEM_FULL = ("-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIBOgIBAAJBAK3e\n"
            "AAAAAAAAAAAAAAAA\n"
            "-----END RSA PRIVATE KEY-----")
PEM_UNCLOSED = "note -----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK3e"


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path / "privacy.db"))


def test_scan_detects_all_fixtures():
    for secret in (SK, GOOGLE, GHP, AKIA, BEARER, PEM_FULL, PEM_UNCLOSED):
        assert scan_secrets(f"prefix {secret} suffix") is True
    assert scan_secrets("plain operational note about billing") is False


def test_redact_removes_bodies_not_just_markers():
    out = redact_secrets(f"a {SK} b {GHP} c")
    assert SK not in out and GHP not in out
    out = redact_secrets(f"a {PEM_FULL} b")
    assert "MIIBOgIBAAJBAK3e" not in out
    assert "AAAAAAAAAAAAAAAA" not in out
    assert "BEGIN" not in out and "PRIVATE KEY" not in out
    assert out.count("[REDACTED_API_KEY]") == 1  # one block → one marker


def test_redact_unclosed_marker_fail_closed():
    out = redact_secrets(PEM_UNCLOSED)
    assert "MIIBOgIBAAJBAK3e" not in out
    assert "BEGIN" not in out


def test_thread_fields_redacted_roundtrip(store):
    store.set_thread(
        topic=f"Auth outage {SK}",
        summary=f"Rotate {GHP} and {PEM_FULL}",
        recent_files=[f"/tmp/{AKIA}.log"],
        pending_focus=f"Check {BEARER} quota",
        client="ci",
    )
    got = store.get_thread()
    blob = " ".join(str(got.get(k, "")) for k in
                    ("topic", "summary", "recent_files", "pending_focus"))
    for secret in (SK, GHP, AKIA, "MIIBOgIBAAJBAK3e", "PRIVATE KEY", BEARER.split()[1]):
        assert secret not in blob
    assert "Rotate" in got["summary"]  # benign context preserved


def test_plugin_template_logs_metadata_not_content():
    src = (REPO / "genesis_memory" / "cli" / "client_registry.py").read_text(encoding="utf-8")
    assert "query.substring(0, 60)" not in src
    assert "content never logged" in src


def test_spool_is_deliberately_raw(tmp_path):
    """Spool captures command evidence byte-identical (GC'd, TTL'd); only
    memory/ledger paths redact. This test pins that distinction."""
    engine = SpoolEngine(spool_dir=tmp_path / "spool")
    raw = f"export KEY={SK}\n"
    spool_id, _ = engine.write_spool(raw, command="env")
    assert engine.read_spool_raw_bytes(spool_id).decode() == raw

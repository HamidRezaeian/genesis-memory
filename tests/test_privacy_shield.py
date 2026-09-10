"""Zero-Trust Privacy Shield: structural + Shannon-entropy secret detection."""

from __future__ import annotations

import base64
import secrets

import pytest

from genesis_memory.core import privacy_shield as ps
from genesis_memory.daemon.server import Store, redact_secrets, scan_secrets


def test_shannon_entropy_basics():
    assert ps.shannon_entropy("") == 0.0
    assert ps.shannon_entropy("aaaaaaaa") == 0.0
    assert ps.shannon_entropy("ab") == 1.0
    assert 3.9 < ps.shannon_entropy("abcdefghijklmnop") <= 4.0
    rnd = base64.b64encode(secrets.token_bytes(48)).decode()
    assert ps.shannon_entropy(rnd) > 4.5


_RNG = __import__("random").Random(20260910)


def _b64(n):
    return base64.b64encode(_RNG.randbytes(n)).decode()


def _urlsafe(n):
    return base64.urlsafe_b64encode(_RNG.randbytes(n)).decode().rstrip("=")


@pytest.mark.parametrize("token", [
    _b64(24), _b64(32), _urlsafe(32), _urlsafe(48),
    "hno9rk7fFoVWFCicqvyGdBtrTLhVbDzsWr9Ufi972Bs",
    "DxGXgEsEvJ/HCfbl/h/gnF8P2FvEUukz",  # base64 with slashes must not pass as a path
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
])
def test_random_credentials_trip_entropy_gate(token):
    assert ps.is_high_entropy_token(token)
    clean, rep = ps.redact(f"token {token} end")
    assert token not in clean
    assert rep.by_detector.get("entropy") == 1
    assert rep.max_entropy_bits >= ps.ENTROPY_THRESHOLD_BITS


@pytest.mark.parametrize("benign", [
    "plain operational note about billing",
    "Use SubconsciousMemoryHook2024 for the migration",
    "commit ee98d3ed628aae3f221b7aea3020f49f8f1784f5abbdf0fb50a09fd1d8d6e834 fixed it",
    "https://github.com/HamidRezaeian/genesis-memory/blob/main/README.md",
    "/Users/dev/genesis-memory/genesis_memory/proxy/pricing_engine.py",
    "/Users/irezaeian/Library/Application/Code/User/globalStorage/saoudrizwan.claude-dev/settings",
    "genesis_memory.proxy.pytest_spool ALLOWLIST_SUBCOMMANDS execute_spooled",
    "genesis_memory.cli.client_registry.ClientRegistry.discover_all",
    "ThisIsAVeryLongCamelCaseIdentifierNameThatIsNotASecret",
    "PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 npm_config_yes=true",
    "id=hook:opencode:1789077178190:abc123 status=active",
    "DATABASE_URL=postgres://localhost/genesis",
    "some-kebab-case-identifier-with-many-parts-here",
])
def test_no_false_positives_on_code_and_prose(benign):
    clean, rep = ps.redact(benign)
    assert rep.clean, (benign, rep.by_detector)
    assert clean == benign
    assert ps.scan(benign) is False


@pytest.mark.parametrize("secret,detector", [
    ("sk-abcdefghijklmnopqrst1234", "openai"),
    ("sk-proj-" + "a1B2" * 8, "openai"),
    ("AIza" + "a" * 30, "google"),
    ("ghp_" + "b" * 30, "github"),
    ("AKIA" + "C" * 16, "aws_access"),
    ("ASIA" + "D" * 16, "aws_access"),
    ("xox" + "b-1234567890-abcdefghijklmnop", "slack"),  # built at runtime: keeps push-protection quiet
    ("sk_live_" + "z" * 24, "stripe"),
    ("npm_" + "q" * 36, "npm"),
    ("hf_" + "H" * 34, "huggingface"),
    ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "jwt"),
    ("Bearer " + "c" * 32, "bearer"),
])
def test_structural_detectors(secret, detector):
    text = f"prefix {secret} suffix"
    assert ps.scan(text)
    clean, rep = ps.redact(text)
    assert secret not in clean
    assert detector in rep.by_detector, rep.by_detector
    assert clean.startswith("prefix ") and clean.endswith(" suffix")


def test_assignment_detector_keeps_key_redacts_value():
    clean, rep = ps.redact("API_KEY=supersecretvalue123\npassword: hunter2hunter2\n")
    assert "supersecretvalue123" not in clean and "hunter2hunter2" not in clean
    assert "API_KEY=" in clean and "password: " in clean
    assert rep.by_detector["assignment"] == 2


def test_basic_auth_in_url_redacted():
    clean, rep = ps.redact("clone https://user:passw0rd123@host.example.com/x.git")
    assert "passw0rd123" not in clean
    assert "@host.example.com/x.git" in clean
    assert "basic_auth_url" in rep.by_detector


def test_pem_block_body_never_survives():
    pem = ("-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK3e\nAAAA\n-----END RSA PRIVATE KEY-----")
    clean, rep = ps.redact(f"a {pem} b")
    assert "MIIBOgIBAAJBAK3e" not in clean and "BEGIN" not in clean
    assert clean.count(ps.REDACTED_KEY) == 1
    assert rep.by_detector["pem_block"] == 1


def test_redact_bytes_roundtrips_non_utf8_when_clean():
    raw = b"\xff\xfe\x00 binary blob " + b"\x89PNG" + b" 12 passed\n"
    out, rep = ps.redact_bytes(raw)
    assert out == raw and rep.clean


def test_redact_bytes_scrubs_secret_and_reports():
    raw = b"export OPENAI_API_KEY=sk-abcdefghijklmnopqrst1234\nok\n"
    out, rep = ps.redact_bytes(raw)
    assert b"sk-abcdefghijklmnopqrst1234" not in out
    assert b"ok\n" in out
    assert not rep.clean


def test_explain_reports_spans_without_leaking():
    text = "k sk-abcdefghijklmnopqrst1234 and " + secrets.token_urlsafe(32)
    findings = ps.explain(text)
    detectors = {f["detector"] for f in findings}
    assert {"openai", "entropy"} <= detectors
    for f in findings:
        assert set(f) >= {"detector", "start", "end", "length"}
        assert text[f["start"]:f["end"]] not in str({k: v for k, v in f.items() if k != "start"})


def test_daemon_delegates_to_shield():
    rnd = secrets.token_urlsafe(32)
    assert scan_secrets(f"note {rnd}") is True
    assert rnd not in redact_secrets(f"note {rnd}")
    assert redact_secrets(None) == ""
    assert redact_secrets("benign") == "benign"


def test_store_rejects_high_entropy_payload(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    try:
        with pytest.raises(ValueError):
            store.remember("deploy token " + secrets.token_urlsafe(32))
        assert store.calls["secrets_blocked"] >= 1
        assert store.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    finally:
        store.db.close()


def test_report_to_dict_shape():
    _clean, rep = ps.redact("Bearer " + "c" * 32)
    d = rep.to_dict()
    assert d["redactions"] == 1 and d["clean"] is False
    assert d["by_detector"] == {"bearer": 1}
    assert "max_entropy_bits" in d
    assert "ShieldReport" in repr(rep)

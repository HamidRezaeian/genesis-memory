"""Tests for GENESIS Lossless Spooling Engine and Deterministic Summary Parser.

Validates OpenCode's 4 non-negotiable caveats:
1. Byte-for-byte dereference roundtrip fidelity (zero hallucination).
2. Deterministic summary parser with golden pytest fixtures (counts + node IDs + tracebacks).
3. Windows encoding (UTF-8, cp1252, Persian/emojis, CRLF) and atomic write safety.
4. Retention GC (TTL + size caps) and live fetch-rate metric accounting.
"""

import json
import os
from pathlib import Path
import sys
import time
import pytest

REPO = Path(__file__).resolve().parent.parent

from genesis_memory.proxy.spool import SpoolEngine
from genesis_memory.proxy.summary_parser import parse_pytest_summary, parse_generic_summary
from genesis_memory.daemon.server import Store, handle


def test_spool_atomic_roundtrip_byte_fidelity(tmp_path: Path) -> None:
    """Verifies that spooled content is written atomically and read back byte-for-byte."""
    engine = SpoolEngine(spool_dir=tmp_path)
    payload_str = "Line 1: Initializing test\nLine 2: Running assert 1 == 1\nLine 3: Finished."
    payload_bytes = payload_str.encode("utf-8")

    spool_id, log_path = engine.write_spool(payload_bytes, command="pytest tests/ -v", exit_code=0)

    assert log_path.exists()
    assert len(spool_id) == 8
    # Ensure no leftover temporary files in directory
    assert len(list(tmp_path.glob("*.tmp*"))) == 0

    # Read raw bytes: guaranteed 100% byte fidelity
    retrieved_bytes = engine.read_spool_raw_bytes(spool_id)
    assert retrieved_bytes == payload_bytes

    # Read formatted
    read_res = engine.read_spool(spool_id, offset=0, max_lines=10)
    assert read_res["content"] == payload_str
    assert read_res["total_lines"] == 3
    assert read_res["returned_lines"] == 3
    assert read_res["exit_code"] == 0
    assert read_res["is_truncated"] is False


def test_spool_windows_encoding_mojibake_and_crlf(tmp_path: Path) -> None:
    """Verifies safe handling of non-UTF8/cp1252 bytes, CRLF line endings, and Persian text."""
    engine = SpoolEngine(spool_dir=tmp_path)
    # Mixed Persian, emoji, Windows CRLF, and raw Latin-1 bytes
    persian_text = "خطای محاسباتی در سیستم: مقدار صفر غیرمجاز است ❌\r\nLine 2: Windows CRLF test\r\n"
    mixed_bytes = persian_text.encode("utf-8") + b"\x96\x97\r\n"

    spool_id, log_path = engine.write_spool(mixed_bytes, command="run_calc.cmd")

    # Byte fidelity preserved regardless of weird bytes
    assert engine.read_spool_raw_bytes(spool_id) == mixed_bytes

    # String dereference survives without crash
    read_res = engine.read_spool(spool_id)
    assert "خطای محاسباتی" in read_res["content"]
    assert "Windows CRLF test" in read_res["content"]


def test_spool_retention_ttl_and_lru_size_caps(tmp_path: Path) -> None:
    """Verifies that expired spools are pruned and size caps evict oldest files first."""
    # 1. Test TTL expiration
    engine = SpoolEngine(spool_dir=tmp_path, ttl_days=1.0)
    id1, path1 = engine.write_spool("old log", command="cmd1")
    id2, path2 = engine.write_spool("new log", command="cmd2")

    # Artificially age path1 back 2 days
    old_time = time.time() - (2.0 * 86400.0)
    os.utime(path1, (old_time, old_time))
    meta1 = tmp_path / f"{id1}.meta.json"
    if meta1.exists():
        os.utime(meta1, (old_time, old_time))

    deleted = engine.prune(max_age_days=1.0)
    assert deleted >= 1
    assert not path1.exists()
    assert path2.exists()

    # 2. Test Size Cap Eviction (LRU)
    cap_dir = tmp_path / "cap_test"
    engine_cap = SpoolEngine(spool_dir=cap_dir, max_bytes=1000)

    # Write three 400-byte files (total 1200 bytes > 1000 byte cap)
    spool_a, path_a = engine_cap.write_spool("A" * 400)
    time.sleep(0.01)
    spool_b, path_b = engine_cap.write_spool("B" * 400)
    time.sleep(0.01)
    spool_c, path_c = engine_cap.write_spool("C" * 400)

    deleted_cap = engine_cap.prune(max_bytes=1000)
    assert deleted_cap >= 1
    # Oldest file (A) should have been evicted first
    assert not path_a.exists()
    assert path_c.exists()


def test_spool_fetch_rate_metric_accounting(tmp_path: Path) -> None:
    """Verifies that spools_created and spools_dereferenced accurately compute fetch_rate_pct."""
    db_file = tmp_path / "test_counters.db"
    store = Store(str(db_file))
    engine = SpoolEngine(spool_dir=tmp_path / "spool", store=store)

    # Initially 0
    telemetry = engine.get_telemetry()
    assert telemetry["spools_created"] == 0
    assert telemetry["spools_dereferenced"] == 0
    assert telemetry["fetch_rate_pct"] == 0.0

    # Create 5 spools
    ids = []
    for i in range(5):
        sid, _ = engine.write_spool(f"Log content {i}")
        ids.append(sid)

    telemetry = engine.get_telemetry()
    assert telemetry["spools_created"] == 5
    assert telemetry["spools_dereferenced"] == 0
    assert telemetry["fetch_rate_pct"] == 0.0

    # Dereference 1 spool -> fetch rate becomes 20%
    engine.read_spool(ids[0])
    telemetry = engine.get_telemetry()
    assert telemetry["spools_dereferenced"] == 1
    assert telemetry["fetch_rate_pct"] == 20.0

    # Dereference another -> fetch rate becomes 40%
    engine.read_spool_raw_bytes(ids[1])
    telemetry = engine.get_telemetry()
    assert telemetry["spools_dereferenced"] == 2
    assert telemetry["fetch_rate_pct"] == 40.0


def test_deterministic_pytest_summary_golden_fixtures() -> None:
    """Validates deterministic summary parser against 4 golden pytest output fixtures."""
    # Golden 1: All Passed
    golden_passed = """
============================= test session starts =============================
platform win32 -- Python 3.12.8, pytest-7.4.4, pluggy-1.0.0
rootdir: C:\\Users\\Hamid\\source\\repos\\GENESIS
collected 54 items

tests\\test_one.py ...................................................... [100%]

============================= 54 passed in 2.79s ==============================
"""
    summary1 = parse_pytest_summary(golden_passed, exit_code=0, spool_id="a1b2c3d4")
    assert "pytest: PASSED (exit_code=0)" in summary1
    assert "54 passed in 2.79s" in summary1
    assert "ctx:log/a1b2c3d4" in summary1
    assert "genesis_log(id='a1b2c3d4')" in summary1

    # Golden 2: Multi-Test Failures with Short Tracebacks
    golden_failed = """
============================= test session starts =============================
platform win32 -- Python 3.12.8
collected 10 items

tests/test_tax.py .F...F..
=================================== FAILURES ===================================
_________________________________ test_zero ____________________________________
tests/test_tax.py:12: in test_zero
    assert calc(0) == 0
E   ZeroDivisionError: division by zero
_________________________________ test_rate ____________________________________
tests/test_tax.py:24: in test_rate
    assert calc(100) == 15
E   AssertionError: assert 10 == 15
=========================== short test summary info ===========================
FAILED tests/test_tax.py::test_zero - ZeroDivisionError: division by zero
FAILED tests/test_tax.py::test_rate - AssertionError: assert 10 == 15
========================= 2 failed, 8 passed in 0.45s =========================
"""
    summary2 = parse_pytest_summary(golden_failed, exit_code=1, spool_id="e5f6g7h8")
    assert "pytest: FAILED (exit_code=1)" in summary2
    assert "2 failed, 8 passed in 0.45s" in summary2
    assert "FAILED tests/test_tax.py::test_zero" in summary2
    assert "FAILED tests/test_tax.py::test_rate" in summary2
    assert "ZeroDivisionError" in summary2
    assert "ctx:log/e5f6g7h8" in summary2
    assert "genesis_log(id='e5f6g7h8')" in summary2
    # Bounded to < 10 lines
    assert len(summary2.splitlines()) <= 8

    # Golden 3: Syntax/Collection Fatal Crash (no short test summary info)
    golden_crash = """
============================= test session starts =============================
platform win32 -- Python 3.12.8
collected 0 items / 1 error

==================================== ERRORS ====================================
________________________ ERROR collecting tests/test_bad.py ____________________
tests/test_bad.py:4: in <module>
    import nonexistent_module_xyz
E   ModuleNotFoundError: No module named 'nonexistent_module_xyz'
=========================== 1 error in 0.08s ==================================
"""
    summary3 = parse_pytest_summary(golden_crash, exit_code=2, spool_id="c1c2c3c4")
    assert "pytest: FAILED (exit_code=2)" in summary3
    assert "1 error in 0.08s" in summary3
    assert "ModuleNotFoundError" in summary3
    assert "ctx:log/c1c2c3c4" in summary3
    assert "genesis_log(id='c1c2c3c4')" in summary3


def test_generic_command_summary_parser() -> None:
    """Verifies that generic commands truncate middle lines when exceeding threshold."""
    # Short output: returned as-is
    short_out = "line 1\nline 2\nline 3"
    res_short = parse_generic_summary(short_out, exit_code=0, spool_id="s1s2s3s4", command="git status")
    assert "line 1" in res_short
    assert "line 3" in res_short
    assert "Truncated" not in res_short

    # Long output: truncated with header, tail, and spool handle
    long_out = "\n".join([f"Output line {i}" for i in range(100)])
    res_long = parse_generic_summary(long_out, exit_code=0, spool_id="l1l2l3l4", command="npm run build", head_lines=3, tail_lines=3)
    assert "Command: npm run build" in res_long
    assert "Output line 0" in res_long
    assert "Output line 2" in res_long
    assert "Truncated 94 lines" in res_long
    assert "Output line 99" in res_long
    assert "genesis_log(id='l1l2l3l4')" in res_long


def test_genesis_log_mcp_tool_dereference_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that the genesis_log tool in genesis_daemon successfully dereferences spools."""
    db_file = tmp_path / "daemon.db"
    spool_dir = tmp_path / "spool"
    monkeypatch.setenv("GENESIS_SPOOL_DIR", str(spool_dir))

    store = Store(str(db_file))
    engine = SpoolEngine(spool_dir=spool_dir, store=store)

    spool_id, _ = engine.write_spool(
        "Header\nKey detail: secret_param=42\nTrailer line",
        command="pytest",
        exit_code=0,
    )

    # Call genesis_log tool via JSON-RPC handle
    req = {
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tools/call",
        "params": {
            "name": "genesis_log",
            "arguments": {"id": spool_id},
        },
    }
    resp = handle(store, req)
    assert resp is not None
    assert "result" in resp
    text_json = json.loads(resp["result"]["content"][0]["text"])
    assert text_json["id"] == spool_id
    assert "secret_param=42" in text_json["content"]
    assert text_json["total_lines"] == 3

    # Call with grep filter
    req_grep = {
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tools/call",
        "params": {
            "name": "genesis_log",
            "arguments": {"id": spool_id, "grep": "secret_param"},
        },
    }
    resp_grep = handle(store, req_grep)
    text_grep = json.loads(resp_grep["result"]["content"][0]["text"])
    assert text_grep["matched_lines"] == 1
    assert text_grep["content"].strip() == "Key detail: secret_param=42"


def test_spool_500_line_gate_and_chunked_escape(tmp_path: Path) -> None:
    """Verifies that large logs (>500 lines) trigger actionable refusal unless full='chunked'."""
    engine = SpoolEngine(spool_dir=tmp_path / "spool")
    # Generate 600-line log with an embedded error
    lines = [f"Normal execution line {i}" for i in range(600)]
    lines[150] = "FAILED tests/test_core.py::test_alpha - AssertionError"
    raw = "\n".join(lines)
    spool_id, _ = engine.write_spool(raw, command="pytest", exit_code=1)

    # Call without grep or offset -> Trigger Gate
    refused = engine.read_spool(spool_id)
    assert refused.get("refused") is True
    assert refused["total_lines"] == 600
    assert len(refused["anchors"]) >= 1
    assert refused["anchors"][0]["line"] == 151
    assert "escape" in refused
    assert refused["escape"]["full"] == "chunked"

    # Call with offset -> Bypasses Gate
    sliced = engine.read_spool(spool_id, offset=150, max_lines=5)
    assert sliced.get("refused") is None
    assert "FAILED tests/test_core.py::test_alpha" in sliced["content"]

    # Call with full='chunked' and reason -> Chunked Pagination
    chunked = engine.read_spool(spool_id, full="chunked", reason="Need full sequential audit", page=0)
    assert chunked.get("refused") is None
    assert chunked["page"] == 0
    assert chunked["total_pages"] == 2
    assert chunked["next_page"] == 1
    assert chunked["returned_lines"] == 400


def test_spool_byte_metrics_and_deduplication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that spooled_bytes and dereferenced_bytes are accounted accurately without double-counting."""
    spool_dir = tmp_path / "spool"
    monkeypatch.setenv("GENESIS_SPOOL_DIR", str(spool_dir))
    db_file = tmp_path / "test_dedup.db"
    store = Store(str(db_file))
    engine = SpoolEngine(spool_dir=spool_dir, store=store)

    content = "Hello world! 1234567890"  # 23 bytes
    spool_id, _ = engine.write_spool(content)

    # Calling Store.read_spool should increment dereference counter EXACTLY ONCE
    store.read_spool(spool_id, offset=0, lines=10)
    st = store.status()
    assert st["calls"]["spools_dereferenced"] == 1

    telem = engine.get_telemetry()
    assert telem["spools_dereferenced"] == 1
    assert telem["spools_created"] == 1
    assert telem["spooled_bytes"] == len(content.encode("utf-8"))
    assert telem["dereferenced_bytes"] > 0
    assert telem["byte_deref_ratio_pct"] > 0.0


def test_genesis_run_allowlist_and_execution() -> None:
    """Verifies that genesis run executes allowlisted commands, spools output, and preserves exit code."""
    from genesis_memory.cli.run import is_spoolable_command, execute_spooled

    assert is_spoolable_command(["pytest", "tests/"]) is True
    assert is_spoolable_command(["python", "-m", "pytest"]) is True
    assert is_spoolable_command(["git", "status"]) is True
    assert is_spoolable_command(["npm", "test"]) is True
    assert is_spoolable_command(["cargo", "test"]) is True
    assert is_spoolable_command(["vim", "file.py"]) is False
    assert is_spoolable_command(["python", "interactive.py"]) is False

    # Execute a safe allowlisted command (git status)
    ret = execute_spooled(["git", "status"])
    assert ret == 0


def test_pytest_spool_plugin_metadata_parsing() -> None:
    """Verifies that structured __GENESIS_PYTEST_META__ sentinel is parsed with 100% fidelity."""
    sentinel_output = """
============================= test session starts =============================
tests/test_foo.py .F
__GENESIS_PYTEST_META__={"exit_code": 1, "passed": 1, "failed": 1, "skipped": 0, "failures": [{"nodeid": "tests/test_foo.py::test_bar", "location": "tests/test_foo.py:10", "error": "assert 1 == 2"}]}
"""
    summary = parse_pytest_summary(sentinel_output, exit_code=1, spool_id="m1m2m3m4")
    assert "pytest: FAILED (exit_code=1)" in summary
    assert "Passed: 1, Failed: 1, Skipped: 0" in summary
    assert "tests/test_foo.py::test_bar (tests/test_foo.py:10) — assert 1 == 2" in summary
    assert "ctx:log/m1m2m3m4" in summary


def test_spool_id_validation_rejects_escape(tmp_path: Path) -> None:
    """R3: only engine-generated 8-hex ids; paths, absolutes, separators,
    malformed ids and symlink escapes are rejected before file access."""
    engine = SpoolEngine(spool_dir=tmp_path)
    outside = tmp_path / "outside.log"
    outside.write_text("SENSITIVE OUTSIDE", encoding="utf-8")

    for bad in ["../outside", "..\\outside", "/abs/path", "a/b",
                "ABCDEF12", "short", "toolong123", "", "dead beef",
                "........", None, 12345]:
        with pytest.raises(ValueError, match="^invalid spool id$"):
            engine.read_spool(bad)
        with pytest.raises(ValueError, match="^invalid spool id$"):
            engine.read_spool_raw_bytes(bad)

    # Symlink pointing inside is still rejected (policy: no symlinks)
    link = tmp_path / "aaaaaaaa.log"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="^invalid spool id$"):
        engine.read_spool_raw_bytes("aaaaaaaa")


def test_spool_valid_ids_still_readable(tmp_path: Path) -> None:
    """R3: legit generated ids work through text, raw-byte and MCP paths."""
    engine = SpoolEngine(spool_dir=tmp_path)
    spool_id, _ = engine.write_spool("hello spool world", command="echo hi")
    assert engine.read_spool_raw_bytes(spool_id) == b"hello spool world"
    res = engine.read_spool(spool_id)
    assert "hello spool world" in res["content"]
    # MCP-bounded error for unknown-but-wellformed id (not a traceback)
    with pytest.raises(FileNotFoundError):
        engine.read_spool("ffffffff")


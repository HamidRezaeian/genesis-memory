"""GENESIS Deterministic Summary Parser — LLM-Free Output Compaction.

Implements OpenCode's locked summary specification:
- Exit code + overall counts
- Failing test node IDs
- Short failure traceback / error reason
- Bounded to 5-10 lines total
- Never drops failing node IDs
- Lossless spool reference pointer [ctx:log/<id>]
"""

import json
import re
from typing import Dict, List, Optional, Tuple


def parse_pytest_summary(raw_output: str, exit_code: int, spool_id: str) -> str:
    """Parses pytest stdout/stderr into a deterministic, bounded summary (<10 lines).

    Supports structured sentinel metadata (__GENESIS_PYTEST_META__=) emitted by
    genesis.proxy.pytest_spool plugin with graceful regex fallback.
    """
    raw_size_kb = round(len(raw_output.encode("utf-8")) / 1024, 1)
    lines = raw_output.splitlines()
    handle_line = (
        f"[full log: ctx:log/{spool_id} ({len(lines)} lines / {raw_size_kb} KB) — "
        f"tool: genesis_log(id='{spool_id}')]"
    )

    # 1. Check for structured plugin metadata sentinel (zero-regex priority)
    for line in lines:
        sline = line.strip()
        if sline.startswith("__GENESIS_PYTEST_META__="):
            json_str = sline[len("__GENESIS_PYTEST_META__="):].strip()
            try:
                meta = json.loads(json_str)
                p_code = meta.get("exit_code", exit_code)
                passed = meta.get("passed", 0)
                failed = meta.get("failed", 0)
                skipped = meta.get("skipped", 0)
                counts_str = f"Passed: {passed}, Failed: {failed}, Skipped: {skipped}"

                if p_code == 0:
                    return f"pytest: PASSED (exit_code=0) {handle_line}\n{counts_str}"

                res = [
                    f"pytest: FAILED (exit_code={p_code}) {handle_line}",
                    counts_str,
                ]
                failures = meta.get("failures", [])
                if failures:
                    res.append("Failing tests:")
                    for f in failures[:8]:
                        loc = f.get("location", "")
                        node = f.get("nodeid", "")
                        err = f.get("error", "")
                        err_snippet = f" — {err}" if err else ""
                        res.append(f"  • {node} ({loc}){err_snippet}")
                    if len(failures) > 8:
                        res.append(f"  • ... and {len(failures) - 8} more failures spooled.")
                elif p_code == 2:
                    res.append("Error: Test collection failed (syntax/import/fixture error before test execution).")
                return "\n".join(res)
            except Exception:
                pass

    # 2. Fallback to terminal output parsing
    # Extract final summary line (e.g., "=== 2 failed, 40 passed in 1.23s ===")
    counts_line = ""
    for line in reversed(lines):
        line_clean = line.strip().strip("=")
        if any(keyword in line_clean for keyword in ("passed", "failed", "error", "skipped", "collected")):
            counts_line = line.strip()
            break

    # Extract failing node IDs from short test summary info
    # Format: "FAILED tests/test_foo.py::test_bar - AssertionError: ..."
    failing_nodes: List[str] = []
    short_summary_idx = -1
    for i, line in enumerate(lines):
        if "short test summary info" in line.lower():
            short_summary_idx = i
            break

    if short_summary_idx != -1:
        for line in lines[short_summary_idx + 1:]:
            line_str = line.strip()
            if line_str.startswith("FAILED "):
                failing_nodes.append(line_str)
            elif line_str.startswith("ERROR "):
                failing_nodes.append(line_str)
            elif line_str.startswith("==="):
                break

    # Fallback: scan for any line starting with FAILED if short summary section was missing
    if not failing_nodes:
        for line in lines:
            line_str = line.strip()
            if line_str.startswith("FAILED ") and "::" in line_str:
                failing_nodes.append(line_str)

    # Extract short error reasons from FAILURES block if failing_nodes don't have descriptions
    extracted_reasons: Dict[str, str] = {}
    failure_line_anchors: Dict[str, int] = {}
    current_failing_test = ""
    for idx, line in enumerate(lines, 1):
        match_failure_header = re.match(r"^_{3,}\s+(.+?)\s+_{3,}$", line.strip())
        if match_failure_header:
            current_failing_test = match_failure_header.group(1).strip()
            failure_line_anchors[current_failing_test] = idx
            continue

        if current_failing_test:
            # Look for pytest 'E   ' error lines or failure location (path.py:123: AssertionError)
            if line.startswith("E   "):
                extracted_reasons[current_failing_test] = line.strip()
            elif re.search(r"^\S+\.py:\d+:\s+\w+", line.strip()) and current_failing_test not in extracted_reasons:
                extracted_reasons[current_failing_test] = line.strip()

    # Build locked summary with byte & line metrics
    raw_size_kb = round(len(raw_output.encode("utf-8")) / 1024, 1)
    handle_line = (
        f"[full log: ctx:log/{spool_id} ({len(lines)} lines / {raw_size_kb} KB) — "
        f"tool: genesis_log(id='{spool_id}')]"
    )

    if exit_code == 0:
        res = [
            f"pytest: PASSED (exit_code=0) {handle_line}",
            counts_line or "All tests passed successfully.",
        ]
        return "\n".join(res)

    res = [
        f"pytest: FAILED (exit_code={exit_code}) {handle_line}",
        counts_line or f"Tests failed with exit code {exit_code}.",
    ]

    if failing_nodes:
        res.append("Failing tests:")
        for fn in failing_nodes[:8]:  # Bound to top 8 failing nodes
            # If node doesn't have reason inline, check extracted_reasons
            node_clean = fn
            anchor = ""
            for test_name, reason in extracted_reasons.items():
                if test_name in fn:
                    if " - " not in fn:
                        node_clean = f"{fn} — {reason}"
                    break
            for test_name, lnum in failure_line_anchors.items():
                if test_name in fn:
                    anchor = f" [@L{lnum}]"
                    break
            res.append(f"  • {node_clean}{anchor}")

        if len(failing_nodes) > 8:
            res.append(f"  • ... and {len(failing_nodes) - 8} more failures spooled.")
    else:
        # Syntax error, collection error, or fatal crash
        if exit_code == 2:
            res.append("Error: Test collection failed (syntax/import/fixture error before test execution).")
        error_lines = [l.strip() for l in lines if any(k in l.lower() for k in ("error:", "syntaxerror", "importerror", "modulenotfounderror"))]
        if error_lines:
            res.append(f"Failure reason: {error_lines[-1]}")
        else:
            # Fallback to last non-empty line
            non_empty = [l.strip() for l in lines if l.strip()]
            tail = non_empty[-1] if non_empty else "Non-zero exit code"
            res.append(f"Failure output tail (parsed=false — summary is a tail, not a verdict): {tail}")

    return "\n".join(res)


def parse_generic_summary(
    raw_output: str,
    exit_code: int,
    spool_id: str,
    command: str = "",
    head_lines: int = 4,
    tail_lines: int = 4,
) -> str:
    """Summarizes arbitrary command output if it exceeds threshold, with line-anchored error scanning."""
    lines = raw_output.splitlines()
    total = len(lines)
    raw_size_kb = round(len(raw_output.encode("utf-8")) / 1024, 1)
    handle_line = (
        f"[full log: ctx:log/{spool_id} ({total} lines / {raw_size_kb} KB) — "
        f"tool: genesis_log(id='{spool_id}')]"
    )

    # Short output: no truncation needed
    if total <= (head_lines + tail_lines + 4):
        cmd_prefix = f"[{command}] " if command else ""
        return f"{cmd_prefix}(exit_code={exit_code}) {handle_line}\n{raw_output}".strip()

    head = lines[:head_lines]
    tail = lines[-tail_lines:]

    # Scan for critical indicator lines in the middle (e.g. tsc, cargo, npm, ruff)
    middle_errors: List[str] = []
    error_pattern = re.compile(r"(error|fail|traceback|exception|fatal)", re.IGNORECASE)
    for idx, line in enumerate(lines[head_lines:-tail_lines], head_lines + 1):
        if error_pattern.search(line):
            middle_errors.append(f"  • [@L{idx}] {line.strip()[:120]}")
            if len(middle_errors) >= 8:
                break

    cmd_str = f"Command: {command}\n" if command else ""
    res = [
        f"{cmd_str}Exit code: {exit_code} {handle_line}",
        "\n".join(head),
    ]
    if middle_errors:
        res.append("Key indicator lines:")
        res.extend(middle_errors)
    res.append(f"... [Truncated {total - len(head) - len(tail)} lines. Use genesis_log(id='{spool_id}') to view full output] ...")
    res.append("\n".join(tail))
    return "\n".join(res)


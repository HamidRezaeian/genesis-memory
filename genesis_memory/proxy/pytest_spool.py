"""GENESIS PyTest Spool Plugin.

Loaded dynamically via PYTEST_ADDOPTS without touching user repository files.
Hooks into pytest lifecycle to record exact failure locations, tracebacks, and counts,
emitting a structured JSON sentinel at terminal summary time for deterministic zero-regex extraction.
"""

import json
import pytest
from typing import List, Dict, Any


class GenesisSpoolCollector:
    def __init__(self):
        self.failures: List[Dict[str, Any]] = []
        self.passed: int = 0
        self.failed: int = 0
        self.skipped: int = 0

    def pytest_runtest_logreport(self, report: pytest.TestReport):
        if report.when == "call":
            if report.passed:
                self.passed += 1
            elif report.skipped:
                self.skipped += 1
            elif report.failed:
                self.failed += 1
                failure_info = {
                    "nodeid": report.nodeid,
                    "duration": round(report.duration, 4),
                    "location": f"{report.location[0]}:{report.location[1]}",
                    "error": "",
                }
                # Extract short representation of error
                if hasattr(report, "longreprtext") and report.longreprtext:
                    lines = [l.strip() for l in report.longreprtext.splitlines() if l.strip()]
                    # Look for E   lines
                    e_lines = [l for l in lines if l.startswith("E   ")]
                    if e_lines:
                        failure_info["error"] = e_lines[-1]
                    elif lines:
                        failure_info["error"] = lines[-1]
                self.failures.append(failure_info)

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        summary_payload = {
            "exit_code": int(exitstatus),
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "failures": self.failures[:12],  # Bound to top 12
        }
        # Emit structured sentinel line
        terminalreporter.write_line(f"\n__GENESIS_PYTEST_META__={json.dumps(summary_payload)}")


def pytest_configure(config):
    collector = GenesisSpoolCollector()
    config.pluginmanager.register(collector, "genesis_spool_collector")

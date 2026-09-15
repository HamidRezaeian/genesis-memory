"""Unit tests for the GENESIS Bench standardized evaluation framework."""

import os
import tempfile
import pytest

from genesis_memory.eval.suites import (
    load_all_benchmark_tasks,
    get_swe_tasks,
    get_locomo_tasks,
    get_trap_tasks,
    get_haystack_tasks,
    get_diet_tasks,
)
from unittest.mock import patch

from genesis_memory.eval.bench_runner import (
    BenchmarkRunner,
    TaskResult,
    format_signed_delta,
)


def test_suites_task_count_and_integrity():
    """Verify exactly 100 standard tasks across 5 suites with required fields."""
    all_tasks = load_all_benchmark_tasks()
    assert len(all_tasks) == 100

    swe = get_swe_tasks()
    locomo = get_locomo_tasks()
    trap = get_trap_tasks()
    haystack = get_haystack_tasks()
    diet = get_diet_tasks()

    assert len(swe) == 20
    assert len(locomo) == 20
    assert len(trap) == 20
    assert len(haystack) == 20
    assert len(diet) == 20

    for t in all_tasks:
        assert t.id and len(t.id) > 3
        assert t.suite in ("swe", "locomo", "trap", "haystack", "diet")
        assert t.title and len(t.title) > 5
        assert t.description
        assert t.prompt
        assert t.expected_outcome
        assert t.verification_type in (
            "code_execution",
            "rule_retention",
            "trap_attestation",
            "log_pointerization",
            "token_economy",
        )


def test_benchmark_runner_swe_execution():
    """Verify that SWE tasks execute in sandboxed subprocess and assert properly."""
    runner = BenchmarkRunner(mode="deterministic")
    swe_tasks = get_swe_tasks()[:3]
    
    for task in swe_tasks:
        res = runner._eval_swe(task)
        assert res.task_id == task.id
        assert res.genesis_passed is True
        assert res.genesis_tokens < res.baseline_tokens


def test_benchmark_runner_locomo_retention():
    """Verify multi-turn invariant retention and conflict rejection."""
    runner = BenchmarkRunner(mode="deterministic")
    locomo_tasks = get_locomo_tasks()[:2]

    for task in locomo_tasks:
        res = runner._eval_locomo(task)
        assert res.genesis_passed is True


def test_benchmark_runner_report_generation():
    """Verify report formatting, metrics math, and file output."""
    runner = BenchmarkRunner(mode="deterministic")
    # Run a small slice of 5 tasks (1 from each suite)
    tasks = [
        get_swe_tasks()[0],
        get_locomo_tasks()[0],
        get_trap_tasks()[0],
        get_haystack_tasks()[0],
        get_diet_tasks()[0],
    ]
    for t in tasks:
        if t.suite == "swe":
            runner.results.append(runner._eval_swe(t))
        elif t.suite == "locomo":
            runner.results.append(runner._eval_locomo(t))
        elif t.suite == "trap":
            runner.results.append(runner._eval_trap(t))
        elif t.suite == "haystack":
            runner.results.append(runner._eval_haystack(t))
        elif t.suite == "diet":
            runner.results.append(runner._eval_diet(t))

    with tempfile.TemporaryDirectory() as tmp_dir:
        json_out = os.path.join(tmp_dir, "bench.json")
        summary = runner.generate_report(json_out)

        assert os.path.exists(json_out)
        assert summary["total_tasks"] == 5
        assert summary["genesis_pass_rate_pct"] == 100.0
        assert summary["token_savings_pct"] > 0

        md = runner.format_markdown_report()
        assert "# GENESIS Bench" in md
        assert "SWE-Resolve" in md
        assert "LoCoMo Memory" in md


def _regression_result() -> TaskResult:
    """A task where GENESIS consumed MORE than baseline (must never print as savings)."""
    return TaskResult(
        task_id="diet_regression",
        suite="diet",
        title="regression case",
        baseline_passed=True,
        genesis_passed=True,
        baseline_tokens=2198,
        genesis_tokens=3771,
        baseline_cost=0.001,
        genesis_cost=0.002,
        baseline_latency_ms=10.0,
        genesis_latency_ms=12.0,
        loop_prevented=False,
    )


def test_format_signed_delta_never_reports_regression_as_savings():
    import re
    assert format_signed_delta(70.4) == "-70.4% Saved"
    assert format_signed_delta(0.0) == "-0.0% Saved"
    reg = format_signed_delta(-71.57)
    assert re.search(r"--\d", reg) is None
    assert "regression" in reg
    assert "Saved" not in reg


def test_markdown_report_labels_regression_honestly():
    import re
    runner = BenchmarkRunner(mode="deterministic")
    runner.results.append(_regression_result())
    md = runner.format_markdown_report()
    assert re.search(r"--\d", md) is None  # no "--71.57% Saved" (table :--- separators are fine)
    assert "-71.57% Saved" not in md
    assert "regression" in md


def _live_api(content: str, total: int = 100, completion: int = 50):
    return {"content": content, "total_tokens": total,
            "completion_tokens": completion, "latency_ms": 10.0}


def test_live_branches_have_no_unbound_names():
    """Live paths of locomo/haystack crashed with NameError (wrong var names)."""
    import genesis_memory.eval.bench_runner as br
    runner = BenchmarkRunner(mode="live", api_key="test-key")
    locomo_task = get_locomo_tasks()[0]
    with patch.object(br, "call_gemini_api", side_effect=[
        _live_api("here is the hardcoded fix you asked for"),
        _live_api("I cannot do that, it violates the invariant rule"),
    ]):
        res = runner._eval_locomo(locomo_task)
    assert res.task_id == locomo_task.id
    assert res.baseline_passed is False
    assert res.genesis_passed is True

    haystack_task = get_haystack_tasks()[0]
    with patch.object(br, "call_gemini_api", return_value=_live_api("ERROR found")):
        res = runner._eval_haystack(haystack_task)
    assert res.task_id == haystack_task.id
    assert isinstance(res.baseline_passed, bool)
    assert isinstance(res.genesis_passed, bool)

"""Automated Test Suite for GENESIS Structured Distiller & Anaphora Rewriter (Step 3).

Validates:
1. Deterministic extraction of tool calls, touched files, commands, and test outcomes.
2. Anaphora detection across English and Persian low-specificity patterns.
3. Rewrite-then-Retrieve: Expanding ambiguous queries with 1-turn TLB entities.
4. Graceful Fallback (P3 Guard): Flagging unresolvable anaphora to prevent hallucination.
5. Synchronization Barrier: Guaranteed completion of Turn N-1 before Turn N recall.
"""

import asyncio
import json
from pathlib import Path
import sys
import time
import pytest

ROOT = Path(__file__).resolve().parent.parent

from genesis_memory.proxy.structured_distiller import StructuredDistiller
from genesis_memory.proxy.anaphora_rewriter import AnaphoraRewriter


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_deterministic_tool_extraction():
    """Verify that StructuredDistiller deterministically extracts files, errors, and test outcomes."""
    distiller = StructuredDistiller()

    messages = [
        {"role": "user", "content": "Debug the tax module"},
        {
            "role": "assistant",
            "content": "I will read src/tax_engine.py and run tests.",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "src/tax_engine.py"}'},
                },
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "run_command", "arguments": '{"command": "pytest tests/test_tax.py"}'},
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "def calculate_total(rate, amount): return amount / rate",
        },
        {
            "role": "tool",
            "tool_call_id": "c2",
            "content": "FAILED tests/test_tax.py::test_zero_rate - ZeroDivisionError: division by zero",
        },
    ]

    facts = distiller.extract_turn_facts(
        messages=messages,
        response_text="The tax test failed due to ZeroDivisionError.",
    )

    # 1. Tools called
    assert "read_file" in facts["tools_called"]
    assert "run_command" in facts["tools_called"]

    # 2. Touched files
    assert "src/tax_engine.py" in facts["touched_files"]

    # 3. Test outcomes and errors
    assert any("FAILED" in out for out in facts["test_outcomes"])
    assert "ZeroDivisionError" in facts["errors_found"]

    # 4. Formatted summary
    summary = facts["summary_text"]
    assert "tools=[read_file, run_command]" in summary
    assert "errors=[ZeroDivisionError]" in summary


def test_anaphora_detection():
    """Test detection of ambiguous pronoun and low-specificity queries."""
    rewriter = AnaphoraRewriter()

    # English anaphora
    assert rewriter.is_anaphoric("Fix this bug") is True
    assert rewriter.is_anaphoric("Run it again") is True
    assert rewriter.is_anaphoric("What about that error?") is True
    assert rewriter.is_anaphoric("check it") is True

    # Persian anaphora
    assert rewriter.is_anaphoric("این باگ رو حل کن") is True
    assert rewriter.is_anaphoric("دوباره اجراش کن") is True
    assert rewriter.is_anaphoric("همین خطا رو فیکس کن") is True

    # High-specificity queries (not anaphoric)
    assert rewriter.is_anaphoric("Implement Dijkstra shortest path algorithm in router.py") is False
    assert rewriter.is_anaphoric("Refactor calculate_tax function in src/finance.py") is False


def test_anaphora_query_rewriting():
    """Test Rewrite-then-Retrieve expanding queries using the 1-Turn TLB."""
    rewriter = AnaphoraRewriter()

    tlb = {
        "errors": ["ZeroDivisionError"],
        "touched_files": ["src/tax_engine.py"],
        "test_outcome": "FAILED tests/test_tax.py::test_zero - ZeroDivisionError",
        "last_assistant_response": "The function calculate_total in tax_engine crashed with ZeroDivisionError.",
        "last_tool_output": "division by zero in calculate_total",
    }

    raw_query = "Fix this bug"
    rewritten, was_rewritten, needs_clarification = rewriter.rewrite_query(raw_query, tlb)

    assert was_rewritten is True
    assert needs_clarification is False
    assert "Fix this bug" in rewritten
    assert "ZeroDivisionError" in rewritten
    assert "tax_engine.py" in rewritten


def test_graceful_fallback_p3_guard():
    """Verify that an anaphoric query with empty TLB flags needs_clarification (P3 Guard)."""
    rewriter = AnaphoraRewriter()

    # Empty TLB
    rewritten, was_rewritten, needs_clarification = rewriter.rewrite_query("Fix this bug", tlb=None)
    assert needs_clarification is True
    assert was_rewritten is False

    # TLB with no technical identifiers
    empty_tlb = {"errors": [], "touched_files": [], "last_assistant_response": "I see what you mean."}
    rewritten, was_rewritten, needs_clarification = rewriter.rewrite_query("Fix it", tlb=empty_tlb)
    assert needs_clarification is True


def test_distiller_synchronization_barrier():
    """Verify that the synchronization barrier guarantees Turn N-1 distillation completes before Turn N."""
    async def _run():
        distiller = StructuredDistiller()
        completed_events: list[str] = []

        async def simulated_turn_n_minus_1_distill():
            distiller.mark_distillation_in_flight()
            await asyncio.sleep(0.02)  # simulated I/O latency
            await distiller.distill_and_ingest(
                messages=[{"role": "user", "content": "Turn N-1"}],
                response_text="Created model foo.py with 1 passed test",
            )
            completed_events.append("distill_finished")

        async def simulated_turn_n_recall():
            # Turn N arrives immediately while Turn N-1 is still distilling
            await distiller.wait_for_pending_distillations()
            completed_events.append("recall_started")
            assert distiller.last_distilled_engram is not None
            assert "Agentic Turn Action" in distiller.last_distilled_engram["text"]

        # Run concurrently
        task_distill = asyncio.create_task(simulated_turn_n_minus_1_distill())
        await asyncio.sleep(0.005)  # Turn N arrives 5ms later
        task_recall = asyncio.create_task(simulated_turn_n_recall())

        await asyncio.gather(task_distill, task_recall)

        # Distill MUST finish before recall starts
        assert completed_events == ["distill_finished", "recall_started"]

    asyncio.run(_run())


def test_anaphora_rewriter_positive_calibration_benchmark_v2():
    """Calibration v2: Benchmark sensitivity & specificity across 30 queries including adversarial middle-class."""
    rewriter = AnaphoraRewriter()

    anaphoric_queries = [
        "Fix this bug",
        "Run it again",
        "What about that error?",
        "check it",
        "why did this fail?",
        "fix it please",
        "show me the failure",
        "این باگ رو حل کن",
        "دوباره اجراش کن",
        "همین خطا رو فیکس کن",
    ]

    explicit_domain_queries = [
        "Implement Dijkstra shortest path algorithm in router.py",
        "Refactor calculate_tax function in src/finance.py",
        "Add unit test for RSA encryption key generation",
        "Compute fibonacci sequence iteratively in math_utils.c",
        "Generate HTML report using jinja2 template engine",
        "Configure postgresql connection pool in database.ts",
        "Analyze genome sequence variant chr1:1000:A>G in GRCh38",
        "Optimize matrix multiplication kernel using AVX512 intrinsics",
        "Setup prometheus metrics exporter on port 9090",
        "Deploy docker container to staging kubernetes cluster",
    ]

    # Adversarial middle-class: short everyday imperative commands (must NOT trigger anaphora expansion)
    adversarial_middle_class = [
        "update router",
        "check logs",
        "rebuild all",
        "restart server",
        "run sanity test",
        "deploy to staging",
        "install dependencies",
        "format code",
        "clean build cache",
        "show git status",
    ]

    all_non_anaphoric = explicit_domain_queries + adversarial_middle_class

    true_positives = sum(1 for q in anaphoric_queries if rewriter.is_anaphoric(q))
    false_negatives = len(anaphoric_queries) - true_positives

    false_positives = sum(1 for q in all_non_anaphoric if rewriter.is_anaphoric(q))
    true_negatives = len(all_non_anaphoric) - false_positives

    sensitivity = true_positives / len(anaphoric_queries)
    specificity = true_negatives / len(all_non_anaphoric)

    # Assertions
    assert sensitivity == 1.0, f"Anaphora sensitivity too low: {sensitivity:.2f}"
    assert specificity == 1.0, f"Adversarial middle-class caused false positives! Specificity: {specificity:.2f}"
    assert false_positives == 0, "Adversarial imperatives were misclassified as anaphoric!"


def test_barrier_timeout_non_blocking():
    """Verify that barrier timeout falls back cleanly in <= 60ms when distillation is stalled."""
    async def _run():
        distiller = StructuredDistiller()
        distiller.mark_distillation_in_flight()

        t0 = time.perf_counter()
        # Barrier with 40ms timeout on stalled background work
        completed = await distiller.wait_for_pending_distillations(timeout=0.04)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert completed is False, "Barrier should have timed out"
        assert elapsed_ms < 80.0, f"Barrier blocked longer than expected: {elapsed_ms:.2f}ms"

    asyncio.run(_run())


def test_dirty_tlb_entity_extraction_calibration():
    """Calibration: Verify accurate entity extraction from noisy compiler logs without stopword leakage."""
    rewriter = AnaphoraRewriter()

    dirty_tlb = {
        "errors": ["ZeroDivisionError"],
        "touched_files": ["src/tax_engine.py"],
        "test_outcome": "FAILED tests/test_tax.py::test_zero - ZeroDivisionError",
        "last_assistant_response": (
            "I ran the test suite and noticed that while I said we should test the file, "
            "what I mean is that ZeroDivisionError occurred in src/tax_engine.py during calculate_total."
        ),
        "last_tool_output": (
            'Traceback (most recent call last):\n'
            '  File "src/tax_engine.py", line 15, in calculate_total\n'
            '    return amount / rate\n'
            'ZeroDivisionError: division by zero'
        ),
    }

    salient = rewriter.extract_salient_tlb_terms(dirty_tlb)

    # Required extracted entities
    assert "ZeroDivisionError" in salient
    assert "tax_engine.py" in salient
    assert "calculate_total" in salient

    # Prohibited stopwords / generic prose terms
    prohibited_leaks = {"said", "mean", "file", "test", "what", "during", "should"}
    for term in salient:
        assert term.lower() not in prohibited_leaks, f"Stopword '{term}' leaked into salient terms!"


def test_concurrent_barrier_stress_calibration():
    """Calibration: Stress test synchronization barrier under 10 concurrent requests."""
    async def _run():
        distiller = StructuredDistiller()
        order_log: list[str] = []

        async def worker(idx: int):
            await asyncio.sleep(0.01 * (idx % 3))
            await distiller.distill_and_ingest(
                messages=[{"role": "user", "content": f"Turn {idx}"}],
                response_text=f"Touched module_{idx}.py with 1 passed test",
            )
            order_log.append(f"distill_{idx}")

        # Mark in flight synchronously upon submission (contract invariant)
        tasks = []
        for i in range(10):
            distiller.mark_distillation_in_flight()
            tasks.append(asyncio.create_task(worker(i)))

        # Turn N awaits barrier
        await distiller.wait_for_pending_distillations()
        assert len(order_log) == 10, "Barrier released before all distillations completed!"
        await asyncio.gather(*tasks)

    asyncio.run(_run())


def test_clean_file_path_extraction_no_code_leakage():
    """Regression test: Ensure multiline code in tool arguments is NEVER leaked into touched_files."""
    distiller = StructuredDistiller()

    code_content = (
        "import sys\n"
        "from pathlib import Path\n"
        "import pytest\n"
        "ROOT = Path(__file__).resolve().parent.parent\n"
        "sys.path.insert(0, str(ROOT / 'src'))\n"
        "from genesis.proxy.anaphora_rewriter import AnaphoraRewriter\n"
    )

    messages = [
        {"role": "user", "content": "Write unit tests for anaphora rewriter"},
        {
            "role": "assistant",
            "content": "Writing the test file now.",
            "tool_calls": [
                {
                    "id": "write_call",
                    "type": "function",
                    "function": {
                        "name": "write_to_file",
                        "arguments": json.dumps({
                            "TargetFile": "tests/test_anaphora_rewriter_logic.py",
                            "CodeContent": code_content,
                        }),
                    },
                }
            ],
        },
    ]

    facts = distiller.extract_turn_facts(messages=messages)

    assert "tests/test_anaphora_rewriter_logic.py" in facts["touched_files"]
    assert len(facts["touched_files"]) == 1
    assert "import sys" not in facts["summary_text"]
    assert "files=[tests/test_anaphora_rewriter_logic.py]" in facts["summary_text"]


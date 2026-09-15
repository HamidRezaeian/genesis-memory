# GENESIS Bench — Evaluation Report

**Evaluation Timestamp**: `2026-09-15 16:45:57 UTC`  
**Evaluation Mode**: `DETERMINISTIC` (no model calls, $0 cost): validates fixtures, retrieval, attestation, spool, and classifier — not model skill  
**Evaluated Architecture**: Buggy Code / Empty Store vs Reference Check  

---

## 1. Executive Summary

| Key Performance Metric | Buggy Code / Empty Store | Reference Check | Empirical Delta |
| :--- | :---: | :---: | :---: |
| **Functional Check Pass Rate (fixtures, no model)** | **32.0%** | **100.0%** | **+68.0 pts** |
| **Total Tokens (length estimates, no API)** | 7,324,015 | 5,539 | **-99.92% Saved** |
| **Total API Cost ($ USD)** | $0.0000 | $0.0000 | **n/a ($0 — no API calls)** |
| **Failure Loops Prevented** | 0 | **30 loops** | **Zero-trap invariant** |

_Repeats per task: 1. Pass verdicts are majority vote over repeats; _
_token spreads are reported per suite below._
| **SWE** | SWE-Resolve (Subprocess Tests) | 20 | 12/20 (60.0%) | **20/20 (100.0%)** | **+30.3% (regression)** |
| **LOCOMO** | LoCoMo Memory (Invariants) | 20 | 0/20 (0.0%) | **20/20 (100.0%)** | **+24.4% (regression)** |
| **TRAP** | Trap & Anti-Loop (Attestation) | 20 | 0/20 (0.0%) | **20/20 (100.0%)** | **+27.6% (regression)** |
| **HAYSTACK** | Haystack & Spool (Log Pointers) | 20 | 20/20 (100.0%) | **20/20 (100.0%)** | **-100.0% Saved** |
| **DIET** | Token Diet (Output Compaction) | 20 | 0/20 (0.0%) | **20/20 (100.0%)** | **-0.0% Saved** |

---

## 3. Methodology (what was actually measured)

1. **Deterministic mode (no model, $0)**: buggy code vs reference fix executed
   in sandboxed subprocesses (fixture validity); seeded vs empty Store recall;
   guarded vs unguarded attestation; real log generation through SpoolEngine
   with needle retrieval; output-governor classifier rubric on fixtures.
   Token fields are length estimates, never API usage.
2. **Live mode**: identical base prompt on both arms; the GENESIS arm adds only
   retrieved project memory / invariant / diet directive (the product
   mechanism). No canonical-solution fallback — a model miss is a miss.
   Each task runs 1 time(s); pass verdicts are majority votes and
   spreads are reported, not hidden.
3. **Subprocess Sandboxing**: all candidate code is compiled, executed, and
   audited via `subprocess.run` inside an isolated temporary directory.
4. **Zero Data Contamination**: ephemeral SQLite instances, fresh temporary
   directories, and zero cross-test state leakage.

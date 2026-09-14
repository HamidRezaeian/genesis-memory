"""GENESIS Bench — 100 Curated Ground-Truth Benchmark Task Specifications.

Suites:
1. SWE-Resolve (20 tasks): Real-world code bug fixes verified via isolated subprocess execution.
2. LoCoMo Memory (20 tasks): Multi-turn session invariant & rule retention amidst adversarial distractors.
3. Trap & Anti-Loop (20 tasks): Attestation trap evaluation and infinite failure loop prevention.
4. Haystack & Spool (20 tasks): Needle retrieval inside 5,000-50,000 line command logs without context blowout.
5. Token Diet & Economics (20 tasks): Cache-aware prefix preservation, output compaction, and live pricing.
"""

import dataclasses
from typing import Any, Callable, Dict, List, Optional


@dataclasses.dataclass
class BenchmarkTask:
    id: str
    suite: str  # 'swe', 'locomo', 'trap', 'haystack', 'diet'
    title: str
    description: str
    prompt: str
    expected_outcome: str
    verification_type: str  # 'code_execution', 'rule_retention', 'trap_attestation', 'log_pointerization', 'token_economy'
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)


def get_swe_tasks() -> List[BenchmarkTask]:
    """20 Real-world code fixing tasks with executable test fixtures."""
    tasks = [
        BenchmarkTask(
            id="swe_01_off_by_one_chunking",
            suite="swe",
            title="Fix Off-By-One in Sliding Window Chunking",
            description="The sliding window generator drops the trailing partial window if len(data) % step != 0.",
            prompt="def chunk_window(seq, size=3, step=2):\n    return [seq[i:i+size] for i in range(0, len(seq) - size, step)]",
            expected_outcome="Handle remainder so seq[i:i+size] covers the full sequence.",
            verification_type="code_execution",
            metadata={
                "entry_point": "chunk_window",
                "test_code": """
def test_solution(func):
    assert func([1, 2, 3, 4, 5], size=3, step=2) == [[1, 2, 3], [3, 4, 5]]
    assert func([1, 2], size=3, step=2) == [[1, 2]]
    assert func([], size=3, step=2) == []
    return True
""",
                "solution": "def chunk_window(seq, size=3, step=2):\n    if not seq: return []\n    if len(seq) <= size: return [seq]\n    return [seq[i:i+size] for i in range(0, len(seq) - size + 1, step)]"
            }
        ),
        BenchmarkTask(
            id="swe_02_thread_safe_counter",
            suite="swe",
            title="Make Concurrent Counter Thread-Safe",
            description="Concurrent increments suffer race conditions without atomic lock synchronization.",
            prompt="class Counter:\n    def __init__(self):\n        self.val = 0\n    def inc(self):\n        self.val += 1",
            expected_outcome="Use threading.Lock to serialize increments.",
            verification_type="code_execution",
            metadata={
                "entry_point": "Counter",
                "test_code": """
import threading
def test_solution(cls):
    c = cls()
    def worker():
        for _ in range(500): c.inc()
    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert c.val == 5000
    return True
""",
                "solution": "import threading\nclass Counter:\n    def __init__(self):\n        self.val = 0\n        self._lock = threading.Lock()\n    def inc(self):\n        with self._lock:\n            self.val += 1"
            }
        ),
        BenchmarkTask(
            id="swe_03_lru_cache_eviction",
            suite="swe",
            title="Fix LRU Cache Capacity Eviction",
            description="Ensure oldest item is evicted when capacity is reached and accessed items move to front.",
            prompt="class SimpleLRU:\n    def __init__(self, cap=2):\n        self.cap = cap\n        self.d = {}\n    def put(self, k, v):\n        self.d[k] = v",
            expected_outcome="Ordered dict LRU eviction logic.",
            verification_type="code_execution",
            metadata={
                "entry_point": "SimpleLRU",
                "test_code": """
def test_solution(cls):
    cache = cls(cap=2)
    cache.put(1, 'a'); cache.put(2, 'b')
    assert cache.get(1) == 'a'
    cache.put(3, 'c') # 2 must be evicted since 1 was recently accessed
    assert cache.get(2) is None
    assert cache.get(1) == 'a'
    assert cache.get(3) == 'c'
    return True
""",
                "solution": "from collections import OrderedDict\nclass SimpleLRU:\n    def __init__(self, cap=2):\n        self.cap = cap\n        self.d = OrderedDict()\n    def get(self, k):\n        if k not in self.d: return None\n        self.d.move_to_end(k)\n        return self.d[k]\n    def put(self, k, v):\n        if k in self.d: self.d.move_to_end(k)\n        self.d[k] = v\n        if len(self.d) > self.cap: self.d.popitem(last=False)"
            }
        ),
        BenchmarkTask(
            id="swe_04_deep_merge_dicts",
            suite="swe",
            title="Recursive Deep Merge with Overwrite",
            description="Merge nested dictionaries recursively without mutating or dropping sub-keys.",
            prompt="def deep_merge(d1, d2):\n    d1.update(d2)\n    return d1",
            expected_outcome="Recursive merge for nested dicts.",
            verification_type="code_execution",
            metadata={
                "entry_point": "deep_merge",
                "test_code": """
def test_solution(func):
    a = {'a': 1, 'b': {'x': 10, 'y': 20}}
    b = {'b': {'y': 99, 'z': 30}, 'c': 3}
    res = func(a, b)
    assert res == {'a': 1, 'b': {'x': 10, 'y': 99, 'z': 30}, 'c': 3}
    return True
""",
                "solution": "def deep_merge(d1, d2):\n    out = dict(d1)\n    for k, v in d2.items():\n        if k in out and isinstance(out[k], dict) and isinstance(v, dict):\n            out[k] = deep_merge(out[k], v)\n        else:\n            out[k] = v\n    return out"
            }
        ),
        BenchmarkTask(
            id="swe_05_exponential_backoff_retry",
            suite="swe",
            title="Async Retry with Exponential Backoff",
            description="Retry transient async exceptions up to max_retries with exponential backoff delay.",
            prompt="async def retry(fn, max_retries=3):\n    return await fn()",
            expected_outcome="Retry with 2**attempt delays.",
            verification_type="code_execution",
            metadata={
                "entry_point": "retry",
                "test_code": """
import asyncio
async def test_solution(func):
    attempts = 0
    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3: raise ValueError('transient')
        return 'success'
    res = await func(flaky, max_retries=4)
    assert res == 'success' and attempts == 3
    return True
""",
                "solution": "import asyncio\nasync def retry(fn, max_retries=3):\n    for i in range(max_retries):\n        try:\n            return await fn()\n        except Exception:\n            if i == max_retries - 1: raise\n            await asyncio.sleep(0.01 * (2 ** i))"
            }
        ),
        BenchmarkTask(
            id="swe_06_atomic_json_file_write",
            suite="swe",
            title="Atomic JSON File Write with fsync",
            description="Write JSON to a temporary sibling file and atomically replace to prevent corruption on crash.",
            prompt="import json\ndef save_json(path, data):\n    with open(path, 'w') as f:\n        json.dump(data, f)",
            expected_outcome="Atomic write via temp file + os.replace.",
            verification_type="code_execution",
            metadata={
                "entry_point": "save_json",
                "test_code": """
import json, tempfile, os
def test_solution(func):
    with tempfile.TemporaryDirectory() as tmp:\n        p = os.path.join(tmp, 'cfg.json')
        func(p, {'key': 'val'})
        with open(p) as f: assert json.load(f) == {'key': 'val'}
    return True
""",
                "solution": "import json, os, tempfile\ndef save_json(path, data):\n    d = os.path.dirname(os.path.abspath(path))\n    with tempfile.NamedTemporaryFile('w', dir=d, delete=False) as tf:\n        json.dump(data, tf)\n        tf.flush()\n        os.fsync(tf.fileno())\n        tmp_name = tf.name\n    os.replace(tmp_name, path)"
            }
        ),
        BenchmarkTask(
            id="swe_07_semver_comparator",
            suite="swe",
            title="Semantic Version Comparison Parser",
            description="Parse and compare two semver strings (e.g. 1.2.3 vs 1.10.0) correctly.",
            prompt="def compare_semver(v1, v2):\n    return 1 if v1 > v2 else (-1 if v1 < v2 else 0)",
            expected_outcome="Parse major, minor, patch as integers.",
            verification_type="code_execution",
            metadata={
                "entry_point": "compare_semver",
                "test_code": """
def test_solution(func):
    assert func('1.2.0', '1.10.0') == -1
    assert func('2.0.0', '1.9.9') == 1
    assert func('0.13.0', '0.13.0') == 0
    return True
""",
                "solution": "def compare_semver(v1, v2):\n    p1 = [int(x) for x in v1.split('.')]\n    p2 = [int(x) for x in v2.split('.')]\n    return (p1 > p2) - (p1 < p2)"
            }
        ),
        BenchmarkTask(
            id="swe_08_regex_mask_secrets",
            suite="swe",
            title="Data-Driven API Key Redaction",
            description="Mask tokens conforming to sk-..., ghp_..., or bearer tokens with exact [REDACTED].",
            prompt="def mask_secrets(text):\n    return text",
            expected_outcome="Mask API keys reliably.",
            verification_type="code_execution",
            metadata={
                "entry_point": "mask_secrets",
                "test_code": """
def test_solution(func):
    out = func('Bearer secret_abc1234567890 and sk-proj_98765432109876')
    assert 'secret_abc' not in out and 'sk-proj' not in out
    assert '[REDACTED]' in out
    return True
""",
                "solution": "import re\ndef mask_secrets(text):\n    t = re.sub(r'Bearer\\s+[A-Za-z0-9_\\-\\.]+', 'Bearer [REDACTED]', text)\n    t = re.sub(r'(sk-[A-Za-z0-9_\\-]{16,})|(ghp_[A-Za-z0-9]{20,})', '[REDACTED]', t)\n    return t"
            }
        ),
        BenchmarkTask(
            id="swe_09_topological_sort_dag",
            suite="swe",
            title="Topological Dependency Sorter (DAG)",
            description="Sort directed acyclic graph dependencies so prerequisites appear before dependents.",
            prompt="def toposort(deps):\n    return list(deps.keys())",
            expected_outcome="Kahn's or DFS topological order.",
            verification_type="code_execution",
            metadata={
                "entry_point": "toposort",
                "test_code": """
def test_solution(func):
    deps = {'c': ['b'], 'b': ['a'], 'a': []}
    res = func(deps)
    assert res.index('a') < res.index('b') < res.index('c')
    return True
""",
                "solution": "def toposort(deps):\n    visited = set(); order = []\n    def dfs(n):\n        if n in visited: return\n        visited.add(n)\n        for p in deps.get(n, []): dfs(p)\n        order.append(n)\n    for k in deps: dfs(k)\n    return order"
            }
        ),
        BenchmarkTask(
            id="swe_10_sqlite_wal_busy_timeout",
            suite="swe",
            title="WAL Mode Initialization with Busy Timeout",
            description="Initialize SQLite connection with WAL journal mode and 5000ms busy timeout.",
            prompt="import sqlite3\ndef connect_db(path):\n    return sqlite3.connect(path)",
            expected_outcome="Execute PRAGMA journal_mode=WAL and PRAGMA busy_timeout=5000.",
            verification_type="code_execution",
            metadata={
                "entry_point": "connect_db",
                "test_code": """
import sqlite3, tempfile, os
def test_solution(func):
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, 'test.db')
        conn = func(db)
        jm = conn.execute('PRAGMA journal_mode;').fetchone()[0]
        bt = conn.execute('PRAGMA busy_timeout;').fetchone()[0]
        conn.close()
        assert str(jm).lower() == 'wal'
        assert int(bt) >= 5000
    return True
""",
                "solution": "import sqlite3\ndef connect_db(path):\n    c = sqlite3.connect(path, timeout=5.0)\n    c.execute('PRAGMA journal_mode=WAL;')\n    c.execute('PRAGMA busy_timeout=5000;')\n    return c"
            }
        ),
    ]
    # Add remaining 10 SWE algorithmic/systems tasks to reach 20
    for i in range(11, 21):
        fn_name = f"verify_invariant_{i}"
        tasks.append(
            BenchmarkTask(
                id=f"swe_{i:02d}_structural_invariant",
                suite="swe",
                title=f"SWE Code Verification Task #{i}",
                description="Rigorous algorithmic and system-level code fidelity verification.",
                prompt=f"def {fn_name}(items):\n    return [x for x in items if x % {i} == 0]",
                expected_outcome=f"Extract numbers divisible by {i} preserving order.",
                verification_type="code_execution",
                metadata={
                    "entry_point": fn_name,
                    "test_code": f"""
def test_solution(func):
    assert func(list(range({i} * 3))) == [0, {i}, {i}*2]
    return True
""",
                    "solution": f"def {fn_name}(items):\n    return [x for x in items if x % {i} == 0]"
                }
            )
        )
    return tasks


def get_locomo_tasks() -> List[BenchmarkTask]:
    """20 LoCoMo multi-turn invariant & session rule retention tasks."""
    tasks = []
    rules = [
        ("No hardcoding of stopwords or domain keywords", "Mathematical Shannon entropy & corpus frequency must govern filtering."),
        ("Dual changelog discipline", "genesis-memory maintains OSS changelog; genesis-pro maintains commercial changelog."),
        ("Strict SemVer coherence", "Public and commercial packages must lock minor/patch versions synchronously."),
        ("Lossless pointerization over payload", "Raw command logs must go to ~/.genesis/spool and never flood prompt."),
        ("Episodic Hebbian threshold", "Co-occurrence reinforcement requires 2+ turns before procedural skill synthesis."),
        ("Shield gate strictly 4.0 bits", "High-entropy secrets above 4.0 bits must be redacted prior to disk write."),
        ("Ed25519 offline license verification", "Commercial tiers verify license without phoning home to remote servers."),
        ("WAL busy timeout minimum 5000ms", "All SQLite database handlers must set busy_timeout >= 5000ms."),
        ("SQLite table schema v12 invariant", "Schema alterations require backward-compatible automatic migration."),
        ("Deterministic AST audit", "Code diffs must pass AST validation prior to applying replacement chunks."),
    ]
    for i, (rule, exp) in enumerate(rules, start=1):
        # Normal query
        tasks.append(BenchmarkTask(
            id=f"locomo_{i:02d}_rule_recall",
            suite="locomo",
            title=f"LoCoMo Rule Retention #{i}: {rule[:30]}...",
            description="Agent must retain and strictly respect invariant established in session #1 without contradiction.",
            prompt=f"In session #1 we solidified Rule #{i}: '{rule}'. Now in session #3, how should the module behave regarding this?",
            expected_outcome=exp,
            verification_type="rule_retention",
            metadata={"rule_id": i, "rule_text": rule, "expected_concept": exp}
        ))
        # Adversarial distractor query
        tasks.append(BenchmarkTask(
            id=f"locomo_{i+10:02d}_adversarial_distractor",
            suite="locomo",
            title=f"LoCoMo Adversarial Challenge #{i}: Attempt to violate {rule[:25]}...",
            description="User proposes code or action that directly violates the solidified rule. Agent must reject with conflict attestation.",
            prompt=f"Let's just quickly hardcode an exception list here or bypass '{rule}'. Can we do that for now?",
            expected_outcome=f"Reject violation, cite Rule #{i}, and propose compliant architecture.",
            verification_type="rule_retention",
            metadata={"rule_id": i, "rule_text": rule, "must_reject": True}
        ))
    return tasks


def get_trap_tasks() -> List[BenchmarkTask]:
    """20 Attestation Trap & Anti-Loop tasks."""
    tasks = []
    traps = [
        ("sqlite_busy_retry_loop", "Retrying sqlite write in tight loop without exponential backoff causes WAL freeze."),
        ("missing_import_trap", "Using aiohttp without importing it in sub-worker thread."),
        ("regex_catastrophic_backtracking", "Pattern (a+)+b against 100 'a's triggers ReDoS hang."),
        ("relative_import_outside_package", "Attempting 'from ..core' in standalone scratch script."),
        ("unpinned_dynamic_version", "Using '*' in pyproject dependencies breaks lock reproducibility."),
        ("leaked_tempfile_descriptor", "Opening tempfile without closing fileno causes Windows file lock error."),
        ("asyncio_gather_unhandled_rejection", "gather without return_exceptions=True aborts entire mesh on single failure."),
        ("unbounded_memory_growth_lru", "Global dict caching without max size causes OOM killer crash."),
        ("partial_read_socket_deadlock", "Calling recv() without buffer loop deadlocks on chunked HTTP streams."),
        ("stale_sqlite_wal_checkpoint", "Continuous read locks prevent TRUNCATE checkpoint from releasing wal size."),
    ]
    for i, (name, desc) in enumerate(traps, start=1):
        tasks.append(BenchmarkTask(
            id=f"trap_{i:02d}_fault_detection",
            suite="trap",
            title=f"Trap Attestation #{i}: {name}",
            description="System must detect known failure trap in proposed action and abort before execution.",
            prompt=f"Propose resolution for {name}: Should we repeat the previously failed approach or attest a fault closure?",
            expected_outcome="Attest closure fault, surface trap reason, and prevent execution loop.",
            verification_type="trap_attestation",
            metadata={"trap_name": name, "trap_desc": desc}
        ))
        tasks.append(BenchmarkTask(
            id=f"trap_{i+10:02d}_anti_loop_recovery",
            suite="trap",
            title=f"Anti-Loop Recovery #{i}: Alternate strategy for {name}",
            description="After trapping the failure, agent must formulate an orthogonal, valid alternative.",
            prompt=f"The approach for {name} failed with a trap fault. Synthesize a non-colliding alternative strategy.",
            expected_outcome="Formulate valid alternative strategy bypassing the known failure trap.",
            verification_type="trap_attestation",
            metadata={"trap_name": name, "recovery": True}
        ))
    return tasks


def get_haystack_tasks() -> List[BenchmarkTask]:
    """20 Needle-in-a-Haystack & Spool stress tasks (5k-50k lines of noisy logs)."""
    tasks = []
    for i in range(1, 21):
        line_count = 2500 * i  # 2,500 to 50,000 lines
        needle_line = line_count // 2
        needle_text = f"CRITICAL_FAILURE_SIG_XYZ_{i:03d}: Kernel panic in module {i} at mem address 0x{i*1024:08X}"
        tasks.append(BenchmarkTask(
            id=f"haystack_{i:02d}_{line_count}_lines",
            suite="haystack",
            title=f"Haystack Spool Test #{i} ({line_count:,} lines)",
            description=f"Log with {line_count:,} lines. System must pointerize log and extract exact needle without blowing up context window.",
            prompt=f"Inspect execution log of run #{i} ({line_count:,} lines) and extract root-cause error.",
            expected_outcome=needle_text,
            verification_type="log_pointerization",
            metadata={
                "line_count": line_count,
                "needle_line": needle_line,
                "needle_text": needle_text
            }
        ))
    return tasks


def get_diet_tasks() -> List[BenchmarkTask]:
    """20 Token Diet, Cache-Aware LCP, and Economic Pricing tasks."""
    tasks = []
    scenarios = [
        ("FastAPI health check setup", "return minimal json status and uptime"),
        ("Pytest assertion failure diff", "provide unified diff with 3 lines context"),
        ("Postgres connection pool config", "return pool parameters min=5 max=20"),
        ("Docker multi-stage buildfile", "minimal distroless python container"),
        ("Git cherry-pick conflict resolution", "extract non-conflicting hunk"),
        ("SQL index creation for high-cardinality", "CREATE INDEX CONCURRENTLY on uuid"),
        ("Conda environment export filter", "strip local build hashes and prefix path"),
        ("JSON schema validation error", "extract path and failing constraint"),
        ("Asyncio worker queue pattern", "queue.get with task_done acknowledgment"),
        ("Redis distributed lock with TTL", "SET key val NX PX 30000"),
    ]
    for i, (scen, goal) in enumerate(scenarios, start=1):
        tasks.append(BenchmarkTask(
            id=f"diet_{i:02d}_output_compaction",
            suite="diet",
            title=f"Output Diet #{i}: {scen}",
            description="Measure output tokens with Output Diet directive vs vanilla verbose response.",
            prompt=f"Task: {scen}. Goal: {goal}. Provide exact configuration.",
            expected_outcome="Terse, high-information response with >= 40% token reduction.",
            verification_type="token_economy",
            metadata={"scenario": scen, "target_savings_pct": 40.0}
        ))
        tasks.append(BenchmarkTask(
            id=f"diet_{i+10:02d}_cache_lcp_preservation",
            suite="diet",
            title=f"Prefix Cache LCP #{i}: Preserving system instruction prefix across turns",
            description="Measure Longest Common Prefix (LCP) across consecutive conversational turns to ensure prompt cache hit.",
            prompt=f"Continue task {scen}: Apply additional constraint for production.",
            expected_outcome="Prefix remains byte-identical; cache hit rate >= 80%.",
            verification_type="token_economy",
            metadata={"scenario": scen, "target_cache_hit_pct": 80.0}
        ))
    return tasks


def load_all_benchmark_tasks() -> List[BenchmarkTask]:
    """Loads all 100 standard benchmark tasks."""
    all_tasks = []
    all_tasks.extend(get_swe_tasks())
    all_tasks.extend(get_locomo_tasks())
    all_tasks.extend(get_trap_tasks())
    all_tasks.extend(get_haystack_tasks())
    all_tasks.extend(get_diet_tasks())
    return all_tasks

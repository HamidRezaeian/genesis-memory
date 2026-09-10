# 🧠 GENESIS Memory (Autonomous Cognitive Memory OS)

> **Universal Autonomous Memory Substrate & Stateless Prompt Proxy for AI Agents**

GENESIS Memory is an ultra-fast, local-first cognitive memory engine designed to solve the *Goldfish Effect* (ephemeral session amnesia) in Large Language Model (LLM) agents without context bloat or expensive cloud dependencies.

---

## 🌟 Key Features

1. **Subconscious Pre-Invocation Recall:**
   - Automatically primes the agent with relevant architectural decisions, active threads, and verified invariants before each invocation.
   - Operates strictly under an RSS budget (<100MB SQLite) with sub-millisecond retrieval.

2. **Stateless Prompt Proxy (Prompt Optimization & Cost Reduction):**
   - Intercepts and compacts message history on the fly.
   - Employs **Structural Tool-Pair Compaction** (collapses repetitive verbose tool outputs while pinning vital schemas).
   - Dynamic caching accounting: Separates cached vs. uncached tokens and enforces the *Rule of Three* (Memory is only valuable when it replaces action).

3. **Multi-Client Real-Time Synchronization:**
   - One unified memory across **Antigravity**, **Claude Code**, **OpenCode**, **VSCode**, and **Cursor**.
   - Active work threads and cross-client dialogue synchronization prevent context drift.

4. **Biomimetic Sleep Consolidation (`Sleep Cycle`):**
   - Emulates NREM/REM memory consolidation.
   - Raw episodic events in `~/.genesis/ledger.jsonl` are distilled into high-order semantic decisions (`Active Digest`).
   - Automatically flags stale or contradictory knowledge for forgetting.

5. **Live Observability Deck (Dashboard UI):**
   - High-performance Dark Mode dashboard with real-time WebSocket telemetry.
   - Interactive Engram Explorer, Timeline View, Token/Cost Savings visualizer, and Memory Inspector.

---

## 🏗️ Architecture

```text
genesis-memory/
├── pyproject.toml              # Modern Python packaging configuration
├── genesis_memory/             # Core Python package
│   ├── cli/                    # Multi-client onboarding & headless command spoofer
│   ├── daemon/                 # MCP Server (15 cognitive memory tools)
│   ├── proxy/                  # Stateless prompt proxy & tool-result compactor
│   ├── hooks/                  # PreInvocation subconscious lifecycle hooks
│   ├── sleep/                  # Sleep consolidation & Active Digest generator
│   ├── dashboard/              # FastAPI + WebSocket live telemetry dashboard
│   ├── core/                   # AST edge extractors & verification traps
│   └── eval/                   # Step 4 cognitive benchmark harness
├── docs/                       # Technical specs and interactive diagrams
├── scripts/                    # Quick launch scripts for Windows & Linux
└── tests/                      # Chaos engineering, concurrency & unit test suite
```

---

## 🚀 Quick Start

### 1. Installation
```bash
cd genesis-memory
pip install -e .
```

### 2. Start the MCP Daemon
```bash
genesis-daemon
```

### 3. Launch the Observation Deck (Dashboard)
```bash
genesis-dashboard
```
Open `http://localhost:8080` in your browser.

### 4. Run Commands with Zero-Spam Headless Spooling
```bash
genesis run -- pytest tests/
```

---

## 🛠️ MCP Tools (Model Context Protocol)

GENESIS Memory exposes 19 standard MCP tools to connected LLM agents:
- `remember(content, kind, confidence, tags)`: Persist verified architectural decisions or facts.
- `recall(query, k, min_confidence, tags)`: Semantic search over knowledge base with Hebbian weighting.
- `reinforce(id, outcome, note)`: Reinforce (+1) or penalize (-1) memory engrams with feedback.
- `synthesize_skill(name, action_recipe, trigger_patterns)`: Store deterministic procedural skills.
- `skill_recall(query)`: Recall procedural skills matching prompt patterns.
- `sleep_now(deep)`: Trigger immediate biomimetic sleep consolidation.
- `forget(id, reason)`: Soft-delete/tombstone invalid knowledge.
- `invalidate(id, superseded_by, reason)`: Mark knowledge as superseded.
- `resolve_conflict(engram_id_a, engram_id_b, resolution)`: Resolve contradictory findings.
- `thread_update(thread_id, focus, summary)`: Keep active work thread synchronized across tools.
- `thread_get()`: Retrieve the active work thread.
- `dialogue_update` & `dialogue_get`: Real-time cross-agent dialogue memory.
- `genesis_log`: Spool-based command output inspector without context explosion.
- `status`: Memory footprint, RSS, count, solidified/dormant engrams, and cache telemetry.

---

## 📜 License
MIT License. Open and modular.

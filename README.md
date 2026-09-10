<p align="center">
  <img src="site/public/favicon.svg" width="80" alt="GENESIS Memory Logo" />
</p>

<h1 align="center">GENESIS Memory</h1>

<p align="center">
  <strong>The Persistent Brain & Token Diet for AI Coding Agents</strong>
</p>

<p align="center">
  <a href="https://github.com/HamidRezaeian/genesis-memory/actions"><img src="https://img.shields.io/badge/tests-232%20passed-22C55E?style=flat-square&logo=pytest" alt="Tests" /></a>
  <a href="https://github.com/HamidRezaeian/genesis-memory"><img src="https://img.shields.io/badge/version-0.4.0-38BDF8?style=flat-square" alt="Version" /></a>
  <a href="#"><img src="https://img.shields.io/badge/MCP%20Tools-19-8B5CF6?style=flat-square" alt="MCP Tools" /></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-gray?style=flat-square" alt="License" /></a>
</p>

<p align="center">
  Stop repeating yourself. Keep <strong>Cursor</strong>, <strong>Claude Code</strong>, <strong>OpenCode</strong>, <strong>Antigravity</strong>, and <strong>VS Code</strong> in sync — while cutting LLM token bills by <strong>40% to 70%</strong>.
</p>

---

## The Problem

Every AI coding agent suffers from **the Goldfish Effect** — ephemeral session amnesia. Each new conversation starts from scratch. Your agent forgets architectural decisions, active work threads, verified invariants, and hard-won debugging insights. You repeat yourself. Tokens are wasted. Context windows overflow.

**GENESIS Memory solves this.**

It provides a local-first, sub-millisecond cognitive memory layer that persists knowledge across sessions, clients, and even sleep cycles — without cloud dependencies, without context bloat, and without compromising privacy.

---

## Key Capabilities

### Subconscious Pre-Invocation Hook
Automatically injects the most relevant architectural decisions, active threads, and solidified rules into every agent prompt — under a strict **200-token budget**. Your agent starts every session already knowing what matters.

### Stateless Prompt Proxy & Token Optimizer
Intercepts LLM traffic and applies **Structural Tool-Pair Compaction**, collapsing verbose tool outputs while preserving critical schemas. Dynamic caching separates cached vs. uncached tokens and enforces the *Rule of Three*: memory is only valuable when it replaces action.

### Cross-Client Memory Continuity
One unified memory graph across **Antigravity**, **Claude Code**, **OpenCode**, **VS Code/Cline**, and **Cursor**. Start a task in Cursor, continue in Claude Code, finish in OpenCode — without losing a single byte of context.

### Biomimetic Sleep Consolidation
Emulates NREM/REM memory consolidation cycles. Raw episodic events are distilled into high-order semantic decisions. Stale or contradictory knowledge is automatically flagged for decay. Skills are synthesized from recurring patterns.

### Hebbian Reinforcement & Challenge Protocol
Agents can reinforce (+1) or penalize (-1) memory engrams based on outcomes. The **Respectful Challenge** mechanism lets agents propose improvements to solidified rules — with human approval required before any change takes effect.

### Zero-Trust Privacy Shield
Automatic entropy-based detection and redaction of API keys, secrets, and tokens before any data hits SQLite. Your proprietary code never leaves your machine.

### Live Observability Dashboard
Real-time WebSocket telemetry with interactive Engram Explorer, Timeline View, Token/Cost Savings visualizer, AST Dependency Graph, and Memory Inspector — all in a premium Dark Mode UI.

### 1-Click Multi-Client Setup
```bash
genesis setup --preview    # See what will be wired
genesis setup              # Wire all detected clients automatically
genesis setup --client cursor --client opencode  # Target specific clients
```

---

## Quick Start

### Installation

```bash
git clone https://github.com/HamidRezaeian/genesis-memory.git
cd genesis-memory
pip install -e .
```

### Wire All Your AI Clients (1-Click)

```bash
genesis setup
```

This auto-detects and configures Cursor, Claude Code, OpenCode, Antigravity, and VS Code/Cline.

### Start the MCP Daemon

```bash
genesis-daemon
```

### Launch the Dashboard

```bash
genesis-dashboard
```

Open [http://localhost:8080](http://localhost:8080) in your browser.

### Run Commands with Headless Spooling

```bash
genesis run -- pytest tests/
genesis run -- npm test
genesis run -- cargo build
```

Zero-spam output capture with intelligent summarization — no context window explosion.

---

## Architecture

```text
genesis-memory/
├── pyproject.toml              # Modern Python packaging (v0.4.0)
├── genesis_memory/             # Core Python package
│   ├── cli/                    # 1-click setup, headless spooler, auth commands
│   ├── daemon/                 # MCP Server (19 cognitive memory tools)
│   ├── proxy/                  # Stateless prompt proxy & tool-result compactor
│   ├── hooks/                  # Subconscious pre-invocation lifecycle hooks
│   ├── sleep/                  # Biomimetic sleep consolidation & digest generator
│   ├── dashboard/              # FastAPI + WebSocket live telemetry dashboard
│   ├── core/                   # AST edge extractors, licensing engine, verification
│   └── eval/                   # Cognitive benchmark harness (Step 4)
├── site/                       # React/Vite commercial landing page
├── docs/                       # Technical specs and interactive diagrams
├── scripts/                    # Quick launch scripts (Windows & Linux)
└── tests/                      # 232 tests — chaos, concurrency & unit suite
```

---

## MCP Tools Reference

GENESIS Memory exposes **19 standard MCP tools** to connected LLM agents:

| Tool | Description |
|------|-------------|
| `remember` | Persist verified architectural decisions, facts, or patterns |
| `recall` | Semantic search over knowledge base with Hebbian weighting |
| `reinforce` | Reinforce (+1) or penalize (-1) memory engrams with outcome feedback |
| `forget` | Soft-delete/tombstone invalid knowledge |
| `invalidate` | Mark knowledge as superseded by newer findings |
| `resolve_conflict` | Resolve contradictory engrams with human-approved resolution |
| `challenge_rule` | Propose improvements to solidified rules (requires human approval) |
| `get_dependencies` | Retrieve AST-level code dependency edges |
| `attest_closure` | Verify and attest task completion with evidence |
| `synthesize_skill` | Store deterministic procedural skills from recurring patterns |
| `skill_recall` | Recall procedural skills matching prompt patterns |
| `sleep_now` | Trigger immediate biomimetic sleep consolidation cycle |
| `genesis_log` | Spool-based command output inspector (zero context explosion) |
| `status` | Memory footprint, RSS, engram counts, cache telemetry |
| `thread_update` | Synchronize active work threads across all clients |
| `thread_get` | Retrieve the current active work thread |
| `dialogue_update` | Update cross-agent dialogue memory |
| `dialogue_get` | Retrieve cross-agent dialogue state |
| `cross_client_resolve` | Resolve cross-client memory conflicts |

---

## Commercial Licensing

GENESIS Memory uses an **offline-first cryptographic licensing system** (HMAC-SHA256). No network calls required — works fully air-gapped.

### Tiers

| | Community | Developer Pro | Enterprise Gateway |
|---|---|---|---|
| **Price** | Free & Open Source | $14/mo or $99 lifetime | $39/seat/month |
| Local SQLite Memory | ✅ | ✅ | ✅ |
| Subconscious Hook | ✅ | ✅ | ✅ |
| Headless Spooling | ✅ | ✅ | ✅ |
| 1-Click Client Setup | ✅ | ✅ | ✅ |
| High-Ratio Compactor | — | ✅ | ✅ |
| Hebbian Sleep Distillation | — | ✅ | ✅ |
| Visual Telemetry Dashboard | — | ✅ | ✅ |
| Priority Token Budget | — | ✅ | ✅ |
| Team Shared Memory Sync | — | — | ✅ |
| On-Premises Docker Gateway | — | — | ✅ |
| Zero-Leak Audit Logs | — | — | ✅ |
| SLA Support | — | — | ✅ |

### Activation

```bash
genesis auth --key GEN-PRO-<your-license-key>
genesis license              # Check current tier and status
```

---

## Supported Clients

| Client | Config Path | Status |
|--------|-------------|--------|
| **Cursor** | `~/.cursor/mcp.json` | ✅ Full Support |
| **Claude Code** | `~/.claude/mcp.json` | ✅ Full Support |
| **OpenCode** | `~/.config/opencode/opencode.jsonc` | ✅ Full Support |
| **Antigravity** | `~/.gemini/antigravity-ide/mcp/` | ✅ Full Support |
| **VS Code / Cline** | `.vscode/mcp.json` | ✅ Full Support |

---

## Testing

```bash
# Run full test suite
pytest tests/

# Run specific test modules
pytest tests/test_genesis_daemon.py      # Core MCP daemon
pytest tests/test_licensing.py           # Licensing engine
pytest tests/test_challenge_rule.py      # Challenge protocol
pytest tests/test_hebbian_and_skills.py  # Sleep & skills
pytest tests/test_tool_compactor.py      # Token compaction
pytest tests/test_genesis_proxy.py       # Proxy TTFT
```

**Current status:** 232 tests, 231 passed, 1 skipped, 0 failures.

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/amazing-feature`)
3. Write tests for your changes
4. Ensure all 232+ tests pass (`pytest tests/`)
5. Commit with conventional commits (`feat:`, `fix:`, `docs:`)
6. Open a Pull Request

---

## Links

- [GitHub Repository](https://github.com/HamidRezaeian/genesis-memory)
- [Landing Page](https://genesis-memory.dev) *(coming soon)*
- [Documentation](docs/)

---

<p align="center">
  <strong>Built with obsessive attention to token efficiency and developer experience.</strong><br/>
  <sub>GENESIS Memory — because your AI agent deserves a brain that doesn't reset every session.</sub>
</p>

## License

MIT License. Open and modular.

# 🧠 GENESIS Memory Architecture

## 1. Core Philosophy: The Rule of Three
> **Memory is only valuable when it replaces an action, not when it adds overhead to the context.**

Traditional agent memory systems blindly append conversation logs or vectorized chunks to the context window, causing exponential token cost escalation and diluting attention (the "needle in a haystack" problem).

GENESIS Memory operates under three foundational pillars:
1. **First-Order Epistemic Priority:** Grounded architectural decisions, verified facts, and project invariants take precedence over general model training biases.
2. **Stateless Transformation:** LLM clients remain stateless. The prompt proxy strips verbose tool payloads and compresses message bodies dynamically without mutating the client's session state.
3. **Biological Memory Consolidation:** Memory is divided into:
   - **Working Memory / Subconscious Buffer:** Pre-invocation injection of the active work thread, uncompacted recent dialogue, and relevant decisions.
   - **Episodic Ledger (`ledger.jsonl`):** Append-only raw trace of session receipts, actions, and observations.
   - **Semantic Knowledge (`memory.db`):** Consolidated, deduplicated, and audited high-order decisions stored in an ultra-compact SQLite FTS5 database (<100MB RSS budget).

---

## 2. Component Pipeline

```mermaid
graph TD
    Client[LLM Client / IDE] -->|1. PreInvocation Hook| Hook[Subconscious Hook]
    Hook -->|FTS5 BM25 Recall| DB[(SQLite memory.db)]
    Hook -->|Inject Pinned Context| Client
    Client -->|2. Send Prompt / Tool Calls| Proxy[Stateless Proxy :8000]
    Proxy -->|Tool Result Compaction| Compactor[Tool Compactor]
    Proxy -->|Body Compression| Compressor[Content Compressor]
    Proxy -->|Accounting & Upstream| LLM[LLM Provider API]
    LLM -->|Streamed Response| Proxy
    Proxy -->|Streamed Tokens| Client
    Client -->|3. Save Decision / Fact| Daemon[MCP Server]
    Daemon -->|Secret Scanner & Veto Queue| DB
    Night[Periodic / Session End] -->|4. Sleep Consolidation| Sleep[Sleep Engine]
    Sleep -->|Prune & Abstract| DB
    Sleep -->|Auto-Update| Digest[Active Digest in AGENTS.md]
    DB -->|5. Telemetry Stream| Dashboard[Dashboard Web UI :8080]
```

---

## 3. Storage Invariants & Security
- **Strictly Local:** SQLite with WAL mode enabled. Zero external network calls for memory storage.
- **Fail-Closed Secret Scanner:** All content passed to `remember()` passes through sub-millisecond regex filters blocking API keys (OpenAI, Anthropic, AWS, GitHub) and private key PEM blocks.
- **Audited Tombstoning:** Memories are never silently destroyed. `forget()` marks them with tombstone markers preserving auditability.

# 🔌 GENESIS Memory MCP Tools Specification

The Model Context Protocol (MCP) server runs via standard I/O (JSON-RPC) and exposes the following tools:

| Tool Name | Parameters | Description |
|---|---|---|
| `remember` | `content` (str), `kind` (decision/fact/outcome), `confidence` (float), `tags` (list) | Stores a verified architectural decision, invariant, or outcome in SQLite with secret scanning. |
| `recall` | `query` (str), `k` (int), `min_confidence` (float), `tags` (list) | FTS5 BM25 semantic query retrieving top-k scored engrams. |
| `forget` | `id` (int), `reason` (str) | Marks an engram as tombstoned/forgotten. |
| `invalidate` | `id` (int), `superseded_by` (int), `reason` (str) | Invalidates an engram in favor of a newer one. |
| `resolve_conflict` | `engram_id_a` (int), `engram_id_b` (int), `resolution` (str) | Resolves contradictory records and archives resolution rationale. |
| `thread_update` | `thread_id` (str), `topic` (str), `summary` (str), `pending_focus` (str) | Updates the live cross-client active work thread. |
| `thread_get` | none | Fetches the current active work thread. |
| `dialogue_update` | `role` (str), `content` (str), `client` (str) | Appends a dialogue turn into the fast circular dialogue buffer. |
| `dialogue_get` | `limit` (int) | Retrieves the latest uncompacted dialogue turns. |
| `genesis_log` | `id` (str), `offset` (int), `grep` (str), `full` (str) | Dereferences raw headless command output from `~/.genesis/spool/` without context blowout. |
| `status` | none | Returns database statistics, RSS consumption, episode counts, and schema version. |
| `get_dependencies` | `engram_id` (int) | Retrieves graph relationships and AST links for an engram. |
| `attest_closure` | `engram_id` (int), `proof` (str) | Attests mathematical or test-backed closure to an engram. |

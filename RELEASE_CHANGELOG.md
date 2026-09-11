# GENESIS Memory — Release Changelog

## v0.6.0 — "First public PyPI release" (2026-09-11)

First installable release (`pip install genesis-memory && genesis setup`). Everything
an agent needs now ships **inside the product** — no repo checkout, no side files,
no user instruction required:

- **Standing spool order in every capsule** (`hooks/subconscious_hook.py`): a permanent
  Layer-3 directive injected from the first turn on every client, so long tool outputs
  never flood model context again. Pointer-short (41 chars) to hold the 200-token
  capsule cap; telemetry `spool_rule_applied`.
- **Contract ships in the wheel** (`genesis_memory/data/llms.txt|openapi.json`):
  the deck resolves them via `importlib.resources` with repo-root fallback; a test
  pins the packaged copy byte-identical to root.
- **CLI `--help` no longer tracebacks** (`cli/run.py`): `-h/--help/help` print usage,
  exit 0 (was `FileNotFoundError` — the worst possible first run after install).
- **Cross-OS fixture SHA** (`eval/step4_harness.py`): sort by POSIX path so the
  locked hash matches on Windows and Linux.
- Suite: **437 passing / 0 failing**.

## v0.5.0 — "Mission Control" (2026-09-10)

This release rebuilds the two user-facing surfaces (landing page and telemetry dashboard) from first principles, makes GENESIS universally compatible with every AI coding environment through a data-driven client registry and config exporter, hardens SQLite for many concurrent agents, ships a real entropy-based privacy shield at every storage boundary, extends the headless spooler to 60+ toolchains, adds an Anthropic-compatible gateway route, and takes the test suite from **274 passing / 6 failing** to **421 passing / 0 failing**.

Every entry below states **WHAT** changed, **WHY** that implementation was chosen, and the **IMPACT** on the product.

---

## 1. Baseline repairs (the suite was red on macOS)

### `genesis_memory/daemon/server.py` — `rss_mb()`
- **What:** `ru_maxrss` is now divided by 1 048 576 on macOS and 1 024 on Linux.
- **Why:** BSD/macOS report `ru_maxrss` in **bytes**, Linux in **kilobytes**. The old code reported 27 600 MB on a Mac and failed both RSS gates.
- **Impact:** Accurate RSS telemetry everywhere; the `<100 MB` product budget is enforceable again.

### `genesis_memory/hooks/subconscious_hook.py` — hook session keys
- **What:** Per-turn dialogue rows are keyed `hook:<client>:<time_ns>:<random>` instead of `hook:<client>:<ms>`.
- **Why:** Three rapid turns within one millisecond collided on a fast machine and clobbered each other (`test_hook_rapid_turns_do_not_clobber`).
- **Impact:** Cross-client dialogue capture never loses a turn.

### `genesis_memory/eval/step4_harness.py` + `scratch/step4_prereg_v2.json` — fixture hash
- **What:** `compute_fixture_sha()` canonicalises paths to POSIX separators and line endings to LF; the pre-registered SHA was updated to the canonical value of the committed fixture.
- **Why:** The lock was computed on another OS/checkout; the hash is meant to lock *content*, not `\r\n` or `\`.
- **Impact:** The Step-4 validity gate is OS-independent and passes on every checkout.

### `genesis_memory/proxy/pricing_engine.py` + new `genesis_memory/proxy/pricing_catalog.json`
- **What:** Extracted `normalize_catalog()`; the engine now loads `disk cache → bundled snapshot (437 models) → live refresh`. `engine.source` reports which one is active.
- **Why:** A test (and the product claim "400+ models") depended on network state in `~/.genesis`. Offline-first software must ship its own baseline.
- **Impact:** Deterministic pricing on first launch and in air-gapped environments; dollars-saved telemetry never reads zero.

### `tests/conftest.py` — hermetic hook
- **What:** The autouse fixture points `subconscious_hook.REPO_ROOT` at an empty temp directory.
- **Why:** The hook shells out to `git status` and appends a "Delta" line; token-budget assertions therefore depended on the developer's dirty working tree.
- **Impact:** Zero flaky tests regardless of git state.

---

## 2. SQLite concurrency hardening

### New `genesis_memory/core/db.py`
- **What:** `connect()` (WAL, `busy_timeout=5000`, `synchronous=NORMAL`, `foreign_keys=ON`, optional read-only URI, optional cache cap), `transaction()` (`BEGIN IMMEDIATE … COMMIT/ROLLBACK`), `retry_on_lock()` / `@locked_retry` (exponential backoff + `os.urandom` jitter), `is_lock_error()`, `checkpoint()`.
- **Why:** WAL alone is not enough — writers that find the lock held must *wait* (busy handler), acquire the RESERVED lock up-front (`BEGIN IMMEDIATE`) so they serialise instead of hitting `SQLITE_BUSY_SNAPSHOT` mid-transaction, and retry the rare storm that outlasts the handler. No `typing`/`dataclasses`/`random` imports: the module is loaded by the RSS-budgeted daemon (measured 1.3 MB).
- **Impact:** Multiple agents in multiple processes share one `memory.db` with zero `database is locked`. Verified by `tests/test_db_hardening.py` (8 threads × 25 writes; 4 spawned processes × 15 JSON-RPC calls).

### Adoption across every process
- `daemon/server.py`: `Store` opens via `_dbx.connect(path, cache_kib=1000)`; migration runs under `retry_on_lock`; `handle()` now wraps `_handle_once()` and **rolls back + retries** a tool call that loses a write race instead of returning an error to the agent.
- `core/team_sync.py` (8 sites), `sleep/sleep_daemon.py`, `sleep/digest_generator.py` (read-only), `sleep/sleep_consolidation.py` (read-only), `sleep/end_session.py` (read-only), `cli/doctor.py` (read-only), `cli/init_cmd.py` (init + read-only handshake), `cli/run.py` (read-only skills list), `dashboard/server.py` (read-only everywhere).
- **Why:** One contract, one code path. Dashboards and report generators can never mutate the store by accident.

---

## 3. Zero-trust privacy shield

### New `genesis_memory/core/privacy_shield.py`
- **What:** 15 structural detectors (OpenAI/Anthropic `sk-`, Google `AIza`, GitHub `gh*_`/`github_pat_`, AWS `AKIA/ASIA`, Slack `xox*`, Stripe, SendGrid, npm, Hugging Face, JWT, `Bearer`, PEM blocks incl. unclosed fail-closed, basic-auth URLs, `KEY=value` assignments) plus a **Shannon-entropy gate** (≥4.0 bits/char over ≥20-char tokens, mixed-class requirement in the mid band, hex/URL/path/identifier negatives). API: `redact()`, `scan()`, `redact_bytes()` (surrogateescape-safe), `explain()`, `ShieldReport`.
- **Why:** The README promised "entropy-based detection" but the code was seven regexes. Real credentials often have no vendor prefix; entropy catches them. Hex is excluded because git SHAs and digests are legitimate memory and hex credentials are caught by the assignment detector.
- **Impact:** Random 32-byte tokens are caught; prose, paths, URLs, `snake_case`, `CamelCase` and SHAs are untouched (both properties pinned by tests). Telemetry counts redactions, never their content.

### `daemon/server.py` delegates
- `scan_secrets()` / `redact_secrets()` now call the shield; `SECRET_PATTERNS` remains for import compatibility.

### `proxy/spool.py` — redaction **before** the atomic write
- **What:** `write_spool()` runs `redact_bytes()` on the payload, records `privacy_shield` in the `.meta.json`, increments `secrets_blocked`. `GENESIS_SPOOL_RAW=1` opts out.
- **Why:** The spool was previously documented as "deliberately RAW". Command output is the most common place real secrets appear (`env`, failing tests printing config). Non-secret bytes still round-trip exactly.
- **Impact:** Secrets never touch disk anywhere in GENESIS. `tests/test_privacy_redaction.py` was updated from "spool is raw" to "spool redacts; benign bytes byte-identical; opt-out works".

---

## 4. Universal client support

### `genesis_memory/cli/client_registry.py` — rewritten as a data-driven registry
- **What:** `ClientSpec` dataclass (config path resolver, detector, renderer, configured-check, format, comment token, `owns_file`, aux wiring, aliases) and a `CLIENT_SPECS` table of **20 environments**: OpenCode, Cursor, Claude Code, Claude Desktop, Antigravity, Windsurf, Zed, VS Code (Copilot agent mode `servers`), Cline, Roo Code, Continue.dev (yaml drop-in), JetBrains Junie/AI Assistant, Neovim (mcphub → Avante/CodeCompanion/Copilot.lua), Emacs (gptel/aidermacs/mcp.el), Aider, OpenAI Codex CLI (toml), Gemini CLI, Amazon Q, Goose (yaml), and an SDK/framework gateway (`genesis.env` for LangChain/LlamaIndex/CrewAI/AutoGen/OpenAI/Anthropic). `ClientRegistry` gains `specs()`, `spec(id_or_alias)`, `client_ids()`, `render_config()`, `unwire_client()`; all legacy getters and `generate_patch()`/`wire_client()`/`discover_all()` signatures are preserved.
- **Why:** The old module was 860 lines of copy-pasted `if client_id == …` branches with a 350-line JavaScript f-string embedded in Python. Adding a client meant editing four places. A table plus small renderers makes a new client a ~10-line entry and lets the exporter, dashboard and setup share one truth.
- **Impact:** `genesis setup` wires every major AI environment; structured configs are deep-merged (existing servers preserved), text configs get an idempotent marker block, native clients write nothing. Detection remains strictly read-only (test asserts no filesystem writes).

### New `genesis_memory/cli/templates/opencode_plugin.js`
- **What:** The OpenCode plugin moved out of the f-string into a real `.js` file (byte-identical to previous output, verified by diff; `node --check` clean).
- **Why:** Syntax highlighting, linting and diffing for a 350-line plugin; no more `{{`/`}}` escaping bugs.

### New `genesis_memory/cli/config_formats.py`
- **What:** Dependency-free emitters `to_json`, `to_yaml`, `to_toml`, `to_env`, `render(fmt)`, and `merge_marker_block` / `strip_marker_block` / `has_marker_block`.
- **Why:** Pulling PyYAML/tomli-w for a dozen keys is not worth the dependency; emitted TOML is verified to round-trip with `tomllib`, YAML with PyYAML where available.
- **Impact:** GENESIS can speak every config dialect an editor uses without touching a byte outside its own block.

### New `genesis_memory/cli/export_config.py` + CLI subcommands
- **What:** `genesis export-config [--client id|alias] [--format json|yaml|toml|env|lua|elisp|native] [--out file] [--all]` and `genesis clients [--json]`. `genesis dashboard` launches Mission Control.
- **Why:** Even with 20 auto-wired clients there will always be a 21st; the exporter renders from the same specs so copy-paste equals auto-wire.

---

## 5. Headless spooler expansion — `genesis_memory/cli/run.py`
- **What:** `ALLOWLIST_COMMANDS` grew from 8 to 60+ (Python, JS/TS, Rust, Go, .NET, JVM, C-family, Ruby, PHP, Elixir, Swift, VCS, containers, infra). `ALLOWLIST_SUBCOMMANDS` covers 30 multi-word tools. `HEADLESS_ENV` (50 variables) and `build_headless_env()` make every toolchain non-interactive, colour-free and pager-free. `normalize_program()` handles `.exe/.cmd/.bat/.ps1/.sh` and `./gradlew`. Per-tool interactive-flag rejection (`git rebase -i`, `docker run -it`, `kubectl logs -f`, `--watch`) plus global `--interactive/--watch`.
- **Why:** Scoped rejection instead of a global flag set so `pytest -p plugin` and `pip install -e .` remain spoolable while `git add -p` is refused.
- **Impact:** Any build/test/lint/infra command becomes a pointer, never a context explosion; the child process can never block on a TTY.

---

## 6. Gateway — Anthropic Messages route — `genesis_memory/proxy/proxy_server.py`
- **What:** `POST /v1/messages` (and `/messages`) with structural compaction (`format_type="anthropic"`), memory-capsule injection from the last user turn, `x-api-key`/`anthropic-version` injection, byte-exact SSE relay, usage accounting (`input_tokens`, `cache_read_input_tokens`, `output_tokens`), shadow-mode non-mutation, and fail-open on validator rejection. Env: `GENESIS_ANTHROPIC_UPSTREAM_URL`, `GENESIS_ANTHROPIC_KEY`/`ANTHROPIC_API_KEY`.
- **Why:** "Any custom agent connects with zero code changes" must hold for the Anthropic SDK, not only OpenAI.
- **Impact:** `ANTHROPIC_BASE_URL=http://127.0.0.1:8000` is a drop-in. Covered by `tests/test_proxy_anthropic_route.py`.

---

## 7. Mission Control dashboard — complete re-creation

### `genesis_memory/dashboard/server.py` (rewritten)
- **What:** `DashboardRepo` (read-only SQL, schema-tolerant), `build_overview()` (one round-trip payload), **SSE** `GET /api/stream`, `GET /api/engrams` (FTS5 BM25 search, filters, pagination), `/api/engram/<id>`, `/api/graph` (engram nodes + conflict/supersession/lexical synapses + AST edges), `/api/skills`, `/api/conflicts`, `/api/thread`, `/api/dialogue`, `/api/timeline`, `/api/edges`, `/api/clients` (live registry), `/api/spool`, `/api/ledger`, `/api/proxy_telemetry`, `/api/pricing` (falls back to the bundled catalog when the gateway is offline), `/api/sleep`; **POST** `/api/shield` (redaction preview, never stored), `/api/conflicts/<id>/resolve`, `/api/reinforce`, `/api/sleep/now`. Path-traversal-safe static serving; `make_server()` for tests; `main()` entry (`--port --db --no-sleep --open`).
- **Why:** The old server polled six endpoints and served a 4,500-line HTML file that referenced a proxy-only pricing route. Real-time telemetry needs a push channel; a cockpit needs one payload per tick.
- **Impact:** Zero lag, zero layout shift; every action explicit and audited. `tests/test_dashboard_server.py` covers all routes including SSE and 404/traversal.

### `genesis_memory/dashboard/static/index.html` (rewritten) · `dashboard.html` (deleted)
- **What:** A zero-dependency cockpit: live ticker (tokens/dollars saved, engrams, RSS), 10 views with keyboard shortcuts `1–0`, `⌘K` command palette, **Memory Universe** (force-directed orbital canvas graph — orbit = utility, size = tokens, glow = recency, conflict dashes, travelling signal pulses, drag/zoom/inspect/reinforce), **Engram Explorer** (BM25 search, `↑/↓` navigation, detail pane), **Conflict Deck** (accept/keep/dismiss), **Skill Browser**, **Sleep & Hebbian** (τ curves with live dots, report generation, `sleep now`), **Token Diet** (prompt anatomy, bypass reasons, pricing catalog), **Client Mesh** (animated 20-client conduit with detection state), **Privacy Shield sandbox** (entropy bars + detector hits), **Spool & Ledger**.
- **Why:** The previous dashboard had no graph physics, no keyboard ergonomics and no live channel. Rebuilding on canvas + SSE with hand-written CSS keeps it under one file, instant to load and dependency-free.

---

## 8. Landing page — complete re-creation (`site/`)
- **What:** React 19 + Vite 7 + Framer Motion 12. Files: `index.html`, `src/index.css` (design system: OLED black, electric cyan, emerald, deep slate, holographic cards, magnetic buttons, terminal), `src/lib/hooks.js` (pointer parallax, in-view, eased counters, holo vars), `src/lib/shield.js` (browser port of the privacy shield — same detectors and 4.0-bit threshold), and components: `NeuralField` (3D-projected 1,100-particle cortex with parallax, depth fog, specular flares, travelling pulses), `Hero` (ignition intro, word-reveal headline, live savings ticker, orbiting engram chips, client marquee), **`SpoolDemo`** (4,000 lines stream, collapse to a single point of light, 84-token pointer emitted, −98.4 % ring), **`DietSlider`** (turns/tool-lines sliders → live prompt anatomy, 176-token capsule budget bar), **`ShieldSandbox`** (type anything → live redaction + per-token entropy bars), **`Conduit`** (Cursor → Claude Code → Windsurf → Zed → Neovim memory passing with pulses and provenance log), **`SleepViz`** (Hebbian τ curves with sliding engrams, reinforce button, NREM/REM/WAKE cycle, skill distillation), `Features` (bento + mini universe), `Setup` (simulated `genesis setup` over 20 clients + live `export-config` tabs in native/json/yaml/toml/env), `Pricing`, `Faq`, `Footer`. Removed the old 1,200-line `App.jsx`, `App.css`, stock Vite/React assets.
- **Why:** The brief demanded "show, don't tell". Each capability is a working simulator built from the same numbers the product produces. Canvas 2D with manual perspective projection gives 60 fps 3D without a WebGL dependency the corporate registry couldn't provide.
- **Impact:** `npm run build` → 416 kB JS / 8 kB CSS; `npm run preview` on `http://localhost:4173`.
- **Toolchain note:** pinned `vite@7.3.1`, `@vitejs/plugin-react@5`, `framer-motion@12`, `lucide-react@0.500` because the available npm mirror forbids Vite 8.

---

## 9. Tests (274 → 421 passing, 0 failing)
- New: `tests/test_db_hardening.py` (16 tests: pragmas, read-only, transactions, retry, thread storm, **multi-process storm**, dispatcher retry), `tests/test_privacy_shield.py` (40+ parametrised cases: entropy math, structural detectors, false-positive corpus, byte round-trip, store rejection), `tests/test_universal_clients.py` (registry completeness, aliases, read-only discovery, every spec renders in its dialect with TOML round-trip, marker-block idempotence and unwire, deep-merge, export-config formats/CLI/`--all`, `genesis clients`, 34 accepted toolchains, 21 rejected interactive shapes, headless env, end-to-end `genesis run -- git log`), `tests/test_dashboard_server.py` (all routes, SSE stream, POST actions, traversal 404, missing-DB tolerance, CLI wiring), `tests/test_proxy_anthropic_route.py` (compaction + usage, streaming + error forwarding, shadow mode).
- Updated: `tests/test_privacy_redaction.py` (spool now redacts), `tests/test_dialogue_buffer.py` (plugin invariants read from the template), `tests/conftest.py` (hermetic hook).

---

## 10. Packaging & docs
- `pyproject.toml`: version **0.5.0**, new description, `package-data` for `pricing_catalog.json`, `templates/*.js`, `static/*.html`; pytest config.
- `README.md`: rewritten — universal client table, Mermaid architecture, benchmarks, quick start, capability deep-dives.
- `site/package.json`: renamed/versioned.
- This file.

---

### Upgrade notes
- No schema migration required (schema stays at v9). WAL is enabled automatically on first connection.
- Spool output is now redacted by default; set `GENESIS_SPOOL_RAW=1` for forensic captures.
- `dashboard.html` was removed; `genesis-dashboard` / `genesis dashboard` serve `static/index.html` on port **8090** (`GENESIS_DASHBOARD_PORT` to change).

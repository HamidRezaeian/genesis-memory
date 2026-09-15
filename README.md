<p align="center">
  <img src="https://raw.githubusercontent.com/HamidRezaeian/genesis-memory/main/site/public/favicon.svg" width="84" alt="GENESIS Memory" />
</p>

<h1 align="center">GENESIS Memory</h1>

<p align="center">
  <strong>The persistent brain &amp; token diet for <em>every</em> AI coding agent.</strong>
</p>

<p align="center">
  <a href="https://hamidrezaeian.github.io/genesis-memory-site/"><img src="https://img.shields.io/badge/website-interactive_demo-00F0FF?style=flat-square&logo=googlechrome&logoColor=white" alt="Website" /></a>
  <img src="https://img.shields.io/badge/tests-440%20passed-22C55E?style=flat-square&logo=pytest" alt="Tests" />
  <img src="https://img.shields.io/badge/version-0.14.1-00F0FF?style=flat-square" alt="Version" />
  <img src="https://img.shields.io/badge/clients-20%20auto--wired-8B5CF6?style=flat-square" alt="Clients" />
  <img src="https://img.shields.io/badge/MCP%20tools-19%20%2B%20gateway-38BDF8?style=flat-square" alt="MCP Tools" />
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/license-BSL_1.1-gray?style=flat-square" alt="License" />
</p>

<p align="center">
  One local SQLite memory shared by <strong>Cursor</strong>, <strong>Claude Code</strong>, <strong>VS Code</strong>, <strong>Zed</strong>, <strong>Windsurf</strong>, <strong>JetBrains</strong>, <strong>Neovim</strong>, <strong>Emacs</strong>, <strong>OpenCode</strong>, <strong>Antigravity</strong>, every terminal agent and every SDK —<br/>
  while collapsing 4,000-line tool outputs into 84-token pointers and cutting outbound tokens by 70.4% via transparent output diet.
</p>



---

## Why

Every AI coding agent suffers from the **Goldfish Effect**: each session starts from blank slate. Architectural decisions, invariants, cross-client active threads, and hard-won debugging insight evaporate. You repeat yourself; the agent re-reads 80k tokens of previous test output; your token bill grows.

GENESIS is a **local-first cognitive memory OS** that operates across three clean, modular primitives:

| Primitive | What it does | Network / Execution | Who uses it |
|---|---|---|---|
| **MCP** (stdio) | 19 cognitive tools (`remember`, `recall`, `reinforce`, `thread_update`, `dialogue_update`, …) or a single unified `genesis` gateway tool | **100% Local** · Offline · Zero network · Direct SQLite WAL | Cursor, Claude Desktop, Claude Code, VS Code, Zed, Windsurf, JetBrains, Neovim, Emacs, Codex, Gemini CLI, Cline, Roo |
| **Hook** (pre-prompt) | Injects a ≤200-token *subconscious capsule* (active thread, last cross-client dialogue, top engrams) before model turn | **100% Local** · Offline · Sub-10ms execution · Zero API keys | Cursor 1.7 (`hooks.json`), Claude Code (`PrePrompt`), OpenCode plugin, Antigravity |
| **Proxy** (gateway) | Stateless OpenAI & Anthropic reverse proxy (`127.0.0.1:8000`). Tool-pair compaction, output token diet, anaphora rewriting | **Local Gateway** · Transparent HTTP pass-through | Aider, OpenCode (`genesis-proxy`), LangChain, LlamaIndex, CrewAI, AutoGen, any SDK |

> **Crucial Guarantee:** MCP and Hook are **100% local and offline**. Installing GENESIS does **not** hijack your IDE's native model subscriptions (e.g. Claude 3.5 Sonnet in Cursor or Claude Desktop). Your models continue talking directly to their official backends; GENESIS supplies memory alongside them.

---

---

## 🚀 3-Step Quick Start (Takes 30 Seconds)

You do not need to configure databases, spin up servers, or change your API keys.

### Step 1: Install the package
```bash
pip install genesis-memory
```

### Step 2: Auto-connect your AI tools (memory + optional proxy gateway)
```bash
genesis setup --yes
```
> **What this does:** Scans your computer for installed AI tools (Cursor, Claude Desktop, Claude Code, VS Code, OpenCode, Zed, JetBrains, etc.) and automatically connects them to a shared local memory. Your existing models, settings, and subscriptions are **100% untouched**. At the end of an interactive run it also offers to continue straight into Proxy gateway setup (`genesis proxy setup`) — one install flow, no second command to remember.

Single-command variant with the proxy included (non-interactive, CI-friendly):
```bash
genesis setup --yes --proxy --proxy-upstream-url https://openrouter.ai/api/v1 --proxy-api-key sk-or-v1-...
```

> **Same interpreter rule:** run the installer and the CLI with the same Python — `python -m pip install genesis-memory`, then `genesis ...` (or `python -m genesis_memory.cli.run ...`). Mixing a Microsoft-Store Python `pip` with an Anaconda `genesis`, or vice versa, installs the package where the CLI cannot see it.

### Step 3: Verify connection
```bash
genesis doctor
```
> You will see green checkmarks `✅ [OK]` confirming which AI clients are actively wired. `doctor` validates the real database schema with a live recall probe (not just a table count), so green actually means usable. **You're done!**

---

## 💡 How to use it in your daily workflow

Once installed, just open your favorite AI coding tool (Cursor, Claude, VS Code, etc.) and code as usual:

* **Save important decisions:**
  > *"Remember that we use SQLite in WAL mode and all timeouts must be 5000ms."*
  *(The agent calls `remember` and stores it permanently across all your tools.)*

* **Recall past context:**
  > *"What did we decide about the connection pool in our last session?"*
  *(The agent calls `recall` and pulls the exact decision from your shared memory.)*

* **Seamless cross-tool switching:**
  Fix a bug in **Cursor**, then open **OpenCode** or **Claude Code** — the agent automatically knows what you were working on without copy-pasting history.

---

## 🎛️ Optional Features (Only when you need them)

Everything below is completely optional. You only run these when you specifically want them:

| What you want to do | Command | What it does |
|---|---|---|
| **Open Visual Cockpit** | `genesis dashboard --open` | Opens interactive 3D memory visualizer & engram explorer in your browser (`:8090`). |
| **Setup & Connect Proxy** | `genesis proxy setup` (or `genesis setup --yes --proxy ...` inline during initial install) | 1-Minute wizard: enter provider URL & key, auto-wire clients, and start gateway. |
| **Check Proxy Status** | `genesis proxy status` | Checks if the proxy is running and prints active PID. |
| **Stop Proxy** | `genesis proxy stop` | Shuts down the background proxy. |
| **Start Proxy** | `genesis proxy start` | Starts local gateway on `http://127.0.0.1:8000` (requires credentials). |
| **Run Spooled Tests** | `genesis run -- pytest tests/` | Runs tests headlessly, collapsing 4,000 lines of output into an 84-token summary. |
| **100% Undo / Revert** | `genesis setup --revert` | Restores all your IDE config files to their exact pre-installation state from atomic backups. |
| **Upgrade to Latest** | `genesis upgrade` | 1-click self-upgrade to the latest release. |

---

## The Stateless Proxy Gateway (`127.0.0.1:8000`)

The GENESIS Proxy is an **intelligent, pass-through reverse proxy** that runs locally on `http://127.0.0.1:8000/v1`. It sits between your AI client and your upstream LLM provider (OpenRouter, OpenAI, Groq, Anthropic, etc.). Before sending prompts to your model, it injects relevant episodic memories, eliminates repetitive tool output bloat, and applies output token diets — cutting costs while keeping your agent continuously aware of past decisions.

> [!IMPORTANT]
> **Strict Gatekeeping Invariant:** The proxy **refuses to run** until you configure your upstream provider credentials. It will never start in a broken or unconfigured state.

---

### ⚡ 1-Minute Setup: Connecting Your Provider (e.g. OpenRouter)

Suppose you have an API key from **OpenRouter** (or OpenAI / Groq) and want to use it across your AI coding tools:

#### 1. Run the Setup Wizard
```bash
genesis proxy setup
```
*(Or non-interactively: `genesis proxy setup --upstream-url https://openrouter.ai/api/v1 --api-key sk-or-v1-... --yes`)*

#### 2. Enter Your Provider Credentials
The wizard asks for two things:
1. **Upstream Provider Base URL:** The API endpoint where you obtained your key.
   * For OpenRouter: `https://openrouter.ai/api/v1`
   * For OpenAI: `https://api.openai.com/v1`
   * For Groq: `https://api.groq.com/openai/v1`
   *(⚠️ Note: Enter your provider's API URL here, **NOT** `127.0.0.1:8000`.)*
2. **Provider API Key:** Your secret key (e.g. `sk-or-v1-...`).

#### 3. Automatic Client Activation (With Your Consent)
GENESIS scans your machine for compatible installed clients and asks:
```text
🔍 Detected compatible AI clients on your system:
  • OpenCode (C:\Users\Hamid\.config\opencode\opencode.jsonc)
  • Aider (~/.aider.conf.yml)
  • Python SDK / Frameworks (~/.genesis/genesis.env)

? Automatically activate GENESIS Proxy on these clients? [Y/n]: y
```
Once approved, GENESIS configures each client and shows a confirmed checkmark list:
```text
⚙️  Configuring clients for GENESIS Proxy (http://127.0.0.1:8000/v1)...
──────────────────────────────────────────────────────────────────────────────
  ✅ OpenCode                         -> C:\Users\Hamid\.config\opencode\opencode.jsonc
  ✅ Aider                            -> ~/.aider.conf.yml
  ✅ Python SDKs / Frameworks         -> ~/.genesis/genesis.env
──────────────────────────────────────────────────────────────────────────────
```
And starts the background gateway automatically (`🟢 Online`).

---

### 🎨 Model Freedom: Choose Any Model on the Fly!

You do **not** have to lock in or configure a model name up front during setup. You are completely free to pick **any model at any moment** directly inside your client:

* **In OpenCode:**
  Open your model selector (or press `Ctrl+P` / type `/model`). All models are neatly organized under the **`GENESIS Proxy`** category:
  * `GENESIS Auto (Stateless)`
  * `GPT-4o`
  * `Claude 3.5 Sonnet` / `Claude 3.7 Sonnet`
  * `DeepSeek V3` / `DeepSeek R1`
  * `Gemini 2.0 Flash`
  * *(or any custom model slug supported by your provider)*
* **In Aider:**
  Run `aider --model <any-model>` or let Aider use the proxy base URL directly.
* **In Python / SDKs:**
  Pass whatever model you want: `client.chat.completions.create(model="any-model-name", ...)`

GENESIS Proxy dynamically passes through your requested model verbatim to your upstream provider while injecting subconscious memory and governance into every request.

---

### Managing the Background Proxy

```bash
genesis proxy status    # Check status and health (🟢 Online or ⚪ Standby)
genesis proxy stop      # Stop the background proxy
genesis proxy start     # Start the background proxy (requires credentials)
genesis proxy setup     # Re-configure provider URL, API key, or clients
```
*Daemon logs are written to `~/.genesis/proxy.log` and active PID is tracked at `~/.genesis/proxy.pid`.*

---

### How Routing, APIs, and Keys Work

The proxy is completely **transparent and fail-open**:

* **Upstream Routing:**
  * Requests to `http://127.0.0.1:8000/v1/chat/completions` (OpenAI format) are forwarded to your configured Upstream Base URL (e.g. `https://openrouter.ai/api/v1/chat/completions`).
  * Requests to `http://127.0.0.1:8000/v1/messages` (Anthropic format) are forwarded to `GENESIS_ANTHROPIC_UPSTREAM_URL` (default: `https://api.anthropic.com/v1`).
* **Model Pass-Through:**
  * Whatever model your client requests (e.g. `gpt-4o`, `claude-3-5-sonnet`, `deepseek-chat`, `gpt 6 astra`), the proxy forwards verbatim to your upstream provider.
* **API Key Forwarding:**
  * **Configured Key:** The proxy automatically injects your configured upstream API key if the client sends `"not-needed"` or dummy credentials.
  * **Client Header Pass-Through:** If the client provides its own `Authorization: Bearer <KEY>` header, the proxy forwards it directly.


### 3. Client Integration Recipes

#### Python — OpenAI SDK
```python
from openai import OpenAI
import os

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key=os.environ.get("OPENAI_API_KEY", "not-needed"),
)

response = client.chat.completions.create(
    model="gpt-4o",  # or any upstream model
    messages=[{"role": "user", "content": "Fix the connection timeout in db.py"}],
)
print(response.choices[0].message.content)
```

#### Python — Anthropic SDK
```python
from anthropic import Anthropic
import os

client = Anthropic(
    base_url="http://127.0.0.1:8000",
    api_key=os.environ.get("ANTHROPIC_API_KEY", "not-needed"),
)

message = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello through GENESIS gateway"}],
)
print(message.content[0].text)
```

#### Terminal Agents — Aider CLI
```bash
# Point Aider directly at the local gateway
aider --openai-api-base http://127.0.0.1:8000/v1 --model gpt-4o
```

#### Terminal Agents — OpenCode
In `~/.config/opencode/opencode.jsonc`, `genesis setup` registers the `GENESIS Proxy` provider. To use it, simply select `GENESIS Proxy` in OpenCode's model picker:
```jsonc
"provider": {
  "genesis-proxy": {
    "npm": "@ai-sdk/openai-compatible",
    "name": "GENESIS Proxy",
    "options": {
      "baseURL": "http://127.0.0.1:8000/v1",
      "apiKey": "not-needed"
    }
  }
}
```

#### Frameworks (LangChain, LlamaIndex, CrewAI, AutoGen)
Export the standard environment variable — zero code modifications required:
```bash
export OPENAI_BASE_URL="http://127.0.0.1:8000/v1"
```

### 4. Telemetry & Observability Endpoints

The proxy exposes live read-only telemetry:
* `GET http://127.0.0.1:8000/health` — Gateway status, upstream URL, active sessions, RSS memory
* `GET http://127.0.0.1:8000/v1/telemetry` — Real-time token counts, cache hit rates, TTFT latency, cost savings
* `GET http://127.0.0.1:8000/v1/receipts` — Itemized per-request receipts (usage and model costs, bodies never logged)
* `GET http://127.0.0.1:8000/v1/pricing` — Live token pricing catalog for 1,000+ models

---

## Output Token Diet & Empirical Live Benchmark

Input diet is lossless compression; output diet is developer choice. Since output tokens cost 2–5× more than input tokens and dictate user-facing latency, GENESIS enforces three transparent output governance mechanisms:

1. **Terse by Default (`GENESIS_OUTPUT_DIET=1` / `=auto`):** Injects a static, prefix-cache friendly directive instructing the model to eliminate conversational filler, hedging, and unsolicited tutorials. In `auto` mode, it automatically yields whenever depth or step-by-step explanation is explicitly requested.
2. **Turn-Shape Structural Budget (`GENESIS_TINY_BUDGET=1`):** Short acknowledgment and confirmation turns cannot reasonably require essays. They are bounded at `256` completion tokens. Caller-set `max_tokens` are always respected.
3. **Diffs, Not Pastes:** Instructs models to reply with concise unified diffs rather than re-pasting full 300-line files. A non-intrusive file-echo monitor observes and tracks paste violations.

### 100% Live Empirical Benchmark (Google Gemini 3.8 Flash)

Measured live against Google Generative Language API on frontier model `gemini-flash-latest` (Gemini 3.8 Flash) using official developer credentials and real coding prompts:

| Scenario | User Prompt | Raw Model (Without Diet) | With GENESIS Output Diet | Token Savings | Real Latency |
|:---|:---|:---|:---|:---:|:---:|
| **1. Code Bug Fix** | Increase `timeout_ms` default from 1000 to 5000 in DB Pool | **365 tokens**<br>*(Rewrote entire 40-line class + narrative explanations)* | **266 tokens**<br>*(Byte-exact unified diff only · zero file re-paste)* | **−27.1%** | 4.99s / 13.1s |
| **2. Turn Acknowledgment** | *"Thanks, the WAL configuration works properly now. Ready to continue."* | **194 tokens**<br>*(Polite conversational filler + unprompted PostgreSQL/SQLite guide)* | **7 tokens**<br>*(Pure acknowledgment: "Provide the next task or requirements.")* | **−96.4%** | 4.25s ➔ 2.89s (**1.5× faster**) |
| **3. Status Inspection** | *"Is the genesis daemon currently running and what is its status...?"* | **391 tokens**<br>*(Multi-page Linux manual, curl scripts, systemd tutorials)* | **48 tokens**<br>*(Actionable shell command only: `pgrep` + `ps`)* | **−87.7%** | 8.17s ➔ 6.64s (**1.2× faster**) |
| **Average Across Turns** | *Composite real-world developer workflow* | **950 total tokens** | **321 total tokens** | **−70.4%** | **Significant Speedup** |

*Reproducible runner script and raw JSON results: [`scratch/live_eval_runner.py`](scratch/live_eval_runner.py) and [`scratch/live_eval_results.json`](scratch/live_eval_results.json).*

---

## Universal client support

`genesis setup` is a **data-driven registry** (`genesis_memory/cli/client_registry.py`): each client declares where its config lives, how to detect it, which primitives it supports and how to render the patch in its own dialect. JSON/JSONC files are deep-merged (your existing servers and comments survive); YAML/TOML/Lisp/dotenv files get an idempotent `>>> genesis-memory >>>` marker block that re-runs replace and `--revert` removes.

| Client | Config | Primitives | Format | How It Connects |
|---|---|---|---|---|
| Cursor | `~/.cursor/mcp.json` + `hooks.json` | MCP · Hook | json | Local stdio MCP + pre-prompt hook. Direct LLM calls untouched. |
| Claude Code | `~/.claude/settings.json` + `~/.claude.json` | Hook · MCP | json | PrePrompt lifecycle hook + stdio MCP. |
| Claude Desktop | `…/Claude/claude_desktop_config.json` | MCP | json | Native stdio MCP tools. Direct Anthropic connection. |
| OpenCode | `~/.config/opencode/opencode.jsonc` + `plugins/` | MCP · Proxy · Hook | jsonc | Stdio MCP + plugin hook + optional `GENESIS Proxy` provider. |
| Antigravity IDE | `.agents/` (native) | MCP · Hook | native | Built-in native integration. |
| Windsurf (Codeium) | `~/.codeium/windsurf/mcp_config.json` | MCP · Proxy | json | Cascade MCP integration via mcp_config.json |
| Zed | `~/.config/zed/settings.json` → `context_servers` | MCP · Proxy | jsonc | Context servers in settings.json + OpenAI-compatible endpoint |
| VS Code (Copilot agent mode) | `…/Code/User/mcp.json` → `servers` | MCP · Proxy | jsonc | Native MCP servers (user-level mcp.json) |
| Cline | `…/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` | MCP · Proxy | json | Extension MCP settings + base URL proxy provider |
| Roo Code | `…/rooveterinaryinc.roo-cline/settings/mcp_settings.json` | MCP · Proxy | json | Global MCP settings + base URL proxy provider |
| Continue.dev | `~/.continue/mcpServers/genesis-memory.yaml` | MCP · Proxy | yaml | Drop-in MCP block for VS Code & JetBrains |
| JetBrains Junie / AI Assistant | `~/.junie/mcp/mcp.json` | MCP · Proxy | json | Junie global MCP; AI Assistant imports the same JSON |
| Neovim (mcphub · Avante · CodeCompanion) | `~/.config/mcphub/servers.json` (+ Lua) | MCP · Proxy | json / lua | mcphub.nvim servers.json; Lua snippet via export-config |
| Emacs (gptel · aidermacs · mcp.el) | `~/.emacs.d/genesis-memory.el` | MCP · Proxy | elisp | Self-contained .el: mcp-hub server + gptel backend |
| Aider | `~/.aider.conf.yml` | Proxy | yaml | `openai-api-base` pointing to local gateway |
| OpenAI Codex CLI | `~/.codex/config.toml` | MCP · Proxy | toml | `[mcp_servers.genesis-memory]` TOML table |
| Gemini CLI | `~/.gemini/settings.json` | MCP | json | `mcpServers` in settings.json |
| Amazon Q Developer CLI | `~/.aws/amazonq/mcp.json` | MCP | json | Stdio MCP configuration |
| Goose (Block) | `~/.config/goose/config.yaml` | MCP · Proxy | yaml | Extensions stdio configuration |
| Any SDK / framework | `~/.genesis/genesis.env` | Proxy | env | Pass `OPENAI_BASE_URL=http://127.0.0.1:8000/v1` |

---

## Architecture

```mermaid
flowchart LR
  subgraph Clients["20 AI clients · any SDK"]
    C1[Cursor] --- C2[Claude Code] --- C3[VS Code / Zed / JetBrains] --- C4[Neovim / Emacs] --- C5[LangChain · CrewAI · …]
  end
  Clients -- "stdio MCP (19 tools)" --> D[genesis-daemon<br/>&lt;100 MB RSS · stdlib only]
  Clients -- "pre-prompt hook<br/>≤200-token capsule" --> H[subconscious_hook.py]
  Clients -- "OPENAI_BASE_URL / ANTHROPIC_BASE_URL" --> P[genesis-proxy :8000<br/>/v1/chat/completions · /v1/messages]
  D & H & P --> S[(memory.db<br/>SQLite WAL · busy 5000ms<br/>BEGIN IMMEDIATE + retry)]
  R[genesis run<br/>headless spooler] -- "redacted bytes" --> SP[(~/.genesis/spool<br/>TTL 7d · LRU 500MB)]
  S --> SL[sleep daemon<br/>Hebbian decay · skills · digest]
  S & SP & P --> M[Mission Control :8090<br/>SSE telemetry · read-only]
  PS[[Privacy Shield<br/>17 patterns + Shannon entropy]] -.guards.-> S & SP
```

### Repository layout

```text
genesis_memory/
├── core/
│   ├── db.py                 # WAL + busy_timeout + BEGIN IMMEDIATE + jittered retry (shared by every process)
│   ├── output_governor.py    # Output diet detection, tiny-turn classifier, diff directive
│   ├── privacy_shield.py     # 17 structural patterns + Shannon entropy detector (redact/scan)
│   ├── hebbian_engine.py     # Stability τ, power-law decay, reinforcement (+1/-1)
│   ├── skill_synthesizer.py  # Recurring workflow outcomes → procedural skills
│   └── ast_edge_extractor.py # AST dependency closure for attest_closure
├── daemon/server.py          # stdio MCP daemon · 19 cognitive tools · SQLite WAL engine
├── hooks/subconscious_hook.py# ≤200-token subconscious capsule injection (Cursor, Claude, OpenCode)
├── proxy/                    # aiohttp gateway (OpenAI + Anthropic), supervisor, compactor, pricing engine
│   ├── launcher.py           # Standalone foreground proxy CLI (genesis-proxy)
│   ├── supervisor.py         # On-demand background daemon manager (genesis proxy start/stop)
│   ├── proxy_server.py       # Core proxy routing, SSE streaming, output diet, receipts
│   └── pricing_catalog.json  # Bundled official pricing for 1,000+ models
├── cli/
│   ├── run.py                # genesis CLI entrypoint · 60+ toolchain headless spooler
│   ├── client_registry.py    # Universal data-driven client matrix (20 specs, dialect renderers)
│   ├── config_formats.py     # Dependency-free JSON/YAML/TOML/env emitters
│   ├── export_config.py      # genesis export-config / genesis clients
│   └── init_cmd.py           # genesis setup · preview · timestamped backup · --revert
├── sleep/                    # Hebbian consolidation cycle, digest generator, ledger
site/                         # React 19 + Vite 7 landing page with live interactive benchmarks
tests/                        # Comprehensive test suite (399+ automated tests)
```

---

## Configuration & Environment Variables

Every component of GENESIS can be customized via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `GENESIS_DAEMON_DB` | `~/.genesis/memory.db` | Path to shared SQLite episodic memory database |
| `GENESIS_PROXY_PORT` | `8000` | Port for the stateless proxy gateway |
| `GENESIS_PROXY_HOST` | `127.0.0.1` | Network interface to bind proxy gateway |
| `GENESIS_UPSTREAM_URL` | `https://api.openai.com/v1` | Target upstream API for `/v1/chat/completions` |
| `GENESIS_ANTHROPIC_UPSTREAM_URL` | `https://api.anthropic.com/v1` | Target upstream API for `/v1/messages` |
| `GENESIS_TARGET_MODEL` | `gemini-3.5-flash` | Model mapped to virtual `genesis-stateless` requests |
| `GENESIS_PROXY_MODE` | `shadow` | Execution mode: `shadow` (observe-only) or `live` (strip/compact) |
| `GENESIS_OUTPUT_DIET` | `0` | Output diet: `0` (off), `1` (always terse), or `auto` (adaptive to depth requests) |
| `GENESIS_TINY_BUDGET` | `1` | Cap acknowledgments at 256 completion tokens (`1` on, `0` off) |
| `GENESIS_CONTENT_COMPRESS` | `0` | Enable bulk content compression with local recovery store |
| `GENESIS_MCP_TOOL_MODE` | `all` | MCP tool exposure: `all` (19 individual tools) or `gateway` (single `genesis` tool) |
| `GENESIS_SPOOL_RAW` | `0` | Set `1` to bypass privacy redaction in headless spool files |

---

## MCP tools reference

| Tool | Purpose |
|---|---|
| `remember` / `recall` / `forget` / `invalidate` | Episodic store with FTS5 BM25 ranking, utility scoring, supersession |
| `reinforce` | Hebbian +1 / −1 from developer outcomes |
| `challenge_rule` / `resolve_conflict` | Propose an improved project rule; human veto queue |
| `thread_update` / `thread_get` | Unified cross-client active working thread |
| `dialogue_update` / `dialogue_get` / `cross_client_resolve` | Cross-client conversational continuity with provenance |
| `synthesize_skill` / `skill_recall` | Procedural memory and reproducible workflows |
| `get_dependencies` / `attest_closure` | AST dependency closure attestation |
| `genesis_log` | Dereference spooled output (grep / pagination / chunked) |
| `sleep_now` / `status` | Hebbian sleep consolidation trigger; diagnostics & counters |
| `genesis` | One-tool gateway: `{op: "recall", query: …}` routes every operation above |

---

## Testing

```bash
# Full test suite
python -m pytest tests/

# Concurrency, lock-storm, and proxy integrity tests
python -m pytest tests/test_genesis_proxy.py
python -m pytest tests/test_output_governor.py
python -m pytest tests/test_db_hardening.py
python -m pytest tests/test_privacy_shield.py
python -m pytest tests/test_universal_clients.py
```

---

## Licensing

Source-available under the Business Source License 1.1 (free for individuals,
teams under 10, and all non-production use; converts to MIT on 2029-09-11).
Versions ≤ v0.6.1 stay MIT forever. See LICENSE.

Offline-first Ed25519-signed licensing — works air-gapped.

| | Community | Developer Pro | Enterprise Gateway |
|---|---|---|---|
| Price | Free / Community | $14 mo · $12 annual | $39 seat/mo · $32 annual |
| Local SQLite memory · hook · spooler · 20-client setup · privacy shield · compactor · sleep · live pricing · Anthropic route | ✅ | ✅ | ✅ |
| Mission Control dashboard · license entitlements · priority token budget | — | ✅ | ✅ |
| Team shared memory sync · on-prem Docker gateway · audit logs · SLA | — | — | ✅ |

```bash
genesis auth --key GEN-PRO-<key>
genesis license
```

---

## Links

- **Official Website & Interactive Simulator:** [https://hamidrezaeian.github.io/genesis-memory-site/](https://hamidrezaeian.github.io/genesis-memory-site/)
- **PyPI Package:** [https://pypi.org/project/genesis-memory/](https://pypi.org/project/genesis-memory/)
- **GitHub Repository:** [https://github.com/HamidRezaeian/genesis-memory](https://github.com/HamidRezaeian/genesis-memory)
- **Release Changelog:** [RELEASE_CHANGELOG.md](RELEASE_CHANGELOG.md)
- **Documentation:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/MCP_SPEC.md](docs/MCP_SPEC.md) · [llms.txt](llms.txt) · [openapi.json](openapi.json)

<p align="center"><sub>GENESIS Memory — because your AI agent deserves a brain that doesn't reset every session.</sub></p>

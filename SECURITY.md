# GENESIS Memory — Security Model

This document states the threat model plainly: what is protected, what is
deliberately trusted, and what is still on the roadmap. Last updated for
v0.14.4.

## What lives on your disk (and with what permissions)

| Path | Contents | Permissions |
|---|---|---|
| `~/.genesis/memory.db` (+ `-wal`/`-shm`) | Your episodic memory (SQLite WAL) | OS default umask — keep your home directory private |
| `~/.genesis/proxy_config.json` | Upstream provider URL + API key | `0600` (owner-only; set atomically where supported) |
| `~/.genesis/license.json` | Commercial license key (Pro) | OS default umask |
| `~/.genesis/spool/` | Redacted headless command output, TTL 7d, 500 MB LRU cap | OS default umask |
| `~/.genesis/proxy.log`, `daemon.log` | Operational logs (metadata only — bodies never logged) | OS default umask |

## What never leaves your machine (unless you configure it)

- **MCP + Hook are 100% local and offline.** They open no sockets except the
  proxy health probe to `127.0.0.1`. Installing GENESIS does not touch your
  IDE's native model subscriptions.
- **The proxy forwards prompts only to the upstream YOU configured**
  (`genesis proxy setup`). It never phones home: no telemetry upload, no
  key exfiltration channel. The only outbound calls the CLI ever makes are
  the optional pricing-catalog refresh and the cached 24 h PyPI version check
  on `genesis doctor`/`upgrade` (0.8 s timeout, silent when offline).

## Key handling

- The provider key travels to the background proxy **via child-process
  environment only** (`GENESIS_UPSTREAM_KEY`) — never on argv (which is
  world-readable via `/proc/<pid>/cmdline`).
- Setup output masks the key (`sk-or-...7890`); it is never printed in full.
- Upstream URLs are classified at setup: **link-local/cloud-metadata ranges
  are hard-blocked**; loopback/RFC1918/on-prem hosts require explicit
  confirmation (`--allow-private-upstream`), so a pasted internal URL can
  never silently exfiltrate your key (SSRF guard).

## Local gateway boundaries

- The proxy binds loopback only and rejects non-loopback `Host` headers
  (403) — basic DNS-rebinding protection.
- Browsers get `Access-Control-Allow-Origin` **only for loopback pages**
  (the local dashboard); foreign origins get no read access.
- Residual risk (by design for a local dev gateway): **any process on your
  machine** can call `http://127.0.0.1:8000/v1`. Do not run the proxy on
  shared/multi-user hosts without OS-level isolation.

## Privacy shield

- `remember()`, thread/dialogue writes, spool files, and recovery stores pass
  the Zero-Trust shield (structural patterns + Shannon entropy) **before**
  bytes touch disk. Secrets are rejected or redacted; counters record blocks.
- `GENESIS_SPOOL_RAW=1` **disables redaction** for forensics. Any process
  that can set your environment can set this — treat it like `sudo`.
- Terminal summaries printed by `genesis run` are parsed excerpts, not the
  redacted spool file — assume anything printed may reach model context.

## Honest limitations (roadmap, not marketing)

- **Memory provenance**: `remember()` records no source client, trust level,
  or approval flag. Treat recalled text as *quoted data*, never as
  instructions — a malicious README or tool output could otherwise plant a
  "rule". Provenance + approval for rule-like memories is planned.
- **Token estimates** (`len(chars)//4`) are heuristics, always labeled
  `estimate` — never tokenizer counts.
- **Benchmark numbers** in the README are illustrative single runs (n=3);
  a repeated, variance-reporting harness is in progress.

## Wipe everything

```bash
genesis setup --revert   # restore all IDE configs from atomic backups
rm -rf ~/.genesis        # delete memory, spool, logs, license, proxy config
```

## Reporting vulnerabilities

Open a GitHub issue at
`https://github.com/HamidRezaeian/genesis-memory/issues` (or a private
security advisory for key-handling flaws). Please include version
(`genesis --version`), OS, and `genesis doctor -v` output with secrets
redacted.

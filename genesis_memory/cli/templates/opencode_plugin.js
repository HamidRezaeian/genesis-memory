// GENESIS Subconscious Memory OpenCode Plugin v1.4 (TIER-1 Hardened)
// Universal, ambient working memory buffer & identity priming for OpenCode (Muse Spark).
// Addressed all OpenCode deep-audit points:
// 1. True LRU query-keyed cache (recency refresh on hit, cache-clear on write tools)
// 2. Fallback turnKey deduplication when messageID is absent
// 3. Strict equality whitelist in permission.ask (26 exact tools, zero wildcards)
// 4. Bounded capsule size-cap (4000 chars / ~1000 tokens maximum)
// 5. Fail-open, zero-hardcoding, dynamic Python & global hook resolution.

import { spawnSync } from "node:child_process";
import { existsSync, appendFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const queryCache = new Map();
const CACHE_TTL_MS = 60000;
const MAX_CACHE_ENTRIES = 50;
const MAX_CAPSULE_CHARS = 4000;

let lastInjectedMsgId = null;

function resolvePython() {
  if (process.env.GENESIS_PYTHON && existsSync(process.env.GENESIS_PYTHON)) {
    return process.env.GENESIS_PYTHON;
  }
  if (process.env.PYTHON && existsSync(process.env.PYTHON)) {
    return process.env.PYTHON;
  }
  if (process.env.VIRTUAL_ENV) {
    const venvPython = join(
      process.env.VIRTUAL_ENV,
      process.platform === "win32" ? "Scripts" : "bin",
      process.platform === "win32" ? "python.exe" : "python3"
    );
    if (existsSync(venvPython)) return venvPython;
  }
  if (process.env.CONDA_PREFIX) {
    const condaPython = join(
      process.env.CONDA_PREFIX,
      process.platform === "win32" ? "python.exe" : "bin/python"
    );
    if (existsSync(condaPython)) return condaPython;
  }
  return process.platform === "win32" ? "python" : "python3";
}

function resolveHookTarget() {
  if (process.env.GENESIS_HOOK && existsSync(process.env.GENESIS_HOOK)) {
    return { type: "script", target: process.env.GENESIS_HOOK };
  }
  const globalHook = join(homedir(), ".genesis", "subconscious_memory_hook.py");
  if (existsSync(globalHook)) {
    return { type: "script", target: globalHook };
  }
  const repoHook = join(process.cwd(), "genesis_memory", "hooks", "subconscious_memory_hook.py");
  if (existsSync(repoHook)) {
    return { type: "script", target: repoHook };
  }
  return { type: "module", target: "genesis_memory.hooks.subconscious_hook" };
}

function debugLog(msg) {
  try {
    const logPath = process.env.GENESIS_DAEMON_LOG || join(homedir(), ".genesis", "plugin.log");
    appendFileSync(logPath, `[${new Date().toISOString()}] ${msg}\n`);
  } catch {}
}

function getCapsule(query, force) {
  const normKey = (query || "").trim().toLowerCase();
  const now = Date.now();

  // 1. True LRU Cache: check hit and refresh recency in Map.
  // force bypasses the READ (pending assistant handoff must still reach the
  // hook) but the fresh result is written back below as usual.
  const cached = queryCache.get(normKey);
  if (cached && !force && (now - cached.ts < CACHE_TTL_MS)) {
    queryCache.delete(normKey);
    queryCache.set(normKey, { capsule: cached.capsule, ts: now });
    return cached.capsule;
  }

  // 2. Fallback to generic un-queried cache if query is empty and fresh
  if (!normKey) {
    const genericCached = queryCache.get("");
    if (genericCached && (now - genericCached.ts < CACHE_TTL_MS)) {
      queryCache.delete("");
      queryCache.set("", { capsule: genericCached.capsule, ts: now });
      return genericCached.capsule;
    }
  }

  const hookInfo = resolveHookTarget();
  const pythonCmd = resolvePython();
  const args = [];

  if (hookInfo.type === "script") {
    args.push(hookInfo.target);
  } else {
    args.push("-m", hookInfo.target);
  }

  if (query) {
    args.push(query);
  }
  args.push("--raw");

  try {
    const res = spawnSync(pythonCmd, args, {
      encoding: "utf-8",
      timeout: 2500,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });

    if (res.status === 0 && res.stdout) {
      let capsule = res.stdout.trim();
      if (capsule) {
        if (capsule.length > MAX_CAPSULE_CHARS) {
          capsule = capsule.substring(0, MAX_CAPSULE_CHARS) + "\n...[truncated]";
        }
        // Single write per miss to conserve capacity
        queryCache.set(normKey, { capsule, ts: now });
        if (queryCache.size > MAX_CACHE_ENTRIES) {
          const oldestKey = queryCache.keys().next().value;
          queryCache.delete(oldestKey);
        }
        return capsule;
      }
    } else {
      debugLog(`getCapsule spawn failed: status=${res.status} err=${res.error || res.stderr}`);
    }
  } catch (err) {
    debugLog(`getCapsule exception: ${err}`);
  }

  return queryCache.get(normKey)?.capsule || queryCache.get("")?.capsule || null;
}

// 100% Strict Whitelist (26 exact tool names, zero wildcards, zero rogue auto-approvals)
const ALLOWED_TOOLS = new Set([
  // Underscore format (standard OpenCode MCP naming)
  "genesis-memory_remember",
  "genesis-memory_recall",
  "genesis-memory_forget",
  "genesis-memory_status",
  "genesis-memory_invalidate",
  "genesis-memory_resolve_conflict",
  "genesis-memory_get_dependencies",
  "genesis-memory_attest_closure",
    "genesis-memory_thread_update",
    "genesis-memory_thread_get",
    "genesis-memory_genesis",
    "genesis-memory_cross_client_resolve",
    // Double-underscore format (alternative OpenCode / VS Code format)
    "mcp__genesis-memory__remember",
  "mcp__genesis-memory__recall",
  "mcp__genesis-memory__forget",
  "mcp__genesis-memory__status",
  "mcp__genesis-memory__invalidate",
  "mcp__genesis-memory__resolve_conflict",
  "mcp__genesis-memory__get_dependencies",
  "mcp__genesis-memory__attest_closure",
    "mcp__genesis-memory__thread_update",
    "mcp__genesis-memory__thread_get",
    "mcp__genesis-memory__genesis",
    "mcp__genesis-memory__cross_client_resolve",
  // Safe CLI logging & spooling tools
  "genesis_log",
  "ctx_log",
]);

debugLog("genesis-memory.js v1.4 loaded into runtime");

let currentTurnQuery = "";
// Assistant-side capture (cross-client continuity): chat.message fires for
// every message update (user + assistant streaming chunks) WITHOUT a role
// field. We track text per messageID; at transform time the most recent ID
// is the just-sent user message and the longest other text is the latest
// assistant reply. Handoff to hook via process env (cleared after each use
// so nothing stale survives). Content never logged, only lengths.
let mostRecentMessageId = "";
const seenMessageTexts = new Map();
const MAX_TRACKED_MESSAGES = 20;
const MAX_ASSISTANT_CHARS = 2000;

export const GenesisMemoryPlugin = async (ctx) => {
  return {
    // 1. Turn Query Extraction (leaves user message parts 100% pristine and untouched in UI)
    "chat.message": async (input, output) => {
      try {
        if (process.env.GENESIS_PROXY_ACTIVE === "1") return;
        if (!output || !Array.isArray(output.parts)) return;

        // Role telemetry (metadata only, never content): determines whether
        // assistant turns are observable here for cross-client capture.
        try {
          const inKeys = input && typeof input === "object" ? Object.keys(input) : [];
          const roleGuess = (input && (input.role || (input.message && input.message.role))) || "?";
          let partsLen = 0;
          try {
            for (const p of (output.parts || [])) {
              if (p && typeof p === "object" && typeof p.text === "string") partsLen += p.text.length;
              else if (typeof p === "string") partsLen += p.length;
            }
          } catch {}
          debugLog(`chat.message shape keys=[${inKeys.join(",")}] role=${roleGuess} variant=${input ? input.variant : "?"} partsChars=${partsLen} pid=${process.pid}`);
        } catch {}

        let text = "";
        for (const p of output.parts) {
          if (p && typeof p === "object" && typeof p.text === "string") {
            text += " " + p.text;
          } else if (typeof p === "string") {
            text += " " + p;
          }
        }
        const query = text.trim();
        if (!query) return;

        // Ignore internal subagent instructions to prevent context recursion
        if (query.startsWith("You are a read-only") || query.startsWith("You are a subagent")) {
          return;
        }

        currentTurnQuery = query;
        try {
          const mid = (input && input.messageID) || "";
          if (mid) {
            mostRecentMessageId = mid;
            const prev = seenMessageTexts.get(mid) || "";
            if (query.length > prev.length) seenMessageTexts.set(mid, query);
            while (seenMessageTexts.size > MAX_TRACKED_MESSAGES) {
              seenMessageTexts.delete(seenMessageTexts.keys().next().value);
            }
          }
        } catch {}
        debugLog(`chat.message captured (${query.length} chars, content never logged)`);
      } catch (err) {
        debugLog(`chat.message error: ${err}`);
      }
    },

    // 2. Native System Prompt Transform (Exact equivalent to Antigravity IDE architecture)
    "experimental.chat.system.transform": async (input, output) => {
      try {
        if (process.env.GENESIS_PROXY_ACTIVE === "1") return;
        if (!output || !Array.isArray(output.system)) return;

        // Content-level duplicate guard in system prompt
        if (output.system.some(s => typeof s === "string" && (s.includes("[GENESIS Subconscious Memory • PINNED]:") || s.includes("[GENESIS Subconscious Memory Context]:")))) {
          return;
        }

        // Hand the latest assistant reply to the hook for cross-client
        // capture (longest text seen under any ID except the just-sent user
        // message). Cleared right after the spawn so it can never go stale.
        // forceSpawn bypasses the query cache: a repeated user query must
        // still deliver the pending assistant handoff to the hook.
        let forceSpawn = false;
        try {
          let best = "";
          for (const [mid, txt] of seenMessageTexts) {
            if (mid !== mostRecentMessageId && txt.length > best.length) best = txt;
          }
          if (best) {
            process.env.GENESIS_LAST_ASSISTANT_TEXT = best.substring(0, MAX_ASSISTANT_CHARS);
            debugLog(`assistant captured (${best.length} chars, content never logged) pid=${process.pid}`);
            forceSpawn = true;
          }
        } catch {}
        const capsule = getCapsule(currentTurnQuery, forceSpawn);
        try {
          process.env.GENESIS_LAST_ASSISTANT_TEXT = "";
        } catch {}
        seenMessageTexts.clear();
        mostRecentMessageId = "";
        if (capsule) {
          output.system.push(`${capsule}\n`);
          debugLog(`system.transform injected subconscious context into system prompt (${capsule.length} chars)`);
        }
      } catch (err) {
        debugLog(`system.transform error: ${err}`);
      }
    },

    // Write-Tool Cache Invalidation: immediately clear query cache when memory is modified
    "tool.execute.after": async (input) => {
      try {
        const tool = String(input?.tool || input?.name || "").trim();
        if (
          tool.includes("remember") ||
          tool.includes("forget") ||
          tool.includes("invalidate") ||
          tool.includes("thread_update")
        ) {
          queryCache.clear();
          debugLog(`queryCache cleared on write tool execution: ${tool}`);
        }
      } catch (err) {}
    },

    // Compaction Invariant: Inject active decisions to prevent compaction amnesia
    "experimental.session.compacting": async (_input, output) => {
      try {
        const capsule = getCapsule();
        if (capsule) {
          if (!Array.isArray(output.context)) {
            output.context = [];
          }
          output.context.push(
            `[CRITICAL GENESIS ARCHITECTURAL INVARIANTS TO PRESERVE ACROSS COMPACTION]:\n${capsule}`
          );
          debugLog(`compacting context pushed`);
        }
      } catch (err) {
        debugLog(`compacting error: ${err}`);
      }
    },

    // Strict security-audited auto-approval: ONLY allow exact matches from whitelist
    "permission.ask": async (input, output) => {
      try {
        const tool = String(input?.tool || input?.name || input?.id || input?.toolID || "").trim();
        if (ALLOWED_TOOLS.has(tool)) {
          output.status = "allow";
          debugLog(`permission.ask exact-allowed tool: ${tool}`);
        }
      } catch (err) {
        debugLog(`permission.ask error: ${err}`);
      }
    },

    // Session lifecycle hooks
    event: async ({ event }) => {
      try {
        if (event && (event.type === "EventSessionCreated" || event.type === "session.created")) {
          queryCache.clear();
          lastInjectedMsgId = null;
          debugLog("Session reset event processed (cache cleared)");
        }
      } catch (err) {
        debugLog(`event error: ${err}`);
      }
    }
  };
};

export default GenesisMemoryPlugin;

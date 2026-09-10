import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Holo, SectionHead, Reveal, Magnetic, Arrow } from './ui'

const PY = '/usr/bin/python3', DAEMON = '~/genesis_memory/daemon/server.py', DB = '~/.genesis/memory.db'
const mcp = (indent = 2) => JSON.stringify({ mcpServers: { 'genesis-memory': { command: PY, args: [DAEMON], env: { GENESIS_DAEMON_DB: DB } } } }, null, indent)

export const CLIENTS = [
  { id: 'cursor', name: 'Cursor', fam: 'IDE', caps: ['MCP', 'Hook', 'Proxy'], path: '~/.cursor/mcp.json + hooks.json', fmt: 'json', snippet: mcp() },
  { id: 'claude_code', name: 'Claude Code', fam: 'CLI', caps: ['Hook', 'MCP', 'Proxy'], path: '~/.claude/settings.json + ~/.claude.json', fmt: 'json', snippet: JSON.stringify({ env: { GENESIS_DAEMON_DB: DB }, hooks: { PrePrompt: [{ matcher: '.*', hooks: [{ type: 'command', command: `"${PY}" "~/genesis_memory/hooks/subconscious_hook.py" --claude` }] }] } }, null, 2) },
  { id: 'opencode', name: 'OpenCode', fam: 'CLI', caps: ['MCP', 'Proxy', 'Hook'], path: '~/.config/opencode/opencode.jsonc + plugins/', fmt: 'jsonc', snippet: JSON.stringify({ mcp: { 'genesis-memory': { type: 'local', command: [PY, DAEMON], environment: { GENESIS_DAEMON_DB: DB }, enabled: true } }, provider: { 'genesis-proxy': { npm: '@ai-sdk/openai-compatible', options: { baseURL: 'http://127.0.0.1:8000/v1' } } } }, null, 2) },
  { id: 'vscode', name: 'VS Code · Copilot', fam: 'IDE', caps: ['MCP', 'Proxy'], path: '~/…/Code/User/mcp.json', fmt: 'jsonc', snippet: JSON.stringify({ servers: { 'genesis-memory': { type: 'stdio', command: PY, args: [DAEMON], env: { GENESIS_DAEMON_DB: DB } } } }, null, 2) },
  { id: 'zed', name: 'Zed', fam: 'IDE', caps: ['MCP', 'Proxy'], path: '~/.config/zed/settings.json', fmt: 'jsonc', snippet: JSON.stringify({ context_servers: { 'genesis-memory': { command: { path: PY, args: [DAEMON], env: { GENESIS_DAEMON_DB: DB } } } } }, null, 2) },
  { id: 'windsurf', name: 'Windsurf', fam: 'IDE', caps: ['MCP', 'Proxy'], path: '~/.codeium/windsurf/mcp_config.json', fmt: 'json', snippet: mcp() },
  { id: 'antigravity', name: 'Antigravity', fam: 'IDE', caps: ['MCP', 'Hook'], path: '.agents/ (native)', fmt: 'native', snippet: '# Antigravity ships GENESIS natively.\n# .agents/mcp_config.json + .agents/hooks.json are already wired.' },
  { id: 'jetbrains', name: 'JetBrains · Junie', fam: 'IDE', caps: ['MCP', 'Proxy'], path: '~/.junie/mcp/mcp.json', fmt: 'json', snippet: mcp() },
  { id: 'cline', name: 'Cline', fam: 'Plugin', caps: ['MCP', 'Proxy'], path: '…/saoudrizwan.claude-dev/settings/cline_mcp_settings.json', fmt: 'json', snippet: mcp() },
  { id: 'roo', name: 'Roo Code', fam: 'Plugin', caps: ['MCP', 'Proxy'], path: '…/rooveterinaryinc.roo-cline/settings/mcp_settings.json', fmt: 'json', snippet: mcp() },
  { id: 'continue', name: 'Continue.dev', fam: 'Plugin', caps: ['MCP', 'Proxy'], path: '~/.continue/mcpServers/genesis-memory.yaml', fmt: 'yaml', snippet: `name: GENESIS Memory\nversion: 0.5.0\nschema: v1\nmcpServers:\n  - name: genesis-memory\n    command: ${PY}\n    args:\n      - ${DAEMON}\n    env:\n      GENESIS_DAEMON_DB: ${DB}` },
  { id: 'neovim', name: 'Neovim', fam: 'Plugin', caps: ['MCP', 'Proxy'], path: '~/.config/mcphub/servers.json (Avante · CodeCompanion · Copilot.lua)', fmt: 'lua', snippet: `-- mcphub.nvim / Avante / CodeCompanion\nreturn {\n  mcpServers = {\n    ["genesis-memory"] = {\n      command = "${PY}",\n      args = { "${DAEMON}" },\n      env = { GENESIS_DAEMON_DB = "${DB}" },\n    },\n  },\n  proxy = { endpoint = "http://127.0.0.1:8000/v1" },\n}` },
  { id: 'emacs', name: 'Emacs', fam: 'Plugin', caps: ['MCP', 'Proxy'], path: '~/.emacs.d/genesis-memory.el (gptel · aidermacs · mcp.el)', fmt: 'elisp', snippet: `(with-eval-after-load 'mcp\n  (add-to-list 'mcp-hub-servers\n    '("genesis-memory" . (:command "${PY}"\n                          :args ("${DAEMON}")\n                          :env (:GENESIS_DAEMON_DB "${DB}")))))\n(with-eval-after-load 'gptel\n  (gptel-make-openai "GENESIS Proxy"\n    :host "127.0.0.1:8000" :protocol "http" :stream t))` },
  { id: 'aider', name: 'Aider', fam: 'CLI', caps: ['Proxy'], path: '~/.aider.conf.yml', fmt: 'yaml', snippet: `# >>> genesis-memory >>>\nopenai-api-base: http://127.0.0.1:8000/v1\nset-env:\n  - GENESIS_DAEMON_DB=${DB}\n# <<< genesis-memory <<<` },
  { id: 'codex', name: 'Codex CLI', fam: 'CLI', caps: ['MCP', 'Proxy'], path: '~/.codex/config.toml', fmt: 'toml', snippet: `[mcp_servers.genesis-memory]\ncommand = "${PY}"\nargs = ["${DAEMON}"]\n\n[mcp_servers.genesis-memory.env]\nGENESIS_DAEMON_DB = "${DB}"` },
  { id: 'gemini', name: 'Gemini CLI', fam: 'CLI', caps: ['MCP'], path: '~/.gemini/settings.json', fmt: 'json', snippet: mcp() },
  { id: 'amazonq', name: 'Amazon Q', fam: 'CLI', caps: ['MCP'], path: '~/.aws/amazonq/mcp.json', fmt: 'json', snippet: mcp() },
  { id: 'goose', name: 'Goose', fam: 'CLI', caps: ['MCP', 'Proxy'], path: '~/.config/goose/config.yaml', fmt: 'yaml', snippet: `extensions:\n  genesis-memory:\n    enabled: true\n    type: stdio\n    cmd: ${PY}\n    args:\n      - ${DAEMON}\n    envs:\n      GENESIS_DAEMON_DB: ${DB}` },
  { id: 'claude_desktop', name: 'Claude Desktop', fam: 'Desktop', caps: ['MCP'], path: '…/Claude/claude_desktop_config.json', fmt: 'json', snippet: mcp() },
  { id: 'sdk', name: 'Any SDK · LangChain · CrewAI · AutoGen · LlamaIndex', fam: 'Framework', caps: ['Proxy'], path: 'OPENAI_BASE_URL / ANTHROPIC_BASE_URL', fmt: 'env', snippet: `OPENAI_BASE_URL=http://127.0.0.1:8000/v1\nANTHROPIC_BASE_URL=http://127.0.0.1:8000\nGENESIS_DAEMON_DB=${DB}\n\n# from openai import OpenAI\n# client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="not-needed")` },
]
const capTone = { MCP: 'cyan', Hook: 'violet', Proxy: 'emerald' }
const FORMATS = ['native', 'json', 'yaml', 'toml', 'env']

function render(c, f) {
  if (f === 'native') return c.snippet
  const obj = { mcpServers: { 'genesis-memory': { command: PY, args: [DAEMON], env: { GENESIS_DAEMON_DB: DB } } } }
  if (f === 'json') return JSON.stringify(obj, null, 2)
  if (f === 'yaml') return `mcpServers:\n  genesis-memory:\n    command: ${PY}\n    args:\n      - ${DAEMON}\n    env:\n      GENESIS_DAEMON_DB: ${DB}`
  if (f === 'toml') return `[mcpServers.genesis-memory]\ncommand = "${PY}"\nargs = ["${DAEMON}"]\n\n[mcpServers.genesis-memory.env]\nGENESIS_DAEMON_DB = "${DB}"`
  return `OPENAI_BASE_URL=http://127.0.0.1:8000/v1\nOPENAI_API_BASE=http://127.0.0.1:8000/v1\nANTHROPIC_BASE_URL=http://127.0.0.1:8000\nGENESIS_DAEMON_DB=${DB}`
}

export default function Setup() {
  const [sel, setSel] = useState(CLIENTS[0])
  const [f, setF] = useState('native')
  const [copied, setCopied] = useState(false)
  const [wiring, setWiring] = useState(false)
  const [wired, setWired] = useState(new Set())
  const code = render(sel, f)
  const copy = async () => { try { await navigator.clipboard.writeText(code) } catch {} setCopied(true); setTimeout(() => setCopied(false), 1400) }
  const wireAll = () => { if (wiring) return; setWiring(true); setWired(new Set()); CLIENTS.forEach((c, i) => setTimeout(() => { setWired(s => new Set([...s, c.id])); if (i === CLIENTS.length - 1) setTimeout(() => setWiring(false), 400) }, 90 + i * 110)) }

  return (
    <section id="setup" className="section">
      <div className="wrap">
        <SectionHead eyebrow="Universal Client Support" title="Every AI coding tool on Earth." grad="One command." lead="genesis setup discovers what you have installed and wires it — 20 environments, 7 config dialects, structural merges for JSON, idempotent marker blocks for YAML/TOML/Lisp, timestamped backups and a one-shot --revert. Anything else speaks to the drop-in gateway with zero code changes." />
        <Reveal style={{ marginTop: 40 }}>
          <div className="term" style={{ maxWidth: 820, margin: '0 auto', position: 'relative' }}>
            <div className="bar"><i /><i /><i /><span>genesis setup --yes</span><button className="btn sm primary" style={{ marginLeft: 'auto' }} onClick={wireAll} disabled={wiring}>{wiring ? 'wiring…' : 'simulate genesis setup'} <Arrow /></button></div>
            <div className="body" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(230px,1fr))', gap: '6px 18px', minHeight: 200 }}>
              {CLIENTS.map(c => {
                const on = wired.has(c.id)
                return <motion.div key={c.id} animate={{ opacity: wired.size || !wiring ? 1 : .4 }} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
                  <AnimatePresence mode="wait">{on ? <motion.span key="ok" initial={{ scale: 0 }} animate={{ scale: 1 }} className="ok">🔌</motion.span> : <span key="no" className="dim">·</span>}</AnimatePresence>
                  <span style={{ color: on ? '#fff' : 'var(--fg-3)' }}>{c.name.split(' ·')[0]}</span>
                  <span className="dim" style={{ marginLeft: 'auto', fontSize: 10.5 }}>{on ? (c.fmt === 'native' ? 'native' : `${c.fmt} merged`) : c.fmt}</span>
                </motion.div>
              })}
              <AnimatePresence>{wired.size === CLIENTS.length && <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} style={{ gridColumn: '1/-1', marginTop: 8, paddingTop: 10, borderTop: '1px solid var(--line)' }} className="ok">🎉 {CLIENTS.length} clients wired · backup manifest saved · undo anytime with <span className="cy">genesis setup --revert</span></motion.div>}</AnimatePresence>
            </div>
          </div>
        </Reveal>
        <div className="grid" style={{ gridTemplateColumns: 'minmax(0,.9fr) minmax(0,1.1fr)', marginTop: 28, alignItems: 'start' }}>
          <Reveal>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(150px,1fr))', gap: 8 }}>
              {CLIENTS.map(c => (
                <motion.button key={c.id} onClick={() => { setSel(c); setF('native') }} whileHover={{ y: -2 }} whileTap={{ scale: .98 }}
                  className="card" style={{ padding: '12px 12px', textAlign: 'left', border: `1px solid ${sel.id === c.id ? 'rgba(0,240,255,.55)' : 'var(--line)'}`, background: sel.id === c.id ? 'rgba(0,240,255,.07)' : undefined, boxShadow: sel.id === c.id ? '0 0 30px -10px rgba(0,240,255,.6)' : undefined }}>
                  <div style={{ fontWeight: 700, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.name}</div>
                  <div style={{ display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' }}>{c.caps.map(k => <span key={k} className={`pill ${capTone[k]}`} style={{ fontSize: 9.5, padding: '1px 6px' }}>{k}</span>)}</div>
                </motion.button>))}
            </div>
          </Reveal>
          <Reveal delay={0.1}>
            <Holo tilt={false}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}>
                <b>{sel.name}</b><span className="mono" style={{ fontSize: 11, color: 'var(--fg-3)' }}>{sel.path}</span>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 2, padding: 2, background: 'rgba(148,163,184,.08)', borderRadius: 8 }}>
                  {FORMATS.map(x => <button key={x} onClick={() => setF(x)} className="mono" style={{ fontSize: 11, padding: '4px 9px', borderRadius: 6, background: f === x ? 'var(--bg-3)' : 'transparent', color: f === x ? '#fff' : 'var(--fg-3)' }}>{x}</button>)}
                </div>
              </div>
              <div style={{ padding: 16, position: 'relative' }}>
                <div className="mono" style={{ fontSize: 11, color: 'var(--fg-3)', marginBottom: 8 }}>$ genesis export-config --client {sel.id} --format {f}</div>
                <AnimatePresence mode="wait"><motion.pre key={sel.id + f} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: .25 }}
                  style={{ margin: 0, fontFamily: 'var(--mono)', fontSize: 12.5, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#cfe3ee', minHeight: 220 }}>{code}</motion.pre></AnimatePresence>
                <button className="btn sm" onClick={copy} style={{ position: 'absolute', right: 16, top: 12 }}>{copied ? 'copied ✓' : 'copy'}</button>
              </div>
            </Holo>
          </Reveal>
        </div>
        <Reveal style={{ marginTop: 28, display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap' }}>
          <Magnetic><a className="btn primary" href="https://github.com/HamidRezaeian/genesis-memory#quick-start" target="_blank" rel="noreferrer">Install GENESIS <Arrow /></a></Magnetic>
          <span className="pill">pip install genesis-memory</span><span className="pill">genesis setup --preview</span><span className="pill">genesis clients --json</span>
        </Reveal>
      </div>
      <style>{`@media (max-width: 980px){ #setup .grid{grid-template-columns:1fr!important} }`}</style>
    </section>
  )
}

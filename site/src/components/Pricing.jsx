import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Holo, SectionHead, Reveal, Magnetic, Arrow } from './ui'

const TIERS = [
  { name: 'Community', price: [0, 0], tone: 'fg', tag: 'Free & open source', cta: 'pip install genesis-memory', href: 'https://github.com/HamidRezaeian/genesis-memory',
    feats: ['Local SQLite memory (WAL, 5000 ms busy timeout)', 'Subconscious 200-token hook', 'Lossless headless spooling (60+ toolchains)', 'Universal 1-click setup · 20 clients', 'Zero-trust privacy shield', 'Stdio MCP server · 19 tools'] },
  { name: 'Developer Pro', price: [14, 12], tone: 'cyan', tag: 'Most popular', hot: true, cta: 'Start Pro', href: '#',
    feats: ['Everything in Community', 'High-ratio structural compactor', 'Hebbian sleep distillation & skills', 'Mission Control dashboard', 'Live pricing · dollars saved', 'Priority token budget', 'OpenAI + Anthropic gateway routes'] },
  { name: 'Enterprise Gateway', price: [39, 32], tone: 'fg', tag: 'Per seat', cta: 'Talk to us', href: 'mailto:hello@genesis-memory.dev',
    feats: ['Everything in Pro', 'Team shared memory sync (vector clocks)', 'On-prem Docker gateway', 'Zero-leak audit logs', 'SSO & seat management', 'SLA support'] },
]

export default function Pricing() {
  const [annual, setAnnual] = useState(true)
  return (
    <section id="pricing" className="section">
      <div className="wrap">
        <SectionHead eyebrow="Pricing" tone="emerald" title="Pays for itself" grad="on day one." lead="A single 4,000-line test run dieted to 84 tokens saves about $0.20 at GPT-4o rates. Most developers recoup Pro within their first morning." />
        <Reveal style={{ display: 'flex', justifyContent: 'center', marginTop: 26 }}>
          <div style={{ display: 'inline-flex', padding: 4, borderRadius: 12, background: 'rgba(148,163,184,.08)', border: '1px solid var(--line)', gap: 4, position: 'relative' }}>
            {['Monthly', 'Annual · −15%'].map((l, i) => <button key={l} onClick={() => setAnnual(i === 1)} style={{ padding: '8px 16px', borderRadius: 9, fontWeight: 700, fontSize: 13, color: annual === (i === 1) ? '#001317' : 'var(--fg-2)', background: annual === (i === 1) ? 'linear-gradient(180deg,#16e6ff,#00b8cc)' : 'transparent', transition: '.3s' }}>{l}</button>)}
          </div>
        </Reveal>
        <div className="grid g3" style={{ marginTop: 34, alignItems: 'stretch' }}>
          {TIERS.map((t, i) => (
            <Reveal key={t.name} delay={i * 0.08}>
              <Holo style={{ height: '100%', border: t.hot ? '1px solid rgba(0,240,255,.45)' : undefined, boxShadow: t.hot ? '0 0 80px -30px rgba(0,240,255,.6)' : undefined }}>
                <div className="pad" style={{ display: 'flex', flexDirection: 'column', gap: 16, height: '100%' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}><b style={{ fontSize: 15 }}>{t.name}</b><span className={`pill ${t.tone}`}>{t.tag}</span></div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                    <AnimatePresence mode="wait"><motion.span key={annual ? 'a' : 'm'} initial={{ y: 10, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -10, opacity: 0 }} transition={{ duration: .25 }} className="mono" style={{ fontSize: 46, fontWeight: 700, letterSpacing: '-.04em', color: `var(--${t.tone})` }}>${t.price[annual ? 1 : 0]}</motion.span></AnimatePresence>
                    <span style={{ color: 'var(--fg-3)', fontSize: 13 }}>{t.price[0] === 0 ? 'forever' : `/ ${t.name.startsWith('Enterprise') ? 'seat / ' : ''}month`}</span>
                  </div>
                  <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: 9, flex: 1 }}>
                    {t.feats.map(f => <li key={f} style={{ display: 'flex', gap: 10, fontSize: 13.5, color: 'var(--fg-2)' }}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={`var(--${t.tone})`} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none', marginTop: 2 }}><path d="M5 12l5 5L20 7" /></svg>{f}</li>)}
                  </ul>
                  <Magnetic strength={0.2}><a className={`btn ${t.hot ? 'primary' : ''}`} href={t.href} style={{ width: '100%' }}>{t.cta} <Arrow /></a></Magnetic>
                </div>
              </Holo>
            </Reveal>))}
        </div>
        <Reveal style={{ marginTop: 18 }} className="center"><p className="mono" style={{ fontSize: 12, color: 'var(--fg-3)' }}>Offline-first HMAC-SHA256 licensing · works fully air-gapped · <code>genesis auth --key GEN-PRO-…</code></p></Reveal>
      </div>
    </section>
  )
}

const FAQ = [
  ['How is this different from .cursorrules or CLAUDE.md?', 'Those are static text you maintain by hand and paste into every prompt in full. GENESIS is a live, queried memory: the hook injects only the ≤200 tokens relevant to this prompt, memories decay and are reinforced from outcomes, conflicts are staged for your veto, and the same store is shared by every client you use.'],
  ['Does any data leave my machine?', 'No. Storage is a local SQLite file, the proxy runs on 127.0.0.1, licensing is verified offline with HMAC-SHA256, and the only outbound call is the optional pricing-catalog refresh (a bundled snapshot ships in the wheel). Secrets are redacted before they can be persisted.'],
  ['What happens if the proxy or daemon is down?', 'Every integration is fail-open. Clients keep working exactly as before; you just lose the diet and the memory capsule until the process is back. The spool and store are append-only WAL files — nothing corrupts on a crash.'],
  ['Will it lock my database when several agents write at once?', 'No. Every connection uses WAL mode, a 5000 ms busy timeout and BEGIN IMMEDIATE transactions with jittered retry. The test suite runs an 8-process write storm against one file and asserts zero lock errors.'],
  ['My editor isn\'t on the list.', 'If it speaks MCP, `genesis export-config --format json|yaml|toml` gives you the snippet. If it speaks OpenAI or Anthropic, point its base URL at the gateway. Open an issue and we\'ll add native auto-wiring.'],
]

export function Faq() {
  const [open, setOpen] = useState(0)
  return (
    <section id="faq" className="section">
      <div className="wrap" style={{ maxWidth: 820 }}>
        <SectionHead eyebrow="FAQ" title="Straight answers." />
        <div style={{ marginTop: 30, display: 'grid', gap: 10 }}>
          {FAQ.map(([q, a], i) => (
            <Reveal key={q} delay={i * 0.04}>
              <div className="card" style={{ border: `1px solid ${open === i ? 'rgba(0,240,255,.35)' : 'var(--line)'}` }}>
                <button onClick={() => setOpen(open === i ? -1 : i)} style={{ width: '100%', textAlign: 'left', padding: '18px 22px', display: 'flex', alignItems: 'center', gap: 14, fontWeight: 700, fontSize: 16 }}>
                  {q}<motion.span animate={{ rotate: open === i ? 45 : 0 }} style={{ marginLeft: 'auto', color: 'var(--cyan)', fontSize: 22, lineHeight: 1 }}>+</motion.span>
                </button>
                <AnimatePresence initial={false}>{open === i && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: .35, ease: [0.2, 0.8, 0.2, 1] }} style={{ overflow: 'hidden' }}>
                    <p style={{ padding: '0 22px 20px', color: 'var(--fg-2)', fontSize: 14.5, lineHeight: 1.7 }}>{a}</p>
                  </motion.div>)}
                </AnimatePresence>
              </div>
            </Reveal>))}
        </div>
      </div>
    </section>
  )
}

export function Footer() {
  return (
    <footer style={{ borderTop: '1px solid var(--line)', padding: '56px 0 40px', position: 'relative', overflow: 'hidden' }}>
      <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(800px 300px at 50% 120%, rgba(0,240,255,.12), transparent 70%)', pointerEvents: 'none' }} />
      <div className="wrap" style={{ position: 'relative', display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr', gap: 28 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontWeight: 900, letterSpacing: '.16em', fontSize: 13 }}><span style={{ width: 20, height: 20, borderRadius: '50%', background: 'radial-gradient(circle at 35% 35%,#fff,var(--cyan) 35%,rgba(0,240,255,.15) 70%,transparent 72%)', boxShadow: '0 0 18px rgba(0,240,255,.7)' }} />GENESIS</div>
          <p style={{ color: 'var(--fg-3)', fontSize: 13, marginTop: 12, maxWidth: 360, lineHeight: 1.6 }}>The persistent brain and token diet for every AI coding agent. Local-first, stdlib-only, tested to the byte.</p>
          <div style={{ display: 'flex', gap: 6, marginTop: 14, flexWrap: 'wrap' }}><span className="pill emerald">421/421 tests</span><span className="pill cyan">MIT</span><span className="pill">python ≥ 3.10</span></div>
        </div>
        {[['Product', ['Token Diet#diet', 'Spooling#spool', 'Privacy Shield#shield', 'Conduit#conduit', 'Sleep#sleep', 'Pricing#pricing']], ['Clients', ['Cursor#setup', 'Claude Code#setup', 'VS Code#setup', 'Zed#setup', 'JetBrains#setup', 'Neovim & Emacs#setup']], ['Resources', ['GitHub|https://github.com/HamidRezaeian/genesis-memory', 'README|https://github.com/HamidRezaeian/genesis-memory#readme', 'Architecture|https://github.com/HamidRezaeian/genesis-memory/blob/main/docs/ARCHITECTURE.md', 'MCP Spec|https://github.com/HamidRezaeian/genesis-memory/blob/main/docs/MCP_SPEC.md', 'Changelog|https://github.com/HamidRezaeian/genesis-memory/blob/main/RELEASE_CHANGELOG.md']]].map(([h, items]) => (
          <div key={h}><div style={{ fontSize: 11, letterSpacing: '.16em', textTransform: 'uppercase', color: 'var(--fg-3)', marginBottom: 12 }}>{h}</div>
            <div style={{ display: 'grid', gap: 8 }}>{items.map(it => { const [l, href] = it.includes('|') ? it.split('|') : it.split('#').length > 1 ? [it.split('#')[0], '#' + it.split('#')[1]] : [it, '#']; return <a key={it} href={href} target={href.startsWith('http') ? '_blank' : undefined} rel="noreferrer" style={{ color: 'var(--fg-2)', fontSize: 13.5 }}>{l}</a> })}</div></div>))}
      </div>
      <div className="wrap" style={{ position: 'relative', marginTop: 40, paddingTop: 20, borderTop: '1px solid var(--line)', display: 'flex', justifyContent: 'space-between', color: 'var(--fg-3)', fontSize: 12 }} >
        <span>© {new Date().getFullYear()} GENESIS Memory</span><span className="mono">memory.db · 127.0.0.1 · yours</span>
      </div>
      <style>{`@media (max-width: 900px){ footer .wrap:first-of-type{grid-template-columns:1fr 1fr!important} }`}</style>
    </footer>
  )
}

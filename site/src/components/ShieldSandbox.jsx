import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { Holo, SectionHead, Reveal } from './ui'
import { analyze, ENTROPY_THRESHOLD_BITS } from '../lib/shield'

const SAMPLE = `# .env accidentally pasted into the agent
export OPENAI_API_KEY=sk-live-4f8b2c9e1d7a6b3f0e5c8a2d9b4f7e1c
DATABASE_URL=postgres://app:Sup3rS3cret!@db.internal:5432/genesis
SLACK_BOT=${'xox' + 'b-2934812-ab7Ff92kLm3Pq'}
deploy token: Q9zX7pL2mN4vB8kR1tY6wE3uI0oP5aS9
# benign lines stay byte-identical
git commit ee98d3ed628aae3f221b7aea3020f49f8f1784f5abbdf0fb50a09fd1d8d6e834 fixed the WAL retry path
see https://github.com/HamidRezaeian/genesis-memory/blob/main/README.md`

export default function ShieldSandbox() {
  const [text, setText] = useState(SAMPLE)
  const res = useMemo(() => analyze(text), [text])
  const flagged = res.tokens.filter(t => t.flagged).length
  return (
    <section id="shield" className="section">
      <div className="wrap">
        <SectionHead eyebrow="Zero-Trust Privacy Shield" tone="rose" title="Secrets never touch disk." grad="Prove it yourself." lead="Every byte headed for SQLite or the spool passes two independent detectors: 15 structural vendor patterns and a live Shannon-entropy gate at 4.0 bits/char. Random credentials trip the gate even with no known prefix; git SHAs, URLs and code identifiers pass through untouched. Type anything — this sandbox runs the exact same algorithm in your browser and stores nothing." />
        <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1.1fr) minmax(0,.9fr)', marginTop: 44, alignItems: 'start' }}>
          <Reveal>
            <Holo tilt={false}>
              <div className="pad tight" style={{ display: 'grid', gap: 14 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}><span className="pill rose">input · never persisted</span><button className="btn sm ghost" onClick={() => setText(SAMPLE)}>reset sample</button></div>
                <textarea value={text} onChange={e => setText(e.target.value)} spellCheck={false}
                  style={{ width: '100%', minHeight: 190, resize: 'vertical', fontFamily: 'var(--mono)', fontSize: 12.5, lineHeight: 1.6, background: '#03050a', border: '1px solid var(--line-2)', borderRadius: 10, padding: 12, outline: 'none', color: '#cfe3ee' }} />
                <div>
                  <div style={{ fontSize: 11, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--fg-3)', marginBottom: 6 }}>what reaches disk</div>
                  <pre style={{ margin: 0, fontFamily: 'var(--mono)', fontSize: 12.5, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: '#03050a', border: '1px solid rgba(52,211,153,.25)', borderRadius: 10, padding: 12, color: '#cfe3ee', minHeight: 100 }}>
                    {res.clean.split(/(\[REDACTED_API_KEY\])/).map((part, i) => part === '[REDACTED_API_KEY]'
                      ? <motion.span key={i} initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} style={{ color: 'var(--rose)', background: 'rgba(251,113,133,.15)', borderRadius: 4, padding: '0 4px', boxShadow: '0 0 12px rgba(251,113,133,.25)' }}>{part}</motion.span>
                      : <span key={i}>{part}</span>)}
                  </pre>
                </div>
              </div>
            </Holo>
          </Reveal>
          <Reveal delay={0.15}>
            <div style={{ display: 'grid', gap: 16 }}>
              <Holo tilt={false}>
                <div className="pad tight">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
                    <span style={{ fontSize: 11, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--fg-3)' }}>Shannon entropy per token</span>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--rose)' }}>gate {ENTROPY_THRESHOLD_BITS.toFixed(1)} bits/char</span>
                  </div>
                  <div style={{ position: 'relative', height: 120 }}>
                    <div style={{ position: 'absolute', left: 0, right: 0, top: `${(1 - 4 / 6) * 100}%`, borderTop: '1px dashed rgba(251,113,133,.6)' }} />
                    <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'flex-end', gap: 3 }}>
                      {res.tokens.map((t, i) => (
                        <motion.div key={i} title={`${t.tok.slice(0, 32)} · ${t.bits.toFixed(2)} bits`} initial={{ height: 0 }} animate={{ height: `${Math.min(100, (t.bits / 6) * 100)}%` }} transition={{ type: 'spring', stiffness: 120, damping: 18 }}
                          style={{ flex: 1, minWidth: 3, borderRadius: '3px 3px 0 0', background: t.flagged ? 'var(--rose)' : t.bits >= 4 ? 'var(--amber)' : 'var(--fg-3)', boxShadow: t.flagged ? '0 0 12px rgba(251,113,133,.6)' : 'none', opacity: t.flagged ? 1 : 0.7 }} />
                      ))}
                    </div>
                  </div>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 8 }}>{res.tokens.length} tokens ≥ 8 chars · <span style={{ color: 'var(--rose)' }}>{flagged} high-entropy credential{flagged === 1 ? '' : 's'}</span> · amber = above gate but shaped like hex/path (allowed)</div>
                </div>
              </Holo>
              <Holo tilt={false}>
                <div className="pad tight">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
                    <span style={{ fontSize: 11, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--fg-3)' }}>Detector hits</span>
                    <span className="mono" style={{ fontSize: 22, fontWeight: 700, color: res.redactions ? 'var(--rose)' : 'var(--emerald)' }}>{res.redactions}</span>
                  </div>
                  {res.findings.length ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {res.findings.map((f, i) => (
                        <motion.div key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 12 }}>
                          <span className={`pill ${f.detector === 'entropy' ? 'rose' : 'amber'}`}>{f.detector}</span>
                          <span className="mono" style={{ color: 'var(--fg-3)' }}>{f.len} chars{f.bits ? ` · ${f.bits.toFixed(2)} bits/char` : ''}</span>
                        </motion.div>))}
                    </div>) : <div style={{ color: 'var(--emerald)', fontSize: 13 }}>Clean — every byte preserved exactly.</div>}
                  <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line)', fontSize: 12.5, color: 'var(--fg-2)', lineHeight: 1.6 }}>
                    Applied at three boundaries: <code className="mono" style={{ color: 'var(--cyan)' }}>remember()</code> rejects the payload outright, thread &amp; dialogue fields are redacted in place, and the spool scrubs command output <em>before</em> the atomic write. Telemetry counts what was blocked — never what it was.
                  </div>
                </div>
              </Holo>
            </div>
          </Reveal>
        </div>
      </div>
      <style>{`@media (max-width: 980px){ #shield .grid{grid-template-columns:1fr!important} }`}</style>
    </section>
  )
}

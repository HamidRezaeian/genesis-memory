import { useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Holo, SectionHead, Reveal } from './ui'
import { useCounter, fmt } from '../lib/hooks'

/**
 * Prompt Diet — the subconscious hook injects a bounded capsule (≤200 tokens; 176 in
 * the reference run) instead of the entire memory store. Drag the slider to change
 * how much history/tool-output the agent *would* have carried and watch the proxy's
 * structural compaction + the hook's budget hold the line.
 */
const CAPSULE = [
  ['thread', '• [Active Work Thread (fresh · 4m ago from cursor)]: Topic: WAL retry path · Summary: adopt BEGIN IMMEDIATE + jittered retry · Focus: verify multi-process storm'],
  ['dialogue', '• [Last Dialogue Turn (2m ago via claude)]: User: "what is the busy timeout?" → Assistant: "5000 ms, set in core/db.py"'],
  ['skill', '• [Skill · run tests headlessly]: genesis run -- pytest tests/ -q (conf 0.92)'],
  ['memory', '• [Memory #41 - decision]: Never store raw secrets; redact at the storage boundary'],
  ['memory', '• [Memory #17 - fact]: Proxy gateway listens on 127.0.0.1:8000 and compacts tool outputs'],
  ['directive', '• [Recall rule]: when the user refers to prior work, call recall() before answering.'],
]
const CAPSULE_TOKENS = 176
const BUDGET = 200

export default function DietSlider() {
  const [turns, setTurns] = useState(14)
  const [toolLines, setToolLines] = useState(2200)
  const raw = useMemo(() => 900 + turns * 1450 + toolLines * 18, [turns, toolLines]) // what a naive agent carries
  // After GENESIS: last turn retained (≈ 1 turn), history collapsed to one capsule, tool output → 84-token pointers per call
  const toolCalls = Math.max(1, Math.round(toolLines / 400))
  const after = 900 + 1450 + CAPSULE_TOKENS + toolCalls * 84
  const savedPct = 100 * (1 - after / raw)
  const rawC = useCounter(raw, { duration: 500 })
  const afterC = useCounter(after, { duration: 500 })
  const pctC = useCounter(savedPct, { duration: 500 })
  const dollars = ((raw - after) / 1e6) * 2.5 * 400 // GPT-4o input rate × 400 requests/day

  return (
    <section id="diet" className="section">
      <div className="wrap">
        <SectionHead eyebrow="Subconscious Prompt Diet" tone="emerald" title="Every prompt starts already knowing —" grad="in 176 tokens." lead="The pre-invocation hook distills your whole memory store into a strict ≤200-token capsule: the active thread, the last cross-client dialogue turn, matching skills, top engrams and one recall directive. The stateless gateway then collapses history and replaces tool payloads with pointers. Drag to see the diet hold under load." />
        <div className="grid" style={{ gridTemplateColumns: 'minmax(0,.9fr) minmax(0,1.1fr)', marginTop: 44, alignItems: 'stretch' }}>
          <Reveal>
            <Holo tilt={false} style={{ height: '100%' }}>
              <div className="pad" style={{ display: 'flex', flexDirection: 'column', gap: 26, height: '100%' }}>
                <Slider label="Conversation turns the agent would carry" value={turns} min={1} max={60} onChange={setTurns} suffix="turns" />
                <Slider label="Lines of tool output (tests, logs, diffs)" value={toolLines} min={0} max={20000} step={100} onChange={setToolLines} suffix="lines" />
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <Metric label="naive context" value={fmt.int(rawC)} tone="rose" sub="tokens / request" />
                  <Metric label="with GENESIS" value={fmt.int(afterC)} tone="emerald" sub="tokens / request" />
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--fg-3)', marginBottom: 8 }}><span>prompt anatomy after diet</span><span className="mono" style={{ color: 'var(--emerald)' }}>−{pctC.toFixed(1)}%</span></div>
                  <div style={{ display: 'flex', height: 28, borderRadius: 8, overflow: 'hidden', gap: 2 }}>
                    <Seg w={(900 + 1450) / after} c="var(--cyan)" label="current turn" />
                    <Seg w={CAPSULE_TOKENS / after} c="var(--emerald)" label="capsule 176" />
                    <Seg w={(toolCalls * 84) / after} c="var(--violet)" label={`${toolCalls} pointer${toolCalls > 1 ? 's' : ''}`} />
                  </div>
                  <div style={{ display: 'flex', height: 10, borderRadius: 6, overflow: 'hidden', marginTop: 6, opacity: .35 }}>
                    <div style={{ width: `${(after / raw) * 100}%`, background: 'var(--emerald)' }} /><div style={{ flex: 1, background: 'repeating-linear-gradient(135deg, var(--rose) 0 4px, transparent 4px 8px)' }} />
                  </div>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 6 }}>striped = tokens you no longer pay for · ≈ {fmt.usd(dollars)} / day at GPT-4o rates for a 400-request team</div>
                </div>
              </div>
            </Holo>
          </Reveal>
          <Reveal delay={0.15}>
            <div className="term" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
              <div className="bar"><i /><i /><i /><span>subconscious_hook.py --raw · injected before every prompt</span><span className="pill emerald" style={{ marginLeft: 'auto' }}>{CAPSULE_TOKENS} / {BUDGET} tokens</span></div>
              <div className="body" style={{ flex: 1 }}>
                <div className="dim">[GENESIS Subconscious Memory | Live Telemetry: 6 engrams • {CAPSULE_TOKENS} tokens • ↓ 99.2% payload vs {fmt.int(21_480)} tok DB]:</div>
                <AnimatePresence>{CAPSULE.map(([k, t], i) => (
                  <motion.div key={k + i} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.2 + i * 0.12 }} style={{ marginTop: 8, display: 'flex', gap: 8 }}>
                    <span className={`pill ${k === 'thread' ? 'cyan' : k === 'dialogue' ? 'violet' : k === 'skill' ? 'amber' : k === 'directive' ? 'rose' : 'emerald'}`} style={{ alignSelf: 'flex-start', flex: 'none' }}>{k}</span>
                    <span style={{ color: '#cfe3ee' }}>{t}</span>
                  </motion.div>))}
                </AnimatePresence>
                <div style={{ marginTop: 18, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
                  <div className="dim">budget check · {CAPSULE.length} entries · every line budget-gated, diet directive always last</div>
                  <div className="bar-track" style={{ marginTop: 8 }}><motion.div className="bar-fill" initial={{ width: 0 }} animate={{ width: `${(CAPSULE_TOKENS / BUDGET) * 100}%` }} transition={{ duration: 1.2, delay: 0.8 }} /></div>
                </div>
              </div>
            </div>
          </Reveal>
        </div>
      </div>
      <style>{`@media (max-width: 980px){ #diet .grid{grid-template-columns:1fr!important} }`}</style>
    </section>
  )
}

function Slider({ label, value, min, max, step = 1, onChange, suffix }) {
  const p = ((value - min) / (max - min)) * 100
  return (
    <label style={{ display: 'block' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10, fontSize: 13 }}><span style={{ color: 'var(--fg-2)' }}>{label}</span><span className="mono" style={{ color: '#fff' }}>{fmt.int(value)} {suffix}</span></div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(Number(e.target.value))} style={{ '--p': `${p}%` }} />
    </label>
  )
}
function Metric({ label, value, tone, sub }) {
  return (<div style={{ padding: 14, borderRadius: 12, border: '1px solid var(--line)', background: 'rgba(5,7,11,.6)' }}>
    <div style={{ fontSize: 11, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--fg-3)' }}>{label}</div>
    <div className="mono" style={{ fontSize: 30, fontWeight: 700, color: `var(--${tone})`, letterSpacing: '-.03em', margin: '4px 0' }}>{value}</div>
    <div style={{ fontSize: 11, color: 'var(--fg-3)' }}>{sub}</div></div>)
}
function Seg({ w, c, label }) {
  return <motion.div animate={{ width: `${w * 100}%` }} transition={{ type: 'spring', stiffness: 80, damping: 20 }} style={{ background: c, display: 'grid', placeItems: 'center', minWidth: 2, overflow: 'hidden' }}><span className="mono" style={{ fontSize: 10, color: '#001317', fontWeight: 700, whiteSpace: 'nowrap', padding: '0 6px' }}>{w > 0.14 ? label : ''}</span></motion.div>
}

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Holo, SectionHead, Reveal } from './ui'

/**
 * SleepViz — Hebbian decay curves (retention = e^(−age/τ)) with live engram dots
 * sliding down their curves; reinforcement widens τ. A second panel replays the
 * three-phase consolidation cycle and distils recurring outcomes into a skill.
 */
const PHASES = [
  { k: 'NREM', title: 'Decay & prune', tone: 'cyan', desc: 'Every engram\'s retention is recomputed from its stability τ. Dormant memories fall below the 20% threshold and are tombstoned — never deleted.' },
  { k: 'REM', title: 'Distil skills', tone: 'fg', desc: 'Recurring successful outcomes with shared trigger patterns are compressed into one deterministic procedural skill.' },
  { k: 'WAKE', title: 'Write digest', tone: 'fg', desc: 'Top decisions and facts are rendered into a bounded Active Digest that primes the next session\'s first prompt.' },
]
const OUTCOMES = ['genesis run -- pytest → 421 passed', 'genesis run -- pytest -x → green', 'pytest tests/ headless → green', 'genesis run -- pytest -q → exit 0']
// Decay curves, strongest last. Rank is encoded in dash + weight, never hue:
// dotted slate → solid white → dashed white → solid cyan (solidified = goal).
const CURVES = [
  { tau: 7, col: 'rgba(163,177,194,.55)', dash: [2, 4], lw: 1.2, label: 'fresh τ=7d' },
  { tau: 14, col: 'rgba(216,224,234,.8)', dash: [], lw: 1.4, label: 'reinforced ×1 τ=14d' },
  { tau: 30, col: 'rgba(235,238,242,.9)', dash: [7, 4], lw: 1.6, label: 'reinforced ×3 τ=30d' },
  { tau: 90, col: 'rgba(0,240,255,.95)', dash: [], lw: 2, label: 'solidified τ=90d' },
]

export default function SleepViz() {
  const canvasRef = useRef(null)
  // Perf: skip drawing while offscreen (same pattern as Conduit/NeuralField).
  const visibleRef = useRef(true)
  useEffect(() => {
    const el = canvasRef.current; if (!el || !('IntersectionObserver' in window)) return
    const io = new IntersectionObserver(([e]) => { visibleRef.current = e.isIntersecting }, { threshold: 0.02 })
    io.observe(el); return () => io.disconnect()
  }, [])
  const [phase, setPhase] = useState(0)
  const [reinforced, setReinforced] = useState(0)
  const [distilled, setDistilled] = useState(false)
  const engrams = useRef(Array.from({ length: 22 }, (_, i) => ({ age: Math.random() * 40, tau: [7, 7, 7, 14, 14, 30, 90][i % 7], tone: ['cyan', 'violet', 'emerald'][i % 3], speed: 0.6 + Math.random() })))

  useEffect(() => {
    const id = setInterval(() => setPhase(p => (p + 1) % PHASES.length), 3400)
    return () => clearInterval(id)
  }, [])
  useEffect(() => { if (phase === 1) { const t = setTimeout(() => setDistilled(true), 1200); return () => clearTimeout(t) } if (phase === 0) setDistilled(false) }, [phase])

  useEffect(() => {
    const cv = canvasRef.current, ctx = cv.getContext('2d'); let raf, last = performance.now()
    const tones = { cyan: '0,240,255', violet: '216,224,234', emerald: '235,238,242', amber: '0,240,255' }
    const frame = (now) => {
      const dt = Math.min(0.05, (now - last) / 1000); last = now
      if (!visibleRef.current) { raf = requestAnimationFrame(frame); return }
      const dpr = window.devicePixelRatio || 1; const r = cv.getBoundingClientRect()
      if (cv.width !== Math.round(r.width * dpr)) { cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr) }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); const w = r.width, h = r.height; ctx.clearRect(0, 0, w, h)
      const pad = { l: 40, r: 16, t: 18, b: 28 }, W = w - pad.l - pad.r, H = h - pad.t - pad.b
      ctx.strokeStyle = 'rgba(148,163,184,.12)'; ctx.lineWidth = 1
      for (let i = 0; i <= 4; i++) { const y = pad.t + H * i / 4; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke(); ctx.fillStyle = '#5f6d80'; ctx.font = '10px JetBrains Mono'; ctx.textAlign = 'right'; ctx.fillText((100 - 25 * i) + '%', pad.l - 8, y + 3) }
      ctx.textAlign = 'center'; for (const d of [0, 15, 30, 45, 60]) ctx.fillText(d + 'd', pad.l + W * d / 60, h - 8)
      CURVES.forEach(({ tau, col, dash, lw }) => { ctx.beginPath(); for (let x = 0; x <= W; x += 2) { const days = x / W * 60; const y = pad.t + H * (1 - Math.exp(-days / tau)); x ? ctx.lineTo(pad.l + x, y) : ctx.moveTo(pad.l + x, y) } ctx.strokeStyle = col; ctx.lineWidth = lw; ctx.setLineDash(dash); ctx.stroke(); ctx.setLineDash([]) })
      // dormancy threshold
      ctx.setLineDash([4, 5]); ctx.strokeStyle = 'rgba(148,163,184,.5)'; ctx.beginPath(); ctx.moveTo(pad.l, pad.t + H * .8); ctx.lineTo(w - pad.r, pad.t + H * .8); ctx.stroke(); ctx.setLineDash([]); ctx.textAlign = 'right'; ctx.font = '10.5px Inter'; ctx.lineWidth = 3; ctx.strokeStyle = '#0a0f16'; ctx.strokeText('dormancy threshold · tombstone below', w - pad.r - 6, pad.t + H * .8 - 6); ctx.fillStyle = 'rgba(148,163,184,.9)'; ctx.fillText('dormancy threshold · tombstone below', w - pad.r - 6, pad.t + H * .8 - 6)
      // engrams sliding down their curve; reinforcement (phase NREM click) bumps tau
      for (const e of engrams.current) {
        e.age += dt * e.speed * (phase === 0 ? 2.2 : 0.7); if (e.age > 60) { e.age = 0; e.tau = [7, 14, 30, 90][Math.floor(Math.random() * 4)] }
        const ret = Math.exp(-e.age / e.tau); const x = pad.l + W * e.age / 60, y = pad.t + H * (1 - ret)
        const dormant = ret < 0.2; const col = dormant ? '93,107,125' : tones[e.tone]
        const gg = ctx.createRadialGradient(x, y, 0, x, y, 9); gg.addColorStop(0, `rgba(${col},.9)`); gg.addColorStop(1, `rgba(${col},0)`); ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2); ctx.fill()
        ctx.fillStyle = `rgba(${col},1)`; ctx.beginPath(); ctx.arc(x, y, dormant ? 2 : 3.2, 0, Math.PI * 2); ctx.fill()
        if (dormant && phase === 0) { ctx.strokeStyle = 'rgba(148,163,184,.6)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x - 4, y - 4); ctx.lineTo(x + 4, y + 4); ctx.moveTo(x + 4, y - 4); ctx.lineTo(x - 4, y + 4); ctx.stroke() }
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame); return () => cancelAnimationFrame(raf)
  }, [phase])

  const reinforce = () => { const cands = engrams.current.filter(e => e.tau < 90); if (!cands.length) return; const e = cands[Math.floor(Math.random() * cands.length)]; e.tau = e.tau === 7 ? 14 : e.tau === 14 ? 30 : 90; e.age = Math.min(e.age, 5); setReinforced(r => r + 1) }

  return (
    <section id="sleep" className="section">
      <div className="wrap">
        <SectionHead eyebrow="Biomimetic Sleep Consolidation" tone="violet" title="Memory that forgets on purpose" grad="and learns while you sleep." lead="Memories fade unless you use them — yours do too. Every memory here carries a stability score: recalling it strengthens the trace, dead weight gets archived instead of silently deleted, and repeated wins are distilled into skills you can replay. Press reinforce and watch a synapse strengthen." />
        <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1.25fr) minmax(0,.75fr)', marginTop: 44, alignItems: 'stretch' }}>
          <Reveal>
            <div className="card" style={{ height: 440, position: 'relative' }}>
              <canvas ref={canvasRef} style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 38 }} />
              <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 38, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 18, borderTop: '1px solid var(--line)', background: 'rgba(5,7,11,.72)' }}>
                {CURVES.map(c => (
                  <span key={c.label} className="mono" style={{ fontSize: 10.5, color: 'var(--fg-2)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ width: 22, borderTop: `${c.lw}px ${c.dash.length ? 'dashed' : 'solid'} ${c.col}`, display: 'inline-block' }} />{c.label}
                  </span>))}
              </div>
              <div style={{ position: 'absolute', right: 14, top: 12, display: 'flex', gap: 6 }}>
                <button className="btn sm primary" onClick={reinforce}>▲ reinforce{reinforced ? ` · ${reinforced}` : ' a random engram'}</button>
              </div>
            </div>
          </Reveal>
          <Reveal delay={0.15}>
            <Holo tilt={false} style={{ height: 440 }}>
              <div className="pad tight" style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: 12 }}>
                <div style={{ display: 'flex', gap: 6 }}>
                  {PHASES.map((p, i) => <button key={p.k} onClick={() => setPhase(i)} className="pill" style={{ flex: 1, justifyContent: 'center', padding: '7px 8px', borderColor: i === phase ? `color-mix(in srgb, var(--${p.tone}) 60%, transparent)` : undefined, color: i === phase ? `var(--${p.tone})` : undefined, background: i === phase ? `color-mix(in srgb, var(--${p.tone}) 8%, transparent)` : undefined, transition: '.3s' }}>{p.k}</button>)}
                </div>
                <AnimatePresence mode="wait">
                  <motion.div key={phase} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: .35 }}>
                    <div style={{ fontWeight: 800, fontSize: 18, color: `var(--${PHASES[phase].tone})` }}>{PHASES[phase].title}</div>
                    <div style={{ color: 'var(--fg-2)', fontSize: 13.5, marginTop: 6, lineHeight: 1.6 }}>{PHASES[phase].desc}</div>
                  </motion.div>
                </AnimatePresence>
                <div style={{ marginTop: 'auto', position: 'relative', minHeight: 170 }}>
                  <div style={{ fontSize: 11, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--fg-3)', marginBottom: 8 }}>Skill distillation</div>
                  <AnimatePresence mode="wait">
                    {!distilled ? (
                      <motion.div key="raw" exit={{ opacity: 0, scale: .9, filter: 'blur(4px)' }} transition={{ duration: .5 }} style={{ display: 'grid', gap: 6 }}>
                        {OUTCOMES.map((o, i) => <motion.div key={o} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * .08 }} className="mono" style={{ fontSize: 11.5, padding: '6px 10px', borderRadius: 8, border: '1px solid var(--line)', color: 'var(--fg-2)', display: 'flex', gap: 8 }}><span style={{ color: '#fff' }}>outcome</span>{o}</motion.div>)}
                      </motion.div>
                    ) : (
                      <motion.div key="skill" initial={{ opacity: 0, scale: .85 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: 'spring', stiffness: 160, damping: 16 }}
                        style={{ padding: 14, borderRadius: 12, border: '1px solid rgba(0,240,255,.5)', background: 'rgba(0,240,255,.06)', boxShadow: '0 0 40px -10px rgba(0,240,255,.5)' }}>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><span className="pill cyan">skill</span><b>Run test suite headlessly</b><span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--cyan)' }}>conf 0.92</span></div>
                        <div className="mono" style={{ fontSize: 12, marginTop: 8, color: '#cfe3ee' }}>genesis run -- pytest tests/ -q</div>
                        <div style={{ fontSize: 11.5, color: 'var(--fg-3)', marginTop: 6 }}>triggers: run tests · pytest · verify &nbsp;·&nbsp; invariant: exit code preserved &nbsp;·&nbsp; from 4 outcomes</div>
                      </motion.div>)}
                  </AnimatePresence>
                </div>
              </div>
            </Holo>
          </Reveal>
        </div>
      </div>
      <style>{`@media (max-width: 980px){ #sleep .grid{grid-template-columns:1fr!important} }`}</style>
    </section>
  )
}

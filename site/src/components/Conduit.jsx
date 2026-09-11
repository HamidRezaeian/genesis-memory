import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { SectionHead, Reveal } from './ui'

const AGENTS = [
  { id: 'cursor', name: 'Cursor', tone: 'cyan', mode: 'MCP · Hook · Proxy', say: 'Decided: adopt BEGIN IMMEDIATE for all writes. Remembering as decision #41.', tool: 'remember(kind="decision")' },
  { id: 'claude', name: 'Claude Code', tone: 'violet', mode: 'Hook · MCP · Proxy', say: 'Picking up #41 from Cursor via the capsule. Wiring the retry helper now.', tool: 'thread_update(topic="WAL retry")' },
  { id: 'windsurf', name: 'Windsurf', tone: 'emerald', mode: 'MCP · Proxy', say: 'Cascade sees the thread. Tests pass in 8 procs — attesting closure.', tool: 'attest_closure(entity="core/db.py")' },
  { id: 'zed', name: 'Zed', tone: 'amber', mode: 'context_servers', say: '"That change" resolved → decision #41. Reinforcing it (+1).', tool: 'cross_client_resolve(q="that change")' },
  { id: 'nvim', name: 'Neovim', tone: 'rose', mode: 'mcphub · Avante', say: 'Skill synthesized: "harden sqlite writes" (confidence 0.92).', tool: 'synthesize_skill(...)' },
]
const STEP_MS = 2600

export default function Conduit() {
  const [step, setStep] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [log, setLog] = useState([])
  const canvasRef = useRef(null)
  const pulses = useRef([])

  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => setStep(s => (s + 1) % AGENTS.length), STEP_MS)
    return () => clearInterval(id)
  }, [playing])
  useEffect(() => {
    const a = AGENTS[step]
    setLog(l => [{ id: Date.now(), agent: a, ts: new Date().toLocaleTimeString([], { hour12: false }) }, ...l].slice(0, 6))
    // spawn a pulse from this agent to the nucleus and one to the next agent
    pulses.current.push({ from: step, to: -1, p: 0, tone: a.tone }, { from: -1, to: (step + 1) % AGENTS.length, p: -0.45, tone: a.tone })
  }, [step])

  useEffect(() => {
    const cv = canvasRef.current; const ctx = cv.getContext('2d'); let raf, last = performance.now()
    const tones = { cyan: '0,240,255', violet: '167,139,250', emerald: '52,211,153', amber: '251,191,36', rose: '251,113,133' }
    const pos = (i, w, h) => { if (i < 0) return [w / 2, h / 2]; const a = (i / AGENTS.length) * Math.PI * 2 - Math.PI / 2; return [w / 2 + Math.cos(a) * Math.min(w * 0.38, 250), h / 2 + Math.sin(a) * Math.min(h * 0.36, 150)] }
    const frame = (now) => {
      const dt = Math.min(0.05, (now - last) / 1000); last = now
      const dpr = window.devicePixelRatio || 1; const r = cv.getBoundingClientRect()
      if (cv.width !== Math.round(r.width * dpr)) { cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr) }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); const w = r.width, h = r.height; ctx.clearRect(0, 0, w, h)
      // nucleus
      const g = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, 70); g.addColorStop(0, 'rgba(0,240,255,.55)'); g.addColorStop(0.4, 'rgba(0,240,255,.12)'); g.addColorStop(1, 'rgba(0,240,255,0)'); ctx.fillStyle = g; ctx.beginPath(); ctx.arc(w / 2, h / 2, 70, 0, Math.PI * 2); ctx.fill()
      ctx.fillStyle = '#fff'; ctx.font = '600 11px JetBrains Mono'; ctx.textAlign = 'center'; ctx.fillText('memory.db', w / 2, h / 2 + 4); ctx.fillStyle = 'rgba(163,177,194,.8)'; ctx.font = '10px Inter'; ctx.fillText('SQLite · WAL', w / 2, h / 2 + 18)
      // spokes
      AGENTS.forEach((a, i) => { const [x, y] = pos(i, w, h); ctx.beginPath(); ctx.moveTo(w / 2, h / 2); ctx.lineTo(x, y); ctx.strokeStyle = `rgba(${tones[a.tone]},${i === step ? .55 : .14})`; ctx.lineWidth = i === step ? 1.6 : 1; ctx.setLineDash(i === step ? [] : [3, 6]); ctx.stroke(); ctx.setLineDash([]) })
      // pulses
      pulses.current = pulses.current.filter(p => p.p < 1.05)
      for (const p of pulses.current) { p.p += dt * 0.9; if (p.p < 0) continue; const [x1, y1] = pos(p.from, w, h), [x2, y2] = pos(p.to, w, h); const t = Math.min(1, p.p); const e = t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; const x = x1 + (x2 - x1) * e, y = y1 + (y2 - y1) * e
        const gg = ctx.createRadialGradient(x, y, 0, x, y, 12); gg.addColorStop(0, 'rgba(255,255,255,1)'); gg.addColorStop(.3, `rgba(${tones[p.tone]},.9)`); gg.addColorStop(1, `rgba(${tones[p.tone]},0)`); ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(x, y, 12, 0, Math.PI * 2); ctx.fill()
        // trail
        for (let k = 1; k <= 6; k++) { const tt = Math.max(0, t - k * 0.035); const ee = tt < .5 ? 2 * tt * tt : 1 - Math.pow(-2 * tt + 2, 2) / 2; ctx.fillStyle = `rgba(${tones[p.tone]},${0.35 - k * 0.05})`; ctx.beginPath(); ctx.arc(x1 + (x2 - x1) * ee, y1 + (y2 - y1) * ee, 4 - k * 0.5, 0, Math.PI * 2); ctx.fill() } }
      // agent nodes
      AGENTS.forEach((a, i) => { const [x, y] = pos(i, w, h); const on = i === step; const rr = on ? 26 : 20
        if (on) { const hg = ctx.createRadialGradient(x, y, rr * .6, x, y, rr * 2.2); hg.addColorStop(0, `rgba(${tones[a.tone]},.45)`); hg.addColorStop(1, `rgba(${tones[a.tone]},0)`); ctx.fillStyle = hg; ctx.beginPath(); ctx.arc(x, y, rr * 2.2, 0, Math.PI * 2); ctx.fill() }
        ctx.beginPath(); ctx.arc(x, y, rr, 0, Math.PI * 2); ctx.fillStyle = '#05070b'; ctx.fill(); ctx.strokeStyle = `rgba(${tones[a.tone]},${on ? 1 : .5})`; ctx.lineWidth = on ? 2 : 1.2; ctx.stroke()
        ctx.fillStyle = on ? '#fff' : 'rgba(163,177,194,.9)'; ctx.font = `${on ? 700 : 600} 12px Inter`; ctx.textAlign = 'center'; ctx.fillText(a.name, x, y + 4)
        ctx.fillStyle = `rgba(${tones[a.tone]},.9)`; ctx.font = '9.5px JetBrains Mono'; ctx.fillText(a.mode, x, y + rr + 14) })
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame); return () => cancelAnimationFrame(raf)
  }, [step])

  const a = AGENTS[step]
  return (
    <section id="conduit" className="section">
      <div className="wrap">
        <SectionHead eyebrow="Cross-Client Conduit" tone="violet" title="Start in Cursor. Continue in Claude Code." grad="Finish in Neovim." lead="One SQLite memory, three primitives (MCP · Hook · Proxy), every editor. Watch a decision made in one agent become working context in the next — thread, dialogue and engrams travel across the conduit in milliseconds, with provenance attached." />
        <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1.2fr) minmax(0,.8fr)', marginTop: 44, alignItems: 'stretch' }}>
          <Reveal>
            <div className="card" style={{ height: 460, position: 'relative' }}>
              <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
              <div style={{ position: 'absolute', left: 14, top: 12, display: 'flex', gap: 6 }}>
                <button className="btn sm" onClick={() => setPlaying(p => !p)}>{playing ? '⏸ pause' : '▶ play'}</button>
                <button className="btn sm ghost" onClick={() => setStep(s => (s + 1) % AGENTS.length)}>step →</button>
              </div>
              <div style={{ position: 'absolute', right: 14, top: 12 }} className="pill violet">{AGENTS.length} clients · 1 memory</div>
            </div>
          </Reveal>
          <Reveal delay={0.15}>
            <div className="card" style={{ height: 460, display: 'flex', flexDirection: 'column' }}>
              <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: `var(--${a.tone})`, boxShadow: `0 0 10px var(--${a.tone})` }} /><b>{a.name}</b><span className="mono" style={{ fontSize: 11, color: 'var(--fg-3)', marginLeft: 'auto' }}>{a.mode}</span>
              </div>
              <div style={{ padding: 18, flex: 1, overflow: 'hidden' }}>
                <AnimatePresence mode="popLayout">
                  {log.map((e, i) => (
                    <motion.div key={e.id} layout initial={{ opacity: 0, y: -10, scale: .98 }} animate={{ opacity: 1 - i * 0.14, y: 0, scale: 1 }} exit={{ opacity: 0 }} transition={{ type: 'spring', stiffness: 260, damping: 26 }}
                      style={{ padding: '12px 14px', borderRadius: 12, border: `1px solid ${i === 0 ? `color-mix(in srgb, var(--${e.agent.tone}) 50%, transparent)` : 'var(--line)'}`, background: i === 0 ? `color-mix(in srgb, var(--${e.agent.tone}) 6%, transparent)` : 'rgba(5,7,11,.5)', marginBottom: 10 }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 11, marginBottom: 6 }}><span style={{ color: `var(--${e.agent.tone})`, fontWeight: 700 }}>{e.agent.name}</span><span className="mono" style={{ color: 'var(--fg-3)' }}>{e.ts}</span><span className="mono" style={{ marginLeft: 'auto', color: 'var(--fg-3)', fontSize: 10.5 }}>{e.agent.tool}</span></div>
                      <div style={{ fontSize: 13.5, color: i === 0 ? '#fff' : 'var(--fg-2)' }}>{e.agent.say}</div>
                    </motion.div>))}
                </AnimatePresence>
              </div>
              <div style={{ padding: '12px 18px', borderTop: '1px solid var(--line)', display: 'flex', gap: 14, fontSize: 11.5, color: 'var(--fg-3)' }}>
                <span><b style={{ color: 'var(--cyan)' }}>MCP</b> tools</span><span><b style={{ color: 'var(--violet)' }}>Hook</b> capsule</span><span><b style={{ color: 'var(--emerald)' }}>Proxy</b> gateway</span><span style={{ marginLeft: 'auto' }} className="mono">provenance-labelled · never invents</span>
              </div>
            </div>
          </Reveal>
        </div>
      </div>
      <style>{`@media (max-width: 980px){ #conduit .grid{grid-template-columns:1fr!important} }`}</style>
    </section>
  )
}

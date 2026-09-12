import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { SectionHead, Reveal } from './ui'

const AGENTS = [
  { id: 'cursor', name: 'Cursor', tone: 'cyan', mode: 'MCP · Hook · Proxy', say: 'Decided: adopt BEGIN IMMEDIATE for all writes. Remembering as decision #41.', tool: 'remember(kind="decision")' },
  { id: 'claude', name: 'Claude Code', tone: 'violet', mode: 'Hook · MCP · Proxy', say: 'Picking up #41 from Cursor via the capsule. Wiring the retry helper now.', tool: 'thread_update(topic="WAL retry")' },
  { id: 'windsurf', name: 'Windsurf', tone: 'emerald', mode: 'MCP · Proxy', say: 'Cascade sees the thread. Tests pass in 8 procs — attesting closure.', tool: 'attest_closure(entity="core/db.py")' },
  { id: 'zed', name: 'Zed', tone: 'amber', mode: 'context_servers', say: '"That change" resolved → decision #41. Reinforcing it (+1).', tool: 'cross_client_resolve(q="that change")' },
  { id: 'nvim', name: 'Neovim', tone: 'fg', mode: 'mcphub · Avante', say: 'Skill synthesized: "harden sqlite writes" (confidence 0.92).', tool: 'synthesize_skill(...)' },
]
const STEP_MS = 2600

export default function Conduit() {
  const [step, setStep] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [log, setLog] = useState([])
  const canvasRef = useRef(null)
  const pulses = useRef([])
  // Perf: skip all drawing while the canvas is offscreen (rAF keeps ticking
  // cheaply so pulses/steps resume exactly where they left off).
  const visibleRef = useRef(true)
  useEffect(() => {
    const el = canvasRef.current; if (!el || !('IntersectionObserver' in window)) return
    const io = new IntersectionObserver(([e]) => { visibleRef.current = e.isIntersecting }, { threshold: 0.02 })
    io.observe(el); return () => io.disconnect()
  }, [])

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
    if (pulses.current.length > 12) pulses.current = pulses.current.slice(-12)
  }, [step])

  useEffect(() => {
    const cv = canvasRef.current; const ctx = cv.getContext('2d'); let raf, last = performance.now()
    const tones = { cyan: '0,240,255', fg: '238,243,249', violet: '216,224,234', emerald: '216,224,234', amber: '216,224,234', rose: '216,224,234' }
    const pos = (i, w, h) => { if (i < 0) return [w / 2, h / 2]; const a = (i / AGENTS.length) * Math.PI * 2 - Math.PI / 2; return [w / 2 + Math.cos(a) * Math.min(w * 0.38, 250), h / 2 + Math.sin(a) * Math.min(h * 0.36, 150)] }
    const frame = (now) => {
      const dt = Math.min(0.05, (now - last) / 1000); last = now
      if (!visibleRef.current) { raf = requestAnimationFrame(frame); return }
      const dpr = window.devicePixelRatio || 1; const r = cv.getBoundingClientRect()
      if (cv.width !== Math.round(r.width * dpr)) { cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr) }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); const w = r.width, h = r.height; ctx.clearRect(0, 0, w, h)
      // nucleus + slow orbit ring (same language as hero dashed orbits)
      const g = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, 70); g.addColorStop(0, 'rgba(0,240,255,.55)'); g.addColorStop(0.4, 'rgba(0,240,255,.12)'); g.addColorStop(1, 'rgba(0,240,255,0)'); ctx.fillStyle = g; ctx.beginPath(); ctx.arc(w / 2, h / 2, 70, 0, Math.PI * 2); ctx.fill()
      ctx.beginPath(); ctx.arc(w / 2, h / 2, 46, 0, Math.PI * 2); ctx.strokeStyle = 'rgba(0,240,255,.22)'; ctx.lineWidth = 1; ctx.setLineDash([2, 7]); ctx.lineDashOffset = -now / 90; ctx.stroke(); ctx.setLineDash([])
      ctx.fillStyle = '#fff'; ctx.font = '600 11px JetBrains Mono'; ctx.textAlign = 'center'; ctx.fillText('memory.db', w / 2, h / 2 + 4); ctx.fillStyle = 'rgba(163,177,194,.8)'; ctx.font = '10px Inter'; ctx.fillText('SQLite · WAL', w / 2, h / 2 + 18)
      // spokes: active spoke is a fading signal gradient, idle ones hairline dashed
      AGENTS.forEach((a, i) => { const [x, y] = pos(i, w, h)
        if (i === step) { ctx.strokeStyle = 'rgba(0,240,255,.12)'; ctx.lineWidth = 5; ctx.beginPath(); ctx.moveTo(w / 2, h / 2); ctx.lineTo(x, y); ctx.stroke()
          const sg = ctx.createLinearGradient(w / 2, h / 2, x, y); sg.addColorStop(0, 'rgba(0,240,255,.9)'); sg.addColorStop(1, 'rgba(0,240,255,.15)'); ctx.strokeStyle = sg; ctx.lineWidth = 2 }
        else { ctx.strokeStyle = 'rgba(148,163,184,.22)'; ctx.lineWidth = 1 }
        ctx.beginPath(); ctx.moveTo(w / 2, h / 2); ctx.lineTo(x, y); ctx.setLineDash(i === step ? [] : [3, 6]); ctx.stroke(); ctx.setLineDash([]) })
      // pulses
      pulses.current = pulses.current.filter(p => p.p < 1.05)
      for (const p of pulses.current) { p.p += dt * 0.9; if (p.p < 0) continue; const [x1, y1] = pos(p.from, w, h), [x2, y2] = pos(p.to, w, h); const t = Math.min(1, p.p); const e = t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; const x = x1 + (x2 - x1) * e, y = y1 + (y2 - y1) * e
        const gg = ctx.createRadialGradient(x, y, 0, x, y, 12); gg.addColorStop(0, 'rgba(255,255,255,1)'); gg.addColorStop(.3, `rgba(${tones[p.tone]},.9)`); gg.addColorStop(1, `rgba(${tones[p.tone]},0)`); ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(x, y, 12, 0, Math.PI * 2); ctx.fill()
        // trail
        for (let k = 1; k <= 6; k++) { const tt = Math.max(0, t - k * 0.035); const ee = tt < .5 ? 2 * tt * tt : 1 - Math.pow(-2 * tt + 2, 2) / 2; ctx.fillStyle = `rgba(${tones[p.tone]},${0.35 - k * 0.05})`; ctx.beginPath(); ctx.arc(x1 + (x2 - x1) * ee, y1 + (y2 - y1) * ee, 4 - k * 0.5, 0, Math.PI * 2); ctx.fill() } }
      // agent nodes: compact discs, labels sit BELOW (never inside) so long
      // names can't overflow; active node gets a rotating orbit ring
      AGENTS.forEach((a, i) => { const [x, y] = pos(i, w, h); const on = i === step; const rr = on ? 13 : 10
        ctx.beginPath(); ctx.arc(x, y, rr, 0, Math.PI * 2); ctx.fillStyle = '#05070b'; ctx.fill(); ctx.strokeStyle = on ? 'rgba(0,240,255,1)' : 'rgba(148,163,184,.45)'; ctx.lineWidth = on ? 2 : 1.2; ctx.stroke()
        if (on) { ctx.beginPath(); ctx.arc(x, y, rr + 7, 0, Math.PI * 2); ctx.strokeStyle = 'rgba(0,240,255,.4)'; ctx.lineWidth = 1; ctx.setLineDash([3, 5]); ctx.lineDashOffset = -now / 50; ctx.stroke(); ctx.setLineDash([])
          ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill() }
        ctx.fillStyle = on ? '#fff' : 'rgba(163,177,194,.9)'; ctx.font = `${on ? 700 : 600} 12px Inter`; ctx.textAlign = 'center'; ctx.fillText(a.name, x, y + rr + 16)
        ctx.fillStyle = 'rgba(95,109,128,.9)'; ctx.font = '9px JetBrains Mono'; ctx.fillText(a.mode, x, y + rr + 29) })
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame); return () => cancelAnimationFrame(raf)
  }, [step])

  const a = AGENTS[step]
  return (
    <section id="conduit" className="section">
      <div className="wrap">
        <SectionHead eyebrow="Cross-Client Conduit" title="Start in Cursor. Continue in Claude Code." grad="Finish in Neovim." lead="One SQLite memory, three primitives (MCP · Hook · Proxy), every editor. Watch a decision made in one agent become working context in the next — thread, dialogue and engrams travel across the conduit in milliseconds, with provenance attached." />
        <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1.2fr) minmax(0,.8fr)', marginTop: 44, alignItems: 'stretch' }}>
          <Reveal>
            <div className="card" style={{ height: 460, position: 'relative' }}>
              <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
              <div style={{ position: 'absolute', left: 14, top: 12, display: 'flex', border: '1px solid var(--line)', borderRadius: 10, overflow: 'hidden', background: 'rgba(5,7,11,.7)', backdropFilter: 'blur(8px)' }}>
                <button className="btn sm ghost" style={{ border: 0, borderRadius: 0 }} onClick={() => setPlaying(p => !p)}>{playing ? '⏸ pause' : '▶ play'}</button>
                <span style={{ width: 1, background: 'var(--line)' }} />
                <button className="btn sm ghost" style={{ border: 0, borderRadius: 0 }} onClick={() => setStep(s => (s + 1) % AGENTS.length)}>step →</button>
              </div>
              <div style={{ position: 'absolute', right: 14, top: 12 }} className="pill">{AGENTS.length} clients · 1 memory</div>
            </div>
          </Reveal>
          <Reveal delay={0.15}>
            <div className="card" style={{ height: 460, display: 'flex', flexDirection: 'column' }}>
              <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 10 }}>
                <motion.span key={step} initial={{ scale: 0.4, opacity: 0.4 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: 'spring', stiffness: 400, damping: 15 }} style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--cyan)', boxShadow: '0 0 10px var(--cyan)', flex: 'none' }} /><b>{a.name}</b><span className="mono" style={{ fontSize: 11, color: 'var(--fg-3)', marginLeft: 'auto' }}>{a.mode}</span>
              </div>
              <div style={{ padding: 18, flex: 1, overflow: 'hidden' }}>
                <AnimatePresence mode="popLayout">
                  {log.map((e, i) => (
                    <motion.div key={e.id} layout initial={{ opacity: 0, y: -10, scale: .98 }} animate={{ opacity: 1 - i * 0.14, y: 0, scale: 1 }} exit={{ opacity: 0 }} transition={{ type: 'spring', stiffness: 260, damping: 26 }}
                      style={{ padding: '12px 14px', borderRadius: 12, border: i === 0 ? '1px solid rgba(0,240,255,.5)' : 'var(--line)', background: i === 0 ? 'rgba(0,240,255,.06)' : 'rgba(5,7,11,.5)', marginBottom: 10 }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 11, marginBottom: 6, minWidth: 0 }}><span style={{ color: `var(--${e.agent.tone})`, fontWeight: 700, flex: 'none' }}>{e.agent.name}</span><span className="mono" style={{ color: 'var(--fg-3)', flex: 'none' }}>{e.ts}</span><span className="mono" style={{ marginLeft: 'auto', color: 'var(--fg-2)', fontSize: 10, border: '1px solid var(--line)', borderRadius: 6, padding: '1px 7px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '52%' }}>{e.agent.tool}</span></div>
                      <div style={{ fontSize: 13.5, color: i === 0 ? '#fff' : 'var(--fg-2)' }}>{e.agent.say}</div>
                    </motion.div>))}
                </AnimatePresence>
              </div>
              <div style={{ padding: '12px 18px', borderTop: '1px solid var(--line)', display: 'flex', gap: 14, fontSize: 11.5, color: 'var(--fg-3)' }}>
                <span><b style={{ color: '#fff' }}>MCP</b> tools</span><span><b style={{ color: '#fff' }}>Hook</b> capsule</span><span><b style={{ color: '#fff' }}>Proxy</b> gateway</span><span style={{ marginLeft: 'auto' }} className="mono">provenance-labelled · never invents</span>
              </div>
            </div>
          </Reveal>
        </div>
      </div>
      <style>{`@media (max-width: 980px){ #conduit .grid{grid-template-columns:1fr!important} }`}</style>
    </section>
  )
}

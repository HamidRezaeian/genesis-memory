import { useEffect, useRef } from 'react'
import { Holo, SectionHead, Reveal } from './ui'

const FEATURES = [
  { t: 'Bulletproof SQLite concurrency', d: 'WAL journal, 5000 ms busy timeout, BEGIN IMMEDIATE transactions and jittered retry. Eight agents in eight processes hammering one memory.db — zero "database is locked". Proven in the test suite.', tone: 'cyan', tag: 'core/db.py' },
  { t: 'Stateless gateway · OpenAI & Anthropic', d: 'OPENAI_BASE_URL or ANTHROPIC_BASE_URL → 127.0.0.1:8000. Structural tool-pair compaction, anaphora rewriting, memory capsule injection, byte-exact SSE passthrough, fail-open on any doubt.', tone: 'emerald', tag: '/v1/chat/completions · /v1/messages' },
  { t: 'Respectful challenge protocol', d: 'Agents can propose a better rule than a solidified one. GENESIS stages a conflict for human veto instead of silently complying or silently overwriting.', tone: 'violet', tag: 'challenge_rule()' },
  { t: 'Live pricing · dollars, not vibes', d: 'A 437-model pricing catalog ships in the wheel and refreshes live. Every stripped token is converted into real money saved at your model\'s rate, with prompt-cache discounts applied.', tone: 'amber', tag: 'pricing_engine' },
  { t: 'AST dependency closure', d: 'Extracted import graphs let an agent attest that its working context covers the real blast radius of a change — before it claims the task is done.', tone: 'cyan', tag: 'attest_closure()' },
  { t: 'Mission Control dashboard', d: 'A zero-dependency cockpit streaming SSE telemetry: memory universe, engram explorer, conflict deck, skill browser, token diet, client mesh, privacy sandbox.', tone: 'rose', tag: 'genesis dashboard' },
]

export default function Features() {
  return (
    <section id="features" className="section">
      <div className="wrap">
        <SectionHead eyebrow="Architecture" title="Built like infrastructure," grad="not a plugin." lead="Stdlib-only daemon under 100 MB RSS. Every invariant is a test. Every claim is a counter you can read." />
        <div className="grid g3" style={{ marginTop: 44 }}>
          {FEATURES.map((f, i) => (
            <Reveal key={f.t} delay={i * 0.06}>
              <Holo style={{ height: '100%' }}>
                <div className="pad" style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%' }}>
                  <span className="mono" style={{ fontSize: 11, color: `var(--${f.tone})`, letterSpacing: '.08em' }}>{f.tag}</span>
                  <h3 style={{ fontSize: 20, fontWeight: 800 }}>{f.t}</h3>
                  <p style={{ color: 'var(--fg-2)', fontSize: 14, lineHeight: 1.65 }}>{f.d}</p>
                </div>
              </Holo>
            </Reveal>))}
        </div>
        <Reveal style={{ marginTop: 28 }}>
          <MiniUniverse />
        </Reveal>
      </div>
    </section>
  )
}

/** A compact orbital memory graph, the same visual language as Mission Control. */
function MiniUniverse() {
  const ref = useRef(null)
  const visibleRef = useRef(true)
  useEffect(() => {
    const cv = ref.current, ctx = cv.getContext('2d'); let raf, t = 0
    const io = ('IntersectionObserver' in window) ? new IntersectionObserver(([e]) => { visibleRef.current = e.isIntersecting }, { threshold: 0.02 }) : null
    if (io) io.observe(cv)
    const N = 46; const nodes = Array.from({ length: N }, (_, i) => ({ o: i % 4, a: (i * 2.399963) % (Math.PI * 2), r: 3 + (i % 5), tone: i % 7 === 0 ? '0,240,255' : '216,224,234', sp: 0.05 + (i % 3) * 0.02 }))
    const links = []; for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) if ((i * 31 + j * 17) % 23 === 0) links.push([i, j])
    const frame = () => {
      if (!visibleRef.current) { raf = requestAnimationFrame(frame); return }
      t += 0.016; const dpr = window.devicePixelRatio || 1; const r = cv.getBoundingClientRect()
      if (cv.width !== Math.round(r.width * dpr)) { cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr) }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); const w = r.width, h = r.height; ctx.clearRect(0, 0, w, h); const cx = w / 2, cy = h / 2, R = Math.min(w, h) * 0.46
      const orb = [0.22, 0.42, 0.64, 0.88]
      orb.forEach((o, i) => { ctx.beginPath(); ctx.ellipse(cx, cy, R * o, R * o * 0.42, 0, 0, Math.PI * 2); ctx.strokeStyle = `rgba(0,240,255,${.12 - i * .02})`; ctx.setLineDash([3, 7]); ctx.stroke(); ctx.setLineDash([]) })
      const pos = nodes.map(n => { const a = n.a + t * n.sp * (1 / (1 + n.o)); return [cx + Math.cos(a) * R * orb[n.o], cy + Math.sin(a) * R * orb[n.o] * 0.42 + Math.sin(t + n.a) * 3] })
      for (const [i, j] of links) { ctx.beginPath(); ctx.moveTo(...pos[i]); ctx.lineTo(...pos[j]); ctx.strokeStyle = 'rgba(0,240,255,.12)'; ctx.stroke() }
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, 40); g.addColorStop(0, 'rgba(0,240,255,.5)'); g.addColorStop(1, 'rgba(0,240,255,0)'); ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, 40, 0, Math.PI * 2); ctx.fill()
      nodes.forEach((n, i) => { const [x, y] = pos[i]; const gg = ctx.createRadialGradient(x, y, 0, x, y, n.r * 2.4); gg.addColorStop(0, `rgba(${n.tone},.5)`); gg.addColorStop(1, `rgba(${n.tone},0)`); ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(x, y, n.r * 2.4, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = `rgba(${n.tone},1)`; ctx.beginPath(); ctx.arc(x, y, n.r * 0.55, 0, Math.PI * 2); ctx.fill() })
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame); return () => { cancelAnimationFrame(raf); if (io) io.disconnect() }
  }, [])
  return (
    <div className="card" style={{ position: 'relative', height: 300, display: 'grid', gridTemplateColumns: '1fr 1fr', overflow: 'hidden' }}>
      <div className="pad" style={{ position: 'relative', zIndex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 10 }}>
        <span className="eyebrow"><i />Memory Universe · Mission Control</span>
        <h3 style={{ fontSize: 26, fontWeight: 800 }}>See your agent think.</h3>
        <p style={{ color: 'var(--fg-2)', fontSize: 14, lineHeight: 1.65, maxWidth: 440 }}>Orbit = utility. Size = tokens. Glow = recency. Red dashes = conflicts awaiting your veto. Drag, zoom, click any engram to inspect and reinforce it — all read-only over SQLite WAL, streamed live via SSE.</p>
        <div className="mono" style={{ fontSize: 12, color: 'var(--cyan)' }}>$ genesis dashboard --open</div>
      </div>
      <canvas ref={ref} style={{ position: 'absolute', right: 0, top: 0, width: '55%', height: '100%' }} />
    </div>
  )
}

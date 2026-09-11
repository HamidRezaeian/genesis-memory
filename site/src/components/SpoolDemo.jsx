import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Holo, SectionHead, Reveal, ProgressRing, Stat } from './ui'
import { useCounter, fmt } from '../lib/hooks'

const FILES = ['tests/test_spool.py', 'tests/test_genesis_daemon.py', 'tests/test_privacy_shield.py', 'tests/test_db_hardening.py', 'tests/test_universal_clients.py', 'tests/test_dashboard_server.py', 'tests/test_tool_compactor.py', 'tests/test_hebbian_and_skills.py']
const TOTAL_LINES = 4000
const POINTER_TOKENS = 84

function genLine(i) {
  const f = FILES[i % FILES.length]
  const r = (i * 7919) % 100
  if (r < 3) return { t: `${f}::test_case_${i} FAILED`, c: 'err' }
  if (r < 6) return { t: `E   AssertionError: expected 'wal' got 'delete' (line ${100 + (i % 800)})`, c: 'err' }
  if (r < 12) return { t: `${f}::test_case_${i} PASSED                                   [${Math.min(99, Math.floor(i / 40))}%]`, c: 'ok' }
  if (r < 20) return { t: `DEBUG genesis.spool: write ${(i * 131) % 9999} bytes -> ~/.genesis/spool/${(i * 2654435761 >>> 0).toString(16).slice(0, 8)}.log`, c: 'dim' }
  if (r < 28) return { t: `INFO  genesis.daemon: recall q="busy timeout wal" hits=3 ms=${(i % 17) + 2}`, c: 'dim' }
  return { t: `${f}::test_case_${i} PASSED`, c: '' }
}

const SUMMARY = [
  { t: '[pytest tests/] exit_code=1  ·  3,912 passed · 37 failed · 51 skipped in 41.2s', c: 'am' },
  { t: 'FAILED tests/test_db_hardening.py::test_case_1203 — AssertionError line 703', c: 'err' },
  { t: 'FAILED tests/test_spool.py::test_case_2214 — AssertionError line 214', c: 'err' },
  { t: '… 35 more failing node ids in full log', c: 'dim' },
  { t: '[full log: ctx:log/9f3c1a7e (4,000 lines / 312.6 KB) — tool: genesis_log(id="9f3c1a7e", grep="FAILED")]', c: 'cy' },
]

export default function SpoolDemo() {
  const [phase, setPhase] = useState('idle') // idle → streaming → collapsing → pointer
  const [shown, setShown] = useState(0)
  const bodyRef = useRef(null)
  const [runs, setRuns] = useState(0)

  const start = () => { setPhase('streaming'); setShown(0) }
  useEffect(() => {
    if (phase !== 'streaming') return
    let raf, last = performance.now()
    const tick = (now) => {
      const dt = now - last; last = now
      setShown(s => {
        const next = Math.min(TOTAL_LINES, s + Math.max(1, Math.round(dt * (0.9 + s / 900))))
        if (next >= TOTAL_LINES) { setTimeout(() => setPhase('collapsing'), 350); return TOTAL_LINES }
        return next
      })
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [phase])
  useEffect(() => { if (bodyRef.current && phase === 'streaming') bodyRef.current.scrollTop = bodyRef.current.scrollHeight }, [shown, phase])
  useEffect(() => { if (phase === 'collapsing') { const id = setTimeout(() => { setPhase('pointer'); setRuns(r => r + 1) }, 1400); return () => clearTimeout(id) } }, [phase])
  useEffect(() => { const id = setTimeout(start, 900); return () => clearTimeout(id) }, []) // eslint-disable-line

  const rawTokens = Math.round(shown * 19.8)
  const tokens = useCounter(phase === 'pointer' ? POINTER_TOKENS : rawTokens, { duration: phase === 'pointer' ? 1200 : 200 })
  const saved = phase === 'pointer' ? 1 - POINTER_TOKENS / (TOTAL_LINES * 19.8) : 0
  const window = phase === 'streaming' ? Array.from({ length: Math.min(shown, 60) }, (_, k) => genLine(shown - Math.min(shown, 60) + k)) : []

  return (
    <section id="spool" className="section">
      <div className="wrap">
        <SectionHead eyebrow="Lossless Headless Spooling" title="4,000 lines of output." grad="84 tokens of context." lead="genesis run wraps pytest, cargo, npm, tsc, go, dotnet, docker, ruff and 60+ other toolchains in a strict headless environment, captures every byte to an atomic local spool, and hands your agent a cryptographic pointer instead of a wall of text. Nothing is lost — the agent dereferences on demand." />
        <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1.45fr) minmax(0,.8fr)', marginTop: 44, alignItems: 'start' }}>
          <Reveal>
            <div className="term" style={{ height: 460, display: 'flex', flexDirection: 'column', position: 'relative' }}>
              <div className="bar"><i /><i /><i /><span>genesis run -- pytest tests/ &nbsp;·&nbsp; CI=1 TERM=dumb NO_COLOR=1</span>
                <span style={{ marginLeft: 'auto' }} className={`pill ${phase === 'pointer' ? 'emerald' : phase === 'idle' ? '' : 'cyan'}`}>{phase === 'streaming' ? 'capturing' : phase === 'collapsing' ? 'compacting' : phase === 'pointer' ? 'pointer emitted' : 'ready'}</span></div>
              <div ref={bodyRef} className="body" style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
                <AnimatePresence mode="wait">
                  {phase === 'streaming' && (
                    <motion.div key="stream" exit={{ opacity: 0, scaleY: 0.02, filter: 'blur(6px)' }} transition={{ duration: 1.1, ease: [0.7, 0, 0.3, 1] }} style={{ transformOrigin: '50% 100%' }}>
                      {window.map((l, i) => <div key={i} className={l.c} style={{ whiteSpace: 'pre' }}>{l.t}</div>)}
                    </motion.div>
                  )}
                  {phase === 'collapsing' && (
                    <motion.div key="collapse" style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}>
                      <motion.div initial={{ scale: 3, opacity: 0 }} animate={{ scale: [3, 0.9, 1], opacity: [0, 1, 1] }} transition={{ duration: 1.2, ease: [0.2, 0.8, 0.2, 1] }}
                        style={{ width: 14, height: 14, borderRadius: '50%', background: '#fff', boxShadow: '0 0 40px 14px rgba(0,240,255,.8), 0 0 120px 40px rgba(0,240,255,.35)' }} />
                    </motion.div>
                  )}
                  {phase === 'pointer' && (
                    <motion.div key="ptr" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6 }}>
                      <div className="dim">$ genesis run -- pytest tests/</div>
                      {SUMMARY.map((l, i) => <motion.div key={i} className={l.c} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.15 + i * 0.12 }}>{l.t}</motion.div>)}
                      <div style={{ marginTop: 18, padding: 12, border: '1px solid rgba(0,240,255,.25)', borderRadius: 10, background: 'rgba(0,240,255,.05)' }}>
                        <div className="dim" style={{ fontSize: 11, marginBottom: 6 }}>agent dereferences only what it needs</div>
                        <div><span className="vi">genesis_log</span>(id=<span className="am">"9f3c1a7e"</span>, grep=<span className="am">"FAILED"</span>, lines=<span className="am">40</span>)</div>
                        <div className="dim">→ 37 matching lines · 1,204 bytes · byte-identical to disk</div>
                      </div>
                      <div style={{ marginTop: 16 }}><button className="btn sm" onClick={start}>Run again ↻</button></div>
                    </motion.div>
                  )}
                  {phase === 'idle' && <div key="idle" className="dim">$ genesis run -- pytest tests/<span className="caret" style={{ marginLeft: 6 }} /></div>}
                </AnimatePresence>
              </div>
              {phase === 'streaming' && <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 3, background: 'linear-gradient(90deg,var(--cyan),var(--emerald))', width: `${(shown / TOTAL_LINES) * 100}%`, transition: 'width .1s', boxShadow: '0 0 12px var(--cyan)' }} />}
            </div>
          </Reveal>
          <Reveal delay={0.15}>
            <Holo tilt={false}>
              <div className="pad" style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
                <div style={{ display: 'flex', gap: 18, alignItems: 'center' }}>
                  <ProgressRing value={phase === 'pointer' ? 0.984 : (shown / TOTAL_LINES) * 0.02} size={110} stroke={9} color={phase === 'pointer' ? 'var(--emerald)' : 'var(--cyan)'}>
                    <div style={{ textAlign: 'center' }}><div className="mono" style={{ fontSize: 22, fontWeight: 700, color: phase === 'pointer' ? 'var(--emerald)' : '#fff' }}>{phase === 'pointer' ? '−98.4%' : fmt.pct((shown / TOTAL_LINES) * 100, 0)}</div><div style={{ fontSize: 10, color: 'var(--fg-3)', letterSpacing: '.12em' }}>{phase === 'pointer' ? 'TOKEN DIET' : 'CAPTURED'}</div></div>
                  </ProgressRing>
                  <div>
                    <div className="mono" style={{ fontSize: 40, fontWeight: 700, letterSpacing: '-.04em', lineHeight: 1, color: phase === 'pointer' ? 'var(--emerald)' : 'var(--cyan)' }}>{fmt.int(tokens)}</div>
                    <div style={{ fontSize: 12, color: 'var(--fg-3)', letterSpacing: '.12em', textTransform: 'uppercase', marginTop: 4 }}>tokens {phase === 'pointer' ? 'in agent context' : 'without GENESIS'}</div>
                  </div>
                </div>
                <div className="grid g2" style={{ gap: 14 }}>
                  <Stat value={fmt.int(shown)} label="lines captured" tone="fg" />
                  <Stat value={`${(shown * 0.0782).toFixed(1)} KB`} label="bytes spooled" tone="fg" />
                  <Stat value={phase === 'pointer' ? '9f3c1a7e' : '········'} label="sha-256 pointer" tone="cyan" />
                  <Stat value={phase === 'pointer' ? 'exit 1' : '—'} label="exit code preserved" tone="amber" />
                </div>
                <div style={{ borderTop: '1px solid var(--line)', paddingTop: 16, fontSize: 13, color: 'var(--fg-2)', lineHeight: 1.6 }}>
                  <b style={{ color: '#fff' }}>Why it matters.</b> A single verbose test run can burn 80k tokens of context. Pointer-not-payload keeps the conversation lean, the log byte-exact, and the agent's judgement intact. Secrets in output are scrubbed <em>before</em> the spool file is written.
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>{['pytest', 'cargo', 'npm', 'tsc', 'go', 'dotnet', 'docker', 'ruff', 'git', 'gradle', 'kubectl', 'terraform', '+50'].map(t => <span key={t} className="pill">{t}</span>)}</div>
                <div className="mono" style={{ fontSize: 11, color: 'var(--fg-3)' }}>{runs} run{runs === 1 ? '' : 's'} · {fmt.int(runs * (TOTAL_LINES * 19.8 - POINTER_TOKENS))} tokens saved in this session</div>
              </div>
            </Holo>
          </Reveal>
        </div>
      </div>
      <style>{`@media (max-width: 980px){ #spool .grid{grid-template-columns:1fr!important} }`}</style>
    </section>
  )
}

import { useEffect, useRef, useState } from 'react'
import { motion, useScroll, useSpring } from 'framer-motion'
import Nav from './components/Nav'
import Hero from './components/Hero'
import SpoolDemo from './components/SpoolDemo'
import DietSlider from './components/DietSlider'
import ShieldSandbox from './components/ShieldSandbox'
import Conduit from './components/Conduit'
import SleepViz from './components/SleepViz'
import Features from './components/Features'
import Setup from './components/Setup'
import Pricing, { Faq, Footer } from './components/Pricing'

/** Cursor-following light: a soft cyan specular that makes the OLED black feel like glass. */
function CursorLight() {
  const ref = useRef(null)
  useEffect(() => {
    let x = window.innerWidth / 2, y = window.innerHeight / 2, tx = x, ty = y, raf
    const onMove = (e) => { tx = e.clientX; ty = e.clientY }
    const tick = () => { x += (tx - x) * 0.12; y += (ty - y) * 0.12; if (ref.current) ref.current.style.transform = `translate3d(${x - 300}px, ${y - 300}px, 0)`; raf = requestAnimationFrame(tick) }
    window.addEventListener('pointermove', onMove, { passive: true }); raf = requestAnimationFrame(tick)
    return () => { window.removeEventListener('pointermove', onMove); cancelAnimationFrame(raf) }
  }, [])
  return <div ref={ref} aria-hidden style={{ position: 'fixed', left: 0, top: 0, width: 600, height: 600, borderRadius: '50%', pointerEvents: 'none', zIndex: 1, background: 'radial-gradient(circle, rgba(0,240,255,.07), rgba(0,240,255,.02) 40%, transparent 70%)', mixBlendMode: 'screen', willChange: 'transform' }} />
}

function ScrollProgress() {
  const { scrollYProgress } = useScroll()
  const w = useSpring(scrollYProgress, { stiffness: 120, damping: 24 })
  return <motion.div style={{ position: 'fixed', top: 0, left: 0, right: 0, height: 2, transformOrigin: '0 50%', scaleX: w, background: 'var(--cyan)', zIndex: 50, boxShadow: '0 0 12px var(--cyan)' }} />
}

/** Intro curtain: a nucleus that ignites, then reveals the page. */
function Ignition({ onDone }) {
  const [gone, setGone] = useState(false)
  useEffect(() => { const t = setTimeout(() => { setGone(true); onDone?.() }, 1150); return () => clearTimeout(t) }, [onDone])
  if (gone) return null
  return (
    <motion.div initial={{ opacity: 1 }} animate={{ opacity: 0 }} transition={{ delay: 0.75, duration: 0.45 }} style={{ position: 'fixed', inset: 0, zIndex: 100, background: '#000', display: 'grid', placeItems: 'center', pointerEvents: 'none' }}>
      <motion.div initial={{ scale: 0.2, opacity: 0 }} animate={{ scale: [0.2, 1, 22], opacity: [0, 1, 0] }} transition={{ duration: 1.05, times: [0, 0.45, 1], ease: [0.2, 0.8, 0.2, 1] }}
        style={{ width: 60, height: 60, borderRadius: '50%', background: 'radial-gradient(circle at 40% 40%, #fff, var(--cyan) 30%, rgba(0,240,255,.25) 60%, transparent 70%)', boxShadow: '0 0 60px 20px rgba(0,240,255,.6)' }} />
    </motion.div>
  )
}

export default function App() {
  return (
    <div className="noise" style={{ position: 'relative' }}>
      <Ignition />
      <ScrollProgress />
      <CursorLight />
      <Nav />
      <main style={{ position: 'relative', zIndex: 2 }}>
        <Hero />
        <SpoolDemo />
        <DietSlider />
        <ShieldSandbox />
        <Conduit />
        <SleepViz />
        <Features />
        <Setup />
        <Pricing />
        <Faq />
      </main>
      <Footer />
    </div>
  )
}

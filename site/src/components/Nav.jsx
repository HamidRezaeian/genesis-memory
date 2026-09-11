import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Magnetic } from './ui'

const LINKS = [['Diet', '#diet'], ['Spool', '#spool'], ['Shield', '#shield'], ['Conduit', '#conduit'], ['Sleep', '#sleep'], ['Clients', '#setup'], ['Pricing', '#pricing']]

export default function Nav() {
  const [scrolled, setScrolled] = useState(false)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const fn = () => setScrolled(window.scrollY > 24)
    fn(); window.addEventListener('scroll', fn, { passive: true })
    return () => window.removeEventListener('scroll', fn)
  }, [])
  return (
    <motion.header initial={{ y: -40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ duration: 0.8, ease: [0.2, 0.8, 0.2, 1] }}
      style={{ position: 'fixed', top: 0, left: 0, right: 0, zIndex: 40, transition: 'background .4s, border-color .4s, backdrop-filter .4s',
        background: scrolled ? 'rgba(0,0,0,.62)' : 'transparent', backdropFilter: scrolled ? 'blur(18px) saturate(140%)' : 'none',
        borderBottom: `1px solid ${scrolled ? 'var(--line)' : 'transparent'}` }}>
      <div className="wrap" style={{ display: 'flex', alignItems: 'center', height: 64, gap: 24 }}>
        <a href="#top" style={{ display: 'flex', alignItems: 'center', gap: 10, fontWeight: 900, letterSpacing: '.16em', fontSize: 13 }}>
          <span style={{ width: 22, height: 22, borderRadius: '50%', background: 'radial-gradient(circle at 35% 35%,#fff,var(--cyan) 35%,rgba(0,240,255,.15) 70%,transparent 72%)', boxShadow: '0 0 22px rgba(0,240,255,.7)' }} />
          GENESIS
        </a>
        <nav style={{ display: 'flex', gap: 4, marginLeft: 'auto' }} className="nav-links">
          {LINKS.map(([l, h]) => <a key={h} href={h} className="btn ghost sm">{l}</a>)}
        </nav>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <a className="btn ghost sm" href="https://github.com/HamidRezaeian/genesis-memory" target="_blank" rel="noreferrer" style={{ gap: 8 }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 .5C5.7.5.5 5.7.5 12c0 5.1 3.3 9.4 7.9 10.9.6.1.8-.3.8-.6v-2c-3.2.7-3.9-1.4-3.9-1.4-.5-1.3-1.3-1.7-1.3-1.7-1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.8 1.3 3.4 1 .1-.8.4-1.3.7-1.6-2.6-.3-5.3-1.3-5.3-5.7 0-1.3.5-2.3 1.2-3.1-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.2 1.2a11 11 0 0 1 5.8 0c2.2-1.5 3.2-1.2 3.2-1.2.6 1.6.2 2.8.1 3.1.8.8 1.2 1.8 1.2 3.1 0 4.4-2.7 5.4-5.3 5.7.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6 4.6-1.5 7.9-5.8 7.9-10.9C23.5 5.7 18.3.5 12 .5z" /></svg>
            GitHub
          </a>
          <Magnetic strength={0.25}><a className="btn primary sm" href="#setup">genesis setup</a></Magnetic>
          <button className="btn ghost sm menu-btn" onClick={() => setOpen(v => !v)} aria-label="menu" style={{ display: 'none' }}>☰</button>
        </div>
      </div>
      <AnimatePresence>{open && (
        <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} style={{ overflow: 'hidden', background: 'rgba(0,0,0,.9)', borderTop: '1px solid var(--line)' }}>
          <div className="wrap" style={{ display: 'flex', flexDirection: 'column', padding: '10px 0 16px' }}>
            {LINKS.map(([l, h]) => <a key={h} href={h} className="btn ghost" onClick={() => setOpen(false)} style={{ justifyContent: 'flex-start' }}>{l}</a>)}
          </div>
        </motion.div>)}
      </AnimatePresence>
      <style>{`@media (max-width: 900px){ .nav-links{display:none!important} .menu-btn{display:inline-flex!important} }`}</style>
    </motion.header>
  )
}

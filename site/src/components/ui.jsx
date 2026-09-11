import { useRef } from 'react'
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion'
import { useHoloVars, useInView } from '../lib/hooks'

/** Holographic glass card with cursor-tracked specular lighting + optional 3D tilt. */
export function Holo({ children, className = '', tilt = true, style, ...rest }) {
  const [ref, onMove] = useHoloVars()
  const rx = useMotionValue(0), ry = useMotionValue(0)
  const srx = useSpring(rx, { stiffness: 180, damping: 18 })
  const sry = useSpring(ry, { stiffness: 180, damping: 18 })
  const handleMove = (e) => {
    onMove(e)
    if (!tilt) return
    const r = ref.current.getBoundingClientRect()
    const px = (e.clientX - r.left) / r.width - 0.5
    const py = (e.clientY - r.top) / r.height - 0.5
    ry.set(px * 6); rx.set(-py * 6)
  }
  const reset = () => { rx.set(0); ry.set(0) }
  return (
    <motion.div ref={ref} onPointerMove={handleMove} onPointerLeave={reset}
      className={`card holo ${className}`}
      style={{ rotateX: srx, rotateY: sry, transformPerspective: 1200, transformStyle: 'preserve-3d', ...style }} {...rest}>
      {children}
    </motion.div>
  )
}

/** Magnetic element: gently pulls toward the cursor within a radius. */
export function Magnetic({ children, strength = 0.35, radius = 120, as = 'div', className = '', ...rest }) {
  const ref = useRef(null)
  const x = useMotionValue(0), y = useMotionValue(0)
  const sx = useSpring(x, { stiffness: 260, damping: 20, mass: 0.6 })
  const sy = useSpring(y, { stiffness: 260, damping: 20, mass: 0.6 })
  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect()
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2
    const dx = e.clientX - cx, dy = e.clientY - cy
    const d = Math.hypot(dx, dy)
    if (d < radius + Math.max(r.width, r.height) / 2) { x.set(dx * strength); y.set(dy * strength) } else { x.set(0); y.set(0) }
  }
  const Comp = motion[as] || motion.div
  return (
    <Comp ref={ref} onPointerMove={onMove} onPointerLeave={() => { x.set(0); y.set(0) }} style={{ x: sx, y: sy, display: 'inline-block' }} className={className} {...rest}>
      {children}
    </Comp>
  )
}

export function Reveal({ children, delay = 0, y = 26, className = '', once = true, ...rest }) {
  const [ref, inView] = useInView({ threshold: 0.18 }, once)
  return (
    <motion.div ref={ref} className={className} initial={{ opacity: 0, y, filter: 'blur(6px)' }}
      animate={inView ? { opacity: 1, y: 0, filter: 'blur(0px)' } : {}}
      transition={{ duration: 0.8, delay, ease: [0.2, 0.8, 0.2, 1] }} {...rest}>
      {children}
    </motion.div>
  )
}

/** Word-by-word headline reveal. */
export function Words({ text, className = '', delay = 0, stagger = 0.045 }) {
  const words = text.split(' ')
  return (
    <span className={className} style={{ display: 'inline' }}>
      {words.map((w, i) => (
        <span key={i} style={{ display: 'inline-block', overflow: 'hidden', verticalAlign: 'bottom', paddingBottom: '0.08em', marginBottom: '-0.08em' }}>
          <motion.span style={{ display: 'inline-block' }} initial={{ y: '110%', opacity: 0 }} animate={{ y: 0, opacity: 1 }}
            transition={{ duration: 0.9, delay: delay + i * stagger, ease: [0.2, 0.8, 0.2, 1] }}>{w}</motion.span>
          {i < words.length - 1 ? '\u00A0' : ''}
        </span>
      ))}
    </span>
  )
}

export function SectionHead({ eyebrow, tone = '', title, grad, lead, center = true }) {
  return (
    <Reveal className={center ? 'center' : ''}>
      {eyebrow && <span className={`eyebrow ${tone}`}><i />{eyebrow}</span>}
      <h2 className="h2">{title} {grad && <span className="grad">{grad}</span>}</h2>
      {lead && <p className="lead">{lead}</p>}
    </Reveal>
  )
}

export function Stat({ value, label, tone = 'cyan' }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span className="mono" style={{ fontSize: 28, fontWeight: 700, color: `var(--${tone})`, letterSpacing: '-0.03em', lineHeight: 1 }}>{value}</span>
      <span style={{ fontSize: 12, color: 'var(--fg-3)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>{label}</span>
    </div>
  )
}

export function ProgressRing({ value, size = 84, stroke = 7, color = 'var(--cyan)', children }) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r
  return (
    <div style={{ position: 'relative', width: size, height: size, flex: 'none' }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="rgba(148,163,184,.14)" strokeWidth={stroke} fill="none" />
        <motion.circle cx={size / 2} cy={size / 2} r={r} stroke={color} strokeWidth={stroke} fill="none" strokeLinecap="round"
          style={{ filter: `drop-shadow(0 0 8px ${color})` }} strokeDasharray={c} animate={{ strokeDashoffset: c * (1 - Math.max(0, Math.min(1, value))) }}
          transition={{ type: 'spring', stiffness: 60, damping: 18 }} />
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}>{children}</div>
    </div>
  )
}

export const Arrow = () => (<svg className="arrow" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg>)

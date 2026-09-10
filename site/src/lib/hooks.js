import { useEffect, useRef, useState, useCallback } from 'react'

/** Global normalized mouse position (-1..1) + smoothed version for parallax. */
export function usePointer(smooth = 0.08) {
  const target = useRef({ x: 0, y: 0 })
  const cur = useRef({ x: 0, y: 0 })
  useEffect(() => {
    const onMove = (e) => {
      target.current.x = (e.clientX / window.innerWidth) * 2 - 1
      target.current.y = (e.clientY / window.innerHeight) * 2 - 1
    }
    let raf
    const tick = () => {
      cur.current.x += (target.current.x - cur.current.x) * smooth
      cur.current.y += (target.current.y - cur.current.y) * smooth
      raf = requestAnimationFrame(tick)
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    raf = requestAnimationFrame(tick)
    return () => { window.removeEventListener('pointermove', onMove); cancelAnimationFrame(raf) }
  }, [smooth])
  return cur
}

/** IntersectionObserver visibility flag (fires once by default). */
export function useInView(options = { threshold: 0.25 }, once = true) {
  const ref = useRef(null)
  const [inView, setInView] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) { setInView(true); if (once) io.disconnect() } else if (!once) setInView(false)
    }, options)
    io.observe(el)
    return () => io.disconnect()
  }, [once]) // eslint-disable-line react-hooks/exhaustive-deps
  return [ref, inView]
}

/** Eased counter that animates toward `target`. */
export function useCounter(target, { duration = 1200, active = true } = {}) {
  const [value, setValue] = useState(0)
  const fromRef = useRef(0)
  useEffect(() => {
    if (!active) return
    const from = fromRef.current
    const t0 = performance.now()
    let raf
    const step = (now) => {
      const p = Math.min(1, (now - t0) / duration)
      const e = 1 - Math.pow(1 - p, 4)
      const v = from + (target - from) * e
      setValue(v)
      if (p < 1) raf = requestAnimationFrame(step); else fromRef.current = target
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, duration, active])
  return value
}

/** Sets --mx/--my CSS vars on the element for holographic hover lighting. */
export function useHoloVars() {
  const ref = useRef(null)
  const onMove = useCallback((e) => {
    const el = ref.current; if (!el) return
    const r = el.getBoundingClientRect()
    el.style.setProperty('--mx', `${((e.clientX - r.left) / r.width) * 100}%`)
    el.style.setProperty('--my', `${((e.clientY - r.top) / r.height) * 100}%`)
  }, [])
  return [ref, onMove]
}

export const fmt = {
  int: (n) => Math.round(n).toLocaleString(),
  k: (n) => (n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(Math.round(n))),
  usd: (n, d = 2) => '$' + Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }),
  pct: (n, d = 1) => Number(n).toFixed(d) + '%',
}

export const estTokens = (text) => Math.max(1, Math.round(text.length / 4))

export function useReducedMotion() {
  const [rm, set] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const fn = () => set(mq.matches)
    mq.addEventListener?.('change', fn)
    return () => mq.removeEventListener?.('change', fn)
  }, [])
  return rm
}

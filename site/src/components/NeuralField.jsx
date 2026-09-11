import { useEffect, useRef } from 'react'
import { usePointer, useReducedMotion } from '../lib/hooks'

/**
 * NeuralField — a 3D point-cloud "cortex" rendered on canvas 2D with perspective
 * projection, mouse parallax, depth fog, specular flares, and signal pulses that
 * travel along synapses. Zero dependencies; ~1.5k particles at 60fps.
 */
export default function NeuralField({ density = 1, intensity = 1, className = '', style }) {
  const canvasRef = useRef(null)
  const pointer = usePointer(0.06)
  const reduced = useReducedMotion()

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d', { alpha: true })
    let W = 0, H = 0, dpr = Math.min(2, window.devicePixelRatio || 1)
    let raf, t0 = performance.now()
    const rng = (a, b) => a + Math.random() * (b - a)

    const N = Math.round((window.innerWidth < 720 ? 420 : 1100) * density)
    // Particles arranged as two intersecting rings + a diffuse sphere → looks like a cortex/orbit hybrid.
    const P = new Float32Array(N * 6) // x,y,z, vx,vy,vz (slow drift)
    for (let i = 0; i < N; i++) {
      const mode = Math.random()
      let x, y, z
      if (mode < 0.45) { const a = rng(0, Math.PI * 2), r = rng(0.85, 1.05); x = Math.cos(a) * r; y = rng(-0.06, 0.06); z = Math.sin(a) * r }
      else if (mode < 0.75) { const a = rng(0, Math.PI * 2), r = rng(0.55, 0.7); x = Math.cos(a) * r; z = Math.sin(a) * r * 0.35; y = Math.sin(a) * r * 0.9 }
      else { const u = Math.random(), v = Math.random(); const th = 2 * Math.PI * u, ph = Math.acos(2 * v - 1), r = Math.cbrt(Math.random()) * 1.35; x = r * Math.sin(ph) * Math.cos(th); y = r * Math.sin(ph) * Math.sin(th); z = r * Math.cos(ph) }
      P.set([x, y, z, rng(-1, 1) * 0.0006, rng(-1, 1) * 0.0006, rng(-1, 1) * 0.0006], i * 6)
    }
    // Synapses between near neighbours (bounded)
    const links = []
    for (let i = 0; i < N; i += 2) {
      let best = -1, bd = 0.09
      for (let j = i + 1; j < Math.min(N, i + 60); j++) {
        const dx = P[i * 6] - P[j * 6], dy = P[i * 6 + 1] - P[j * 6 + 1], dz = P[i * 6 + 2] - P[j * 6 + 2]
        const d = dx * dx + dy * dy + dz * dz
        if (d < bd) { bd = d; best = j }
      }
      if (best >= 0) links.push([i, best, Math.random()])
    }
    const pulses = Array.from({ length: 28 }, () => ({ l: Math.floor(Math.random() * links.length), p: Math.random(), s: rng(0.15, 0.45) }))

    const resize = () => {
      W = canvas.clientWidth; H = canvas.clientHeight
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize); ro.observe(canvas)

    const proj = new Float32Array(N * 4) // sx, sy, depth(0..1), scale
    let rotY = 0, rotX = 0.25

    const frame = (now) => {
      const dt = Math.min(0.05, (now - t0) / 1000); t0 = now
      const mx = pointer.current.x, my = pointer.current.y
      if (!reduced) rotY += dt * 0.08
      const targetRx = 0.25 + my * 0.22, targetRy = rotY + mx * 0.35
      rotX += (targetRx - rotX) * 0.05
      const cy = Math.cos(targetRy), sy = Math.sin(targetRy), cx = Math.cos(rotX), sx = Math.sin(rotX)
      const R = Math.min(W, H) * 0.42
      const camZ = 3.2
      const ox = W / 2 + mx * 18, oy = H / 2 + my * 14

      ctx.clearRect(0, 0, W, H)
      // ambient nucleus glow
      const g = ctx.createRadialGradient(ox, oy, 0, ox, oy, R * 0.9)
      g.addColorStop(0, `rgba(0,240,255,${0.10 * intensity})`); g.addColorStop(0.4, `rgba(0,240,255,${0.03 * intensity})`); g.addColorStop(1, 'rgba(0,240,255,0)')
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H)

      for (let i = 0; i < N; i++) {
        const b = i * 6
        if (!reduced) { P[b] += P[b + 3]; P[b + 1] += P[b + 4]; P[b + 2] += P[b + 5]; if (Math.abs(P[b]) > 1.4) P[b + 3] *= -1; if (Math.abs(P[b + 1]) > 1.4) P[b + 4] *= -1; if (Math.abs(P[b + 2]) > 1.4) P[b + 5] *= -1 }
        let x = P[b], y = P[b + 1], z = P[b + 2]
        // rotate Y then X
        let x1 = x * cy + z * sy, z1 = -x * sy + z * cy
        let y1 = y * cx - z1 * sx, z2 = y * sx + z1 * cx
        const s = camZ / (camZ + z2)
        const o = i * 4
        proj[o] = ox + x1 * R * s; proj[o + 1] = oy + y1 * R * s; proj[o + 2] = (z2 + 1.4) / 2.8; proj[o + 3] = s
      }
      // synapses
      ctx.lineWidth = 1
      for (let k = 0; k < links.length; k++) {
        const [i, j, w] = links[k]
        const a = i * 4, bb = j * 4
        const depth = 1 - (proj[a + 2] + proj[bb + 2]) / 2
        const alpha = (0.05 + depth * 0.22) * intensity * (0.6 + w * 0.6)
        ctx.strokeStyle = `rgba(0,240,255,${alpha})`
        ctx.beginPath(); ctx.moveTo(proj[a], proj[a + 1]); ctx.lineTo(proj[bb], proj[bb + 1]); ctx.stroke()
      }
      // particles with depth fog + specular sparkle
      for (let i = 0; i < N; i++) {
        const o = i * 4
        const depth = 1 - proj[o + 2] // 1 = near
        const r = (0.6 + depth * 1.9) * proj[o + 3]
        const spec = Math.max(0, 1 - Math.hypot((proj[o] - ox) / W - mx * 0.15, (proj[o + 1] - oy) / H - my * 0.15) * 3.2)
        const hue = i % 9 === 0 ? '52,211,153' : i % 13 === 0 ? '167,139,250' : '0,240,255'
        ctx.fillStyle = `rgba(${hue},${(0.25 + depth * 0.7 + spec * 0.4) * intensity})`
        ctx.beginPath(); ctx.arc(proj[o], proj[o + 1], r + spec * 1.2, 0, Math.PI * 2); ctx.fill()
        if (spec > 0.75 && depth > 0.6) { ctx.fillStyle = `rgba(255,255,255,${(spec - 0.75) * 2.5})`; ctx.beginPath(); ctx.arc(proj[o], proj[o + 1], r * 0.6, 0, Math.PI * 2); ctx.fill() }
      }
      // travelling pulses
      if (!reduced) for (const p of pulses) {
        p.p += dt * p.s; if (p.p > 1) { p.p = 0; p.l = Math.floor(Math.random() * links.length) }
        const [i, j] = links[p.l]; const a = i * 4, bb = j * 4
        const x = proj[a] + (proj[bb] - proj[a]) * p.p, y = proj[a + 1] + (proj[bb + 1] - proj[a + 1]) * p.p
        const gg = ctx.createRadialGradient(x, y, 0, x, y, 7); gg.addColorStop(0, 'rgba(255,255,255,.95)'); gg.addColorStop(0.35, 'rgba(0,240,255,.7)'); gg.addColorStop(1, 'rgba(0,240,255,0)')
        ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill()
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [density, intensity, reduced]) // eslint-disable-line react-hooks/exhaustive-deps

  return <canvas ref={canvasRef} className={className} aria-hidden="true" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', ...style }} />
}

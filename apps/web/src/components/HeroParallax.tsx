import { useEffect, useRef, useState } from 'react'
import { ShieldCheck } from 'lucide-react'

/**
 * Purely decorative hero banner -- unlike ActionGraph/ActionFlow, this
 * represents NOTHING from the real data model (see design-system/
 * MASTER.md's data-honesty rule). It's chrome: a mouse-reactive,
 * layered-parallax scene with a subtly 3D-tilting badge, meant to give
 * the dashboard a bit of "alive" personality without claiming to
 * visualize anything real. GPU-cheap (transform only), disabled under
 * prefers-reduced-motion, no new dependency (plain CSS transforms).
 */
export function HeroParallax() {
  const containerRef = useRef<HTMLDivElement>(null)
  const rafRef = useRef<number | null>(null)
  const [tilt, setTilt] = useState({ x: 0, y: 0 })
  const [reduceMotion, setReduceMotion] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduceMotion(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  function handleMouseMove(e: React.MouseEvent<HTMLDivElement>) {
    if (reduceMotion) return
    const el = containerRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const x = (e.clientX - rect.left) / rect.width - 0.5 // -0.5..0.5
    const y = (e.clientY - rect.top) / rect.height - 0.5

    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(() => setTilt({ x, y }))
  }

  function handleMouseLeave() {
    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    setTilt({ x: 0, y: 0 })
  }

  return (
    <div
      ref={containerRef}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      className="relative mb-6 h-64 overflow-hidden rounded-2xl bg-slate-950"
      style={{ perspective: '900px' }}
    >
      {/* depth layer 1 -- slow, far background blobs */}
      <div
        className="pointer-events-none absolute inset-0 transition-transform duration-300 ease-out"
        style={{ transform: `translate(${tilt.x * -18}px, ${tilt.y * -18}px)` }}
      >
        <div className="absolute -left-10 -top-16 h-64 w-64 rounded-full bg-teal-600/25 blur-3xl" />
        <div className="absolute -bottom-20 right-0 h-72 w-72 rounded-full bg-violet-600/20 blur-3xl" />
        <div className="absolute right-1/3 top-0 h-40 w-40 rounded-full bg-orange-500/15 blur-3xl" />
      </div>

      {/* depth layer 2 -- mid, dot grid drifting slightly faster */}
      <svg
        className="pointer-events-none absolute inset-0 h-full w-full transition-transform duration-300 ease-out"
        style={{ transform: `translate(${tilt.x * -34}px, ${tilt.y * -34}px)` }}
        aria-hidden="true"
      >
        <defs>
          <pattern id="hero-dots" width="28" height="28" patternUnits="userSpaceOnUse">
            <circle cx="1.5" cy="1.5" r="1.2" fill="rgba(255,255,255,0.12)" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#hero-dots)" />
      </svg>

      {/* foreground -- tilting badge + tagline, fastest layer */}
      <div
        className="relative flex h-full flex-col items-center justify-center text-center transition-transform duration-300 ease-out"
        style={{
          transform: `rotateX(${tilt.y * -8}deg) rotateY(${tilt.x * 8}deg) translateZ(0)`,
          transformStyle: 'preserve-3d',
        }}
      >
        <div
          className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-teal-400 to-teal-600 shadow-xl shadow-teal-950/50"
          style={{ transform: `translate(${tilt.x * 10}px, ${tilt.y * 10}px)` }}
        >
          <ShieldCheck className="h-8 w-8 text-white" />
        </div>
        <h2 className="text-xl font-bold tracking-tight text-white">Ajanlarınız kontrol altında</h2>
        <p className="mt-1.5 max-w-sm text-sm text-slate-400">
          AI ajanlarınız gerçek sistemlere eriştiğinde, kritik her eylem sizin onayınızı bekler.
        </p>
      </div>
    </div>
  )
}

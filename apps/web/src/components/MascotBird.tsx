import { useEffect, useRef, useState } from 'react'

/**
 * Purely decorative -- a small mascot for the Team page, nothing to do
 * with real data (see design-system/MASTER.md). A chibi bird whose
 * tail is shaped like a key (a small wink at "access"/"guard"), with
 * eyes that track the cursor anywhere on the page. Gated by
 * prefers-reduced-motion (eyes stay centered, no idle bob) same as
 * HeroParallax.
 */
export function MascotBird() {
  const containerRef = useRef<HTMLDivElement>(null)
  const rafRef = useRef<number | null>(null)
  const [pupil, setPupil] = useState({ x: 0, y: 0 })
  const [reduceMotion] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  useEffect(() => {
    if (reduceMotion) return

    function onMove(e: MouseEvent) {
      const el = containerRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      const cx = rect.left + rect.width / 2
      const cy = rect.top + rect.height / 2 - 4
      const angle = Math.atan2(e.clientY - cy, e.clientX - cx)
      const maxOffset = 2.4

      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = requestAnimationFrame(() => {
        setPupil({ x: Math.cos(angle) * maxOffset, y: Math.sin(angle) * maxOffset })
      })
    }

    window.addEventListener('mousemove', onMove)
    return () => {
      window.removeEventListener('mousemove', onMove)
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [reduceMotion])

  return (
    <div
      ref={containerRef}
      className={`h-20 w-20 ${reduceMotion ? '' : 'animate-[mascot-bob_3.5s_ease-in-out_infinite]'}`}
      title="Ekibinizi izliyor"
      aria-hidden="true"
    >
      <svg viewBox="0 0 100 100" className="h-full w-full">
        {/* key-shaped tail: bow near the body, shaft + teeth at the tip */}
        <g stroke="#d97706" strokeWidth="4" strokeLinecap="round" fill="none">
          <circle cx="78" cy="70" r="6" />
          <line x1="83" y1="70" x2="96" y2="70" />
          <line x1="90" y1="70" x2="90" y2="76" />
          <line x1="95" y1="70" x2="95" y2="75" />
        </g>

        {/* legs */}
        <line x1="42" y1="82" x2="40" y2="92" stroke="#d97706" strokeWidth="3" strokeLinecap="round" />
        <line x1="58" y1="82" x2="60" y2="92" stroke="#d97706" strokeWidth="3" strokeLinecap="round" />

        {/* wing */}
        <ellipse cx="30" cy="58" rx="10" ry="15" fill="#0d9488" opacity="0.85" transform="rotate(-15 30 58)" />

        {/* body */}
        <ellipse cx="50" cy="55" rx="30" ry="28" fill="#14b8a6" />

        {/* beak */}
        <path d="M 47 66 L 53 66 L 50 72 Z" fill="#d97706" />

        {/* eyes (sclera) */}
        <circle cx="41" cy="50" r="10" fill="white" />
        <circle cx="61" cy="50" r="10" fill="white" />

        {/* pupils -- follow the cursor */}
        <circle cx={41 + pupil.x} cy={50 + pupil.y} r="4.5" fill="#0f172a" />
        <circle cx={61 + pupil.x} cy={50 + pupil.y} r="4.5" fill="#0f172a" />

        {/* cheek blush */}
        <circle cx="33" cy="60" r="4" fill="#f472b6" opacity="0.35" />
        <circle cx="69" cy="60" r="4" fill="#f472b6" opacity="0.35" />
      </svg>
    </div>
  )
}

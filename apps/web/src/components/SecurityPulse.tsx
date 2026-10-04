import { useEffect, useRef, useState } from 'react'
import { ShieldCheck, X } from 'lucide-react'

/**
 * Purely decorative (see design-system/MASTER.md's data-honesty rule --
 * this is chrome, not a data visualization, exactly like HeroParallax).
 * An abstract, restrained "signal" scene: small dots (standing in for
 * AI agent actions) travel down a vertical channel through a shield
 * checkpoint that pings softly -- standing in for AgentGuard's
 * continuous review, never claiming to show a real, live action
 * stream. Fixed dark background regardless of the app's light/dark
 * theme, so it reads as a static console strip bookending the sidebar,
 * same idea as HeroParallax. User-toggleable from Layout.tsx and
 * persisted to localStorage.
 */
export function SecurityPulse({ onClose }: { onClose: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const rafRef = useRef<number | null>(null)
  const [glow, setGlow] = useState({ x: 0, y: 0 })
  const [reduceMotion] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  useEffect(() => {
    if (reduceMotion) return

    function onMove(e: MouseEvent) {
      const el = containerRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      const nx = Math.max(-1, Math.min(1, (e.clientX - (rect.left + rect.width / 2)) / rect.width)) * 14
      const relY = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height))
      const ny = relY * rect.height * 0.3 - rect.height * 0.15

      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = requestAnimationFrame(() => setGlow({ x: nx, y: ny }))
    }

    window.addEventListener('mousemove', onMove)
    return () => {
      window.removeEventListener('mousemove', onMove)
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [reduceMotion])

  return (
    <aside
      ref={containerRef}
      className="relative flex w-40 shrink-0 flex-col items-center justify-center gap-3 overflow-hidden border-l border-slate-800 bg-slate-950 px-3 py-6"
    >
      <button
        onClick={onClose}
        className="absolute right-2 top-2 z-20 flex h-6 w-6 cursor-pointer items-center justify-center rounded-full text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-300"
        title="Bu görseli gizle"
        aria-label="Görseli kapat"
      >
        <X className="h-3.5 w-3.5" />
      </button>

      <div
        className="pointer-events-none absolute h-56 w-56 rounded-full bg-teal-600/20 blur-3xl transition-transform duration-500 ease-out"
        style={{ transform: `translate(${glow.x}px, ${glow.y}px)` }}
      />

      <svg className="pointer-events-none absolute inset-0 h-full w-full opacity-[0.05] mix-blend-overlay" aria-hidden="true">
        <filter id="pulse-grain">
          <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" stitchTiles="stitch" />
        </filter>
        <rect width="100%" height="100%" filter="url(#pulse-grain)" />
      </svg>

      <div className="relative flex h-64 w-8 flex-col items-center justify-center">
        <div className="absolute h-full w-px bg-gradient-to-b from-transparent via-slate-700 to-transparent" />

        {!reduceMotion && (
          <>
            <span className="absolute h-1.5 w-1.5 animate-[signal-dot_2.6s_linear_infinite] rounded-full bg-teal-400" />
            <span
              className="absolute h-1.5 w-1.5 animate-[signal-dot_2.6s_linear_infinite] rounded-full bg-teal-400"
              style={{ animationDelay: '1.3s' }}
            />
          </>
        )}

        <div className="relative z-10 flex h-8 w-8 items-center justify-center">
          {!reduceMotion && (
            <>
              <span className="absolute inline-flex h-full w-full animate-[radar-ping_3s_ease-out_infinite] rounded-full border border-teal-500/50" />
              <span
                className="absolute inline-flex h-full w-full animate-[radar-ping_3s_ease-out_infinite] rounded-full border border-teal-500/50"
                style={{ animationDelay: '1.5s' }}
              />
            </>
          )}
          <div
            className={`flex h-8 w-8 items-center justify-center rounded-full bg-teal-600 text-white shadow-sm shadow-teal-950/50 ${reduceMotion ? '' : 'pulse-attention'}`}
          >
            <ShieldCheck className="h-4 w-4" />
          </div>
        </div>
      </div>

      <p className="relative z-10 mt-1 text-center text-[11px] uppercase leading-snug tracking-wide text-slate-500">
        Eylemler güvenle
        <br />
        inceleniyor
      </p>
    </aside>
  )
}

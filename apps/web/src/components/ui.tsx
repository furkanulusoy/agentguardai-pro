import type { ReactNode } from 'react'

export function PageHeader({ title, description }: { title: string; description?: string }) {
  return (
    <div className="mb-8">
      <h1 className="text-[28px] font-extrabold tracking-tight text-slate-900 dark:text-slate-50">{title}</h1>
      {description && <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{description}</p>}
    </div>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 dark:shadow-none ${className}`}
    >
      {children}
    </div>
  )
}

// Semantic tones only -- see design-system/MASTER.md. "info"/"warning"/
// "critical"/"healthy" map to real risk_level/status meaning; "slate"
// is for non-semantic labels (e.g. a role name).
export type Tone = 'slate' | 'info' | 'warning' | 'critical' | 'healthy'

const TONE_CLASSES: Record<Tone, string> = {
  slate: 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
  info: 'bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300',
  warning: 'bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  critical: 'bg-red-50 text-red-700 dark:bg-red-500/15 dark:text-red-300',
  healthy: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
}

const TONE_DOT: Record<Tone, string> = {
  slate: 'bg-slate-400',
  info: 'bg-blue-500',
  warning: 'bg-amber-500',
  critical: 'bg-red-500',
  healthy: 'bg-emerald-500',
}

export function Badge({
  children,
  tone = 'slate',
  dot = false,
  pulse = false,
}: {
  children: ReactNode
  tone?: Tone
  dot?: boolean
  pulse?: boolean
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${TONE_CLASSES[tone]}`}
    >
      {dot && (
        <span
          className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[tone]} ${pulse ? 'pulse-attention' : ''}`}
        />
      )}
      {children}
    </span>
  )
}

export function riskTone(riskLevel: string): Tone {
  if (riskLevel === 'LOW') return 'info'
  if (riskLevel === 'MEDIUM') return 'warning'
  if (riskLevel === 'HIGH' || riskLevel === 'CRITICAL') return 'critical'
  return 'slate'
}

export function statusTone(status: string): Tone {
  if (status === 'PENDING') return 'warning'
  if (status === 'APPROVED') return 'healthy'
  if (status === 'DENIED' || status === 'EXPIRED') return 'slate'
  return 'slate'
}

export function Button({
  children,
  variant = 'primary',
  className = '',
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' }) {
  const variants: Record<string, string> = {
    primary: 'bg-teal-600 text-white hover:bg-teal-700 disabled:bg-teal-300 dark:disabled:bg-teal-900',
    secondary:
      'bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 hover:border-slate-400 dark:bg-slate-800 dark:text-slate-200 dark:border-slate-600 dark:hover:bg-slate-700',
    danger:
      'bg-white text-red-600 border border-red-200 hover:bg-red-50 hover:border-red-300 dark:bg-slate-800 dark:text-red-400 dark:border-red-900 dark:hover:bg-red-950',
  }
  return (
    <button
      className={`inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-60 ${variants[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}

export function Spinner({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <div
      className={`animate-spin rounded-full border-2 border-slate-300 border-t-teal-600 dark:border-slate-600 dark:border-t-teal-400 ${className}`}
    />
  )
}

export function EmptyState({ icon, title, description }: { icon: ReactNode; title: string; description?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-700 dark:text-slate-500">
        {icon}
      </div>
      <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</p>
      {description && <p className="mt-1 max-w-sm text-sm text-slate-500 dark:text-slate-400">{description}</p>}
    </div>
  )
}

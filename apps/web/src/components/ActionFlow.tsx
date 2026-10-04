import { Check, X, Clock, Ban } from 'lucide-react'
import type { ApprovalRequest } from '../types'

type StageState = 'complete' | 'active' | 'stopped' | 'muted' | 'upcoming'

interface Stage {
  key: string
  label: string
  state: StageState
}

// Every stage's visual state is derived purely from the real
// ApprovalRequest fields (status/error) -- see design-system/MASTER.md's
// "Data honesty" rule. Nothing here is invented per-request state.
function computeStages(approval: ApprovalRequest): Stage[] {
  const requested: Stage = { key: 'requested', label: 'Eylem İstendi', state: 'complete' }
  const riskEvaluated: Stage = { key: 'risk', label: 'Risk Değerlendirildi', state: 'complete' }

  switch (approval.status) {
    case 'PENDING':
      return [
        requested,
        riskEvaluated,
        { key: 'gate', label: 'Onay Gerekiyor', state: 'active' },
        { key: 'decision', label: 'İnsan Kararı', state: 'upcoming' },
        { key: 'executed', label: 'Eylem Yürütüldü', state: 'upcoming' },
      ]
    case 'EXPIRED':
      return [
        requested,
        riskEvaluated,
        { key: 'gate', label: 'Onay Gerekiyor', state: 'stopped' },
        { key: 'decision', label: 'İnsan Kararı', state: 'upcoming' },
        { key: 'executed', label: 'Eylem Yürütüldü', state: 'upcoming' },
      ]
    case 'DENIED':
      return [
        requested,
        riskEvaluated,
        { key: 'gate', label: 'Onay Gerekiyor', state: 'complete' },
        { key: 'decision', label: 'İnsan Kararı', state: 'stopped' },
        { key: 'executed', label: 'Eylem Yürütüldü', state: 'upcoming' },
      ]
    case 'APPROVED':
      return [
        requested,
        riskEvaluated,
        { key: 'gate', label: 'Onay Gerekiyor', state: 'complete' },
        { key: 'decision', label: 'İnsan Kararı', state: 'complete' },
        {
          key: 'executed',
          label: approval.execution_status === 'SUCCEEDED' ? 'Eylem tamamlandı' : approval.execution_status === 'UNKNOWN' ? 'Sonuç belirsiz' : approval.error ? 'Yürütme engellendi' : 'Yürütme bekleniyor',
          state: approval.execution_status === 'SUCCEEDED' ? 'complete' : approval.error ? 'stopped' : 'active',
        },
      ]
    default:
      return [requested, riskEvaluated]
  }
}

const CIRCLE_CLASSES: Record<StageState, string> = {
  complete: 'bg-emerald-600 border-emerald-600 text-white',
  active: 'bg-amber-500 border-amber-500 text-white pulse-attention',
  stopped: 'bg-red-600 border-red-600 text-white',
  muted: 'bg-slate-300 border-slate-300 text-white dark:bg-slate-600 dark:border-slate-600',
  upcoming: 'bg-white border-slate-300 text-slate-300 dark:bg-slate-800 dark:border-slate-600 dark:text-slate-600',
}

const LINE_CLASSES: Record<StageState, string> = {
  complete: 'bg-emerald-600',
  active: 'bg-slate-200 dark:bg-slate-700',
  stopped: 'bg-slate-200 dark:bg-slate-700',
  muted: 'bg-slate-200 dark:bg-slate-700',
  upcoming: 'bg-slate-200 dark:bg-slate-700',
}

const LABEL_CLASSES: Record<StageState, string> = {
  complete: 'text-slate-900 dark:text-slate-100',
  active: 'text-amber-700 dark:text-amber-400',
  stopped: 'text-red-700 dark:text-red-400',
  muted: 'text-slate-400 dark:text-slate-500',
  upcoming: 'text-slate-400 dark:text-slate-500',
}

function StageIcon({ state }: { state: StageState }) {
  if (state === 'complete') return <Check className="h-3.5 w-3.5" strokeWidth={3} />
  if (state === 'active') return <Clock className="h-3.5 w-3.5" strokeWidth={2.5} />
  if (state === 'stopped') return <X className="h-3.5 w-3.5" strokeWidth={3} />
  if (state === 'muted') return <Ban className="h-3.5 w-3.5" strokeWidth={2.5} />
  return null
}

export function ActionFlow({ approval }: { approval: ApprovalRequest }) {
  const stages = computeStages(approval)

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:gap-0 sm:items-start" role="list" aria-label="Onay durumu akışı">
      {stages.map((stage, i) => (
        <div key={stage.key} className="flex min-w-0 flex-1 items-start sm:last:flex-none" role="listitem">
          <div className="flex min-w-0 items-center gap-2 sm:flex-col sm:gap-0">
            <div
              className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 transition-colors duration-200 ${CIRCLE_CLASSES[stage.state]}`}
            >
              <StageIcon state={stage.state} />
            </div>
            <p className={`sm:mt-2 sm:max-w-[6.5rem] sm:text-center text-xs font-medium leading-tight ${LABEL_CLASSES[stage.state]}`}>
              {stage.label}
            </p>
          </div>
          {i < stages.length - 1 && (
            <div className={`hidden sm:block mt-3.5 h-0.5 flex-1 transition-colors duration-200 ${LINE_CLASSES[stage.state]}`} />
          )}
        </div>
      ))}
    </div>
  )
}

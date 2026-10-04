import type { PolicySimulation } from '../types'

const DECISION_LABELS = {
  ALLOW: 'İzin ver',
  DENY: 'Engelle',
  REQUIRE_APPROVAL: 'Onay gerektir',
} as const

const DECISIONS = ['ALLOW', 'REQUIRE_APPROVAL', 'DENY'] as const

export function PolicySimulationResult({ simulation }: { simulation: PolicySimulation }) {
  if (simulation.total_events === 0) {
    return (
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Son {simulation.window_days} günde bu aksiyon için hiç kayıt yok -- simüle edilecek bir şey
        bulunamadı.
      </p>
    )
  }
  return (
    <div className="text-xs text-slate-600 dark:text-slate-300">
      <p className="mb-2">
        Son {simulation.window_days} günde <strong>{simulation.total_events}</strong> gerçek aksiyon:{' '}
        {DECISIONS.filter((d) => simulation.historical_breakdown[d] > 0)
          .map((d) => `${simulation.historical_breakdown[d]} ${DECISION_LABELS[d]}`)
          .join(', ')}
        .
      </p>
      <ul className="space-y-1">
        {DECISIONS.map((d) => (
          <li key={d}>
            Bu kuralı <strong>{DECISION_LABELS[d]}</strong> yapsaydın:{' '}
            {simulation.would_change_if[d] === 0
              ? 'hiçbir şey değişmezdi'
              : `${simulation.would_change_if[d]} aksiyonun sonucu değişirdi`}
            .
          </li>
        ))}
      </ul>
    </div>
  )
}

import { Fragment, useEffect, useState } from 'react'
import { FlaskConical, ShieldCheck } from 'lucide-react'
import { clearPolicy, fetchEffectivePolicies, setPolicy, simulatePolicy } from '../api/policies'
import { apiErrorMessage } from '../api/client'
import { useToast } from '../context/ToastContext'
import type { EffectivePolicy, PolicyDecision, PolicySimulation } from '../types'
import { Badge, Button, Card, EmptyState, PageHeader, Spinner, riskTone } from '../components/ui'
import { PolicySimulationResult } from '../components/PolicySimulationResult'

const DECISION_LABELS: Record<PolicyDecision, string> = {
  ALLOW: 'İzin ver',
  DENY: 'Engelle',
  REQUIRE_APPROVAL: 'Onay gerektir',
}

function decisionTone(decision: PolicyDecision): 'healthy' | 'critical' | 'warning' {
  if (decision === 'ALLOW') return 'healthy'
  if (decision === 'DENY') return 'critical'
  return 'warning'
}

export function PoliciesPage() {
  const { showToast } = useToast()
  const [policies, setPolicies] = useState<EffectivePolicy[] | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const [simulatingKey, setSimulatingKey] = useState<string | null>(null)
  const [simulations, setSimulations] = useState<Record<string, PolicySimulation>>({})

  async function load() {
    try {
      const data = await fetchEffectivePolicies()
      setPolicies(data)
    } catch (err) {
      showToast(apiErrorMessage(err, 'Policy kuralları yüklenemedi'), 'error')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleChange(row: EffectivePolicy, value: string) {
    const key = `${row.connector_type}:${row.action}`
    setPendingKey(key)
    try {
      if (value === '') {
        await clearPolicy(row.connector_type, row.action)
        showToast(`${row.action} varsayılana döndürüldü`)
      } else {
        await setPolicy(row.connector_type, row.action, value as PolicyDecision)
        showToast(`${row.action} için kural güncellendi`)
      }
      await load()
    } catch (err) {
      showToast(apiErrorMessage(err, 'Kural güncellenemedi'), 'error')
    } finally {
      setPendingKey(null)
    }
  }

  async function handleSimulate(row: EffectivePolicy) {
    const key = `${row.connector_type}:${row.action}`
    if (simulatingKey === key) {
      setSimulatingKey(null)
      return
    }
    setSimulatingKey(key)
    if (!simulations[key]) {
      try {
        const result = await simulatePolicy(row.connector_type, row.action)
        setSimulations((prev) => ({ ...prev, [key]: result }))
      } catch (err) {
        showToast(apiErrorMessage(err, 'Simülasyon çalıştırılamadı'), 'error')
        setSimulatingKey(null)
      }
    }
  }

  const byConnector = new Map<string, EffectivePolicy[]>()
  for (const row of policies ?? []) {
    const list = byConnector.get(row.connector_type) ?? []
    list.push(row)
    byConnector.set(row.connector_type, list)
  }

  return (
    <div className="policy-page mx-auto max-w-4xl px-8 py-10">
      <PageHeader
        title="Politikalar"
        description="Bu workspace'in agent'ları her connector aksiyonu için ne yapabilir -- sistem varsayılanı her tenant için aynı başlar, burada değiştirilen her satır sadece bu workspace'i etkiler."
      />

      {isLoading ? (
        <Card>
          <div className="flex items-center justify-center py-16">
            <Spinner />
          </div>
        </Card>
      ) : !policies || policies.length === 0 ? (
        <Card>
          <EmptyState
            icon={<ShieldCheck className="h-6 w-6" />}
            title="Görüntülenecek policy bulunamadı"
            description="Henüz kural değerlendirilebilecek bir connector aksiyonu yok."
          />
        </Card>
      ) : (
        <div className="space-y-6">
          {[...byConnector.entries()].map(([connectorType, rows]) => (
            <Card key={connectorType}>
              <div className="border-b border-slate-200 px-6 py-3.5 dark:border-slate-700">
                <h2 className="text-sm font-semibold capitalize text-slate-900 dark:text-slate-100">
                  {connectorType}
                </h2>
              </div>
              <div className="table-scroll">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:text-slate-400">
                    <th className="px-6 py-3 font-semibold">Aksiyon</th>
                    <th className="px-6 py-3 font-semibold">Risk</th>
                    <th className="px-6 py-3 font-semibold">Sistem varsayılanı</th>
                    <th className="px-6 py-3 font-semibold">Bu workspace</th>
                    <th className="px-6 py-3 font-semibold" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                  {rows.map((row) => {
                    const key = `${row.connector_type}:${row.action}`
                    return (
                      <Fragment key={key}>
                      <tr className="transition-colors duration-150 hover:bg-slate-50/80 dark:hover:bg-slate-700/40">
                        <td className="px-6 py-4 font-mono text-xs text-slate-700 dark:text-slate-300">
                          {row.action}
                        </td>
                        <td className="px-6 py-4">
                          <Badge tone={riskTone(row.risk_level)}>{row.risk_level}</Badge>
                        </td>
                        <td className="px-6 py-4">
                          <Badge tone={decisionTone(row.system_default)}>
                            {DECISION_LABELS[row.system_default]}
                          </Badge>
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex items-center gap-2">
                            <select
                              value={row.override ?? ''}
                              disabled={pendingKey === key}
                              onChange={(e) => void handleChange(row, e.target.value)}
                              className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100"
                            >
                              <option value="">
                                Sistem varsayılanı ({DECISION_LABELS[row.system_default]})
                              </option>
                              <option value="ALLOW">İzin ver</option>
                              <option value="REQUIRE_APPROVAL">Onay gerektir</option>
                              <option value="DENY">Engelle</option>
                            </select>
                            {row.override && <Badge tone="info">Özel kural</Badge>}
                          </div>
                        </td>
                        <td className="px-6 py-4">
                          <Button variant="secondary" onClick={() => void handleSimulate(row)}>
                            <FlaskConical className="h-3.5 w-3.5" />
                            Simüle et
                          </Button>
                        </td>
                      </tr>
                      {simulatingKey === key && (
                        <tr>
                          <td colSpan={5} className="bg-slate-50/60 px-6 py-4 dark:bg-slate-900/40">
                            {simulations[key] ? (
                              <PolicySimulationResult simulation={simulations[key]} />
                            ) : (
                              <Spinner className="h-4 w-4" />
                            )}
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

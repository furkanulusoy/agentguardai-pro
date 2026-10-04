import { useCallback, useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import { fetchApprovals } from '../api/approvals'
import { fetchConnectors } from '../api/connectors'
import { apiErrorMessage } from '../api/client'
import { useToast } from '../context/ToastContext'
import type { ApprovalRequest, ConnectorInfo } from '../types'
import { ActionGraph } from '../components/ActionGraph'
import { ApprovalDetailPanel } from '../components/ApprovalDetailPanel'
import { Badge, Card, EmptyState, PageHeader, Spinner, riskTone, statusTone } from '../components/ui'

const STATUS_LABEL: Record<string, string> = {
  PENDING: 'Onay bekliyor',
  APPROVED: 'Onaylandı',
  DENIED: 'Reddedildi',
  EXPIRED: 'Süresi doldu',
}

const POLL_INTERVAL_MS = 4000

export function ApprovalsPage() {
  const { showToast } = useToast()
  const location = useLocation()
  const [approvals, setApprovals] = useState<ApprovalRequest[] | null>(null)
  const [connectors, setConnectors] = useState<ConnectorInfo[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(
    (location.state as { selectId?: string } | null)?.selectId ?? null,
  )

  const load = useCallback(async () => {
    try {
      const data = await fetchApprovals()
      setApprovals(data)
    } catch (err) {
      showToast(apiErrorMessage(err, 'Onay listesi yüklenemedi'), 'error')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    void load()
    void fetchConnectors().then(setConnectors)
    const interval = setInterval(() => void load(), POLL_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [load])

  // Default selection: the most urgent pending item, otherwise the
  // most recent one -- once the user picks something, leave it alone.
  useEffect(() => {
    if (selectedId !== null || !approvals || approvals.length === 0) return
    const firstPending = approvals.find((a) => a.status === 'PENDING')
    setSelectedId((firstPending ?? approvals[0]).id)
  }, [approvals, selectedId])

  function handleResolved(updated: ApprovalRequest) {
    setApprovals((prev) => prev?.map((a) => (a.id === updated.id ? updated : a)) ?? prev)
  }

  const selected = approvals?.find((a) => a.id === selectedId) ?? null
  const pending = approvals?.filter((a) => a.status === 'PENDING') ?? []
  const history = approvals?.filter((a) => a.status !== 'PENDING') ?? []

  return (
    <div className="mx-auto max-w-6xl px-8 py-10">
      <PageHeader
        title="Onaylar"
        description="Ajanların gerçek sistemlerde attığı her hassas eylemin karar yolculuğu."
      />

      {approvals === null ? (
        <div className="flex items-center justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <>
          <Card className="mb-6 p-6">
            <p className="mb-4 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
              Eylem Haritası -- kullanıcı, bağlantı ve son eylemler
            </p>
            <ActionGraph
              connectors={connectors}
              approvals={approvals}
              selectedId={selectedId}
              onSelect={(a) => setSelectedId(a.id)}
            />
          </Card>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_380px]">
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                Bekleyenler ({pending.length})
              </p>
              <Card className="mb-6">
                {pending.length === 0 ? (
                  <EmptyState
                    icon={<ShieldAlert className="h-6 w-6" />}
                    title="Onay bekleyen aksiyon yok"
                    description="Bir ajan hassas bir aksiyon çalıştırmaya çalıştığında burada görünecek."
                  />
                ) : (
                  <ul className="divide-y divide-slate-100 dark:divide-slate-700">
                    {pending.map((a) => (
                      <li key={a.id}>
                        <button
                          onClick={() => setSelectedId(a.id)}
                          className={`flex w-full cursor-pointer items-center justify-between gap-3 px-5 py-3.5 text-left transition-colors duration-150 hover:bg-slate-50 dark:hover:bg-slate-700/40 ${a.id === selectedId ? 'bg-teal-50/70 dark:bg-teal-500/10' : ''}`}
                        >
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                              {a.connector_type}.{a.action}
                            </p>
                            <p className="text-xs text-slate-500 dark:text-slate-400">
                              {a.agent_name ? `${a.agent_name} (${a.requested_by_email})` : a.requested_by_email}
                              {' '}&middot; {new Date(a.created_at).toLocaleTimeString('tr-TR')}
                            </p>
                          </div>
                          <Badge tone={riskTone(a.risk_level)}>{a.risk_level}</Badge>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>

              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Geçmiş</p>
              <Card>
                {history.length === 0 ? (
                  <p className="px-5 py-8 text-center text-sm text-slate-500 dark:text-slate-400">Henüz kayıt yok.</p>
                ) : (
                  <ul className="divide-y divide-slate-100 dark:divide-slate-700">
                    {history.map((a) => (
                      <li key={a.id}>
                        <button
                          onClick={() => setSelectedId(a.id)}
                          className={`flex w-full cursor-pointer items-center justify-between gap-3 px-5 py-3 text-left transition-colors duration-150 hover:bg-slate-50 dark:hover:bg-slate-700/40 ${a.id === selectedId ? 'bg-teal-50/70 dark:bg-teal-500/10' : ''}`}
                        >
                          <div className="min-w-0">
                            <span className="truncate text-sm text-slate-700 dark:text-slate-300">
                              {a.connector_type}.{a.action}
                            </span>
                            <span className="ml-2 text-xs text-slate-400 dark:text-slate-500">
                              {new Date(a.created_at).toLocaleString('tr-TR')}
                            </span>
                          </div>
                          <Badge tone={statusTone(a.status)}>{STATUS_LABEL[a.status] ?? a.status}</Badge>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>

            <Card className="h-fit lg:sticky lg:top-6">
              {selected ? (
                <ApprovalDetailPanel key={selected.id} approval={selected} onResolved={handleResolved} />
              ) : (
                <EmptyState
                  icon={<ShieldAlert className="h-6 w-6" />}
                  title="Bir eylem seçin"
                  description="Detaylarını ve karar yolculuğunu görmek için haritadan veya listeden bir eylem seçin."
                />
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  )
}

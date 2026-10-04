import { useAuth } from '../context/AuthContext'
import { useEffect, useState } from 'react'
import { ScrollText } from 'lucide-react'
import { fetchAuditEvents } from '../api/audit'
import { apiClient, apiErrorMessage } from '../api/client'
import { useToast } from '../context/ToastContext'
import type { AuditEvent, DecisionSource, PolicyDecision } from '../types'
import { Badge, Card, EmptyState, PageHeader, Spinner, riskTone } from '../components/ui'

const DECISION_LABELS: Record<PolicyDecision, string> = {
  ALLOW: 'İzin verildi',
  DENY: 'Engellendi',
  REQUIRE_APPROVAL: 'Onaya düştü',
}

function decisionTone(decision: PolicyDecision): 'healthy' | 'critical' | 'warning' {
  if (decision === 'ALLOW') return 'healthy'
  if (decision === 'DENY') return 'critical'
  return 'warning'
}

const SOURCE_LABELS: Record<DecisionSource, string> = {
  AGENT_NOT_GRANTED: 'Ajana connector erişimi verilmemiş',
  TASK_SCOPE: 'Görev kapsamı dışı',
  AGENT_POLICY: 'Ajana özel kural',
  TENANT_POLICY: 'Workspace kuralı',
  SYSTEM_DEFAULT: 'Sistem varsayılanı',
}

function outcomeSummary(event: AuditEvent): string {
  if(event.execution_status) return ({SUCCEEDED:'Tamamlandı',WAITING:'İnsan onayı bekliyor',EXECUTING:'Yürütülüyor',READY:'Yürütme bekliyor',UNKNOWN:'Sonuç belirsiz · doğrulama gerekli',FAILED:'Başlatılamadı',DENIED:'Çalıştırılmadı',EXPIRED:'Süresi doldu'} as Record<string,string>)[event.execution_status]??event.execution_status
  if (event.decision === 'DENY') return 'Çalıştırılmadı'
  if (event.error) return 'Yürütme hatası'
  return 'Eski kayıt · yürütme sonucu doğrulanamadı'
}

export function AuditPage() {
  const { showToast } = useToast()
  const {user}=useAuth()
  const [management,setManagement]=useState<{id:string;created_at:string;action:string;target:string;actor_id:string}[]>([])
  useEffect(()=>{
    if(user?.permissions?.includes('tenant.admin')) apiClient.get<typeof management>('/tenant/audit/management').then(r=>setManagement(r.data)).catch(()=>showToast('Yönetim günlüğü yüklenemedi','error'))
  },[user,showToast])
  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [connectorFilter, setConnectorFilter] = useState('')
  const [decisionFilter, setDecisionFilter] = useState('')

  async function load() {
    setIsLoading(true)
    try {
      setEvents(
        await fetchAuditEvents({
          connector_type: connectorFilter || undefined,
          decision: decisionFilter || undefined,
        }),
      )
    } catch (err) {
      showToast(apiErrorMessage(err, 'Audit kayıtları yüklenemedi'), 'error')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectorFilter, decisionFilter])

  return (
    <div className="mx-auto max-w-7xl">
      <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <PageHeader
          title="Denetim kayıtları"
          description="Her agent'ın her aksiyonu için gerçek karar kaydı -- sadece onay bekleyenler değil, otomatik izin verilenler ve engellenenler de."
        />
        <div className="flex items-center gap-2">
          <select
            value={connectorFilter}
            onChange={(e) => setConnectorFilter(e.target.value)}
            className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100"
          >
            <option value="">Tüm connector'lar</option>
            <option value="gmail">Gmail</option>
            <option value="github">GitHub</option>
            <option value="slack">Slack</option>
          </select>
          <select
            value={decisionFilter}
            onChange={(e) => setDecisionFilter(e.target.value)}
            className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100"
          >
            <option value="">Tüm kararlar</option>
            <option value="ALLOW">İzin verildi</option>
            <option value="REQUIRE_APPROVAL">Onaya düştü</option>
            <option value="DENY">Engellendi</option>
          </select>
        </div>
      </div>

      <Card className="overflow-x-auto">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Spinner />
          </div>
        ) : !events || events.length === 0 ? (
          <EmptyState
            icon={<ScrollText className="h-6 w-6" />}
            title="Kayıt bulunamadı"
            description="Bir agent bir connector aksiyonu çalıştırdığında burada görünecek."
          />
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:text-slate-400">
                <th className="px-6 py-3 font-semibold">Zaman</th>
                <th className="px-6 py-3 font-semibold">Kim</th>
                <th className="px-6 py-3 font-semibold">Connector.Aksiyon</th>
                <th className="px-6 py-3 font-semibold">Karar</th>
                <th className="px-6 py-3 font-semibold">Kaynak</th>
                <th className="px-6 py-3 font-semibold">Risk</th>
                <th className="px-6 py-3 font-semibold">Sonuç</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {events.map((event) => (
                <tr key={event.id} className="transition-colors duration-150 hover:bg-slate-50/80 dark:hover:bg-slate-700/40">
                  <td className="whitespace-nowrap px-6 py-3 text-xs text-slate-500 dark:text-slate-400">
                    {new Date(event.created_at).toLocaleString('tr-TR')}
                  </td>
                  <td className="px-6 py-3 text-xs text-slate-700 dark:text-slate-300">
                    {event.agent_name ? `${event.agent_name} (${event.user_email})` : event.user_email}
                  </td>
                  <td className="px-6 py-3 font-mono text-xs text-slate-700 dark:text-slate-300">
                    {event.connector_type}.{event.action}
                  </td>
                  <td className="px-6 py-3">
                    <Badge tone={decisionTone(event.decision)}>{DECISION_LABELS[event.decision]}</Badge>
                  </td>
                  <td className="px-6 py-3 text-xs text-slate-500 dark:text-slate-400">
                    {SOURCE_LABELS[event.decision_source] ?? event.decision_source}
                  </td>
                  <td className="px-6 py-3">
                    <Badge tone={riskTone(event.risk_level)}>{event.risk_level}</Badge>
                  </td>
                  <td className="px-6 py-3 text-xs text-slate-600 dark:text-slate-300">
                    {outcomeSummary(event)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {user?.permissions?.includes('tenant.admin')&&<section className="mt-8"><h2 className="mb-2 text-lg font-semibold">Yönetim günlüğü</h2><p className="mb-4 text-sm text-slate-500">İzin ve policy değişiklikleri, iptaller ve onay payload erişimleri. Değer veya secret kaydedilmez.</p><Card className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr className="border-b border-slate-200"><th className="p-4">Zaman</th><th className="p-4">İşlem</th><th className="p-4">Hedef</th><th className="p-4">Aktör ID</th></tr></thead><tbody>{management.map(row=><tr key={row.id} className="border-b border-slate-100"><td className="p-4">{new Date(row.created_at).toLocaleString('tr-TR')}</td><td className="p-4">{row.action}</td><td className="p-4 font-mono">{row.target}</td><td className="p-4 font-mono">{row.actor_id}</td></tr>)}</tbody></table>{!management.length&&<p className="p-6 text-sm text-slate-500">Henüz yönetim değişikliği yok.</p>}</Card></section>}
    </div>
  )
}

import { Fragment, useCallback, useEffect, useState, type FormEvent } from 'react'
import { Bot, Check, ChevronDown, ChevronRight, Copy, FlaskConical, KeyRound, Plug, Plus, X } from 'lucide-react'
import {
  clearAgentPolicy,
  createAgent,
  fetchAgentConnectors,
  fetchAgentPermissions,
  fetchAgentPolicies,
  fetchAgents,
  grantAgentConnector,
  grantAgentPermission,
  revokeAgent,
  revokeAgentConnector,
  revokeAgentPermission,
  setAgentPolicy,
  simulateAgentPolicy,
} from '../api/agents'
import { apiErrorMessage } from '../api/client'
import { useToast } from '../context/ToastContext'
import type {
  Agent,
  AgentConnectorGrant,
  AgentEffectivePolicy,
  AgentPermissionGrant,
  PolicyDecision,
  PolicySimulation,
} from '../types'
import { Badge, Button, Card, EmptyState, PageHeader, Spinner } from '../components/ui'
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

function AgentConnectorPanel({ agent }: { agent: Agent }) {
  const { showToast } = useToast()
  const [grants, setGrants] = useState<AgentConnectorGrant[] | null>(null)
  const [resources, setResources] = useState<Record<string,string>>({})
  const [pendingId,setPendingId] = useState<string|null>(null)
  const load = useCallback(async () => {try {const data=await fetchAgentConnectors(agent.id);setGrants(data);setResources(Object.fromEntries(data.map(g=>[g.credential_id,(g.resources??[]).join(', ')])))}catch(e){showToast(apiErrorMessage(e),'error')}}, [agent.id, showToast])
  useEffect(()=>{void load()},[load])
  async function save(g:AgentConnectorGrant,revoke=false){setPendingId(g.credential_id);try{if(revoke)await revokeAgentConnector(agent.id,g.credential_id);else await grantAgentConnector(agent.id,g.credential_id,(resources[g.credential_id]??'').split(',').map(s=>s.trim()).filter(Boolean));await load();showToast(revoke?'Erişim kaldırıldı':'Kaynak kapsamı kaydedildi')}catch(e){showToast(apiErrorMessage(e),'error')}finally{setPendingId(null)}}
  if(!grants)return <div className="p-6"><Spinner/></div>
  return <div className="px-6 py-4"><p className="mb-4 text-xs text-slate-500">İzinler yalnız bu bağlantı ve belirtilen kaynaklar için geçerlidir. Wildcard kabul edilmez.</p>{grants.length===0?<p className="text-sm">Önce bir servis bağlayın.</p>:grants.map(g=><div className="mb-4 rounded-lg border border-slate-200 p-4 dark:border-slate-700" key={g.credential_id}><div className="mb-3 flex items-center gap-2"><Plug size={15}/><strong className="text-sm">{g.label||g.connector_type}</strong><Badge tone={g.is_granted?'healthy':'slate'}>{g.is_granted?'İzinli':'İzin yok'}</Badge></div>{g.connector_type!=='gmail'&&<label className="block text-xs text-slate-500">İzinli {g.connector_type==='github'?'owner/repo adları':'kanal kimlikleri veya yeni kanal adları'} (virgülle ayırın)<input aria-label={g.label+' kaynak kapsamı'} className="mt-2 mb-3 w-full rounded border border-slate-300 bg-transparent p-2 text-sm" value={resources[g.credential_id]??''} onChange={e=>setResources({...resources,[g.credential_id]:e.target.value})} placeholder={g.connector_type==='github'?'acme/support, acme/docs':'C012ABCDEF'}/></label>}<div className="flex gap-2"><Button disabled={pendingId===g.credential_id} onClick={()=>void save(g)}>Kapsamı kaydet</Button>{g.is_granted&&<Button variant="danger" disabled={pendingId===g.credential_id} onClick={()=>void save(g,true)}>Erişimi kaldır</Button>}</div></div>)}</div>
}

function AgentPermissionPanel({ agent }: { agent: Agent }) {
  const { showToast } = useToast()
  const [grants, setGrants] = useState<AgentPermissionGrant[] | null>(null)
  const [pendingCode, setPendingCode] = useState<string | null>(null)

  async function load() {
    try {
      setGrants(await fetchAgentPermissions(agent.id))
    } catch (err) {
      showToast(apiErrorMessage(err, 'Ajan izinleri yüklenemedi'), 'error')
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id])

  async function handleToggle(grant: AgentPermissionGrant) {
    setPendingCode(grant.code)
    try {
      if (grant.is_granted) {
        await revokeAgentPermission(agent.id, grant.code)
      } else {
        await grantAgentPermission(agent.id, grant.code)
      }
      await load()
    } catch (err) {
      showToast(apiErrorMessage(err, 'İzin güncellenemedi'), 'error')
    } finally {
      setPendingCode(null)
    }
  }

  if (!grants) {
    return (
      <div className="flex items-center justify-center py-8">
        <Spinner />
      </div>
    )
  }

  const anyGranted = grants.some((g) => g.is_granted)

  return (
    <div className="px-6 py-4">
      <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
        {anyGranted
          ? "Bu ajan yalnızca burada işaretli izinlere sahip -- sahibinin diğer izinleri artık geçerli değil."
          : 'Hiçbir izin verilmedi. Bu ajan tüm korumalı işlemler için reddedilir; izinleri açıkça ekleyin.'}
      </p>
      <ul className="space-y-2">
        {grants.map((grant) => (
          <li key={grant.code} className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <KeyRound className="h-3.5 w-3.5 text-slate-400" />
              <span className="text-sm text-slate-700 dark:text-slate-300">{grant.code}</span>
              {grant.description && (
                <span className="text-xs text-slate-400 dark:text-slate-500">
                  {grant.description}
                </span>
              )}
            </div>
            <Button
              variant={grant.is_granted ? 'danger' : 'secondary'}
              onClick={() => void handleToggle(grant)}
              disabled={pendingCode === grant.code}
            >
              {grant.is_granted ? 'İzni kaldır' : 'İzin ver'}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function AgentPolicyPanel({ agent }: { agent: Agent }) {
  const { showToast } = useToast()
  const [policies, setPolicies] = useState<AgentEffectivePolicy[] | null>(null)
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const [simulatingKey, setSimulatingKey] = useState<string | null>(null)
  const [simulations, setSimulations] = useState<Record<string, PolicySimulation>>({})

  async function load() {
    try {
      setPolicies(await fetchAgentPolicies(agent.id))
    } catch (err) {
      showToast(apiErrorMessage(err, 'Ajan politikaları yüklenemedi'), 'error')
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id])

  async function handleChange(row: AgentEffectivePolicy, value: string) {
    const key = `${row.connector_type}:${row.action}`
    setPendingKey(key)
    try {
      if (value === '') {
        await clearAgentPolicy(agent.id, row.connector_type, row.action)
      } else {
        await setAgentPolicy(agent.id, row.connector_type, row.action, value as PolicyDecision)
      }
      await load()
    } catch (err) {
      showToast(apiErrorMessage(err, 'Kural güncellenemedi'), 'error')
    } finally {
      setPendingKey(null)
    }
  }

  async function handleSimulate(row: AgentEffectivePolicy) {
    const key = `${row.connector_type}:${row.action}`
    if (simulatingKey === key) {
      setSimulatingKey(null)
      return
    }
    setSimulatingKey(key)
    if (!simulations[key]) {
      try {
        const result = await simulateAgentPolicy(agent.id, row.connector_type, row.action)
        setSimulations((prev) => ({ ...prev, [key]: result }))
      } catch (err) {
        showToast(apiErrorMessage(err, 'Simülasyon çalıştırılamadı'), 'error')
        setSimulatingKey(null)
      }
    }
  }

  if (!policies) {
    return (
      <div className="flex items-center justify-center py-8">
        <Spinner />
      </div>
    )
  }

  return (
    <table className="w-full text-left text-sm">
      <thead>
        <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:text-slate-400">
          <th className="px-6 py-2.5 font-semibold">Connector.Aksiyon</th>
          <th className="px-6 py-2.5 font-semibold">Tenant kuralı</th>
          <th className="px-6 py-2.5 font-semibold">Bu ajan</th>
          <th className="px-6 py-2.5 font-semibold" />
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
        {policies.map((row) => {
          const key = `${row.connector_type}:${row.action}`
          return (
            <Fragment key={key}>
            <tr>
              <td className="px-6 py-3 font-mono text-xs text-slate-700 dark:text-slate-300">
                {row.connector_type}.{row.action}
              </td>
              <td className="px-6 py-3">
                <Badge tone={decisionTone(row.tenant_override ?? row.system_default)}>
                  {DECISION_LABELS[row.tenant_override ?? row.system_default]}
                  {!row.tenant_override && ' (varsayılan)'}
                </Badge>
              </td>
              <td className="px-6 py-3">
                <div className="flex items-center gap-2">
                  <select
                    value={row.agent_override ?? ''}
                    disabled={pendingKey === key}
                    onChange={(e) => void handleChange(row, e.target.value)}
                    className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100"
                  >
                    <option value="">Tenant kuralını kullan</option>
                    <option value="ALLOW">İzin ver</option>
                    <option value="REQUIRE_APPROVAL">Onay gerektir</option>
                    <option value="DENY">Engelle</option>
                  </select>
                  {row.agent_override && <Badge tone="info">Bu ajana özel</Badge>}
                </div>
              </td>
              <td className="px-6 py-3">
                <Button variant="secondary" onClick={() => void handleSimulate(row)}>
                  <FlaskConical className="h-3.5 w-3.5" />
                  Simüle et
                </Button>
              </td>
            </tr>
            {simulatingKey === key && (
              <tr>
                <td colSpan={4} className="bg-slate-100/60 px-6 py-4 dark:bg-slate-900/60">
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
  )
}

export function AgentsPage() {
  const { showToast } = useToast()
  const [agents, setAgents] = useState<Agent[] | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const [showCreateForm, setShowCreateForm] = useState(false)
  const [name, setName] = useState('')
  const [isCreating, setIsCreating] = useState(false)
  const [newKey, setNewKey] = useState<{ name: string; key: string } | null>(null)
  const [keyCopied, setKeyCopied] = useState(false)
  const [revokingId, setRevokingId] = useState<string | null>(null)

  async function load() {
    try {
      setAgents(await fetchAgents())
    } catch (err) {
      showToast(apiErrorMessage(err, 'Ajanlar yüklenemedi'), 'error')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleCreate(e: FormEvent) {
    e.preventDefault()
    setIsCreating(true)
    try {
      const agent = await createAgent(name.trim())
      setNewKey({ name: agent.name, key: agent.api_key! })
      setKeyCopied(false)
      setName('')
      await load()
    } catch (err) {
      showToast(apiErrorMessage(err, 'Ajan oluşturulamadı'), 'error')
    } finally {
      setIsCreating(false)
    }
  }

  async function handleCopyKey() {
    if (!newKey) return
    await navigator.clipboard.writeText(newKey.key)
    setKeyCopied(true)
  }

  async function handleRevoke(id: string) {
    setRevokingId(id)
    try {
      await revokeAgent(id)
      showToast('Ajan iptal edildi')
      await load()
    } catch (err) {
      showToast(apiErrorMessage(err, 'Ajan iptal edilemedi'), 'error')
    } finally {
      setRevokingId(null)
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-8 py-10">
      <div className="mb-8 flex items-center justify-between">
        <PageHeader
          title="Ajanlar"
          description="Bu workspace'e bağlanan AI ajanlarının kimliği -- her aksiyon artık bir insanın değil, hangi ajanın yaptığıyla izlenebilir."
        />
        <Button
          onClick={() => {
            setShowCreateForm((prev) => !prev)
            setNewKey(null)
          }}
        >
          <Plus className="h-4 w-4" />
          Ajan oluştur
        </Button>
      </div>

      {showCreateForm && (
        <Card className="mb-6 p-5">
          {newKey ? (
            <div>
              <p className="mb-2 text-sm font-medium text-slate-900 dark:text-slate-100">
                "{newKey.name}" oluşturuldu -- bu anahtarı ajanın çalıştığı ortama kopyala
              </p>
              <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
                Bu anahtar sadece bir kere gösteriliyor (AGENTGUARD_AGENT_KEY olarak kullanılır).
                Kaybedersen yeni bir ajan oluşturup eskisini iptal etmen gerekir.
              </p>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={newKey.key}
                  onFocus={(e) => e.target.select()}
                  className="flex-1 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-700 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-300"
                />
                <Button variant="secondary" onClick={() => void handleCopyKey()}>
                  {keyCopied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                  {keyCopied ? 'Kopyalandı' : 'Kopyala'}
                </Button>
              </div>
            </div>
          ) : (
            <form onSubmit={handleCreate} className="flex items-end gap-3">
              <div className="flex-1">
                <label className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300" htmlFor="agentName">
                  Ajan adı
                </label>
                <input
                  id="agentName"
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="CustomerSupportAgent"
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500"
                />
              </div>
              <Button type="submit" disabled={isCreating}>
                {isCreating ? 'Oluşturuluyor...' : 'Oluştur'}
              </Button>
            </form>
          )}
        </Card>
      )}

      <Card>
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Spinner />
          </div>
        ) : !agents || agents.length === 0 ? (
          <EmptyState
            icon={<Bot className="h-6 w-6" />}
            title="Henüz ajan yok"
            description="Bir MCP sunucusu veya başka bir istemci bu workspace'e bağlanacaksa önce bir ajan kimliği oluştur."
          />
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-700">
            {agents.map((agent) => (
              <li key={agent.id}>
                <div className="flex items-center justify-between gap-3 px-6 py-3.5">
                  <button
                    onClick={() => setExpandedId((prev) => (prev === agent.id ? null : agent.id))}
                    className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-left"
                  >
                    {expandedId === agent.id ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
                    )}
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                        {agent.name}
                      </p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        Sahip: {agent.owner_email ?? '—'}
                        {agent.created_by_email && agent.created_by_email !== agent.owner_email && (
                          <> · Oluşturan: {agent.created_by_email}</>
                        )}
                      </p>
                    </div>
                  </button>
                  <div className="flex items-center gap-2">
                    {agent.is_revoked ? (
                      <Badge tone="slate">İptal edildi</Badge>
                    ) : (
                      <Badge tone="healthy">Aktif</Badge>
                    )}
                    {!agent.is_revoked && (
                      <Button
                        variant="danger"
                        onClick={() => void handleRevoke(agent.id)}
                        disabled={revokingId === agent.id}
                      >
                        <X className="h-3.5 w-3.5" />
                        {revokingId === agent.id ? 'İptal ediliyor...' : 'İptal et'}
                      </Button>
                    )}
                  </div>
                </div>
                {expandedId === agent.id && (
                  <div className="border-t border-slate-100 bg-slate-50/60 dark:border-slate-700 dark:bg-slate-900/40">
                    <p className="px-6 pt-4 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                      Connector erişimi
                    </p>
                    <AgentConnectorPanel agent={agent} />
                    <p className="border-t border-slate-100 px-6 pt-4 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:border-slate-700 dark:text-slate-500">
                      RBAC izinleri
                    </p>
                    <AgentPermissionPanel agent={agent} />
                    <p className="border-t border-slate-100 px-6 pt-4 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:border-slate-700 dark:text-slate-500">
                      Aksiyon politikaları
                    </p>
                    <AgentPolicyPanel agent={agent} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

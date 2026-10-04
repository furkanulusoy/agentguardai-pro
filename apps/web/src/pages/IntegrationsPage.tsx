import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Mail, GitBranch, MessageSquare, Plug, ShieldOff, ArrowUpRight, ShieldCheck } from 'lucide-react'
import { fetchConnectors, getGithubAuthorizationUrl, getGmailAuthorizationUrl, getSlackAuthorizationUrl, revokeConnector } from '../api/connectors'
import { executeConnectorAction } from '../api/approvals'
import { apiClient, apiErrorMessage } from '../api/client'
import { useAuth } from '../context/AuthContext'
import type { ConnectorInfo, ExecuteResult } from '../types'
import { Badge, Button, Card, EmptyState, PageHeader, Spinner } from '../components/ui'

const providers = [
  {id:'gmail', name:'Gmail', icon:Mail, description:'Posta kimliklerini listeleyin. E-posta başlıklarını kontrollü erişime açın.', authorize:getGmailAuthorizationUrl},
  {id:'github', name:'GitHub', icon:GitBranch, description:'Depoları listeleyin. Issue kapatma eylemlerini yönetin.', authorize:getGithubAuthorizationUrl},
  {id:'slack', name:'Slack', icon:MessageSquare, description:'Kanal erişimini ve mesaj eylemlerini açık kaynak kapsamlarıyla sınırlandırın.', authorize:getSlackAuthorizationUrl},
]
const actions: Record<string,{id:string;label:string;fields:{key:string;label:string;type?:string;example:string}[]}[]> = {
  gmail:[
    {id:'list_messages',label:'Mesaj kimliklerini listele',fields:[]},
    {id:'read_message',label:'E-posta başlıklarını oku',fields:[{key:'message_id',label:'Mesaj ID',example:'18abc123def'}]},
  ],
  github:[
    {id:'list_repos',label:'Depoları listele',fields:[]},
    {id:'close_issue',label:"Issue kapat",fields:[{key:'repo',label:'Depo',example:'acme/repository'},{key:'issue_number',label:'Issue numarası',type:'number',example:'1'}]},
  ],
  slack:[
    {id:'list_channels',label:'Kanalları listele',fields:[]},
    {id:'send_message',label:'Mesaj gönder',fields:[{key:'channel',label:'Kanal ID',example:'C0123456789'},{key:'text',label:'Mesaj',example:'Mesaj içeriği'}]},
    {id:'delete_message',label:'Mesaj sil',fields:[{key:'channel',label:'Kanal ID',example:'C0123456789'},{key:'ts',label:'Mesaj zaman damgası',example:'1234567890.123456'}]},
    {id:'create_channel',label:'Kanal oluştur',fields:[{key:'name',label:'Kanal adı',example:'agent-updates'}]},
    {id:'invite_user',label:'Kanala kullanıcı davet et',fields:[{key:'channel',label:'Kanal ID',example:'C0123456789'},{key:'user_id',label:'Kullanıcı ID',example:'U0123456789'}]},
  ],
}
export function IntegrationsPage(){
  const {user}=useAuth()
  const [connectors,setConnectors]=useState<ConnectorInfo[]>([])
  const [configured,setConfigured]=useState<Record<string,boolean>>({})
  const [loading,setLoading]=useState(true)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState('')
  const [selected,setSelected]=useState<ConnectorInfo|null>(null)
  const [action,setAction]=useState('')
  const [params,setParams]=useState<Record<string,string>>({})
  const [outcome,setOutcome]=useState<ExecuteResult|null>(null)
  const [uncertain,setUncertain]=useState(false)
  const [operationKey,setOperationKey]=useState('')
  const canWrite=user?.permissions?.includes('connector.write')??false
  const canExecute=user?.permissions?.includes('agent.execute')??false
  async function load(){
    setError('')
    try{
      const [rows,overview]=await Promise.all([fetchConnectors(),apiClient.get<{connectors:{type:string;configured:boolean}[]}>('/tenant/overview')])
      setConnectors(rows);setConfigured(Object.fromEntries(overview.data.connectors.map(c=>[c.type,c.configured])))
    }catch(err){setError(apiErrorMessage(err,'Entegrasyonlar yüklenemedi'))}finally{setLoading(false)}
  }
  useEffect(()=>{void load()},[])
  async function connect(provider:typeof providers[number]){
    setBusy(provider.id);setError('')
    try{window.location.assign(await provider.authorize())}catch(err){setError(apiErrorMessage(err,'OAuth bağlantısı başlatılamadı'));setBusy('')}
  }
  async function revoke(id:string){
    setBusy(id);setError('')
    try{await revokeConnector(id);await load()}catch(err){setError(apiErrorMessage(err,'Bağlantı iptal edilemedi'))}finally{setBusy('')}
  }
  function select(connector:ConnectorInfo){
    setSelected(connector);setAction(actions[connector.connector_type]?.[0]?.id??'');setParams({});setOutcome(null);setUncertain(false);setOperationKey('')
  }
  async function submit(event:React.FormEvent){
    event.preventDefault()
    if(!selected)return
    const definition=actions[selected.connector_type].find(a=>a.id===action)!
    const payload:Record<string,unknown>={}
    for(const field of definition.fields)payload[field.key]=field.type==='number'?Number(params[field.key]):params[field.key]
    const key=crypto.randomUUID();setOperationKey(key);setBusy('execute');setError('')
    try{setOutcome(await executeConnectorAction(selected.id,action,payload,key))}
    catch(err){setError(apiErrorMessage(err,'İstek sonucu doğrulanamadı'));setUncertain(true)}
    finally{setBusy('')}
  }
  const definition=selected?actions[selected.connector_type]?.find(a=>a.id===action):undefined
  return <div>
    <PageHeader title="Entegrasyonlar" description="Servisleri bağlayın. Her ajan için izinleri ve kaynak kapsamını ayrı tanımlayın."/>
    {error&&<div role="alert" className="mb-5 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}<button className="ml-3 underline" onClick={()=>void load()}>Yenile</button></div>}
    <div className="mb-6 grid gap-4 lg:grid-cols-3">{providers.map(provider=>{const Icon=provider.icon;return <Card className="flex flex-col p-6" key={provider.id}>
      <div className="mb-4 flex items-center justify-between"><Icon size={27}/><Badge tone={configured[provider.id]?'healthy':'slate'}>{configured[provider.id]?'OAuth hazır':'Kurulum gerekli'}</Badge></div>
      <h2 className="text-lg font-semibold">{provider.name}</h2><p className="mb-6 mt-2 flex-1 text-sm text-slate-500">{provider.description}</p>
      <Button variant="secondary" disabled={!canWrite||!configured[provider.id]||!!busy} onClick={()=>void connect(provider)}>{busy===provider.id?'Yönlendiriliyor…':'Hesap bağla'}<ArrowUpRight size={15}/></Button>
    </Card>})}</div>
    {Object.values(configured).some(v=>!v)&&<Card className="mb-6 p-5"><div className="flex items-start gap-3"><ShieldCheck className="shrink-0" size={21}/><div><h2 className="mb-1 font-semibold">İlk bağlantıdan önce OAuth kurulumu</h2><p className="text-sm text-slate-500">Dağıtım yöneticiniz sağlayıcı uygulamasının client ID, client secret ve callback adresini sunucuda yapılandırmalıdır. Gizli anahtarlar dashboard'a girilmez. Kurulum adımları proje içindeki docs/LOCAL_PRODUCT.md dosyasında.</p></div></div></Card>}
    <div className="mb-3 flex items-center justify-between"><h2 className="text-lg font-semibold">Bağlı hesaplar</h2><Button variant="secondary" onClick={()=>void load()}>Yenile</Button></div>
    <Card>{loading?<div className="p-10"><Spinner/></div>:connectors.length===0?<EmptyState icon={<Plug size={22}/>} title="Henüz bağlı bir hesap yok" description="OAuth kurulumu tamamlandığında yukarıdan ilk servisinizi bağlayın."/>:<div className="divide-y divide-slate-200 dark:divide-slate-700">{connectors.map(c=><div key={c.id} className="flex flex-wrap items-center justify-between gap-4 p-5"><div><p className="font-semibold">{c.label}</p><p className="mt-1 text-xs text-slate-500">{c.connector_type} · {new Date(c.connected_at).toLocaleDateString('tr-TR')}</p><code className="text-xs text-slate-500">{c.id}</code></div><div className="flex gap-2"><Badge tone={c.is_revoked?'slate':'healthy'}>{c.is_revoked?'İptal edildi':'Bağlı'}</Badge>{!c.is_revoked&&canExecute&&<Button variant="secondary" onClick={()=>select(c)}>Eylem çalıştır</Button>}{!c.is_revoked&&canWrite&&<Button variant="danger" disabled={!!busy} onClick={()=>void revoke(c.id)}><ShieldOff size={14}/>İptal et</Button>}</div></div>)}</div>}</Card>
    {selected&&<Card className="mt-6 p-6"><h2 className="text-lg font-semibold">{selected.label} · Kontrollü eylem</h2><p className="mb-5 mt-2 text-sm text-slate-500">Bu istek sizin kullanıcı kimliğinizle değerlendirilir. Agent SDK istekleri ayrıca ajan izinleri ve kaynak kapsamıyla sınırlandırılır.</p>
      <form onSubmit={e=>void submit(e)} className="grid gap-4"><label className="text-sm">Eylem<select aria-label="Eylem" className="mt-2 block w-full rounded-lg border border-slate-300 bg-transparent p-3" value={action} disabled={!!outcome||uncertain||!!busy} onChange={e=>{setAction(e.target.value);setParams({})}}>{actions[selected.connector_type].map(a=><option key={a.id} value={a.id}>{a.label}</option>)}</select></label>
      {definition?.fields.map(field=><label key={field.key} className="text-sm">{field.label}<input className="mt-2 block w-full rounded-lg border border-slate-300 bg-transparent p-3" type={field.type??'text'} min={field.type==='number'?1:undefined} required maxLength={field.key==='text'?4000:200} placeholder={field.example} disabled={!!outcome||uncertain||!!busy} value={params[field.key]??''} onChange={e=>setParams({...params,[field.key]:e.target.value})}/></label>)}
      {!outcome&&!uncertain&&<Button disabled={!!busy} type="submit">{busy==='execute'?'Değerlendiriliyor…':'Politikaya gönder'}</Button>}</form>
      {outcome&&<div role="status" className="mt-5 rounded-lg border border-slate-200 p-4"><strong>{outcome.status==='completed'?'Eylem tamamlandı':outcome.status==='pending_approval'?'İnsan onayı bekliyor':"Yürütme durumu: "+outcome.status}</strong><p className="mt-2 break-all text-xs">Operasyon: {outcome.operation_id}</p>{outcome.error&&<p className="mt-2 text-sm text-amber-700">{outcome.error}</p>}<Link className="mt-3 inline-block text-sm underline" to={outcome.approval_id?'/approvals':'/executions'}>{outcome.approval_id?'Onay kuyruğuna git':'İşlem geçmişini aç'}</Link>{outcome.result!=null&&<pre className="mt-4 max-h-64 overflow-auto text-xs">{JSON.stringify(outcome.result,null,2)}</pre>}</div>}
      {uncertain&&<p role="alert" className="mt-5 break-all text-sm text-amber-700">İsteği yeni anahtarla tekrarlamayın. İşlem geçmişini kontrol edin. Kurtarma anahtarı: <code>{operationKey}</code></p>}
    </Card>}
    <p className="mt-6 text-sm text-slate-500">Bağlantı kurmak ajanlara otomatik erişim vermez. <Link className="underline" to="/agents">Ajanın izinlerini ve kaynak kapsamını tanımlayın.</Link> Onaylar dashboard içinden takip edilir.</p>
  </div>
}

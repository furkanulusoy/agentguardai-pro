import { useAuth } from '../context/AuthContext'
import { useState } from 'react'
import { CheckCircle2, XCircle, Mail, GitBranch, MessageSquare, Plug, Bot } from 'lucide-react'
import { resolveApproval, reviewApproval } from '../api/approvals'
import { apiErrorMessage } from '../api/client'
import { useToast } from '../context/ToastContext'
import type { ApprovalRequest } from '../types'
import { ActionFlow } from './ActionFlow'
import { Badge, Button, riskTone, statusTone } from './ui'

const CONNECTOR_ICON: Record<string, typeof Mail> = {
  gmail: Mail,
  github: GitBranch,
  slack: MessageSquare,
}

const STATUS_LABEL: Record<string, string> = {
  PENDING: 'Onay bekliyor',
  APPROVED: 'Onaylandı',
  DENIED: 'Reddedildi',
  EXPIRED: 'Süresi doldu',
}

export function ApprovalDetailPanel({
  approval,
  onResolved,
}: {
  approval: ApprovalRequest
  onResolved: (updated: ApprovalRequest) => void
}) {
  const { showToast } = useToast()
  const { user } = useAuth()
  const canApprove = user?.permissions?.includes('approval.approve') ?? false
  const [review,setReview] = useState<{params:Record<string,unknown>;payload_hash:string;decision_source:string}|null>(null)
  const [reviewed,setReviewed] = useState(false)
  const [reviewError,setReviewError] = useState('')
  async function loadReview(){try{setReview(await reviewApproval(approval.id))}catch(e){setReviewError(apiErrorMessage(e))}}

  const [resolving, setResolving] = useState<'approve' | 'deny' | null>(null)

  const Icon = CONNECTOR_ICON[approval.connector_type] ?? Plug
  const isPending = approval.status === 'PENDING'

  async function handleResolve(approved: boolean) {
    setResolving(approved ? 'approve' : 'deny')
    try {
      const updated = await resolveApproval(approval.id, approved, review?.payload_hash)
      showToast(approved ? 'Aksiyon onaylandı' : 'Aksiyon reddedildi')
      onResolved(updated)
    } catch (err) {
      showToast(apiErrorMessage(err, 'İşlem başarısız'), 'error')
    } finally {
      setResolving(null)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-slate-200 p-5 dark:border-slate-700">
        <div className="mb-3 flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
            <Icon className="h-4.5 w-4.5" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-slate-900 dark:text-slate-50">
              {approval.connector_type}.{approval.action}
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">{approval.requested_by_email}</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge tone={riskTone(approval.risk_level)}>{approval.risk_level} risk</Badge>
          <Badge tone={statusTone(approval.status)} dot pulse={isPending}>
            {STATUS_LABEL[approval.status] ?? approval.status}
          </Badge>
          {approval.agent_name && (
            <Badge tone="info">
              <Bot className="h-3 w-3" />
              {approval.agent_name}
            </Badge>
          )}
        </div>
      </div>

      <div className="border-b border-slate-200 p-5 dark:border-slate-700">
        <p className="mb-4 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Karar yolculuğu</p>
        <ActionFlow approval={approval} />
      </div>

      {Object.keys(approval.call_context.kwargs ?? {}).length > 0 && (
        <div className="border-b border-slate-200 p-5 dark:border-slate-700">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Parametreler</p>
          <pre className="overflow-x-auto rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-700 dark:bg-slate-900 dark:text-slate-300">
            {JSON.stringify(approval.call_context.kwargs, null, 2)}
          </pre>
        </div>
      )}

      {approval.status === 'APPROVED' && !approval.error && approval.result != null && (
        <div className="border-b border-slate-200 p-5 dark:border-slate-700">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Sonuç</p>
          <pre className="max-h-40 overflow-auto rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-700 dark:bg-slate-900 dark:text-slate-300">
            {JSON.stringify(approval.result, null, 2)}
          </pre>
        </div>
      )}

      {approval.error && (
        <div className="border-b border-slate-200 p-5 dark:border-slate-700">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Hata</p>
          <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-500/10 dark:text-red-400">{approval.error}</p>
        </div>
      )}

      {isPending && canApprove && <div className="border-b border-slate-200 p-5 dark:border-slate-700">
        <p className="mb-3 text-xs text-slate-500">Audit değerleri maskelidir. Onaylamadan önce gerçek parametreleri inceleyin.</p>
        {!review ? <Button variant="secondary" onClick={()=>void loadReview()}>Parametreleri güvenli görüntüle</Button> : <><pre className="mb-3 max-h-64 overflow-auto rounded bg-slate-100 p-3 text-xs dark:bg-slate-900">{JSON.stringify(review.params,null,2)}</pre><label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/> Hedef kaynağı ve parametreleri inceledim.</label></>}
        {reviewError && <p role="alert" className="mt-2 text-xs text-red-600">{reviewError}</p>}
      </div>}
      {approval.execution_status && <div className="p-5 text-xs"><strong>Yürütme durumu: {approval.execution_status}</strong>{approval.execution_status==='UNKNOWN'&&<p className="mt-2 text-amber-700">Sağlayıcı sonucu doğrulanamadı. Eylemi tekrar göndermeyin; işlem geçmişinden takip edin.</p>}</div>}
      <div className="mt-auto p-5">
        {isPending && canApprove ? (
          <div className="flex gap-2">
            <Button
              className="flex-1 !bg-emerald-600 hover:!bg-emerald-700"
              onClick={() => void handleResolve(true)}
              disabled={resolving !== null || !reviewed}
            >
              <CheckCircle2 className="h-4 w-4" />
              {resolving === 'approve' ? 'Onaylanıyor...' : 'Onayla'}
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              onClick={() => void handleResolve(false)}
              disabled={resolving !== null}
            >
              <XCircle className="h-4 w-4" />
              {resolving === 'deny' ? 'Reddediliyor...' : 'Reddet'}
            </Button>
          </div>
        ) : (
          <p className="text-center text-xs text-slate-400 dark:text-slate-500">
            {approval.resolved_by_email
              ? `${approval.resolved_by_email} tarafından karara bağlandı`
              : 'Karar zaman aşımına uğradı'}
          </p>
        )}
      </div>
    </div>
  )
}

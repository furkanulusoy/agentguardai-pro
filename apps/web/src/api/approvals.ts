import { apiClient } from './client'
import type { ApprovalRequest, ExecuteResult } from '../types'

export async function fetchApprovals(): Promise<ApprovalRequest[]> {
  const { data } = await apiClient.get<ApprovalRequest[]>('/approvals')
  return data
}

export async function fetchApproval(id: string): Promise<ApprovalRequest> {
  const { data } = await apiClient.get<ApprovalRequest>(`/approvals/${id}`)
  return data
}

export async function resolveApproval(id: string, approved: boolean, payloadHash?: string): Promise<ApprovalRequest> {
  const { data } = await apiClient.post<ApprovalRequest>(`/approvals/${id}/resolve`, { approved, payload_hash: payloadHash })
  return data
}

export async function executeConnectorAction(
  connectorId: string,
  action: string,
  params: Record<string, unknown> = {},
  idempotencyKey?: string,
): Promise<ExecuteResult> {
  const { data } = await apiClient.post<ExecuteResult>(`/connectors/${connectorId}/execute`, {
    action,
    params,
  }, { headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined })
  return data
}

const POLL_INTERVAL_MS = 2000

// Nothing on the backend blocks anymore (see CHANGELOG.md's Phase 7) --
// a sensitive action comes back as "pending_approval" immediately, and
// the actual result only exists once a human resolves it. This polls
// GET /approvals/{id} client-side to give callers the same "await and
// get the final answer" experience as before, without tying up a
// server-side connection while nobody has decided yet.
export async function waitForApprovalOutcome(
  approvalId: string,
  { signal }: { signal?: AbortSignal } = {},
): Promise<ApprovalRequest> {
  for (;;) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    const approval = await fetchApproval(approvalId)
    if (approval.status !== 'PENDING' && !['EXECUTING','READY','WAITING'].includes(approval.execution_status ?? '')) return approval
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
  }
}

export async function reviewApproval(id: string): Promise<{params: Record<string,unknown>;payload_hash:string;decision_source:string}> {
 const {data}=await apiClient.get<{params: Record<string,unknown>;payload_hash:string;decision_source:string}>(`/approvals/${id}/review`);return data
}

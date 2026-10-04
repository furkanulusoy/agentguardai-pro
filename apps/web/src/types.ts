// Mirrors the Pydantic response models in apps/api/routers/*.py exactly --
// keep these two in sync by hand for now (no codegen yet).

export interface TokenPair {
  access_token: string
  token_type: string
}

export interface CurrentUser {
  id: string
  email: string
  tenant_id: string
  roles: string[]
  permissions?: string[]
}

export interface Member {
  id: string
  email: string
  roles: string[]
  permissions?: string[]
}

export interface Invitation {
  id: string
  email: string
  role: string
  expires_at: string
  accepted_at: string | null
  // Only ever set on the response to creating an invite -- never when listing.
  invite_token: string | null
}

export interface InvitePreview {
  email: string
  role: string
  tenant_name: string
  expires_at: string
}

export interface TenantSettings {
  notification_repo: string | null
}

export interface PasswordResetLink {
  reset_token: string
  expires_at: string
}

export interface PasswordResetPreview {
  email: string
  expires_at: string
}

export type PolicyDecision = 'ALLOW' | 'DENY' | 'REQUIRE_APPROVAL'

export interface EffectivePolicy {
  connector_type: string
  action: string
  risk_level: string
  system_default: PolicyDecision
  override: PolicyDecision | null
  effective: PolicyDecision
}

export interface PolicySimulation {
  connector_type: string
  action: string
  window_days: number
  total_events: number
  historical_breakdown: Record<PolicyDecision, number>
  would_change_if: Record<PolicyDecision, number>
}

export interface Agent {
  id: string
  name: string
  owner_email: string | null
  created_by_email: string | null
  created_at: string
  is_revoked: boolean
  // Only ever set on the response to creating an agent -- never when listing.
  api_key: string | null
}

export interface AgentConnectorGrant {
  credential_id: string
  connector_type: string
  label: string
  resources?: string[]
  is_granted: boolean
}

export interface AgentPermissionGrant {
  code: string
  description: string
  resources?: string[]
  is_granted: boolean
}

export interface AgentEffectivePolicy {
  connector_type: string
  action: string
  risk_level: string
  system_default: PolicyDecision
  tenant_override: PolicyDecision | null
  agent_override: PolicyDecision | null
  effective: PolicyDecision
}

export interface ConnectorInfo {
  id: string
  connector_type: string
  label: string
  connected_at: string
  is_revoked: boolean
}

export interface ApiError {
  detail: string
}

export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'DENIED' | 'EXPIRED'

export interface ApprovalRequest {
  id: string
  credential_id: string
  connector_type: string
  action: string
  call_context: { args: unknown[]; kwargs: Record<string, unknown> }
  risk_level: string
  status: ApprovalStatus
  requested_by_email: string
  agent_name: string | null
  resolved_by_email: string | null
  created_at: string
  resolved_at: string | null
  result: unknown
  execution_status?: string | null
  operation_id?: string | null
  error: string | null
}

export type DecisionSource =
  | 'AGENT_NOT_GRANTED'
  | 'TASK_SCOPE'
  | 'AGENT_POLICY'
  | 'TENANT_POLICY'
  | 'SYSTEM_DEFAULT'

export interface AuditEvent {
  id: string
  created_at: string
  connector_type: string
  action: string
  call_context: { args: unknown[]; kwargs: Record<string, unknown> }
  user_email: string
  agent_name: string | null
  decision: PolicyDecision
  decision_source: DecisionSource
  risk_level: string
  result: unknown
  execution_status?: string | null
  operation_id?: string | null
  error: string | null
  approval_id: string | null
  approval_status: ApprovalStatus | null
  approval_resolved_by_email: string | null
}

// "completed" -- ran immediately, no approval needed.
// "pending_approval" -- recorded as an ApprovalRequest; the caller
// polls GET /approvals/{approval_id} for the eventual result/error.
export interface ExecuteResult {
  status: 'completed' | 'pending_approval' | 'ready' | 'executing' | 'unknown' | 'failed' | 'denied' | 'expired'
  operation_id: string
  error: string | null
  result: unknown
  approval_id: string | null
}

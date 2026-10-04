import { apiClient } from './client'
import type {
  Agent,
  AgentConnectorGrant,
  AgentEffectivePolicy,
  AgentPermissionGrant,
  PolicyDecision,
  PolicySimulation,
} from '../types'

export async function fetchAgents(): Promise<Agent[]> {
  const { data } = await apiClient.get<Agent[]>('/tenant/agents')
  return data
}

export async function createAgent(name: string, ownerUserId?: string): Promise<Agent> {
  const { data } = await apiClient.post<Agent>('/tenant/agents', {
    name,
    owner_user_id: ownerUserId ?? null,
  })
  return data
}

export async function revokeAgent(id: string): Promise<void> {
  await apiClient.post(`/tenant/agents/${id}/revoke`)
}

export async function fetchAgentPolicies(agentId: string): Promise<AgentEffectivePolicy[]> {
  const { data } = await apiClient.get<AgentEffectivePolicy[]>(`/tenant/agents/${agentId}/policies`)
  return data
}

export async function setAgentPolicy(
  agentId: string,
  connectorType: string,
  action: string,
  decision: PolicyDecision,
): Promise<AgentEffectivePolicy> {
  const { data } = await apiClient.put<AgentEffectivePolicy>(
    `/tenant/agents/${agentId}/policies/${connectorType}/${action}`,
    { decision },
  )
  return data
}

export async function clearAgentPolicy(
  agentId: string,
  connectorType: string,
  action: string,
): Promise<void> {
  await apiClient.delete(`/tenant/agents/${agentId}/policies/${connectorType}/${action}`)
}

export async function fetchAgentConnectors(agentId: string): Promise<AgentConnectorGrant[]> {
  const { data } = await apiClient.get<AgentConnectorGrant[]>(`/tenant/agents/${agentId}/connectors`)
  return data
}

export async function grantAgentConnector(
  agentId: string,
  credentialId: string,
  resources: string[] = [],
): Promise<AgentConnectorGrant> {
  const { data } = await apiClient.put<AgentConnectorGrant>(
    `/tenant/agents/${agentId}/connectors/${credentialId}`,
    { resources },
  )
  return data
}

export async function revokeAgentConnector(agentId: string, credentialId: string): Promise<void> {
  await apiClient.delete(`/tenant/agents/${agentId}/connectors/${credentialId}`)
}

export async function fetchAgentPermissions(agentId: string): Promise<AgentPermissionGrant[]> {
  const { data } = await apiClient.get<AgentPermissionGrant[]>(
    `/tenant/agents/${agentId}/permissions`,
  )
  return data
}

export async function grantAgentPermission(
  agentId: string,
  code: string,
): Promise<AgentPermissionGrant> {
  const { data } = await apiClient.put<AgentPermissionGrant>(
    `/tenant/agents/${agentId}/permissions/${code}`,
  )
  return data
}

export async function revokeAgentPermission(agentId: string, code: string): Promise<void> {
  await apiClient.delete(`/tenant/agents/${agentId}/permissions/${code}`)
}

export async function simulateAgentPolicy(
  agentId: string,
  connectorType: string,
  action: string,
  days = 7,
): Promise<PolicySimulation> {
  const { data } = await apiClient.get<PolicySimulation>(
    `/tenant/agents/${agentId}/policies/${connectorType}/${action}/simulate`,
    { params: { days } },
  )
  return data
}

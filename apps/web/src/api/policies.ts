import { apiClient } from './client'
import type { EffectivePolicy, PolicyDecision, PolicySimulation } from '../types'

export async function fetchEffectivePolicies(): Promise<EffectivePolicy[]> {
  const { data } = await apiClient.get<EffectivePolicy[]>('/tenant/policies')
  return data
}

export async function setPolicy(
  connectorType: string,
  action: string,
  decision: PolicyDecision,
): Promise<EffectivePolicy> {
  const { data } = await apiClient.put<EffectivePolicy>(
    `/tenant/policies/${connectorType}/${action}`,
    { decision },
  )
  return data
}

export async function clearPolicy(connectorType: string, action: string): Promise<void> {
  await apiClient.delete(`/tenant/policies/${connectorType}/${action}`)
}

export async function simulatePolicy(
  connectorType: string,
  action: string,
  days = 7,
): Promise<PolicySimulation> {
  const { data } = await apiClient.get<PolicySimulation>(
    `/tenant/policies/${connectorType}/${action}/simulate`,
    { params: { days } },
  )
  return data
}

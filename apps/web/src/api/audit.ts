import { apiClient } from './client'
import type { AuditEvent } from '../types'

export interface AuditFilters {
  connector_type?: string
  action?: string
  decision?: string
  agent_id?: string
}

export async function fetchAuditEvents(filters: AuditFilters = {}): Promise<AuditEvent[]> {
  const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
  const { data } = await apiClient.get<AuditEvent[]>('/tenant/audit', { params })
  return data
}

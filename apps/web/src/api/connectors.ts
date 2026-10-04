import { apiClient } from './client'
import type { ConnectorInfo } from '../types'

export async function fetchConnectors(): Promise<ConnectorInfo[]> {
  const { data } = await apiClient.get<ConnectorInfo[]>('/connectors')
  return data
}

export async function revokeConnector(id: string): Promise<void> {
  await apiClient.post(`/connectors/${id}/revoke`)
}

export async function getGmailAuthorizationUrl(): Promise<string> {
  const { data } = await apiClient.get<{ authorization_url: string }>('/connectors/gmail/authorize')
  return data.authorization_url
}

export async function getGithubAuthorizationUrl(): Promise<string> {
  const { data } = await apiClient.get<{ authorization_url: string }>('/connectors/github/authorize')
  return data.authorization_url
}

export async function getSlackAuthorizationUrl(): Promise<string> {
  const { data } = await apiClient.get<{ authorization_url: string }>('/connectors/slack/authorize')
  return data.authorization_url
}

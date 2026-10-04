import { apiClient } from './client'
import type { Invitation, Member, PasswordResetLink, TenantSettings } from '../types'

export async function fetchMembers(): Promise<Member[]> {
  const { data } = await apiClient.get<Member[]>('/tenant/members')
  return data
}

export async function fetchInvites(): Promise<Invitation[]> {
  const { data } = await apiClient.get<Invitation[]>('/tenant/invites')
  return data
}

export async function createInvite(email: string, role: string): Promise<Invitation> {
  const { data } = await apiClient.post<Invitation>('/tenant/invites', { email, role })
  return data
}

export async function revokeInvite(id: string): Promise<void> {
  await apiClient.delete(`/tenant/invites/${id}`)
}

export async function fetchTenantSettings(): Promise<TenantSettings> {
  const { data } = await apiClient.get<TenantSettings>('/tenant/settings')
  return data
}

export async function updateTenantSettings(notificationRepo: string | null): Promise<TenantSettings> {
  const { data } = await apiClient.patch<TenantSettings>('/tenant/settings', {
    notification_repo: notificationRepo,
  })
  return data
}

export async function resetMemberPassword(userId: string): Promise<PasswordResetLink> {
  const { data } = await apiClient.post<PasswordResetLink>(`/tenant/members/${userId}/reset-password`)
  return data
}

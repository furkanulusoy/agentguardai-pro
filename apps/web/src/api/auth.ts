import { apiClient } from './client'
import type { CurrentUser, InvitePreview, PasswordResetPreview, TokenPair } from '../types'

export interface RegisterPayload {
  tenant_name: string
  tenant_slug: string
  email: string
  password: string
}

export interface LoginPayload {
  email: string
  password: string
}

export async function register(payload: RegisterPayload): Promise<TokenPair> {
  const { data } = await apiClient.post<TokenPair>('/auth/register', payload)
  return data
}

export async function login(payload: LoginPayload): Promise<TokenPair> {
  const { data } = await apiClient.post<TokenPair>('/auth/login', payload)
  return data
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  const { data } = await apiClient.get<CurrentUser>('/auth/me')
  return data
}

export async function logout(): Promise<void> {
  // No refresh token in the body -- the httpOnly cookie identifies it
  // (apps/api/routers/auth.py's logout reads the cookie first).
  await apiClient.post('/auth/logout', {})
}

export async function previewInvite(token: string): Promise<InvitePreview> {
  const { data } = await apiClient.get<InvitePreview>(`/auth/invites/${token}`)
  return data
}

export async function acceptInvite(inviteToken: string, password: string): Promise<TokenPair> {
  const { data } = await apiClient.post<TokenPair>('/auth/accept-invite', {
    invite_token: inviteToken,
    password,
  })
  return data
}

export async function previewPasswordReset(token: string): Promise<PasswordResetPreview> {
  const { data } = await apiClient.get<PasswordResetPreview>(`/auth/password-reset/${token}`)
  return data
}

export async function resetPassword(resetToken: string, newPassword: string): Promise<TokenPair> {
  const { data } = await apiClient.post<TokenPair>('/auth/reset-password', {
    reset_token: resetToken,
    new_password: newPassword,
  })
  return data
}

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import * as authApi from '../api/auth'
import { tokenStorage, refreshAccessToken } from '../api/client'
import type { CurrentUser } from '../types'

interface AuthContextValue {
  user: CurrentUser | null
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (payload: authApi.RegisterPayload) => Promise<void>
  acceptInvite: (inviteToken: string, password: string) => Promise<void>
  resetPassword: (resetToken: string, newPassword: string) => Promise<void>
  logout: () => Promise<void>
  refetchUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const refetchUser = useCallback(async () => {
    if (!tokenStorage.getAccessToken()) {
      const refreshed = await refreshAccessToken()
      if (!refreshed) { setUser(null); return }
    }
    try {
      const me = await authApi.fetchCurrentUser()
      setUser(me)
    } catch {
      tokenStorage.clear()
      setUser(null)
    }
  }, [])

  useEffect(() => {
    refetchUser().finally(() => setIsLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await authApi.login({ email, password })
    tokenStorage.setAccessToken(tokens.access_token)
    await refetchUser()
  }, [refetchUser])

  const register = useCallback(async (payload: authApi.RegisterPayload) => {
    const tokens = await authApi.register(payload)
    tokenStorage.setAccessToken(tokens.access_token)
    await refetchUser()
  }, [refetchUser])

  const acceptInvite = useCallback(async (inviteToken: string, password: string) => {
    const tokens = await authApi.acceptInvite(inviteToken, password)
    tokenStorage.setAccessToken(tokens.access_token)
    await refetchUser()
  }, [refetchUser])

  const resetPassword = useCallback(async (resetToken: string, newPassword: string) => {
    const tokens = await authApi.resetPassword(resetToken, newPassword)
    tokenStorage.setAccessToken(tokens.access_token)
    await refetchUser()
  }, [refetchUser])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      // Already-invalid/missing refresh cookie shouldn't block a local logout.
    }
    tokenStorage.clear()
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider
      value={{ user, isLoading, login, register, acceptInvite, resetPassword, logout, refetchUser }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

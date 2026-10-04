import type { TokenPair } from '../types'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string) || '/api'

let accessTokenValue: string | null = null
let sessionEpoch = 0
localStorage.removeItem('agentguard_access_token')
localStorage.removeItem('agentguard_refresh_token')

export const tokenStorage = {
  getAccessToken: () => accessTokenValue,
  setAccessToken: (accessToken: string) => {
    sessionEpoch++
    accessTokenValue = accessToken
  },
  clear: () => {
    sessionEpoch++
    accessTokenValue = null
  },
}

export class ApiError extends Error {
  readonly status: number
  readonly data: unknown

  constructor(
    message: string,
    status: number,
    data: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

interface RequestConfig {
  headers?: HeadersInit
  params?: Record<string, string | number | boolean | null | undefined>
}

interface ApiResponse<T> {
  data: T
  status: number
}

function requestUrl(path: string, params?: RequestConfig['params']): string {
  const url = `${BASE_URL}${path}`
  if (!params) return url
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined) query.set(key, String(value))
  })
  const rendered = query.toString()
  return rendered ? `${url}?${rendered}` : url
}

async function parseBody(response: Response): Promise<unknown> {
  const body = await response.text()
  if (!body) return null
  try {
    return JSON.parse(body)
  } catch {
    return body
  }
}

async function rawRequest<T>(
  method: string,
  path: string,
  body?: unknown,
  config: RequestConfig = {},
): Promise<ApiResponse<T>> {
  const headers = new Headers(config.headers)
  headers.set('Accept', 'application/json')
  if (body !== undefined) headers.set('Content-Type', 'application/json')
  if (method === 'POST' && path.endsWith('/execute') && !headers.has('Idempotency-Key')) {
    headers.set('Idempotency-Key', crypto.randomUUID())
  }
  const token = tokenStorage.getAccessToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(requestUrl(path, config.params), {
    method,
    headers,
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await parseBody(response)
  if (!response.ok) throw new ApiError(`HTTP ${response.status}`, response.status, data)
  return { data: data as T, status: response.status }
}

let refreshPromise: Promise<string | null> | null = null

export function refreshAccessToken(): Promise<string | null> {
  refreshPromise ??= performRefresh().finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

async function performRefresh(): Promise<string | null> {
  const epoch = sessionEpoch
  try {
    const response = await fetch(`${BASE_URL}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      credentials: 'include',
      body: '{}',
    })
    const data = (await parseBody(response)) as TokenPair
    if (!response.ok || typeof data?.access_token !== 'string') throw new Error('refresh_failed')
    if (epoch !== sessionEpoch) return null
    tokenStorage.setAccessToken(data.access_token)
    return data.access_token
  } catch {
    if (epoch !== sessionEpoch) return null
    tokenStorage.clear()
    return null
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  config: RequestConfig = {},
): Promise<ApiResponse<T>> {
  try {
    return await rawRequest<T>(method, path, body, config)
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401 || path.startsWith('/auth/')) {
      throw error
    }
    const newAccessToken = await refreshAccessToken()
    if (!newAccessToken) {
      window.location.href = '/login'
      throw error
    }
    return rawRequest<T>(method, path, body, config)
  }
}

export const apiClient = {
  get: <T>(path: string, config?: RequestConfig) => request<T>('GET', path, undefined, config),
  post: <T>(path: string, body?: unknown, config?: RequestConfig) =>
    request<T>('POST', path, body, config),
  put: <T>(path: string, body?: unknown, config?: RequestConfig) =>
    request<T>('PUT', path, body, config),
  patch: <T>(path: string, body?: unknown, config?: RequestConfig) =>
    request<T>('PATCH', path, body, config),
  delete: <T>(path: string, config?: RequestConfig) =>
    request<T>('DELETE', path, undefined, config),
}

export function apiErrorMessage(error: unknown, fallback = 'Something went wrong'): string {
  if (error instanceof ApiError) {
    const detail = (error.data as { detail?: unknown } | null)?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string } | undefined
      if (typeof first?.msg === 'string') return first.msg
    }
  }
  return fallback
}

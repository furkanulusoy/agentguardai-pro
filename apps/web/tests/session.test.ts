import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let client: typeof import('../src/api/client')
let storage: { removeItem: ReturnType<typeof vi.fn>; setItem: ReturnType<typeof vi.fn> }

function response(status: number, data: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(data),
  } as Response
}

beforeEach(async () => {
  vi.resetModules()
  storage = { removeItem: vi.fn(), setItem: vi.fn() }
  vi.stubGlobal('localStorage', storage)
  vi.stubGlobal('window', { location: { href: '' } })
  vi.stubGlobal('fetch', vi.fn())
  client = await import('../src/api/client')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function pendingRefresh() {
  let finish!: (value: Response) => void
  const pending = new Promise<Response>((resolve) => {
    finish = resolve
  })
  vi.mocked(fetch).mockReturnValue(pending)
  return { finish }
}

describe('browser session boundary', () => {
  it('clears legacy tokens and never persists bearer credentials', () => {
    client.tokenStorage.setAccessToken('synthetic-access')
    expect(client.tokenStorage.getAccessToken()).toBe('synthetic-access')
    expect(storage.removeItem.mock.calls).toEqual([
      ['agentguard_access_token'],
      ['agentguard_refresh_token'],
    ])
    expect(storage.setItem).not.toHaveBeenCalled()
  })

  it('shares one cookie-only refresh between concurrent callers', async () => {
    const { finish } = pendingRefresh()
    const a = client.refreshAccessToken()
    const b = client.refreshAccessToken()
    expect(a).toBe(b)
    expect(fetch).toHaveBeenCalledOnce()
    finish(response(200, { access_token: 'synthetic-refreshed' }))
    expect(await a).toBe('synthetic-refreshed')
  })

  it('does not restore a session cleared while refresh was in flight', async () => {
    const { finish } = pendingRefresh()
    const pending = client.refreshAccessToken()
    client.tokenStorage.clear()
    finish(response(200, { access_token: 'synthetic-stale' }))
    expect(await pending).toBeNull()
    expect(client.tokenStorage.getAccessToken()).toBeNull()
  })

  it('does not overwrite a newer login with an older refresh', async () => {
    const { finish } = pendingRefresh()
    const pending = client.refreshAccessToken()
    client.tokenStorage.setAccessToken('synthetic-new-login')
    finish(response(200, { access_token: 'synthetic-stale' }))
    expect(await pending).toBeNull()
    expect(client.tokenStorage.getAccessToken()).toBe('synthetic-new-login')
  })

  it('clears the session on rejected refresh without leaking its error', async () => {
    client.tokenStorage.setAccessToken('synthetic-old')
    vi.mocked(fetch).mockRejectedValue(new Error('synthetic-provider-private'))
    expect(await client.refreshAccessToken()).toBeNull()
    expect(client.tokenStorage.getAccessToken()).toBeNull()
  })

  it('does not refresh rejected login attempts', async () => {
    vi.mocked(fetch).mockResolvedValue(response(401, {}))
    await expect(client.apiClient.post('/auth/login', {})).rejects.toThrow('HTTP 401')
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('preserves the caller idempotency key across an authorized 401 retry', async () => {
    const keys: Array<string | null> = []
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.endsWith('/auth/refresh')) {
        return response(200, { access_token: 'synthetic-new' })
      }
      keys.push(new Headers(init?.headers).get('Idempotency-Key'))
      return keys.length === 1 ? response(401, {}) : response(200, { status: 'completed' })
    })

    await client.apiClient.post('/connectors/synthetic/execute', {}, {
      headers: { 'Idempotency-Key': 'synthetic-stable-operation' },
    })
    expect(keys).toEqual(['synthetic-stable-operation', 'synthetic-stable-operation'])
  })
})

/**
 * What the app does when the server answers 401.
 *
 * WHY THIS MATTERS
 *   The refresh is itself a request through the same client. With a refresh
 *   token that had expired, its 401 was taken as a reason to refresh again,
 *   and the app posted to /auth/refresh until the server's rate limit stopped
 *   it — five 401s and a 429 from one page load, seen in the desktop app with
 *   a stale token in localStorage. The 429 can then refuse the sign-in the
 *   person was about to make.
 *
 *   These run the real store and the real interceptors against a stand-in for
 *   the network.
 */
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { apiClient } from '@/api/client'
import { initAxiosInterceptors, restoreSession, useAuthStore } from './auth'

const REFRESH_KEY = 'seventh_ai_refresh_token'

type Answer = { status: number; data?: unknown }
let seen: { method: string; url: string; authorization: string | undefined }[]

/** Answer every request from `answer`, as the network would. */
function network(answer: (config: InternalAxiosRequestConfig) => Answer | Promise<Answer>) {
  apiClient.defaults.adapter = async (config) => {
    seen.push({ method: (config.method ?? 'get').toUpperCase(), url: config.url ?? '',
                authorization: config.headers?.Authorization as string | undefined })
    const { status, data } = await answer(config)
    const response = { status, data, statusText: '', headers: {}, config }
    if (status >= 400) {
      throw new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_RESPONSE', config, null, response)
    }
    return response
  }
}

/** A token the store can read its claims from. Not signed: nothing here checks it. */
function token(name: string): string {
  const part = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_')
  return `${part({ alg: 'none' })}.${part({ sub: 'u1', tenant_id: 't1', role_id: 4, exp: 4102444800, n: name })}.x`
}
const EXPIRED = token('expired')
const FRESH = token('fresh')

const refreshes = () => seen.filter((r) => r.url === '/api/v1/auth/refresh')
const signedIn = (stored: string | null) => {
  if (stored) localStorage.setItem(REFRESH_KEY, stored)
  else localStorage.removeItem(REFRESH_KEY)
  useAuthStore.setState({ accessToken: EXPIRED, user: { id: 'u1', tenantId: 't1', roleId: 4 }, permissions: [],
                          supportSession: null, platformToken: null })
}

beforeAll(() => initAxiosInterceptors())
beforeEach(() => { seen = [] })

describe('a refresh token that is no longer good', () => {
  it('is tried once, however many requests were refused, and then the session is over', async () => {
    signedIn('stale')
    network(() => ({ status: 401 }))
    const results = await Promise.allSettled([
      apiClient.get('/api/v1/alerts'), apiClient.get('/api/v1/incidents'), apiClient.get('/api/v1/cameras'),
      apiClient.get('/api/v1/sites'), apiClient.get('/api/v1/dashboard/summary'),
    ])
    expect(results.every((r) => r.status === 'rejected')).toBe(true)
    expect(refreshes()).toHaveLength(1)
    expect(seen).toHaveLength(6)                                   // the five, and the one refresh
    expect(localStorage.getItem(REFRESH_KEY)).toBeNull()
    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('is tried once when the page loads, and forgotten', async () => {
    signedIn('stale')
    network(() => ({ status: 401 }))
    await restoreSession()
    expect(refreshes()).toHaveLength(1)
    expect(seen).toHaveLength(1)
    expect(localStorage.getItem(REFRESH_KEY)).toBeNull()
  })
})

describe('an access token that has expired', () => {
  it('is refreshed once for several requests refused together, and each is sent again signed anew', async () => {
    signedIn('good')
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => { release = resolve })
    network(async (config) => {
      if (config.url === '/api/v1/auth/refresh') {
        await held                                  // still in the air while the others are refused
        return { status: 200, data: { access_token: FRESH, refresh_token: 'next' } }
      }
      if (config.url === '/api/v1/auth/me/permissions') return { status: 200, data: { permissions: ['alert:read'] } }
      return config.headers?.Authorization === `Bearer ${FRESH}` ? { status: 200, data: config.url } : { status: 401 }
    })
    const all = Promise.all([apiClient.get('/api/v1/alerts'), apiClient.get('/api/v1/incidents'),
                             apiClient.get('/api/v1/cameras')])
    await new Promise((resolve) => setTimeout(resolve, 20))
    release()
    expect((await all).map((r) => r.data)).toEqual(['/api/v1/alerts', '/api/v1/incidents', '/api/v1/cameras'])
    expect(refreshes()).toHaveLength(1)
    expect(localStorage.getItem(REFRESH_KEY)).toBe('next')
    expect(useAuthStore.getState().accessToken).toBe(FRESH)
  })

  it('with nothing to refresh with ends the session and does not send the request again unsigned', async () => {
    signedIn(null)
    network(() => ({ status: 401 }))
    await expect(apiClient.get('/api/v1/alerts')).rejects.toMatchObject({ response: { status: 401 } })
    expect(seen.map((r) => r.url)).toEqual(['/api/v1/alerts'])
    expect(useAuthStore.getState().user).toBeNull()
  })
})

describe('a wrong password', () => {
  it('is told to the person signing in once, and nothing is refreshed', async () => {
    localStorage.removeItem(REFRESH_KEY)
    useAuthStore.setState({ accessToken: null, user: null, permissions: null, supportSession: null, platformToken: null })
    network(() => ({ status: 401, data: { detail: 'Invalid credentials' } }))
    await expect(useAuthStore.getState().login('demo', 'a@b.c', 'wrong')).rejects.toMatchObject({
      response: { status: 401, data: { detail: 'Invalid credentials' } } })
    expect(seen.map((r) => `${r.method} ${r.url}`)).toEqual(['POST /api/v1/auth/login'])
  })
})

describe('an account with two-factor on', () => {
  const nobody = () => {
    localStorage.removeItem(REFRESH_KEY)
    useAuthStore.setState({ accessToken: null, user: null, permissions: null, supportSession: null, platformToken: null })
  }

  it('is not signed in by its password alone: the store hands back the challenge', async () => {
    nobody()
    network(() => ({ status: 200, data: { requires_2fa: true, challenge_token: 'challenge-1' } }))
    await expect(useAuthStore.getState().login('seventhaivision', 'owner@example.com', 'right')).resolves.toBe('challenge-1')
    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
    expect(localStorage.getItem(REFRESH_KEY)).toBeNull()
    // Nothing is asked of the server as that person until the code is given.
    expect(seen.map((r) => `${r.method} ${r.url}`)).toEqual(['POST /api/v1/auth/login'])
  })

  it('is signed in by the code, sent with the challenge under the names the server reads', async () => {
    nobody()
    let sent: unknown = null
    network((config) => {
      if (config.url === '/api/v1/auth/2fa-verify') {
        sent = JSON.parse(config.data as string)
        return { status: 200, data: { access_token: FRESH, refresh_token: 'r1' } }
      }
      return { status: 200, data: { permissions: ['tenant:manage'] } }
    })
    await useAuthStore.getState().completeTwoFactor('challenge-1', '123456')
    expect(sent).toEqual({ challenge_token: 'challenge-1', totp_code: '123456' })
    expect(useAuthStore.getState().accessToken).toBe(FRESH)
    expect(useAuthStore.getState().permissions).toEqual(['tenant:manage'])
    expect(localStorage.getItem(REFRESH_KEY)).toBe('r1')
  })

  it('signs in at once when the account has no two-factor, as before', async () => {
    nobody()
    network((config) => config.url === '/api/v1/auth/login'
      ? { status: 200, data: { access_token: FRESH, refresh_token: 'r2' } }
      : { status: 200, data: { permissions: [] } })
    await expect(useAuthStore.getState().login('demo', 'a@b.c', 'right')).resolves.toBeNull()
    expect(useAuthStore.getState().accessToken).toBe(FRESH)
  })

  it('does not take a refused code as a reason to refresh', async () => {
    nobody()
    network(() => ({ status: 401, data: { detail: 'Invalid or expired challenge token' } }))
    await expect(useAuthStore.getState().completeTwoFactor('old', '123456')).rejects.toMatchObject({
      response: { status: 401 } })
    expect(seen.map((r) => `${r.method} ${r.url}`)).toEqual(['POST /api/v1/auth/2fa-verify'])
    expect(useAuthStore.getState().accessToken).toBeNull()
  })
})

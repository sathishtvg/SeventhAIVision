/**
 * What the client does when the server answers 401.
 *
 * WHY THIS MATTERS
 *   The refresh is itself a request through this client. With a refresh token
 *   that had expired, its 401 was taken as a reason to refresh again, and the
 *   app posted to /auth/refresh until the server's rate limit stopped it: five
 *   401s and a 429 from one app start, seen in the API log on 2026-10-05. The
 *   429 can then refuse the sign-in the user was about to make.
 *
 *   These run the real interceptor against a stand-in for the network, and the
 *   refresh function is shaped as the store's is: a POST through the same
 *   client that answers true or false and never throws.
 */
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { apiClient, registerRefreshFn } from './client'

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

/** The store's refresh, in shape: post the stored token through the same client. */
function refreshWith(token: string) {
  registerRefreshFn(async () => {
    try {
      const res = await apiClient.post('/api/v1/auth/refresh', { refresh_token: token })
      apiClient.defaults.headers.common['Authorization'] = `Bearer ${(res.data as { access_token: string }).access_token}`
      return true
    } catch {
      return false
    }
  })
}

const refreshes = () => seen.filter((r) => r.url === '/api/v1/auth/refresh')

beforeEach(() => {
  seen = []
  apiClient.defaults.headers.common['Authorization'] = 'Bearer expired'
})

test('a refused refresh is final: one request to refresh, and the caller is told 401', async () => {
  network(() => ({ status: 401 }))          // the access token and the refresh token are both no good
  refreshWith('stale')
  await expect(apiClient.get('/api/v1/alerts')).rejects.toMatchObject({ response: { status: 401 } })
  expect(refreshes()).toHaveLength(1)
  expect(seen.map((r) => r.url)).toEqual(['/api/v1/alerts', '/api/v1/auth/refresh'])
})

test('a wrong password is told to the person signing in, and nothing is refreshed', async () => {
  network(() => ({ status: 401, data: { detail: 'Invalid credentials' } }))
  refreshWith('stale')
  await expect(apiClient.post('/api/v1/auth/login', { email: 'a@b.c' })).rejects.toMatchObject({
    response: { status: 401, data: { detail: 'Invalid credentials' } } })
  expect(refreshes()).toHaveLength(0)
})

test('an expired access token is refreshed once and the request is sent again with the new one', async () => {
  network((config) => {
    if (config.url === '/api/v1/auth/refresh') return { status: 200, data: { access_token: 'fresh' } }
    return config.headers?.Authorization === 'Bearer fresh' ? { status: 200, data: { items: [1] } } : { status: 401 }
  })
  refreshWith('good')
  const res = await apiClient.get('/api/v1/alerts')
  expect(res.data).toEqual({ items: [1] })
  expect(seen.map((r) => `${r.method} ${r.url} ${r.authorization}`)).toEqual([
    'GET /api/v1/alerts Bearer expired', 'POST /api/v1/auth/refresh Bearer expired', 'GET /api/v1/alerts Bearer fresh'])
})

test('several requests refused at once share one refresh', async () => {
  let release: () => void = () => {}
  const held = new Promise<void>((resolve) => { release = resolve })
  network(async (config) => {
    if (config.url === '/api/v1/auth/refresh') {
      await held                             // the refresh is still in the air while the others are refused
      return { status: 200, data: { access_token: 'fresh' } }
    }
    return config.headers?.Authorization === 'Bearer fresh' ? { status: 200, data: config.url } : { status: 401 }
  })
  refreshWith('good')
  const all = Promise.all([apiClient.get('/api/v1/alerts'), apiClient.get('/api/v1/incidents'),
                           apiClient.get('/api/v1/cameras')])
  await new Promise((resolve) => setTimeout(resolve, 20))
  release()
  expect((await all).map((r) => r.data)).toEqual(['/api/v1/alerts', '/api/v1/incidents', '/api/v1/cameras'])
  expect(refreshes()).toHaveLength(1)
})

test('a request that is refused again after a good refresh is not sent a third time', async () => {
  network((config) => (config.url === '/api/v1/auth/refresh'
    ? { status: 200, data: { access_token: 'fresh' } } : { status: 401 }))   // refused for some other reason
  refreshWith('good')
  await expect(apiClient.get('/api/v1/alerts')).rejects.toMatchObject({ response: { status: 401 } })
  expect(seen.map((r) => r.url)).toEqual(['/api/v1/alerts', '/api/v1/auth/refresh', '/api/v1/alerts'])
})

test('after a refresh has finished, the next refusal can start another', async () => {
  let token = 0
  network((config) => {
    if (config.url === '/api/v1/auth/refresh') return { status: 200, data: { access_token: `fresh-${++token}` } }
    return config.headers?.Authorization === `Bearer fresh-${token}` && token > 0 ? { status: 200 } : { status: 401 }
  })
  refreshWith('good')
  await apiClient.get('/api/v1/alerts')
  apiClient.defaults.headers.common['Authorization'] = 'Bearer expired-again'
  await apiClient.get('/api/v1/alerts')
  expect(refreshes()).toHaveLength(2)
})

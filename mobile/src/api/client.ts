import axios from 'axios'

const DEFAULT_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://10.0.2.2:8000'

export const apiClient = axios.create({ baseURL: DEFAULT_URL, timeout: 15_000 })

export function setApiBaseUrl(url: string) {
  apiClient.defaults.baseURL = url
}

let _refresh: (() => Promise<boolean>) | null = null

export function registerRefreshFn(fn: () => Promise<boolean>) {
  _refresh = fn
}

/**
 * Requests whose own 401 is the answer, and never a reason to refresh.
 *
 * WHY THIS EXISTS. The refresh itself is a POST through this client. When the
 * stored refresh token had expired, that POST answered 401, this interceptor
 * took the 401 as a reason to refresh, which posted again, which answered 401
 * — and so on, until the server's rate limit answered 429 instead. One app
 * start with a stale token was five refused refreshes and a rate-limited
 * sixth, which could then refuse the sign-in that followed.
 */
const ITS_OWN_ANSWER = ['/api/v1/auth/refresh', '/api/v1/auth/login']

let _refreshing: Promise<boolean> | null = null

/** One refresh at a time. A screen that opens fires several requests at once;
 *  when the access token has expired every one of them answers 401, and each
 *  used to start a refresh of its own — spending the refresh token on the
 *  first and failing the rest. They now wait for the same one. */
function refreshOnce(): Promise<boolean> {
  if (!_refresh) return Promise.resolve(false)
  if (!_refreshing) {
    _refreshing = _refresh()
      .catch(() => false)
      .finally(() => { _refreshing = null })
  }
  return _refreshing
}

apiClient.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config
    const url: string = original?.url ?? ''
    const itsOwnAnswer = ITS_OWN_ANSWER.some((path) => url.includes(path))
    if (error.response?.status === 401 && original && !original._retry && !itsOwnAnswer && _refresh) {
      original._retry = true
      const ok = await refreshOnce()
      if (ok) {
        original.headers['Authorization'] = apiClient.defaults.headers.common['Authorization']
        return apiClient(original)
      }
    }
    return Promise.reject(error)
  }
)


/**
 * A list endpoint's rows, whichever shape it answers in.
 *
 * WHY THIS EXISTS. /alerts and /incidents return {items, total, limit, offset,
 * has_more}; this app asked for Alert[] and handed the object straight to a
 * FlatList, which renders an object as nothing at all. No error, no empty
 * state, no clue — the dashboard said 9,136 open alerts and the Alerts tab was
 * blank, for every user, on every version.
 *
 * Tolerating both shapes is deliberate: the server has paginated some lists and
 * not others, and a client that only understands one is a blank screen waiting
 * for the next endpoint to change.
 */
export function rows<T>(data: T[] | { items?: T[] } | null | undefined): T[] {
  if (Array.isArray(data)) return data
  return data?.items ?? []
}

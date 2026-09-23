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

apiClient.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config
    if (error.response?.status === 401 && !original._retry && _refresh) {
      original._retry = true
      const ok = await _refresh()
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

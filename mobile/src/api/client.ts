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

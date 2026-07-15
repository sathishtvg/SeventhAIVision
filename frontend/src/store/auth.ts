import { jwtDecode } from 'jwt-decode'
import { create } from 'zustand'
import { login as apiLogin, refreshTokens } from '@/api/auth'
import { apiClient } from '@/api/client'

interface JwtClaims {
  sub: string
  tenant_id: string
  role_id: number
  exp: number
}

export interface AuthUser {
  id: string
  tenantId: string
  roleId: number
}

interface AuthState {
  accessToken: string | null
  user: AuthUser | null
  // Effective permission codes fetched from the backend (Gap 91). null until
  // loaded; usePermission falls back to the hardcoded matrix while null.
  permissions: string[] | null
  login: (tenantSlug: string, email: string, password: string) => Promise<void>
  logout: () => void
  refresh: () => Promise<void>
  loadPermissions: () => Promise<void>
  _setTokens: (accessToken: string, refreshToken: string) => void
}

const REFRESH_KEY = 'seventh_ai_refresh_token'

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  user: null,
  permissions: null,

  _setTokens(accessToken, refreshToken) {
    const claims = jwtDecode<JwtClaims>(accessToken)
    localStorage.setItem(REFRESH_KEY, refreshToken)
    set({
      accessToken,
      user: { id: claims.sub, tenantId: claims.tenant_id, roleId: claims.role_id },
    })
  },

  async login(tenantSlug, email, password) {
    const tokens = await apiLogin(tenantSlug, email, password)
    get()._setTokens(tokens.access_token, tokens.refresh_token)
    await get().loadPermissions()
  },

  async loadPermissions() {
    try {
      const { data } = await apiClient.get<{ permissions: string[] }>('/api/v1/auth/me/permissions')
      set({ permissions: data.permissions })
    } catch {
      // Leave null → usePermission uses the hardcoded fallback matrix
    }
  },

  logout() {
    localStorage.removeItem(REFRESH_KEY)
    set({ accessToken: null, user: null, permissions: null })
  },

  async refresh() {
    const storedRefresh = localStorage.getItem(REFRESH_KEY)
    if (!storedRefresh) {
      get().logout()
      return
    }
    const tokens = await refreshTokens(storedRefresh)
    get()._setTokens(tokens.access_token, tokens.refresh_token)
    await get().loadPermissions()
  },
}))

// Wire axios interceptors once (idempotent because of the eject pattern).
let _requestInterceptorId: number | null = null

export function initAxiosInterceptors() {
  if (_requestInterceptorId !== null) return

  _requestInterceptorId = apiClient.interceptors.request.use((config) => {
    const token = useAuthStore.getState().accessToken
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })

  apiClient.interceptors.response.use(
    (res) => res,
    async (error) => {
      const original = error.config
      if (error.response?.status === 401 && !original._retry) {
        original._retry = true
        try {
          await useAuthStore.getState().refresh()
          const newToken = useAuthStore.getState().accessToken
          if (newToken) original.headers.Authorization = `Bearer ${newToken}`
          return apiClient(original)
        } catch {
          useAuthStore.getState().logout()
        }
      }
      return Promise.reject(error)
    },
  )
}

// Attempt to restore session from stored refresh token on page load.
export async function restoreSession() {
  const stored = localStorage.getItem(REFRESH_KEY)
  if (!stored) return
  try {
    await useAuthStore.getState().refresh()
  } catch {
    localStorage.removeItem(REFRESH_KEY)
  }
}

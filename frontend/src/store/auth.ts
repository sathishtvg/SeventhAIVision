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

/** What the banner needs to say, and what exiting needs to restore. */
export interface ActiveSupportSession {
  id: string
  tenantId: string
  tenantName: string
  expiresAt: string
  /** Shown in the banner. An operator who thinks they can edit and cannot is
   *  confused; one who thinks they cannot and can is dangerous. */
  accessLevel: 'read_only' | 'elevated'
}

interface AuthState {
  accessToken: string | null
  user: AuthUser | null
  /** Set while a platform operator is inside a customer tenant. The whole app
   *  runs as that tenant meanwhile, which is exactly why it is announced in a
   *  banner rather than left to be inferred from the data on screen. */
  supportSession: ActiveSupportSession | null
  /** The operator's own token, parked so leaving the session is instant and
   *  does not depend on the refresh endpoint being reachable. */
  platformToken: string | null
  // Effective permission codes fetched from the backend (Gap 91). null until
  // loaded; usePermission falls back to the hardcoded matrix while null.
  permissions: string[] | null
  login: (tenantSlug: string, email: string, password: string) => Promise<void>
  logout: () => void
  refresh: () => Promise<void>
  loadPermissions: () => Promise<void>
  enterSupportSession: (s: ActiveSupportSession, token: string) => Promise<void>
  exitSupportSession: () => Promise<void>
  _setTokens: (accessToken: string, refreshToken: string) => void
}

const REFRESH_KEY = 'seventh_ai_refresh_token'

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  user: null,
  permissions: null,
  supportSession: null,
  platformToken: null,

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

  async enterSupportSession(session, token) {
    const claims = jwtDecode<JwtClaims>(token)
    set({
      // Parked, not replaced: the refresh token in localStorage still belongs
      // to the operator, and a support token has none of its own.
      platformToken: get().platformToken ?? get().accessToken,
      supportSession: session,
      accessToken: token,
      user: { id: claims.sub, tenantId: claims.tenant_id, roleId: claims.role_id },
    })
    await get().loadPermissions()
  },

  async exitSupportSession() {
    const parked = get().platformToken
    if (!parked) return
    const claims = jwtDecode<JwtClaims>(parked)
    set({
      accessToken: parked,
      platformToken: null,
      supportSession: null,
      user: { id: claims.sub, tenantId: claims.tenant_id, roleId: claims.role_id },
    })
    await get().loadPermissions()
  },

  logout() {
    localStorage.removeItem(REFRESH_KEY)
    set({
      accessToken: null, user: null, permissions: null,
      supportSession: null, platformToken: null,
    })
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
      // A support token has no refresh token of its own, and refreshing would
      // silently hand back the operator's platform token — the app would carry
      // on against a different tenant than the one on screen. Drop out of the
      // session instead, which is the honest thing and what the banner says.
      if (error.response?.status === 401 && useAuthStore.getState().supportSession) {
        await useAuthStore.getState().exitSupportSession()
        return Promise.reject(error)
      }
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

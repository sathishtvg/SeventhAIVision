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

/**
 * Requests whose own 401 is the answer, and never a reason to refresh.
 *
 * The refresh is itself a POST through this client. With a refresh token that
 * had expired, its 401 was taken by the interceptor below as a reason to
 * refresh, which posted again, and so on until the server's rate limit
 * answered 429: five refused refreshes and a rate-limited sixth from one page
 * load, which could then refuse the sign-in that followed. And a wrong
 * password — a 401 from the login itself — was "refreshed" and posted twice.
 */
const ITS_OWN_ANSWER = ['/api/v1/auth/refresh', '/api/v1/auth/login']

let _refreshing: Promise<void> | null = null

/** One refresh at a time. A page that opens fires several requests at once;
 *  when the access token has expired each answers 401, and each used to start
 *  a refresh of its own. They now wait for the same one. */
function refreshOnce(): Promise<void> {
  if (!_refreshing) {
    _refreshing = useAuthStore.getState().refresh().finally(() => { _refreshing = null })
  }
  return _refreshing
}

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
      const url: string = original?.url ?? ''
      const itsOwnAnswer = ITS_OWN_ANSWER.some((path) => url.includes(path))
      if (error.response?.status === 401 && original && !original._retry && !itsOwnAnswer) {
        original._retry = true
        try {
          await refreshOnce()
          const newToken = useAuthStore.getState().accessToken
          // No token after a refresh means there was nothing to refresh with
          // and the session is over: sending the request again unsigned would
          // only be refused a second time.
          if (!newToken) return Promise.reject(error)
          original.headers.Authorization = `Bearer ${newToken}`
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

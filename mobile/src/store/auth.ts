import AsyncStorage from '@react-native-async-storage/async-storage'
import * as SecureStore from 'expo-secure-store'
import { jwtDecode } from 'jwt-decode'
import { create } from 'zustand'
import { apiClient } from '@/api/client'
import { getMyPermissions } from '@/api/auth'

const REFRESH_KEY = 'seventh_ai_refresh_token'
/** Cached so the menu is gated correctly on the next cold start before the
 *  network answers — a guard opening the app in a basement car park should not
 *  see a different menu from the one they saw yesterday. Not a secret: these
 *  are permission codes, and the server enforces them regardless. */
const PERMS_KEY = 'seventh_ai_permissions'

interface JwtClaims {
  sub: string
  tenant_id: string
  role_id: number
  exp: number
}

export interface AuthUser {
  id: string
  email: string
  roleId: number
  tenantId: string
}

interface AuthState {
  accessToken: string | null
  user: AuthUser | null
  /** null means "not known yet" — permission checks pass while it is null.
   *  See src/lib/access.ts for why that is the safe direction. */
  permissions: string[] | null
  loadPermissions: () => Promise<void>
  login: (email: string, password: string, tenantSlug: string) => Promise<void>
  logout: () => Promise<void>
  restoreSession: () => Promise<void>
  refresh: () => Promise<boolean>
}

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  user: null,
  permissions: null,

  /** Never throws: a failed fetch leaves permissions null, which shows the menu
   *  as it was before this gate existed rather than hiding a guard's tools. */
  loadPermissions: async () => {
    try {
      const cached = await AsyncStorage.getItem(PERMS_KEY)
      if (cached) set({ permissions: JSON.parse(cached) as string[] })
    } catch {
      // A bad cache entry is not worth failing sign-in over.
    }
    try {
      const { permissions } = await getMyPermissions()
      set({ permissions })
      await AsyncStorage.setItem(PERMS_KEY, JSON.stringify(permissions))
    } catch {
      // Offline or the endpoint is unreachable: keep whatever the cache gave.
    }
  },

  login: async (email, password, tenantSlug) => {
    const res = await apiClient.post('/api/v1/auth/login', { email, password, tenant_slug: tenantSlug })
    const { access_token, refresh_token } = res.data as { access_token: string; refresh_token: string }
    const claims = jwtDecode<JwtClaims>(access_token)
    const user: AuthUser = { id: claims.sub, email, roleId: claims.role_id, tenantId: claims.tenant_id }
    await SecureStore.setItemAsync(REFRESH_KEY, refresh_token)
    set({ accessToken: access_token, user })
    apiClient.defaults.headers.common['Authorization'] = `Bearer ${access_token}`
    await get().loadPermissions()
  },

  logout: async () => {
    await SecureStore.deleteItemAsync(REFRESH_KEY)
    await AsyncStorage.removeItem(PERMS_KEY)
    delete apiClient.defaults.headers.common['Authorization']
    // Clear the permissions too: the next person to sign in on this handset
    // must not inherit the last one's menu.
    set({ accessToken: null, user: null, permissions: null })
  },

  restoreSession: async () => {
    const refreshToken = await SecureStore.getItemAsync(REFRESH_KEY)
    if (!refreshToken) return
    const ok = await get().refresh()
    if (!ok) await SecureStore.deleteItemAsync(REFRESH_KEY)
  },

  refresh: async () => {
    const refreshToken = await SecureStore.getItemAsync(REFRESH_KEY)
    if (!refreshToken) return false
    try {
      const res = await apiClient.post('/api/v1/auth/refresh', { refresh_token: refreshToken })
      const { access_token, refresh_token: newRefresh } = res.data as { access_token: string; refresh_token: string }
      const claims = jwtDecode<JwtClaims>(access_token)
      const user: AuthUser = {
        id: claims.sub,
        email: get().user?.email ?? '',
        roleId: claims.role_id,
        tenantId: claims.tenant_id,
      }
      await SecureStore.setItemAsync(REFRESH_KEY, newRefresh)
      set({ accessToken: access_token, user })
      apiClient.defaults.headers.common['Authorization'] = `Bearer ${access_token}`
      void get().loadPermissions()
      return true
    } catch {
      return false
    }
  },
}))

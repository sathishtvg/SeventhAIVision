import * as SecureStore from 'expo-secure-store'
import { jwtDecode } from 'jwt-decode'
import { create } from 'zustand'
import { apiClient } from '@/api/client'

const REFRESH_KEY = 'seventh_ai_refresh_token'

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
  login: (email: string, password: string, tenantSlug: string) => Promise<void>
  logout: () => Promise<void>
  restoreSession: () => Promise<void>
  refresh: () => Promise<boolean>
}

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  user: null,

  login: async (email, password, tenantSlug) => {
    const res = await apiClient.post('/api/v1/auth/login', { email, password, tenant_slug: tenantSlug })
    const { access_token, refresh_token } = res.data as { access_token: string; refresh_token: string }
    const claims = jwtDecode<JwtClaims>(access_token)
    const user: AuthUser = { id: claims.sub, email, roleId: claims.role_id, tenantId: claims.tenant_id }
    await SecureStore.setItemAsync(REFRESH_KEY, refresh_token)
    set({ accessToken: access_token, user })
    apiClient.defaults.headers.common['Authorization'] = `Bearer ${access_token}`
  },

  logout: async () => {
    await SecureStore.deleteItemAsync(REFRESH_KEY)
    delete apiClient.defaults.headers.common['Authorization']
    set({ accessToken: null, user: null })
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
      return true
    } catch {
      return false
    }
  },
}))

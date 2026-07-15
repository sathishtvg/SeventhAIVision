import type { TokenResponse } from '@/types/api'
import { apiClient } from './client'

export async function login(tenantSlug: string, email: string, password: string): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/api/v1/auth/login', {
    tenant_slug: tenantSlug,
    email,
    password,
  })
  return data
}

export async function refreshTokens(refreshToken: string): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/api/v1/auth/refresh', {
    refresh_token: refreshToken,
  })
  return data
}

export interface TenantInfo {
  id: string
  name: string
  slug: string
  subdomain: string
  timezone: string
  branding: Record<string, string> | null
}

export async function resolveSubdomain(subdomain: string): Promise<TenantInfo> {
  const { data } = await apiClient.get<TenantInfo>(
    `/api/v1/auth/resolve-tenant/${encodeURIComponent(subdomain)}`
  )
  return data
}

export async function forgotPassword(tenantSlug: string, email: string): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>('/api/v1/auth/forgot-password', {
    tenant_slug: tenantSlug,
    email,
  })
  return data
}

export async function resetPassword(token: string, newPassword: string): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>('/api/v1/auth/reset-password', {
    token,
    new_password: newPassword,
  })
  return data
}

import type { TokenResponse } from '@/types/api'
import { apiClient } from './client'

/**
 * What the server answers to a correct password on an account with two-factor
 * on: no tokens yet, and a challenge that is good for five minutes. The tokens
 * come from `verifyTwoFactor`, once the code from the authenticator is given.
 */
export interface TwoFactorChallenge {
  requires_2fa: true
  challenge_token: string
}

export type LoginAnswer = TokenResponse | TwoFactorChallenge

export const needsCode = (answer: LoginAnswer): answer is TwoFactorChallenge =>
  (answer as TwoFactorChallenge).requires_2fa === true

export async function login(tenantSlug: string, email: string, password: string): Promise<LoginAnswer> {
  const { data } = await apiClient.post<LoginAnswer>('/api/v1/auth/login', {
    tenant_slug: tenantSlug,
    email,
    password,
  })
  return data
}

/** The second step of a two-factor sign-in: the challenge, and the six digits. */
export async function verifyTwoFactor(challengeToken: string, code: string): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/api/v1/auth/2fa-verify', {
    challenge_token: challengeToken,
    totp_code: code,
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

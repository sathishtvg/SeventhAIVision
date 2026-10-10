import { apiClient } from './client'

export interface MyPermissions {
  role_id: number
  permissions: string[]
}

/**
 * The caller's effective permission codes.
 *
 * The endpoint's own docstring says the web AND mobile clients load this to
 * drive UI gating; mobile never did. It matters for custom roles (Gap 91),
 * whose permission sets cannot be hardcoded in a client.
 */
export const getMyPermissions = () =>
  apiClient.get<MyPermissions>('/api/v1/auth/me/permissions').then((r) => r.data)

/**
 * What the server answers to a password.
 *
 * `tokens` — signed in. `code` — the password was right and the account has
 * two-factor on: nothing is signed in until `verifyTwoFactor` is given the six
 * digits, within five minutes.
 */
export type SignInAnswer =
  | { kind: 'tokens'; accessToken: string; refreshToken: string }
  | { kind: 'code'; challengeToken: string }

interface TokenPair { access_token: string; refresh_token: string }

export async function signIn(email: string, password: string, tenantSlug: string): Promise<SignInAnswer> {
  const { data } = await apiClient.post<TokenPair | { requires_2fa: true; challenge_token: string }>(
    '/api/v1/auth/login', { email, password, tenant_slug: tenantSlug })
  if ('requires_2fa' in data && data.requires_2fa) return { kind: 'code', challengeToken: data.challenge_token }
  const tokens = data as TokenPair
  return { kind: 'tokens', accessToken: tokens.access_token, refreshToken: tokens.refresh_token }
}

/** The second step: the challenge, and the six digits, under the names the server reads. */
export async function verifyTwoFactor(challengeToken: string, code: string): Promise<SignInAnswer & { kind: 'tokens' }> {
  const { data } = await apiClient.post<TokenPair>(
    '/api/v1/auth/2fa-verify', { challenge_token: challengeToken, totp_code: code })
  return { kind: 'tokens', accessToken: data.access_token, refreshToken: data.refresh_token }
}

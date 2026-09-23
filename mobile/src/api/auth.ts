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

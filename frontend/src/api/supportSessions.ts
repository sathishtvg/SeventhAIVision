import { apiClient } from './client'

/** A platform operator's deliberate, time-boxed entry into one customer tenant.
 *  Super Admin holds four permissions and none of them read a customer's
 *  operational data (migration 0102), so this is how a support ticket gets
 *  looked at — and how the customer gets a record that it happened. */
export interface SupportSession {
  id: string
  tenant_id: string
  tenant_name: string
  reason: string
  started_at: string
  expires_at: string
  ended_at: string | null
  platform_user_id: string
  platform_user_email: string | null
  is_live: boolean
}

export interface OpenedSupportSession {
  id: string
  tenant_id: string
  tenant_name: string
  expires_at: string
  /** Scoped to the target tenant for the life of the session, and refused the
   *  moment the session ends — the server re-checks on every request. */
  access_token: string
}

export const listSupportSessions = () =>
  apiClient.get<SupportSession[]>('/api/v1/support-sessions').then((r) => r.data)

export const openSupportSession = (data: {
  tenant_id: string
  reason: string
  minutes: number
}) =>
  apiClient.post<OpenedSupportSession>('/api/v1/support-sessions', data).then((r) => r.data)

export const endSupportSession = (sessionId: string) =>
  apiClient.post(`/api/v1/support-sessions/${sessionId}/end`).then((r) => r.data)

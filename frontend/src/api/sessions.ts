import { apiClient as client } from './client'

export interface Session {
  id: string
  device_name: string | null
  last_ip: string | null
  last_seen_at: string | null
  created_at: string
  expires_at: string
}

export const getMySessions = () =>
  client.get<Session[]>('/api/v1/sessions/me').then(r => r.data)

export const revokeMySession = (sessionId: string) =>
  client.delete(`/api/v1/sessions/me/${sessionId}`).then(r => r.data)

export const revokeAllMySessions = () =>
  client.delete('/api/v1/sessions/me').then(r => r.data)

export const getUserSessions = (userId: string) =>
  client.get<Session[]>(`/api/v1/sessions/users/${userId}`).then(r => r.data)

export const revokeAllUserSessions = (userId: string) =>
  client.delete(`/api/v1/sessions/users/${userId}`).then(r => r.data)

export const unlockUserAccount = (userId: string) =>
  client.post(`/api/v1/auth/users/${userId}/unlock`).then(r => r.data)

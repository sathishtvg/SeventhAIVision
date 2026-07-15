import { apiClient } from './client'

export interface Door {
  id: string
  name: string
  location: string | null
  door_type: string
  site_id: string | null
  camera_id: string | null
  is_active: boolean
  is_locked: boolean
  held_open: boolean
  last_event_at: string | null
  created_at: string
}

export interface AccessCredential {
  id: string
  holder_name: string | null
  user_id: string | null
  credential_type: string
  credential_ref: string
  is_active: boolean
  expires_at: string | null
  created_at: string
}

export interface AccessEvent {
  id: string
  door_id: string
  door_name: string | null
  credential_id: string | null
  holder_name: string | null
  event_type: string
  method: string | null
  occurred_at: string
}

export const getDoors = (params?: { site_id?: string }) =>
  apiClient.get<Door[]>('/api/v1/access/doors', { params }).then((r) => r.data)

export const getCredentials = (params?: { limit?: number }) =>
  apiClient
    .get<AccessCredential[]>('/api/v1/access/credentials', { params: { limit: 50, ...params } })
    .then((r) => r.data)

export const getAccessEvents = (params?: { door_id?: string; event_type?: string; limit?: number }) =>
  apiClient
    .get<AccessEvent[]>('/api/v1/access/events', { params: { limit: 50, ...params } })
    .then((r) => r.data)

export const lockDoor = (doorId: string) =>
  apiClient.post(`/api/v1/access/doors/${doorId}/lock`, {}).then((r) => r.data)

export const unlockDoor = (doorId: string, durationSeconds?: number) =>
  apiClient
    .post(`/api/v1/access/doors/${doorId}/unlock`, { duration_seconds: durationSeconds })
    .then((r) => r.data)

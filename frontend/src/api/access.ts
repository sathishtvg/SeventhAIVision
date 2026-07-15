import { apiClient as client } from './client'

export interface AccessDoor {
  id: string
  name: string
  location: string | null
  door_type: string
  is_active: boolean
  site_id: string | null
  camera_id: string | null
  site_name: string | null
  camera_name: string | null
  created_at: string
  updated_at: string
}

export interface AccessCredential {
  id: string
  holder_name: string | null
  user_id: string | null
  user_full_name: string | null
  user_email: string | null
  credential_type: string
  credential_ref: string
  is_active: boolean
  expires_at: string | null
  created_at: string
  updated_at: string
}

export interface AccessRule {
  id: string
  credential_id: string
  door_id: string
  door_name: string
  credential_holder: string | null
  credential_type: string
  schedule_days: string
  time_from: string | null
  time_to: string | null
  is_active: boolean
  created_at: string
}

export interface AccessEvent {
  id: string
  door_id: string
  door_name: string
  door_location: string | null
  credential_id: string | null
  holder_name: string | null
  credential_type: string | null
  credential_ref: string | null
  event_type: string
  denial_reason: string | null
  occurred_at: string
}

export interface AccessDashboard {
  door_summary: { total_doors: number; active_doors: number }
  credential_summary: { total_credentials: number; active_credentials: number }
  event_summary: {
    events_today: number
    granted_today: number
    denied_today: number
    forced_today: number
    tamper_today: number
  }
  recent_events: AccessEvent[]
}

export const getAccessDashboard = () =>
  client.get<AccessDashboard>('/api/v1/access/dashboard').then(r => r.data)

export const listDoors = (params?: { site_id?: string; is_active?: boolean }) =>
  client.get<AccessDoor[]>('/api/v1/access/doors', { params }).then(r => r.data)

export const createDoor = (data: {
  name: string; location?: string; door_type?: string; site_id?: string; camera_id?: string
}) => client.post<{ id: string }>('/api/v1/access/doors', data).then(r => r.data)

export const updateDoor = (id: string, data: Partial<{
  name: string; location: string; door_type: string; is_active: boolean
}>) => client.put(`/api/v1/access/doors/${id}`, data).then(r => r.data)

export const deactivateDoor = (id: string) =>
  client.delete(`/api/v1/access/doors/${id}`).then(r => r.data)

export const listCredentials = (params?: { is_active?: boolean; user_id?: string }) =>
  client.get<AccessCredential[]>('/api/v1/access/credentials', { params }).then(r => r.data)

export const createCredential = (data: {
  holder_name?: string; user_id?: string; credential_type?: string;
  credential_ref: string; expires_at?: string
}) => client.post<{ id: string }>('/api/v1/access/credentials', data).then(r => r.data)

export const deactivateCredential = (id: string) =>
  client.delete(`/api/v1/access/credentials/${id}`).then(r => r.data)

export const listRules = (params?: { door_id?: string; credential_id?: string }) =>
  client.get<AccessRule[]>('/api/v1/access/rules', { params }).then(r => r.data)

export const createRule = (data: {
  credential_id: string; door_id: string; schedule_days?: string;
  time_from?: string; time_to?: string
}) => client.post<{ id: string }>('/api/v1/access/rules', data).then(r => r.data)

export const deleteRule = (id: string) =>
  client.delete(`/api/v1/access/rules/${id}`).then(r => r.data)

export const listAccessEvents = (params?: {
  door_id?: string; event_type?: string; hours?: number; limit?: number
}) => client.get<AccessEvent[]>('/api/v1/access/events', { params }).then(r => r.data)

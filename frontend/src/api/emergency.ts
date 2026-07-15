import { apiClient as client } from './client'

export interface EmergencyBroadcast {
  id: string
  title: string
  message: string
  severity: 'info' | 'warning' | 'critical' | 'drill'
  broadcast_type: 'all' | 'role'
  target_role_ids: number[] | null
  recipient_count: number
  acknowledged_count: number
  sender_name: string | null
  sender_email: string | null
  sent_at: string
  status: string
  created_at: string
  // detail endpoint only
  recipients?: BroadcastRecipient[]
}

export interface BroadcastRecipient {
  user_id: string
  full_name: string
  email: string
  role_name: string
  delivered_at: string | null
  acknowledged_at: string | null
}

export interface MyBroadcast extends EmergencyBroadcast {
  delivered_at: string | null
  acknowledged_at: string | null
}

export const listBroadcasts = (params?: { limit?: number; offset?: number }) =>
  client.get<EmergencyBroadcast[]>('/api/v1/emergency/broadcasts', { params }).then(r => r.data)

export const getBroadcast = (id: string) =>
  client.get<EmergencyBroadcast>(`/api/v1/emergency/broadcasts/${id}`).then(r => r.data)

export const getMyBroadcasts = () =>
  client.get<MyBroadcast[]>('/api/v1/emergency/broadcasts/my').then(r => r.data)

export const sendBroadcast = (data: {
  title: string
  message: string
  severity: string
  broadcast_type: string
  target_role_ids?: number[]
}) => client.post<{ id: string; recipient_count: number; sent_at: string }>(
  '/api/v1/emergency/broadcasts', data
).then(r => r.data)

export const acknowledgeBroadcast = (id: string) =>
  client.post(`/api/v1/emergency/broadcasts/${id}/acknowledge`).then(r => r.data)

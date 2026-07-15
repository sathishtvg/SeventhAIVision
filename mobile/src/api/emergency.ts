import { apiClient } from './client'

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
}

export interface MyBroadcast extends EmergencyBroadcast {
  delivered_at: string | null
  acknowledged_at: string | null
}

export const listBroadcasts = (params?: { limit?: number; offset?: number }) =>
  apiClient
    .get<EmergencyBroadcast[]>('/api/v1/emergency/broadcasts', { params: { limit: 50, ...params } })
    .then((r) => r.data)

export const getMyBroadcasts = () =>
  apiClient.get<MyBroadcast[]>('/api/v1/emergency/broadcasts/my').then((r) => r.data)

export const sendBroadcast = (body: {
  title: string
  message: string
  severity: string
  broadcast_type: string
  target_role_ids?: number[]
}) =>
  apiClient
    .post<{ id: string; recipient_count: number; sent_at: string }>('/api/v1/emergency/broadcasts', body)
    .then((r) => r.data)

export const acknowledgeBroadcast = (id: string) =>
  apiClient.post(`/api/v1/emergency/broadcasts/${id}/acknowledge`).then((r) => r.data)

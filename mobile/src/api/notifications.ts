import { apiClient } from './client'

export interface NotificationChannel {
  id: string
  name: string
  channel_type: 'email' | 'sms' | 'webhook'
  config: Record<string, unknown>
  is_active: boolean
  created_at: string
}

export interface NotificationRule {
  id: string
  channel_id: string
  min_severity: string
  module_types: string[]
  alert_codes: string[]
  is_active: boolean
  created_at: string
}

export interface NotificationLog {
  id: string
  alert_id: string
  channel_id: string
  channel_type: string
  status: 'sent' | 'failed' | 'pending'
  error_detail: string | null
  sent_at: string | null
  created_at: string
}

// Channels
export const getChannels = () =>
  apiClient.get<NotificationChannel[]>('/api/v1/notifications/channels').then((r) => r.data)

export const createChannel = (body: {
  name: string
  channel_type: string
  config: Record<string, unknown>
  is_active?: boolean
}) => apiClient.post<NotificationChannel>('/api/v1/notifications/channels', body).then((r) => r.data)

export const updateChannel = (
  id: string,
  body: { name?: string; config?: Record<string, unknown>; is_active?: boolean },
) => apiClient.put<NotificationChannel>(`/api/v1/notifications/channels/${id}`, body).then((r) => r.data)

export const deleteChannel = (id: string) =>
  apiClient.delete(`/api/v1/notifications/channels/${id}`)

export const testChannel = (id: string) =>
  apiClient.post<{ status: string }>(`/api/v1/notifications/channels/${id}/test`).then((r) => r.data)

// Rules
export const getRules = () =>
  apiClient.get<NotificationRule[]>('/api/v1/notifications/rules').then((r) => r.data)

export const createRule = (body: {
  channel_id: string
  min_severity: string
  module_types: string[]
  alert_codes: string[]
  is_active?: boolean
}) => apiClient.post<NotificationRule>('/api/v1/notifications/rules', body).then((r) => r.data)

export const updateRule = (
  id: string,
  body: { min_severity?: string; module_types?: string[]; alert_codes?: string[]; is_active?: boolean },
) => apiClient.put<NotificationRule>(`/api/v1/notifications/rules/${id}`, body).then((r) => r.data)

export const deleteRule = (id: string) =>
  apiClient.delete(`/api/v1/notifications/rules/${id}`)

// Delivery logs
export const getLogs = (params?: { channel_id?: string; status_filter?: string; limit?: number }) =>
  apiClient
    .get<NotificationLog[]>('/api/v1/notifications/logs', { params: { limit: 100, ...params } })
    .then((r) => r.data)

import type { NotificationChannel, NotificationRule, NotificationLog } from '@/types/api'
import { apiClient } from './client'

// Channels
export const getChannels = () =>
  apiClient.get<NotificationChannel[]>('/api/v1/notifications/channels').then((r) => r.data)

export const createChannel = (body: {
  name: string
  channel_type: string
  config: Record<string, unknown>
  is_active?: boolean
}) => apiClient.post<NotificationChannel>('/api/v1/notifications/channels', body).then((r) => r.data)

export const updateChannel = (id: string, body: { name?: string; config?: Record<string, unknown>; is_active?: boolean }) =>
  apiClient.put<NotificationChannel>(`/api/v1/notifications/channels/${id}`, body).then((r) => r.data)

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
  trigger_events?: string[]
  is_active?: boolean
}) => apiClient.post<NotificationRule>('/api/v1/notifications/rules', body).then((r) => r.data)

export const updateRule = (id: string, body: { min_severity?: string; module_types?: string[]; alert_codes?: string[]; trigger_events?: string[]; is_active?: boolean }) =>
  apiClient.put<NotificationRule>(`/api/v1/notifications/rules/${id}`, body).then((r) => r.data)

export const deleteRule = (id: string) =>
  apiClient.delete(`/api/v1/notifications/rules/${id}`)

// Logs
export const getLogs = (channelId?: string, statusFilter?: string, limit = 100) =>
  apiClient
    .get<NotificationLog[]>('/api/v1/notifications/logs', {
      params: { channel_id: channelId, status_filter: statusFilter, limit },
    })
    .then((r) => r.data)

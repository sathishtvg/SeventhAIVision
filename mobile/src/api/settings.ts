import { apiClient } from './client'

export interface TenantSetting {
  id: string
  setting_key: string
  setting_value: number
  updated_at: string
}

export const SETTING_KEYS = [
  'lpr.confidence_threshold',
  'face.match_threshold',
  'intrusion.breach_cooldown_seconds',
  'evidence.retention_days',
] as const

export type SettingKey = (typeof SETTING_KEYS)[number]

export const getSettings = () =>
  apiClient.get<TenantSetting[]>('/api/v1/settings').then((r) => r.data)

export const getSetting = (key: SettingKey) =>
  apiClient.get<TenantSetting>(`/api/v1/settings/${key}`).then((r) => r.data)

export const upsertSetting = (key: SettingKey, value: number) =>
  apiClient
    .put<TenantSetting>(`/api/v1/settings/${key}`, { setting_value: value })
    .then((r) => r.data)

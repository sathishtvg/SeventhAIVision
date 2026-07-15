import type { TenantSetting } from '@/types/api'
import { apiClient } from './client'

export const getSettings = () =>
  apiClient.get<TenantSetting[]>('/api/v1/settings').then((r) => r.data)

export const upsertSetting = (key: string, value: number) =>
  apiClient.put<TenantSetting>(`/api/v1/settings/${key}`, { setting_value: value }).then((r) => r.data)

import type { TenantSetting } from '@/types/api'
import { apiClient } from './client'

export const getSettings = () =>
  apiClient.get<TenantSetting[]>('/api/v1/settings').then((r) => r.data)

// Widened from `number`: ui.page_labels stores an object. Every other key is
// still scalar, and the backend validates each key's shape on its own.
export const upsertSetting = (key: string, value: unknown) =>
  apiClient.put<TenantSetting>(`/api/v1/settings/${key}`, { setting_value: value }).then((r) => r.data)

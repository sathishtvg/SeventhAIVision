import { apiClient } from './client'

export interface ApiKeyRow {
  id: string
  name: string
  key_prefix: string
  is_active: boolean
  last_used_at: string | null
  expires_at: string | null
  created_at: string
  created_by_email: string | null
}

export interface ApiKeyCreated extends ApiKeyRow {
  key: string
  key_shown_once: true
}

export const listApiKeys = () =>
  apiClient.get<ApiKeyRow[]>('/api/v1/api-keys').then((r) => r.data)

export const createApiKey = (data: { name: string; expires_at?: string }) =>
  apiClient.post<ApiKeyCreated>('/api/v1/api-keys', data).then((r) => r.data)

export const revokeApiKey = (keyId: string) =>
  apiClient.delete<{ id: string; revoked: boolean }>(`/api/v1/api-keys/${keyId}`).then((r) => r.data)

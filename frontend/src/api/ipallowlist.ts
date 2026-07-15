import { apiClient } from './client'

export interface IpRule {
  id: string
  cidr: string
  description: string | null
  is_active: boolean
  created_at: string
  created_by_email: string | null
}

export const listIpRules = () =>
  apiClient.get<IpRule[]>('/api/v1/ip-allowlist').then((r) => r.data)

export const addIpRule = (data: { cidr: string; description?: string }) =>
  apiClient.post<IpRule>('/api/v1/ip-allowlist', data).then((r) => r.data)

export const deleteIpRule = (ruleId: string) =>
  apiClient.delete<{ id: string; deleted: boolean }>(`/api/v1/ip-allowlist/${ruleId}`).then((r) => r.data)

export const toggleIpRule = (ruleId: string) =>
  apiClient.patch<{ id: string; is_active: boolean }>(`/api/v1/ip-allowlist/${ruleId}/toggle`).then((r) => r.data)

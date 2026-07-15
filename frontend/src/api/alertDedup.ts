import { apiClient } from '@/api/client'

export interface DedupRule {
  id: string
  module_type: string | null
  camera_id: string | null
  camera_name: string | null
  window_seconds: number
  is_active: boolean
  created_at: string
  updated_at: string | null
}

export interface DedupRuleBody {
  module_type?: string | null
  camera_id?: string | null
  window_seconds: number
  is_active: boolean
}

export const listDedupRules = (): Promise<DedupRule[]> =>
  apiClient.get('/api/v1/alert-dedup-rules').then((r) => r.data)

export const createDedupRule = (body: DedupRuleBody): Promise<DedupRule> =>
  apiClient.post('/api/v1/alert-dedup-rules', body).then((r) => r.data)

export const updateDedupRule = ({ id, ...body }: DedupRuleBody & { id: string }): Promise<DedupRule> =>
  apiClient.put(`/api/v1/alert-dedup-rules/${id}`, body).then((r) => r.data)

export const deleteDedupRule = (id: string): Promise<void> =>
  apiClient.delete(`/api/v1/alert-dedup-rules/${id}`).then(() => undefined)

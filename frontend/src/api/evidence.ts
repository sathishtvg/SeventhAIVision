import type { Evidence } from '@/types/api'
import { apiClient } from './client'

export const getEvidence = (limit = 50) =>
  apiClient.get<Evidence[]>('/api/v1/evidence', { params: { limit } }).then((r) => r.data)

export const evidenceFileUrl = (evidenceId: string) =>
  `/api/v1/evidence/${evidenceId}/file`

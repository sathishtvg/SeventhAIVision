import type { AuditLog } from '@/types/api'
import { apiClient } from './client'

export interface AuditPage { items: AuditLog[]; has_more: boolean }

export const getAuditLogs = (limit = 100, offset = 0) =>
  apiClient.get<AuditPage>('/api/v1/audit', { params: { limit, offset } }).then((r) => r.data)

export interface VerifyResult {
  verified: boolean
  total_checked: number
  tampered_count: number
  tampered_ids: string[]
  chain_broken: boolean
}

export const verifyAuditChain = (limit = 500) =>
  apiClient.get<VerifyResult>('/api/v1/audit/verify', { params: { limit } }).then((r) => r.data)

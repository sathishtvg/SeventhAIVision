import { apiClient } from './client'

export interface CompliancePolicy {
  id: string
  title: string
  category: string
  description: string | null
  frequency: string
  site_id: string | null
  is_active: boolean
  next_due_at: string | null
  last_completed_at: string | null
  created_at: string
}

export interface ComplianceCheck {
  id: string
  policy_id: string
  policy_title: string | null
  status: 'pending' | 'passed' | 'failed' | 'partial' | 'waived'
  checked_by_user_id: string | null
  checker_name: string | null
  notes: string | null
  score: number | null
  max_score: number | null
  due_at: string | null
  completed_at: string | null
  created_at: string
}

export interface ComplianceSummary {
  total_policies: number
  active_policies: number
  overdue: number
  due_this_week: number
  passed_rate_pct: number
  recent_checks: ComplianceCheck[]
}

export const getComplianceSummary = () =>
  apiClient.get<ComplianceSummary>('/api/v1/compliance/summary').then((r) => r.data)

export const getPolicies = (params?: { category?: string; site_id?: string }) =>
  apiClient.get<CompliancePolicy[]>('/api/v1/compliance/policies', { params }).then((r) => r.data)

export const getComplianceChecks = (params?: { policy_id?: string; status?: string; limit?: number }) =>
  apiClient
    .get<ComplianceCheck[]>('/api/v1/compliance/checks', { params: { limit: 50, ...params } })
    .then((r) => r.data)

export const submitCheck = (body: {
  policy_id: string
  status: string
  notes?: string
  score?: number
  max_score?: number
}) =>
  apiClient.post<ComplianceCheck>('/api/v1/compliance/checks', body).then((r) => r.data)

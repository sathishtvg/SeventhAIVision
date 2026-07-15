import { apiClient } from './client'

export type ViolationType = 'no_show' | 'late_checkin' | 'geofence_failure' | 'early_departure' | 'manual'
export type ViolationStatus = 'open' | 'acknowledged' | 'disputed' | 'waived'

export interface Violation {
  id: string
  guard_user_id: string
  shift_id: string | null
  site_id: string | null
  violation_type: ViolationType
  description: string | null
  points: number
  status: ViolationStatus
  is_auto_generated: boolean
  reported_by_user_id: string | null
  reviewed_by_user_id: string | null
  reviewed_at: string | null
  review_notes: string | null
  occurred_at: string
  created_at: string
  guard_name: string
  site_name: string | null
  reported_by_name: string | null
}

export interface ViolationsSummaryRow {
  guard_user_id: string
  guard_name: string
  total_points: number | null
  violation_count: number
  last_violation_at: string
}

export const listViolations = (filters?: {
  guard_user_id?: string
  site_id?: string
  violation_type?: string
  violation_status?: string
}) => apiClient.get<Violation[]>('/api/v1/violations/', { params: filters }).then((r) => r.data)

export const createViolation = (data: {
  guard_user_id: string
  violation_type?: ViolationType
  description?: string
  points?: number
  shift_id?: string
  site_id?: string
}) => apiClient.post('/api/v1/violations/', data).then((r) => r.data)

export const reviewViolation = (id: string, reviewStatus: 'acknowledged' | 'disputed' | 'waived', reviewNotes?: string) =>
  apiClient
    .put(`/api/v1/violations/${id}/review`, { review_status: reviewStatus, review_notes: reviewNotes })
    .then((r) => r.data)

export const getViolationsSummary = (days?: number) =>
  apiClient
    .get<ViolationsSummaryRow[]>('/api/v1/violations/summary', { params: { days } })
    .then((r) => r.data)

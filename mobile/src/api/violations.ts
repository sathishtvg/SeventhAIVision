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
  occurred_at: string
  created_at: string
  guard_name: string
  site_name: string | null
}

export interface ViolationsSummaryRow {
  guard_user_id: string
  guard_name: string
  total_points: number | null
  violation_count: number
  last_violation_at: string
}

// Both endpoints are self-scoped server-side for guard-tier roles
// (violations.py:58-60) — a guard token only ever sees its own rows, no
// guard_user_id param needed here.
export const getMyViolations = () =>
  apiClient.get<Violation[]>('/api/v1/violations/').then((r) => r.data)

export const getMyViolationsSummary = (days?: number) =>
  apiClient
    .get<ViolationsSummaryRow[]>('/api/v1/violations/summary', { params: { days } })
    .then((r) => r.data)

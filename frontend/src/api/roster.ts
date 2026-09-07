import { apiClient } from './client'

export interface ShiftPattern {
  id: string
  site_id: string
  guard_user_id: string
  label: string | null
  days_of_week: number[]
  start_time: string
  duration_minutes: number
  is_active: boolean
  guard_name?: string
  site_name?: string
  created_at: string
  updated_at: string
}

export interface CoverageShift {
  id: string
  site_id: string | null
  guard_user_id: string
  scheduled_start: string
  scheduled_end: string
  status: string
  pattern_id: string | null
  guard_name: string | null
  site_name: string | null
}

export const listShiftPatterns = () =>
  apiClient.get<ShiftPattern[]>('/api/v1/shifts/roster/patterns').then((r) => r.data)

export const createShiftPattern = (data: {
  site_id: string
  guard_user_id: string
  days_of_week: number[]
  start_time: string
  duration_minutes: number
  label?: string
}) => apiClient.post<ShiftPattern>('/api/v1/shifts/roster/patterns', data).then((r) => r.data)

export const updateShiftPattern = (id: string, data: Partial<{
  site_id: string
  guard_user_id: string
  days_of_week: number[]
  start_time: string
  duration_minutes: number
  label: string
  is_active: boolean
}>) => apiClient.put<ShiftPattern>(`/api/v1/shifts/roster/patterns/${id}`, data).then((r) => r.data)

export const deleteShiftPattern = (id: string) =>
  apiClient.delete(`/api/v1/shifts/roster/patterns/${id}`).then((r) => r.data)

export const generateRoster = (daysAhead = 7) =>
  apiClient.post<{ created: number; days_ahead: number }>(
    '/api/v1/shifts/roster/generate', { days_ahead: daysAhead },
  ).then((r) => r.data)

export const getRosterCoverage = (days = 7) =>
  apiClient.get<{ days: number; shifts: CoverageShift[] }>(
    '/api/v1/shifts/roster/coverage', { params: { days } },
  ).then((r) => r.data)

export const updateShift = (id: string, data: Partial<{
  guard_user_id: string
  site_id: string
  scheduled_start: string
  scheduled_end: string
  handover_notes: string
}>) => apiClient.put(`/api/v1/shifts/${id}`, data).then((r) => r.data)

// ── AI Auto-Scheduler: draft/publish (ShiftSecure Phase 2B) ──────────────────

export interface DraftShift {
  id: string
  guard_user_id: string | null
  site_id: string
  scheduled_start: string
  scheduled_end: string
  shift_type: 'day' | 'night' | 'split'
  warnings: string[]
  guard_name: string | null
  site_name: string
}

export interface RosterBatch {
  id: string
  site_id: string | null
  site_name: string | null
  period_start: string
  period_end: string
  status: 'draft' | 'published' | 'discarded'
  rules_summary: {
    unfilled_slots: number
    coverage_shortfalls: number
    missing_supervisor_days: number
    missing_manager_days: number
  } | null
  generated_at: string
  published_at: string | null
  draft_shifts: DraftShift[]
}

export const autoSchedule = (data: { site_id?: string; period_start: string; period_end: string }) =>
  apiClient.post<RosterBatch>('/api/v1/roster/auto-schedule', data).then((r) => r.data)

export const getBatch = (id: string) =>
  apiClient.get<RosterBatch>(`/api/v1/roster/batches/${id}`).then((r) => r.data)

export const updateDraftShift = (id: string, data: { guard_user_id?: string; scheduled_start?: string; scheduled_end?: string }) =>
  apiClient.put(`/api/v1/roster/draft-shifts/${id}`, data).then((r) => r.data)

export const publishBatch = (id: string) =>
  apiClient.post<{ id: string; status: string; shifts_created: number }>(`/api/v1/roster/batches/${id}/publish`).then((r) => r.data)

export const discardBatch = (id: string) =>
  apiClient.delete(`/api/v1/roster/batches/${id}`).then((r) => r.data)

// ── Leave blocks + shift preferences ──────────────────────────────────────────

export interface LeaveBlock {
  id: string
  guard_user_id: string
  guard_name: string
  start_date: string
  end_date: string
  reason: string | null
  created_at: string
}

export interface AffectedShift {
  id: string
  scheduled_start: string
  scheduled_end: string
  site_name: string | null
}

export const getLeaveBlocks = (guardUserId?: string) =>
  apiClient.get<LeaveBlock[]>('/api/v1/roster/leave-blocks', { params: { guard_user_id: guardUserId } }).then((r) => r.data)

export const createLeaveBlock = (data: { guard_user_id: string; start_date: string; end_date: string; reason?: string }) =>
  apiClient.post<LeaveBlock & { affected_shifts: AffectedShift[] }>('/api/v1/roster/leave-blocks', data).then((r) => r.data)

export const deleteLeaveBlock = (id: string) =>
  apiClient.delete(`/api/v1/roster/leave-blocks/${id}`).then((r) => r.data)

export interface ShiftPreferences {
  guard_user_id: string
  preferred_shift_type: 'day' | 'night' | null
  preferred_off_days: number[] | null
}

export const getPreferences = (guardUserId: string) =>
  apiClient.get<ShiftPreferences>(`/api/v1/roster/preferences/${guardUserId}`).then((r) => r.data)

export const setPreferences = (guardUserId: string, data: { preferred_shift_type?: string | null; preferred_off_days?: number[] | null }) =>
  apiClient.put<ShiftPreferences>(`/api/v1/roster/preferences/${guardUserId}`, data).then((r) => r.data)

// ── Cover requests ────────────────────────────────────────────────────────────
//
// Posts left uncovered when approved leave lands on a published shift. One row
// per uncovered post, resolved by a supervisor assigning somebody or saying no
// cover is needed — nothing fills these automatically.

export interface CoverRequest {
  id: string
  shift_id: string
  status: 'open' | 'filled' | 'dismissed'
  note: string | null
  created_at: string
  resolved_at: string | null
  absent_user_id: string
  absent_guard_name: string | null
  filled_with_user_id: string | null
  filled_with_name: string | null
  scheduled_start: string
  scheduled_end: string
  shift_type: string | null
  site_id: string | null
  site_name: string | null
  /** Who the shift names right now — still the absent guard until covered. */
  current_guard_user_id: string | null
  current_guard_name: string | null
  leave_start: string | null
  leave_end: string | null
  leave_reason: string | null
}

export const getCoverRequests = (statusFilter: 'open' | 'filled' | 'dismissed' | 'all' = 'open') =>
  apiClient
    .get<CoverRequest[]>('/api/v1/roster/cover-requests', { params: { status_filter: statusFilter } })
    .then((r) => r.data)

export const assignCover = (coverId: string, data: { guard_user_id: string; note?: string }) =>
  apiClient.post(`/api/v1/roster/cover-requests/${coverId}/assign`, data).then((r) => r.data)

export const dismissCover = (coverId: string, note?: string) =>
  apiClient.post(`/api/v1/roster/cover-requests/${coverId}/dismiss`, { note }).then((r) => r.data)

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

export const autoSchedule = (data: {
  site_id?: string
  period_start: string
  period_end: string
  rules?: AutoScheduleRules
}) =>
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

// ── Shift definitions ─────────────────────────────────────────────────────────
//
// The named shifts a company runs, held once and pointed at, rather than
// restated as a start time and a duration on every pattern.
//
// The API works in start/end because that is how people describe a shift, and
// derives duration, end time and the midnight crossing server-side so both
// halves cannot disagree about what "19:00 for 12 hours" means.

export type ShiftKind = 'day' | 'night' | 'general' | 'split'

export interface ShiftDefinition {
  id: string
  name: string
  shift_type: ShiftKind
  /** "HH:MM" */
  start_time: string
  /** "HH:MM", derived from start + duration. */
  end_time: string
  duration_minutes: number
  duration_hours: number
  /** True when the shift finishes on a later calendar day — the "+1" badge. */
  crosses_midnight: boolean
  /** Null means fall back to the site's grace, then the tenant default. */
  grace_minutes: number | null
  break_minutes: number
  ot_eligible: boolean
  colour: string | null
  notes: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface ShiftDefinitionInput {
  name: string
  shift_type: ShiftKind
  start_time: string
  end_time: string
  grace_minutes?: number | null
  break_minutes?: number
  ot_eligible?: boolean
  colour?: string | null
  notes?: string | null
}

export const listShiftDefinitions = (includeInactive = false) =>
  apiClient
    .get<ShiftDefinition[]>('/api/v1/shifts/definitions', {
      params: { include_inactive: includeInactive },
    })
    .then((r) => r.data)

export const createShiftDefinition = (data: ShiftDefinitionInput) =>
  apiClient.post<ShiftDefinition>('/api/v1/shifts/definitions', data).then((r) => r.data)

export const updateShiftDefinition = (id: string, data: Partial<ShiftDefinitionInput> & { is_active?: boolean }) =>
  apiClient.put<ShiftDefinition>(`/api/v1/shifts/definitions/${id}`, data).then((r) => r.data)

/** Retires the shift if a pattern or roster already uses it; deletes it if not. */
export const deleteShiftDefinition = (id: string) =>
  apiClient
    .delete<{ id: string; deleted?: boolean; retired?: boolean; in_use_by?: number }>(
      `/api/v1/shifts/definitions/${id}`,
    )
    .then((r) => r.data)

// ── Roster grid ───────────────────────────────────────────────────────────────

export interface GridCell {
  shift_id: string
  site_id: string | null
  site_name: string | null
  shift_definition_id: string | null
  /** Null for shifts created before shift definitions existed. */
  shift_name: string | null
  shift_type: string
  colour: string | null
  start: string
  end: string
  status: string
}

export interface GridEmployee {
  guard_user_id: string
  full_name: string
  designation: string | null
  employment_type: string | null
  role_id: number
  profile_photo_path: string | null
  preferred_shift_type: 'day' | 'night' | null
  /** Keyed by ISO date; a day can hold more than one shift. */
  cells: Record<string, GridCell[]>
  leave_days: string[]
  site_names: string[]
}

export interface RosterGrid {
  start_date: string
  end_date: string
  days: string[]
  employees: GridEmployee[]
  sites: { id: string; name: string; day_guards_required: number; night_guards_required: number }[]
  stats: {
    staff: number
    day_shifts: number
    night_shifts: number
    days_off: number
    required_posts: number
    filled_posts: number
    coverage_pct: number
  }
}

export const getRosterGrid = (startDate: string, endDate: string, siteId?: string) =>
  apiClient
    .get<RosterGrid>('/api/v1/roster/grid', {
      params: { start_date: startDate, end_date: endDate, site_id: siteId || undefined },
    })
    .then((r) => r.data)

export const assignGridCell = (data: {
  guard_user_id: string
  site_id: string
  shift_definition_id: string
  on_date: string
}) => apiClient.post('/api/v1/roster/grid/assign', data).then((r) => r.data)

export const clearGridCell = (shiftId: string) =>
  apiClient.delete(`/api/v1/roster/grid/assign/${shiftId}`).then((r) => r.data)

export type DayPattern = 'all' | 'weekdays' | 'alternate'

export interface BulkAssignResult {
  shift_name: string
  site_name: string
  days_targeted: number
  staff_targeted: number
  created: number
  skipped_on_leave: number
  skipped_clash: number
  per_guard: {
    guard_user_id: string
    full_name: string
    created: number
    skipped_on_leave: number
    skipped_clash: number
  }[]
}

/** Skips rather than fails: leave and existing shifts are expected collisions,
 *  and the result says exactly who was skipped and why. */
export const bulkAssignShifts = (data: {
  shift_definition_id: string
  site_id: string
  start_date: string
  end_date: string
  day_pattern: DayPattern
  guard_user_ids?: string[]
}) => apiClient.post<BulkAssignResult>('/api/v1/roster/grid/bulk-assign', data).then((r) => r.data)

// ── Auto-schedule rules ───────────────────────────────────────────────────────

export type ShiftPatternMode = 'rotation' | 'day_only' | 'night_only'

/** Every field optional. Omitted keeps the scheduler default; a null on a
 *  numeric rule turns that rule OFF, which is not the same as zero. */
export interface AutoScheduleRules {
  shift_pattern?: ShiftPatternMode
  fair_rotation?: boolean
  min_rest_hours?: number | null
  max_consecutive_days?: number | null
  max_night_shifts_per_period?: number | null
  max_off_days_per_period?: number | null
  min_headcount?: number | null
  honour_preferences?: boolean
  respect_leave?: boolean
  overwrite_existing?: boolean
}

import { apiClient } from './client'

// Query-param-JWT auth (same pattern as LiveWall's MJPEG stream URLs) — a
// plain <img src> can't set an Authorization header, so the token travels
// in the URL. Returns null if not authenticated (caller should skip render).
/** A guard's permanent profile photo. Same query-param-JWT reasoning as the
 *  check-in photo below: an <img> cannot send an Authorization header. */
export const profilePhotoUrl = (userId: string, token: string | null) =>
  token ? `${apiClient.defaults.baseURL}/api/v1/users/${userId}/photo?token=${token}` : null

export const checkinPhotoUrl = (shiftId: string, which: 'check_in' | 'check_out', token: string | null) =>
  token ? `${apiClient.defaults.baseURL}/api/v1/shifts/${shiftId}/photo/${which}?token=${token}` : null

export interface LiveAttendanceShift {
  id: string
  guard_user_id: string
  site_id: string | null
  scheduled_start: string
  scheduled_end: string
  actual_start: string | null
  actual_end: string | null
  status: string
  is_late: boolean
  late_minutes: number | null
  overtime_minutes: number | null
  is_within_geofence: boolean | null
  check_in_photo_path: string | null
  check_out_photo_path: string | null
  check_in_liveness_score: number | null
  check_out_liveness_score: number | null
  check_in_is_mock_location: boolean | null
  check_out_is_mock_location: boolean | null
  on_break: boolean
  guard_name: string | null
  guard_phone: string | null
  site_name: string | null
  shift_type: 'day' | 'night' | 'split' | null
  employment_type: 'full_time' | 'part_time' | 'contract' | null
  designation: string | null
  on_leave: boolean
  leave_reason: string | null
  /** Permanent photo, shown until this shift's own check-in selfie exists. */
  profile_photo_path: string | null
  /** Grace actually applied to this row — the site's override, or the tenant
   *  default. Surfaced so the board can explain why someone counts as late. */
  effective_grace_minutes: number
  /** The board's state ladder. Richer than live_status, which stays for the
   *  Command Centre and Site Map — see services/attendance_status.py. */
  monitor_status: MonitorStatus
  live_status: 'not_started' | 'late' | 'checked_in' | 'on_break' | 'checked_out'
}

export type MonitorStatus =
  | 'on_leave' | 'not_yet_on_duty' | 'awaiting' | 'on_time'
  | 'late' | 'on_break' | 'not_reported' | 'checked_out'

/** The eight company-wide figures across the top of the board. */
export interface LiveAttendanceSummary {
  rostered: number
  checked_in: number
  reported: number
  late: number
  not_reported: number
  on_leave: number
  not_yet_on_duty: number
  on_duty: number
}

export interface LiveAttendanceSiteCounts {
  rostered: number
  checked_in: number
  late: number
  not_reported: number
  on_leave: number
  not_yet_on_duty: number
}

export interface LiveAttendanceSite {
  site_id: string | null
  site_name: string
  counts: LiveAttendanceSiteCounts
  guards: LiveAttendanceShift[]
}

export interface LiveAttendanceBoard {
  /** Server clock at the moment the board was built — drives "Last updated"
   *  so a frozen dashboard is visibly frozen rather than quietly wrong. */
  generated_at: string
  grace_minutes: number
  summary: LiveAttendanceSummary
  sites: LiveAttendanceSite[]
  shifts: LiveAttendanceShift[]
}

export const getLiveAttendance = (siteId?: string) =>
  apiClient
    .get<LiveAttendanceBoard>('/api/v1/attendance/live', { params: { site_id: siteId } })
    .then((r) => r.data)

export interface RecentAttendanceRow {
  id: string
  scheduled_start: string
  scheduled_end: string
  actual_start: string | null
  actual_end: string | null
  status: string
  is_late: boolean
  late_minutes: number | null
  overtime_minutes: number | null
  site_name: string | null
}

export const getGuardRecentAttendance = (guardUserId: string, limit = 7) =>
  apiClient
    .get<RecentAttendanceRow[]>(`/api/v1/attendance/guard/${guardUserId}/recent`, { params: { limit } })
    .then((r) => r.data)

export interface AttendanceCorrection {
  id: string
  shift_id: string
  guard_user_id: string
  requested_check_in: string | null
  requested_check_out: string | null
  reason: string
  status: 'pending' | 'approved' | 'rejected'
  reviewed_by_user_id: string | null
  reviewed_at: string | null
  review_notes: string | null
  created_at: string
  guard_name: string
  scheduled_start: string
  scheduled_end: string
  site_name: string | null
}

export const requestCorrection = (data: {
  shift_id: string
  requested_check_in?: string
  requested_check_out?: string
  reason: string
}) => apiClient.post<AttendanceCorrection>('/api/v1/attendance/corrections', data).then((r) => r.data)

export const listCorrections = (status?: string) =>
  apiClient
    .get<AttendanceCorrection[]>('/api/v1/attendance/corrections', { params: { correction_status: status } })
    .then((r) => r.data)

export const approveCorrection = (id: string) =>
  apiClient.put(`/api/v1/attendance/corrections/${id}/approve`).then((r) => r.data)

export const rejectCorrection = (id: string, reviewNotes?: string) =>
  apiClient
    .put(`/api/v1/attendance/corrections/${id}/reject`, { review_notes: reviewNotes })
    .then((r) => r.data)

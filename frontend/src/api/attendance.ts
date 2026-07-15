import { apiClient } from './client'

// Query-param-JWT auth (same pattern as LiveWall's MJPEG stream URLs) — a
// plain <img src> can't set an Authorization header, so the token travels
// in the URL. Returns null if not authenticated (caller should skip render).
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
  live_status: 'not_started' | 'late' | 'checked_in' | 'on_break' | 'checked_out'
}

export interface LiveAttendanceSummary {
  checked_in: number
  on_break: number
  late: number
  not_started: number
  checked_out: number
}

export const getLiveAttendance = (siteId?: string) =>
  apiClient
    .get<{ shifts: LiveAttendanceShift[]; summary: LiveAttendanceSummary }>('/api/v1/attendance/live', {
      params: { site_id: siteId },
    })
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

/**
 * Live attendance monitor — supervisor-facing.
 *
 * This is the "who is on duty right now, and who hasn't turned up" view. On a
 * phone it matters most to a supervisor who is away from a desk, which is
 * exactly when someone needs chasing.
 *
 * Read-only here on purpose: corrections and approvals stay on the web, where
 * there is room to review evidence before altering an attendance record.
 *
 * Shapes mirror frontend/src/api/attendance.ts.
 */
import { apiClient } from './client'

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
  check_in_is_mock_location: boolean | null
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

export interface LiveAttendanceResponse {
  shifts: LiveAttendanceShift[]
  summary: LiveAttendanceSummary
}

export const getLiveAttendance = (siteId?: string) =>
  apiClient
    .get<LiveAttendanceResponse>('/api/v1/attendance/live', {
      params: siteId ? { site_id: siteId } : undefined,
    })
    .then((r) => r.data)

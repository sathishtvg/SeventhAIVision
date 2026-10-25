/**
 * Overtime requests (docs/plans/2026-10-25-roster-autoclose-overtime-design.md): a guard asks for extra hours on
 * their own shift; a supervisor or admin approves or rejects. Only approved overtime is paid, up to the time
 * actually worked past the planned end.
 */
import { apiClient } from './client'

export type OvertimeStatus = 'pending' | 'approved' | 'rejected'

export interface OvertimeRequest {
  id: string
  shift_id: string
  guard_user_id: string
  guard_name: string | null
  hours_requested: number
  reason: string
  status: OvertimeStatus
  decided_by_name: string | null
  decided_at: string | null
  decision_note: string | null
  created_at: string
  site_name: string | null
  scheduled_start: string
  scheduled_end: string
  actual_start: string | null
  actual_end: string | null
  shift_status: string
  auto_closed_at: string | null
}

export interface UnapprovedExtra {
  shift_id: string
  guard_name: string | null
  site_name: string | null
  scheduled_end: string
  actual_end: string
  auto_closed_at: string | null
  approved_hours: number
  unapproved_extra_minutes: number
}

export const getOvertimeRequests = (requestStatus?: OvertimeStatus) =>
  apiClient.get<OvertimeRequest[]>('/api/v1/overtime-requests', { params: { request_status: requestStatus } })
    .then((r) => r.data)

export const approveOvertime = (id: string) =>
  apiClient.post<OvertimeRequest>(`/api/v1/overtime-requests/${id}/approve`).then((r) => r.data)

export const rejectOvertime = (id: string, note: string) =>
  apiClient.post<OvertimeRequest>(`/api/v1/overtime-requests/${id}/reject`, { note }).then((r) => r.data)

export const getUnapprovedExtra = (days = 14) =>
  apiClient.get<UnapprovedExtra[]>('/api/v1/overtime-requests/unapproved-extra', { params: { days } })
    .then((r) => r.data)

/**
 * Asking for overtime (docs/plans/2026-10-25-roster-autoclose-overtime-design.md, section 3).
 *
 * A guard asks for extra hours, with a reason, on their own shift: while it is on, or up to 48 hours after it
 * ended. A supervisor or admin approves or rejects. While a request is waiting or approved, the shift's automatic
 * close (15 hours after check-in, by default) moves later by those hours. Only approved overtime is paid.
 */
import { apiClient } from './client'
import type { Shift } from './patrols'

export type OvertimeStatus = 'pending' | 'approved' | 'rejected'

export interface OvertimeRequest {
  id: string
  shift_id: string
  hours_requested: number
  reason: string
  status: OvertimeStatus
  decision_note: string | null
  decided_by_name: string | null
  created_at: string
}

/** How long after a shift ends the server still accepts a request. */
export const ASK_AFTER_END_HOURS = 48
export const MIN_HOURS = 0.5
export const MAX_HOURS = 12

export const askOvertime = (shiftId: string, hours: number, reason: string) =>
  apiClient
    .post<OvertimeRequest>('/api/v1/overtime-requests', { shift_id: shiftId, hours, reason })
    .then((r) => r.data)

export const getMyOvertimeRequests = (shiftId: string) =>
  apiClient
    .get<OvertimeRequest[]>('/api/v1/overtime-requests', { params: { shift_id: shiftId } })
    .then((r) => r.data)

/** Whether this shift can still be asked about: on now, or ended within the last 48 hours. */
export function canAskOvertime(shift: Pick<Shift, 'status' | 'actual_end'>, now: Date = new Date()): boolean {
  if (shift.status === 'active') return true
  if (shift.status !== 'completed' || !shift.actual_end) return false
  return +now - +new Date(shift.actual_end) <= ASK_AFTER_END_HOURS * 3_600_000
}

/** One step of the hours chooser, kept within what the server accepts. */
export function stepHours(hours: number, delta: number): number {
  return Math.min(MAX_HOURS, Math.max(MIN_HOURS, Math.round((hours + delta) * 2) / 2))
}

/** A line for the guard about the latest request on a shift, or null when there is none. */
export function overtimeStatusLine(requests: OvertimeRequest[] | undefined): string | null {
  const latest = requests?.[0]
  if (!latest) return null
  if (latest.status === 'pending') return `Overtime ${latest.hours_requested} h asked, waiting for your supervisor.`
  if (latest.status === 'approved') return `Overtime ${latest.hours_requested} h approved.`
  return `Overtime ${latest.hours_requested} h not approved${latest.decision_note ? `: ${latest.decision_note}` : '.'}`
}

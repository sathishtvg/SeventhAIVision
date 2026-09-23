import { apiClient } from './client'

export interface LeaveType {
  id: string
  name: string
  default_annual_days: number
  requires_document: boolean
  is_active: boolean
}

export type LeaveRequestStatus = 'pending' | 'approved' | 'rejected' | 'cancelled'

export interface LeaveRequest {
  id: string
  guard_user_id: string
  leave_type_id: string
  start_date: string
  end_date: string
  days_count: number
  reason: string | null
  status: LeaveRequestStatus
  created_at: string
  leave_type_name: string
}

export interface LeaveBalanceRow {
  leave_type_id: string
  name: string
  year: number
  entitled_days: number
  used_days: number
  remaining_days: number
}

export const getLeaveTypes = () =>
  apiClient.get<LeaveType[]>('/api/v1/leave/types').then((r) => r.data)

// Self-scoped server-side for guard-tier roles (leave.py:153-155) — a
// guard token only ever sees its own requests.
export const getMyLeaveRequests = () =>
  apiClient.get<LeaveRequest[]>('/api/v1/leave/requests').then((r) => r.data)

export const getMyLeaveBalances = () =>
  apiClient.get<LeaveBalanceRow[]>('/api/v1/leave/balances').then((r) => r.data)

export const createLeaveRequest = (data: {
  guard_user_id: string
  leave_type_id: string
  start_date: string
  end_date: string
  reason?: string
}) => apiClient.post('/api/v1/leave/requests', data).then((r) => r.data)

export const cancelLeaveRequest = (id: string) =>
  apiClient.put(`/api/v1/leave/requests/${id}/cancel`).then((r) => r.data)


/**
 * Attach the certificate to a request that already exists.
 *
 * Two steps, because that is the shape the server offers: POST /requests
 * creates it, POST /requests/{id}/document attaches the file. The upload is
 * multipart; React Native builds the part from the local file uri.
 */
export const uploadLeaveDocument = (
  requestId: string, file: { uri: string; name: string; mimeType: string },
) => {
  const form = new FormData()
  // The cast is the documented React Native shape for a file part; the DOM's
  // FormData types do not describe it.
  form.append('file', { uri: file.uri, name: file.name, type: file.mimeType } as any)
  return apiClient
    .post(`/api/v1/leave/requests/${requestId}/document`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60_000,   // a photographed certificate over a site's wifi
    })
    .then((r) => r.data)
}

/**
 * Why this leave application cannot be submitted yet, or null when it can.
 *
 * Pure, and the reason is a sentence rather than a boolean, because the old
 * form disabled Submit and said nothing — a guard with a bad date sat looking
 * at a grey button with no idea which field was wrong.
 */
export function leaveRequestBlocker(args: {
  type: LeaveType | undefined
  startDate: string
  endDate: string
  attachment: unknown | null
}): string | null {
  const { type, startDate, endDate, attachment } = args
  if (!type) return 'Choose a leave type.'
  if (!startDate) return 'Choose a start date.'
  if (!endDate) return 'Choose an end date.'
  if (endDate < startDate) return 'The end date is before the start date.'
  // requires_document is the tenant's own flag on the leave type — not a
  // match on the word "medical", which would break for an agency that calls
  // it sick leave.
  if (type.requires_document && !attachment) {
    return `${type.name} needs a supporting document attached.`
  }
  return null
}

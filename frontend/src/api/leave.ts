import { apiClient } from './client'

export interface LeaveType {
  id: string
  name: string
  default_annual_days: number
  requires_document: boolean
  is_active: boolean
  created_at: string
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
  document_path: string | null
  status: LeaveRequestStatus
  reviewed_by_user_id: string | null
  reviewed_at: string | null
  review_notes: string | null
  created_at: string
  guard_name: string
  leave_type_name: string
}

export interface AffectedShift {
  id: string
  scheduled_start: string
  scheduled_end: string
  site_name: string | null
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

export const createLeaveType = (data: { name: string; default_annual_days?: number; requires_document?: boolean }) =>
  apiClient.post<LeaveType>('/api/v1/leave/types', data).then((r) => r.data)

export const updateLeaveType = (id: string, data: Partial<{
  name: string; default_annual_days: number; requires_document: boolean; is_active: boolean
}>) => apiClient.put<LeaveType>(`/api/v1/leave/types/${id}`, data).then((r) => r.data)

export const deleteLeaveType = (id: string) =>
  apiClient.delete(`/api/v1/leave/types/${id}`).then((r) => r.data)

export const listLeaveRequests = (filters?: {
  guard_user_id?: string
  leave_type_id?: string
  request_status?: string
}) => apiClient.get<LeaveRequest[]>('/api/v1/leave/requests', { params: filters }).then((r) => r.data)

export const createLeaveRequest = (data: {
  guard_user_id: string
  leave_type_id: string
  start_date: string
  end_date: string
  reason?: string
}) => apiClient.post('/api/v1/leave/requests', data).then((r) => r.data)

export const uploadLeaveDocument = (requestId: string, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return apiClient
    .post(`/api/v1/leave/requests/${requestId}/document`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data)
}

export const approveLeaveRequest = (id: string) =>
  apiClient
    .put<{ id: string; status: string; affected_shifts: AffectedShift[] }>(`/api/v1/leave/requests/${id}/approve`)
    .then((r) => r.data)

export const rejectLeaveRequest = (id: string, reviewNotes?: string) =>
  apiClient.put(`/api/v1/leave/requests/${id}/reject`, { review_notes: reviewNotes }).then((r) => r.data)

export const cancelLeaveRequest = (id: string) =>
  apiClient.put(`/api/v1/leave/requests/${id}/cancel`).then((r) => r.data)

export const getLeaveBalances = (guardUserId?: string, year?: number) =>
  apiClient
    .get<LeaveBalanceRow[]>('/api/v1/leave/balances', { params: { guard_user_id: guardUserId, year } })
    .then((r) => r.data)

export const setLeaveBalance = (data: { guard_user_id: string; leave_type_id: string; year: number; entitled_days: number }) =>
  apiClient.put('/api/v1/leave/balances', data).then((r) => r.data)

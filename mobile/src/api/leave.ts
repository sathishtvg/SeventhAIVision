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

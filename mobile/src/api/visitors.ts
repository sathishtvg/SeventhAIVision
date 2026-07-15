import { apiClient } from './client'

export interface Visitor {
  id: string
  full_name: string
  id_number: string | null
  company: string | null
  host_name: string | null
  host_user_name: string | null
  purpose: string | null
  vehicle_plate: string | null
  site_id: string | null
  site_name: string | null
  status: 'pending' | 'arrived' | 'departed' | 'cancelled' | 'expired'
  visitor_email: string | null
  qr_token: string | null
  expected_from: string | null
  expected_until: string | null
  created_at: string
}

export interface VisitorLog {
  id: string
  visitor_id: string
  event_type: 'arrival' | 'departure' | 'denied'
  checked_in_by: string | null
  badge_number: string | null
  notes: string | null
  occurred_at: string
}

export interface VisitorCreate {
  full_name: string
  id_number?: string
  company?: string
  host_name?: string
  purpose?: string
  vehicle_plate?: string
  site_id?: string
  visitor_email?: string
  expected_from?: string
  expected_until?: string
}

export interface PaginatedVisitors {
  items: Visitor[]
  total: number
  limit: number
  offset: number
  has_more: boolean
}

export const getVisitors = (params?: {
  status?: string
  site_id?: string
  limit?: number
  offset?: number
}) =>
  apiClient
    .get<PaginatedVisitors>('/api/v1/visitors', { params: { limit: 50, offset: 0, ...params } })
    .then((r) => r.data)

export const getUpcomingVisitors = (hours = 24, site_id?: string) =>
  apiClient
    .get<Visitor[]>('/api/v1/visitors/upcoming', { params: { hours, site_id } })
    .then((r) => r.data)

export const createVisitor = (body: VisitorCreate) =>
  apiClient.post<Visitor>('/api/v1/visitors', body).then((r) => r.data)

export const checkinVisitor = (visitor_id: string) =>
  apiClient.post<VisitorLog>(`/api/v1/visitors/${visitor_id}/checkin`, {}).then((r) => r.data)

export const checkoutVisitor = (visitor_id: string) =>
  apiClient.post<VisitorLog>(`/api/v1/visitors/${visitor_id}/checkout`, {}).then((r) => r.data)

export const qrScanCheckin = (body: { qr_token: string; badge_number?: string; notes?: string }) =>
  apiClient.post<{ event_type: string; visitor: Visitor }>('/api/v1/visitors/qr-scan', body).then((r) => r.data)

export const sendVisitorQR = (visitor_id: string) =>
  apiClient.post<{ ok: boolean; email: string }>(`/api/v1/visitors/${visitor_id}/send-qr`).then((r) => r.data)

export const getVisitorLogs = (params?: { visitor_id?: string; site_id?: string; limit?: number }) =>
  apiClient
    .get<VisitorLog[]>('/api/v1/visitors/logs', { params: { limit: 50, ...params } })
    .then((r) => r.data)

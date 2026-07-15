import { apiClient as client } from './client'

export interface Visitor {
  id: string
  full_name: string
  id_number?: string
  company?: string
  host_user_id?: string
  host_name?: string
  purpose?: string
  vehicle_plate?: string
  site_id?: string
  site_name?: string
  expected_from?: string
  expected_until?: string
  visitor_email?: string
  status: string
  qr_token: string
  qr_email_sent_at?: string
  is_active: boolean
  created_at: string
}

export interface VisitorLog {
  id: string
  visitor_id: string
  visitor_name?: string
  site_id?: string
  site_name?: string
  guard_user_id?: string
  guard_name?: string
  event_type: 'arrival' | 'departure'
  badge_number?: string
  notes?: string
  checkin_method: 'manual' | 'qr_scan'
  qr_token_used?: string
  occurred_at: string
}

export interface VisitorCreate {
  full_name: string
  id_number?: string
  company?: string
  host_user_id?: string
  host_name?: string
  purpose?: string
  vehicle_plate?: string
  site_id?: string
  expected_from?: string
  expected_until?: string
  visitor_email?: string
}

export interface QRScanCheckin {
  qr_token: string
  badge_number?: string
  notes?: string
}

export const listVisitors = () =>
  client.get<{ items: Visitor[] }>('/api/v1/visitors').then(r => r.data.items)

export const getVisitor = (id: string) =>
  client.get<Visitor>(`/api/v1/visitors/${id}`).then(r => r.data)

export const createVisitor = (data: VisitorCreate) =>
  client.post<Visitor>('/api/v1/visitors', data).then(r => r.data)

export const listVisitorLogs = (params?: { site_id?: string; visitor_id?: string }) =>
  client.get<VisitorLog[]>('/api/v1/visitors/logs', { params }).then(r => r.data)

export const checkinVisitor = (visitor_id: string, data: { badge_number?: string; notes?: string }) =>
  client.post(`/api/v1/visitors/${visitor_id}/checkin`, data).then(r => r.data)

export const checkoutVisitor = (visitor_id: string, data: { notes?: string }) =>
  client.post(`/api/v1/visitors/${visitor_id}/checkout`, data).then(r => r.data)

export const deactivateVisitor = (visitor_id: string) =>
  client.delete(`/api/v1/visitors/${visitor_id}`).then(r => r.data)

export const getUpcomingVisitors = (hours = 24, site_id?: string) =>
  client.get<Visitor[]>('/api/v1/visitors/upcoming', { params: { hours, site_id } }).then(r => r.data)

export const getVisitorByQR = (qr_token: string) =>
  client.get<Visitor>(`/api/v1/visitors/by-qr/${qr_token}`).then(r => r.data)

export const qrScanCheckin = (data: QRScanCheckin) =>
  client.post('/api/v1/visitors/qr-scan', data).then(r => r.data)

export const sendVisitorQR = (visitor_id: string) =>
  client.post(`/api/v1/visitors/${visitor_id}/send-qr`).then(r => r.data)

export const getVisitorQRUrl = (visitor_id: string) =>
  `${client.defaults.baseURL}/api/v1/visitors/${visitor_id}/qr.png`

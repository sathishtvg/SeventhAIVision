import { apiClient } from './client'

export interface DispatchRecord {
  id: string
  incident_id: string
  dispatched_guard_id: string
  dispatched_guard_name: string | null
  dispatched_by_id: string
  eta_minutes: number | null
  notes: string | null
  status: 'dispatched' | 'arrived' | 'cancelled'
  dispatched_at: string
  arrived_at: string | null
}

export const dispatchToIncident = (incidentId: string, body: {
  guard_user_id: string
  eta_minutes?: number
  notes?: string
}) =>
  apiClient
    .post<DispatchRecord>(`/api/v1/dispatch/incidents/${incidentId}`, body)
    .then((r) => r.data)

export const markArrived = (incidentId: string) =>
  apiClient
    .post<DispatchRecord>(`/api/v1/dispatch/incidents/${incidentId}/arrived`)
    .then((r) => r.data)

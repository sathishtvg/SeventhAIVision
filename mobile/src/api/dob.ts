import { apiClient } from './client'

export interface DOBEntry {
  id: string
  entry_type: string
  body: string
  severity: string | null
  occurred_at: string
  site_id: string | null
  site_name: string | null
  shift_id: string | null
  related_alert_id: string | null
  related_incident_id: string | null
  author_name: string | null
  author_email: string | null
}

export interface DOBEntryCreate {
  entry_type?: string
  body: string
  severity?: string
  site_id?: string
  shift_id?: string
  related_alert_id?: string
  related_incident_id?: string
}

export const ENTRY_TYPES = [
  'general', 'incident', 'patrol_start', 'patrol_end',
  'visitor_arrival', 'visitor_departure', 'guard_relief',
  'equipment_check', 'maintenance', 'alarm_activation',
  'fire_drill', 'handover',
] as const

export const getDOBEntries = (params?: {
  site_id?: string
  shift_id?: string
  entry_type?: string
  limit?: number
  offset?: number
}) =>
  apiClient
    .get<DOBEntry[]>('/api/v1/dob', { params })
    .then((r) => r.data)

export const getDOBEntry = (entryId: string) =>
  apiClient.get<DOBEntry>(`/api/v1/dob/${entryId}`).then((r) => r.data)

export const createDOBEntry = (data: DOBEntryCreate) =>
  apiClient.post<DOBEntry>('/api/v1/dob', data).then((r) => r.data)

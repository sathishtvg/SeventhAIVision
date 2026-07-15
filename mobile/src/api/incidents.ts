import { apiClient } from './client'

export type IncidentStatus =
  | 'open'
  | 'dispatched'
  | 'en_route'
  | 'on_scene'
  | 'contained'
  | 'investigating'
  | 'resolved'
  | 'closed'

export interface Incident {
  id: string
  camera_id: string
  camera_name?: string
  site_name?: string
  title: string
  description: string | null
  severity: string
  status: IncidentStatus
  is_auto_created: boolean
  resolved_at: string | null
  created_at: string
  updated_at: string
}

export interface IncidentNote {
  id: string
  incident_id: string
  author_user_id: string | null
  note: string
  created_at: string
}

export const getIncidents = (status?: string, siteId?: string, moduleType?: string) => {
  const params: Record<string, string> = {}
  if (status) params.status = status
  if (siteId) params.site_id = siteId
  if (moduleType) params.module_type = moduleType
  return apiClient.get<Incident[]>('/api/v1/incidents', { params }).then((r) => r.data)
}

export const getIncidentNotes = (id: string) =>
  apiClient.get<IncidentNote[]>(`/api/v1/incidents/${id}/notes`).then((r) => r.data)

export const addIncidentNote = (id: string, note: string) =>
  apiClient.post(`/api/v1/incidents/${id}/notes`, { note }).then((r) => r.data)

export const updateIncidentStatus = (
  id: string,
  status: IncidentStatus,
  notes?: string,
  latitude?: number,
  longitude?: number,
) =>
  apiClient
    .put(`/api/v1/incidents/${id}/status`, { status, notes, latitude, longitude })
    .then((r) => r.data)

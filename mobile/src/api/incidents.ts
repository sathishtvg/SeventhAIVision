import { apiClient, rows } from './client'

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
  /** From the timeline, which names the person rather than giving their id. */
  author_name?: string | null
  note: string
  created_at: string
}

export const getIncidents = (status?: string, siteId?: string, moduleType?: string) => {
  const params: Record<string, string> = {}
  // status_filter, not status — see the note in alerts.ts.
  if (status) params.status_filter = status
  if (siteId) params.site_id = siteId
  if (moduleType) params.module_type = moduleType
  return apiClient
    .get<{ items: Incident[] }>('/api/v1/incidents', { params })
    .then((r) => rows(r.data))
}

/** One entry of the incident timeline: a note, or a status change. */
interface TimelineEntry {
  type: 'note' | 'status_change'
  occurred_at: string
  notes: string | null
  actor_name: string | null
  from_status?: string | null
  to_status?: string | null
}

/**
 * The notes on an incident.
 *
 * Read from /timeline, which is where the server keeps them. There is no GET on
 * /incidents/{id}/notes -- only a POST to add one -- so this screen asked for a
 * route that answers 405 and showed "No notes yet" however many notes existed.
 */
export const getIncidentNotes = (id: string) =>
  apiClient
    .get<TimelineEntry[]>(`/api/v1/incidents/${id}/timeline`)
    .then((r) => (r.data ?? [])
      .filter((e) => e.type === 'note')
      .map((e, i): IncidentNote => ({
        id: `${id}-note-${i}`,          // the timeline is a view; entries carry no id
        incident_id: id,
        author_user_id: null,
        author_name: e.actor_name,
        note: e.notes ?? '',
        created_at: e.occurred_at,
      })))

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

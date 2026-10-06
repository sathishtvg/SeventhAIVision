import { apiClient } from './client'

/** What the server answers when a guard is dispatched: the incident's id and
 *  how it now stands. */
export interface DispatchRecord {
  id: string
  dispatched_guard_id: string
  dispatched_at: string
  sla_deadline_at: string | null
  status: string
}

/**
 * Send a named guard to an incident.
 *
 * The body is the server's DispatchBody and nothing else: the guard, and one
 * text of notes. This used to send `eta_minutes` and `notes`, neither of which
 * the server has a field for — so every note typed on the phone was dropped —
 * and an empty `guard_user_id`, which the server cannot store, so every
 * dispatch from the phone failed.
 */
export const dispatchToIncident = (incidentId: string, body: {
  guard_user_id: string
  dispatch_notes?: string
}) =>
  apiClient
    .post<DispatchRecord>(`/api/v1/dispatch/incidents/${incidentId}`, body)
    .then((r) => r.data)

export const markArrived = (incidentId: string) =>
  apiClient
    .post<DispatchRecord>(`/api/v1/dispatch/incidents/${incidentId}/arrived`)
    .then((r) => r.data)

/**
 * Guard response: what a guard does on a dispatch they were sent on
 * (backend/app/routers/incident_responses.py).
 *
 * The phone answers for a sending; it does not make one. Each step is the
 * guard's own act, as a signed-in person, and the server writes it through to
 * the incident's status, its history and its arrival time.
 */
import { apiClient } from './client'

const BASE = '/api/v1/incident-responses'

export type ResponseState = 'SENT' | 'ACCEPTED' | 'DECLINED' | 'EN_ROUTE' | 'ARRIVED' | 'STOOD_DOWN'

export interface ResponseRecord {
  id: string
  incident_id: string
  guard_user_id: string | null
  guard_name: string | null
  dispatched_at: string
  state: ResponseState
  accepted_at: string | null
  en_route_at: string | null
  arrived_at: string | null
}

/** What this person may do next on the response. The server decides. */
export interface May {
  accept: boolean; decline: boolean; en_route: boolean; arrived: boolean; report: boolean; stand_down: boolean
}

export interface Clock {
  started_at: string | null
  /** Null when no time to arrive is set for this severity. */
  due_at: string | null
  met_at: string | null
  breached: boolean
  running: boolean
  seconds_left: number | null
}

/** One incident the caller has been sent on and is still on. */
export interface MySending {
  id: string
  title: string
  description: string | null
  severity: string
  status: string
  site_name: string | null
  camera_name: string | null
  camera_location: string | null
  dispatched_at: string
  /** What whoever sent them wrote for them. */
  dispatch_notes: string | null
  response: ResponseRecord
  arrival: Clock
  may: May
}

export interface Position { latitude: number; longitude: number }

/** A step's body: a note if there is one, and where it was said from if the phone would say. */
const said = (note?: string, at?: Position) => ({ ...(note ? { note } : {}), ...(at ?? {}) })

export const getMySendings = () =>
  apiClient.get<{ items: MySending[]; as_of: string }>(`${BASE}/mine`).then((r) => r.data)

export const acceptResponse = (incidentId: string, at?: Position) =>
  apiClient.post(`${BASE}/${incidentId}/accept`, said(undefined, at)).then((r) => r.data)

/** Not coming, and why. The reason is required: the desk has to send somebody else. */
export const declineResponse = (incidentId: string, reason: string, at?: Position) =>
  apiClient.post(`${BASE}/${incidentId}/decline`, { reason, ...(at ?? {}) }).then((r) => r.data)

export const setOff = (incidentId: string, at?: Position) =>
  apiClient.post(`${BASE}/${incidentId}/en-route`, said(undefined, at)).then((r) => r.data)

export const arrivedAt = (incidentId: string, at?: Position) =>
  apiClient.post(`${BASE}/${incidentId}/arrived`, said(undefined, at)).then((r) => r.data)

/** What was found, said from the ground. */
export const reportFromGround = (incidentId: string, note: string, at?: Position) =>
  apiClient.post(`${BASE}/${incidentId}/report`, { note, ...(at ?? {}) }).then((r) => r.data)

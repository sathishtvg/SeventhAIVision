/**
 * Drone Patrol on the phone: the events a drone raised, their media, and what
 * an officer can do about one from the field.
 *
 * The planning and flying screens stay on the web — a route is not drawn with a
 * thumb. What belongs in a pocket is the other half: something was seen, here
 * is the picture, acknowledge it, escalate it, send someone.
 *
 * Every path and field here mirrors backend/app/routers/drone_operations.py.
 * The repository test backend/tests/test_drone_clients.py fails if a path in
 * this file stops existing on the server.
 */
import { apiClient, rows } from './client'

export type RiskLevel = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export type DroneEventStatus =
  | 'NEW' | 'ACKNOWLEDGED' | 'INVESTIGATING' | 'ESCALATED' | 'RESOLVED' | 'FALSE_POSITIVE'
export type DroneDecision = 'acknowledge' | 'investigate' | 'escalate' | 'resolve' | 'false-positive'

/** Statuses an event can still be acted on in. */
export const OPEN_STATUSES: DroneEventStatus[] = ['NEW', 'ACKNOWLEDGED', 'INVESTIGATING', 'ESCALATED']

export interface DroneEvent {
  id: string
  site_id: string | null
  site_name: string | null
  session_id: string | null
  session_number: string | null
  mission_name: string | null
  drone_name: string | null
  drone_code: string | null
  module_type: string
  label: string | null
  detected_at: string
  detection_count: number
  observed_seconds: number | null
  drone_latitude: number | null
  drone_longitude: number | null
  location_method: string
  zone_name: string | null
  zone_type: string | null
  /** 0–1. The AI's certainty about WHAT it saw — not how serious it is. */
  ai_confidence: number | null
  /** 0–100. How serious, given where and when. A different number on purpose. */
  risk_score: number | null
  risk_level: RiskLevel
  risk_factors: { factor: string; points: number; detail: string }[]
  verification_state: string
  status: DroneEventStatus
  alert_id: string | null
  incident_id: string | null
  resolved_by_name: string | null
  false_positive_reason: string | null
}

export interface DroneMedia {
  id: string
  media_kind: string
  /** 'local' = still on the site's gateway; the file endpoint answers 409. */
  storage_location: string
  sync_state: string
  captured_at: string
  size_bytes: number | null
}

export interface DroneEventDetail extends DroneEvent {
  media: DroneMedia[]
  incident: { id: string; title: string; severity: string; status: string } | null
  alert: { id: string; title: string; severity: string; status: string } | null
}

export interface DroneEventCard {
  event_id: string
  headline: string
  detected_at_site_time: string
  incident: {
    id: string
    title: string
    severity: string
    status: string
    incident_ref: string | null
    dispatched_guard_name: string | null
    dispatched_at: string | null
    guard_arrived_at: string | null
  } | null
}

export interface GuardOption {
  user_id: string
  full_name: string
  available: boolean
  distance_m: number | null
  position_source: string | null
  position_age_s: number | null
}

export interface DispatchResult {
  incident_id: string
  incident_created: boolean
  guard: { user_id: string; full_name: string }
}

export const getDroneEvents = (opts: { openOnly?: boolean; siteId?: string; riskLevel?: RiskLevel } = {}) => {
  const params: Record<string, string | number | boolean> = { limit: 100 }
  if (opts.openOnly) params.open_only = true
  if (opts.siteId) params.site_id = opts.siteId
  if (opts.riskLevel) params.risk_level = opts.riskLevel
  return apiClient
    .get<{ items: DroneEvent[] }>('/api/v1/drone-events', { params })
    .then((r) => rows(r.data))
}

/**
 * The drone event an alert was raised for, or null if it was not a drone's.
 *
 * The alert list carries no link to the event, so the alert screen asks by
 * alert id rather than guessing from the title.
 */
export const findDroneEventForAlert = (alertId: string) =>
  apiClient
    .get<{ items: DroneEvent[] }>('/api/v1/drone-events', { params: { alert_id: alertId, limit: 1 } })
    .then((r) => rows(r.data)[0] ?? null)

export const getDroneEvent = (id: string) =>
  apiClient.get<DroneEventDetail>(`/api/v1/drone-events/${id}`).then((r) => r.data)

export const getDroneEventCard = (id: string) =>
  apiClient.get<DroneEventCard>(`/api/v1/drone-events/${id}/card`).then((r) => r.data)

/**
 * Move an event on. A false positive needs a reason — the server refuses one
 * without it (422), and so does this function before spending the round trip.
 */
export const decideDroneEvent = (id: string, decision: DroneDecision, text?: string) => {
  const note = text?.trim()
  if (decision === 'false-positive') {
    if (!note || note.length < 3) return Promise.reject(new Error('Say why it is a false positive.'))
    return apiClient.post(`/api/v1/drone-events/${id}/false-positive`, { reason: note }).then((r) => r.data)
  }
  return apiClient.post(`/api/v1/drone-events/${id}/${decision}`, { note: note || null }).then((r) => r.data)
}

export const openDroneIncident = (id: string, reason?: string) =>
  apiClient
    .post<{ incident: { id: string; incident_ref: string | null }; created: boolean }>(
      `/api/v1/drone-events/${id}/incident`, { reason: reason?.trim() || null })
    .then((r) => r.data)

/** Guards on shift at the event's site: free before busy, nearest first. */
export const getDroneEventGuards = (id: string) =>
  apiClient.get<GuardOption[]>(`/api/v1/drone-events/${id}/guards`).then((r) => r.data ?? [])

/** Send a guard. With no guard named, the server picks the nearest free one. */
export const dispatchDroneGuard = (id: string, guardUserId?: string, notes?: string) =>
  apiClient
    .post<DispatchResult>(`/api/v1/drone-events/${id}/dispatch`, {
      guard_user_id: guardUserId || null, notes: notes?.trim() || null,
    })
    .then((r) => r.data)

// ── Media ────────────────────────────────────────────────────────────────────

/** Absolute URL of a snapshot or clip, against the server this phone is on. */
export const droneMediaUrl = (mediaId: string): string => {
  const base = (apiClient.defaults.baseURL ?? '').replace(/\/$/, '')
  return `${base}/api/v1/drone-media/${mediaId}/file`
}

/** The media endpoint takes its token in a header only, so an <Image> source
 *  and the clip player both pass these. */
export const droneMediaHeaders = (): Record<string, string> => {
  const token = apiClient.defaults.headers.common['Authorization']
  return token ? { Authorization: token as string } : {}
}

/** A readable message from a failed call: the server's own reason when it gave one. */
export function droneApiError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string }
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

/**
 * Smart Investigation API — every call the investigation screens make, typed to
 * what the backend returns (backend/app/routers/investigations.py).
 *
 * Two things are kept apart here as they are on the server: a RECORD, which
 * lives where it always did and is read there each time, and an ENTRY in an
 * investigation, which only refers to one.
 */
import { apiClient } from './client'

const BASE = '/api/v1/investigations'

// ── Vocabularies ─────────────────────────────────────────────────────────────

export type Kind =
  | 'ALERT' | 'INCIDENT' | 'PLATE_READ' | 'FACE_MATCH' | 'DETECTION' | 'ACCESS' | 'VISITOR' | 'OCCURRENCE'
  | 'DRONE' | 'ALARM' | 'PATROL_SCAN' | 'MAN_DOWN' | 'SITUATION' | 'SENSOR'
export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical'
export type RiskLevel = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export interface Paged<T> { items: T[]; total: number; limit: number; offset: number; has_more: boolean }

// ── Search ───────────────────────────────────────────────────────────────────

/** One record, of whatever kind, in the one shape every source is read in. */
export interface Found {
  kind: Kind
  id: string
  occurred_at: string
  site_id: string | null
  site_name: string | null
  camera_id: string | null
  camera_name: string | null
  event_type: string | null
  title: string
  summary: string | null
  severity: string | null
  status: string | null
  subject_kind: 'VEHICLE' | 'PERSON' | null
  /** The plate, or an id. Never a name. */
  subject_ref: string | null
  /** What to call the subject — null when it is not this reader's to see. */
  subject_label: string | null
  confidence: number | null
  risk_level: RiskLevel | null
  staff_user_id: string | null
  detection_id: string | null
  latitude: number | null
  longitude: number | null
}

/** The search that was run. When a phrase was typed, this is what was made of it. */
export interface Query {
  from: string
  to: string
  kinds: Kind[]
  site_ids: string[]
  camera_ids: string[]
  event_types: string[]
  severities: Severity[]
  risk_levels: RiskLevel[]
  plate: string | null
  person: string | null
  staff_user_id: string | null
  text: string | null
}

export interface Understood { words: string; field: string; as: string }
export interface Said { text: string; understood: Understood[]; assumed: string[]; not_understood: string[] }
export interface NotSearched { kind: Kind; label: string; reason: string }

export interface SearchAnswer extends Paged<Found> {
  query: Query
  phrase: Said | null
  searched: Kind[]
  not_searched: NotSearched[]
  /** How many of each kind there are in all, not only on this page. */
  found: Partial<Record<Kind, number>>
  note: string
}

export interface SearchBody {
  phrase?: string
  from?: string
  to?: string
  kinds?: Kind[]
  site_ids?: string[]
  camera_ids?: string[]
  event_types?: string[]
  severities?: Severity[]
  risk_levels?: RiskLevel[]
  plate?: string
  person?: string
  staff_user_id?: string
  text?: string
  limit?: number
  offset?: number
  oldest_first?: boolean
}

export interface Source {
  kind: Kind
  label: string
  permission: string
  may_search: boolean
  /** Searched only when named: there are too many to be in every search. */
  asked_for: boolean
  answers: { camera: boolean; event_type: boolean; severity: boolean; risk_level: boolean; plate: boolean
             person: boolean; staff: boolean }
}

export interface Sources {
  sources: Source[]
  severities: Severity[]
  risk_levels: RiskLevel[]
  max_days: number
  default_hours: number
  phrase: {
    periods: string[]; kinds: Record<string, string[]>; event_types: Record<string, string[]>; severities: string[]
    plates: string[]; people: string[]; words: string[]; places: string; limits: string
  }
  note: string
}

export const getSources = () => apiClient.get<Sources>(`${BASE}/sources`).then((r) => r.data)

export const search = (body: SearchBody) => apiClient.post<SearchAnswer>(`${BASE}/search`, body).then((r) => r.data)

// ── Where was this seen ──────────────────────────────────────────────────────

export interface Leg {
  from_id: string
  to_id: string
  seconds: number
  /** Null when either camera has no position. */
  metres: number | null
  same_camera: boolean
  same_site: boolean
}

export interface Trail {
  subject: { kind: 'VEHICLE'; plate: string } | { kind: 'PERSON'; watchlist_entry_id: string; name: string | null }
  from: string
  to: string
  sightings: Found[]
  legs: Leg[]
  summary: { sightings: number; first_at: string | null; last_at: string | null; cameras: number; sites: number }
  /** False when there were more sightings than one trail returns. */
  complete: boolean
  searched: Kind[]
  not_searched: NotSearched[]
  /** What a sighting is, and is not, evidence of. */
  basis: string
  not_followed: string
}

export const getTrail = (params: { plate?: string; watchlist_entry_id?: string; from?: string; to?: string }) =>
  apiClient.get<Trail>(`${BASE}/trail`, { params }).then((r) => r.data)

// ── Investigations ───────────────────────────────────────────────────────────

export interface Investigation {
  id: string
  investigation_number: string
  title: string
  reason: string
  status: 'OPEN' | 'CLOSED'
  site_id: string | null
  site_name: string | null
  incident_id: string | null
  situation_id: string | null
  opened_by_user_id: string | null
  opened_by_name: string | null
  opened_at: string
  closed_by_user_id: string | null
  closed_by_name: string | null
  closed_at: string | null
  closing_note: string | null
  updated_at: string
}

export interface InvestigationRow extends Investigation { records: number }

/** SHOWN: the record, as this reader may see it. NOT_PERMITTED: a kind of record
 *  this reader may not read. NOT_AVAILABLE: no longer held, or outside their sites. */
export type EntryState = 'SHOWN' | 'NOT_PERMITTED' | 'NOT_AVAILABLE' | 'NOTE'

export interface Entry {
  id: string
  kind: Kind | 'NOTE'
  label: string
  ref_id: string | null
  occurred_at: string
  site_id: string | null
  note: string | null
  added_by_user_id: string | null
  added_by_name: string | null
  added_at: string
  set_aside_at: string | null
  set_aside_by_user_id: string | null
  set_aside_by_name: string | null
  set_aside_reason: string | null
  state: EntryState
  record: Found | null
}

export interface InvestigationFile extends Investigation {
  items: Entry[]
  counts: { records: number; notes: number; set_aside: number; not_shown: number }
  can_manage: boolean
}

export interface Ref { kind: Kind; id: string; occurred_at: string }

export const listInvestigations = (params: {
  status?: 'OPEN' | 'CLOSED'; site_id?: string; mine?: boolean; q?: string; limit?: number; offset?: number
}) => apiClient.get<Paged<InvestigationRow>>(BASE, { params }).then((r) => r.data)

export const openInvestigation = (body: {
  title: string; reason: string; site_id?: string; incident_id?: string; situation_id?: string
}) => apiClient.post<{ id: string; investigation_number: string; records: number }>(BASE, body).then((r) => r.data)

export const getInvestigation = (id: string) => apiClient.get<InvestigationFile>(`${BASE}/${id}`).then((r) => r.data)

export const addRecords = (id: string, records: Ref[], note?: string) =>
  apiClient.post<{ added: { kind: Kind; id: string }[]; already_filed: { kind: Kind; id: string }[] }>(
    `${BASE}/${id}/items`, { records, note: note || undefined }).then((r) => r.data)

export const addNote = (id: string, note: string, occurredAt?: string) =>
  apiClient.post(`${BASE}/${id}/notes`, { note, occurred_at: occurredAt }).then((r) => r.data)

export const setAside = (id: string, itemId: string, reason: string) =>
  apiClient.post(`${BASE}/${id}/items/${itemId}/set-aside`, { reason }).then((r) => r.data)

export const closeInvestigation = (id: string, note: string) =>
  apiClient.post(`${BASE}/${id}/close`, { note }).then((r) => r.data)

export const reopenInvestigation = (id: string, reason: string) =>
  apiClient.post(`${BASE}/${id}/reopen`, { reason }).then((r) => r.data)

// ── Errors ───────────────────────────────────────────────────────────────────

/** What went wrong, in the server's words where it gave any. A phrase that was
 *  not understood comes back with the words that were not. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many searches in a minute. Wait a moment and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  if (d && typeof d === 'object') {
    const o = d as { message?: string; not_understood?: string[] }
    const words = o.not_understood?.length ? ` Not understood: ${o.not_understood.join(', ')}.` : ''
    return `${o.message ?? 'That could not be done.'}${words}`
  }
  return e?.message ?? 'Something went wrong.'
}

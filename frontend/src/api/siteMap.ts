/**
 * Security Map API — every call the map screens make, typed to what the backend
 * returns (backend/app/routers/site_map.py).
 *
 * One shape for everything that is drawn: a Feature. What differs between a
 * camera, a guard and an incident is in `state` and `detail`.
 */
import { apiClient } from './client'

const BASE = '/api/v1/site-map'

export type LayerKey =
  | 'SITE' | 'CAMERA' | 'GUARD' | 'INCIDENT' | 'ALERT' | 'SITUATION' | 'DRONE' | 'CHECKPOINT' | 'PLACE' | 'DRONE_ZONE'
export type PlaceKind =
  | 'BUILDING' | 'FLOOR' | 'GATE' | 'ACCESS_POINT' | 'EMERGENCY_POINT' | 'ASSEMBLY_POINT' | 'ZONE' | 'PARKING' | 'OTHER'

export interface Feature {
  layer: LayerKey
  id: string
  label: string | null
  /** Null when the platform has no position for it. */
  latitude: number | null
  longitude: number | null
  state: string | null
  /** When the state, or the position, is from. */
  at: string | null
  site_id: string | null
  site_name: string | null
  /** [[lat, lng], ...] when it is an area. */
  outline: [number, number][] | null
  detail: Record<string, unknown>
}

export interface LayerInfo { key: LayerKey; label: string; permission: string; may_see: boolean; live: boolean }

export interface Layers {
  layers: LayerInfo[]
  place_kinds: PlaceKind[]
  can_manage: boolean
  default_hours: number
  max_hours: number
  default_radius_m: number
  max_radius_m: number
  /** A guard's position older than this is drawn as stale. */
  stale_after_s: number
  note: string
}

export interface NotShown { layer: LayerKey; label: string; reason: string }

export interface Features {
  as_of: string
  hours: number
  layers: Partial<Record<LayerKey, Feature[]>>
  /** How many of each layer could not be drawn for want of a position. */
  without_position: Partial<Record<LayerKey, number>>
  /** Layers that had more than one reading carries. */
  more: Partial<Record<LayerKey, boolean>>
  not_shown: NotShown[]
  note: string
}

/** A guard on shift, at the last position they recorded. */
export interface NearGuard {
  user_id: string
  full_name: string | null
  site_name: string | null
  latitude: number | null
  longitude: number | null
  position_source: string | null
  position_at: string | null
  position_age_s: number | null
  stale: boolean
  available: boolean
  busy_incident_id: string | null
  emergency_id: string | null
  /** Null when either position is not known. */
  distance_m: number | null
}

export interface Around {
  subject: Feature
  /** False when the thing itself has no position: then nothing can be said to be near it. */
  located: boolean
  radius_m: number
  nearby: Partial<Record<'CAMERA' | 'DRONE' | 'CHECKPOINT' | 'PLACE', (Feature & { distance_m: number })[]>>
    & { GUARD?: NearGuard[] }
  /** Every guard on shift at its site, nearest first by last recorded position. */
  nearest_guards: NearGuard[]
  not_shown: NotShown[]
  note: string
}

export interface PlaceBody {
  site_id: string
  kind: PlaceKind
  name: string
  parent_id?: string | null
  level?: number | null
  latitude?: number | null
  longitude?: number | null
  polygon?: [number, number][] | null
  door_id?: string | null
  description?: string | null
}

export const getLayers = () => apiClient.get<Layers>(`${BASE}/layers`).then((r) => r.data)

export const getFeatures = (params: { site_id?: string; layers?: LayerKey[]; hours?: number }) =>
  apiClient.get<Features>(`${BASE}/features`, { params, paramsSerializer: { indexes: null } }).then((r) => r.data)

export const getAround = (kind: 'INCIDENT' | 'ALERT' | 'SITUATION', id: string, radiusM?: number) =>
  apiClient.get<Around>(`${BASE}/around`, { params: { kind, id, radius_m: radiusM } }).then((r) => r.data)

export const listPlaces = (params: { site_id?: string; include_retired?: boolean }) =>
  apiClient.get<{ items: Feature[]; kinds: PlaceKind[]; can_manage: boolean }>(`${BASE}/places`, { params })
    .then((r) => r.data)

export const drawPlace = (body: PlaceBody) => apiClient.post<Feature>(`${BASE}/places`, body).then((r) => r.data)

export const changePlace = (id: string, body: Partial<Omit<PlaceBody, 'site_id' | 'kind'>>) =>
  apiClient.patch<Feature>(`${BASE}/places/${id}`, body).then((r) => r.data)

export const retirePlace = (id: string) => apiClient.post(`${BASE}/places/${id}/retire`).then((r) => r.data)

export const restorePlace = (id: string) => apiClient.post(`${BASE}/places/${id}/restore`).then((r) => r.data)

/** What went wrong, in the server's words where it gave any. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

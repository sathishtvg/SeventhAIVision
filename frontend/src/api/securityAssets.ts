/**
 * Security assets and device health API (backend/app/routers/security_assets.py).
 *
 * A health reading is made of what a device or its connection reports to the
 * platform. Every answer that carries one also carries `not_measured`: what the
 * platform does not measure, so that no screen implies it.
 */
import { apiClient } from './client'

const BASE = '/api/v1/security-assets'

export type HealthState = 'OK' | 'DEGRADED' | 'DOWN' | 'NOT_KNOWN' | 'OFF'
export type DeviceKind = 'CAMERA' | 'NVR' | 'SENSOR' | 'DRONE' | 'EDGE_GATEWAY' | 'ALARM_PANEL'
export type AssetKind = DeviceKind | 'SERVER' | 'ACCESS_CONTROLLER' | 'UPS' | 'NETWORK' | 'OTHER'
export type AssetStatus = 'IN_SERVICE' | 'UNDER_REPAIR' | 'SPARE' | 'RETIRED'
export type Warranty = 'IN' | 'OUT' | 'NOT_RECORDED'

export interface Reading {
  kind: DeviceKind
  kind_label: string
  device_id: string
  name: string
  site_id: string | null
  site_name: string | null
  asset_id: string | null
  asset_code: string | null
  state: HealthState
  /** Why it was read so. The server's words. */
  reasons: string[]
  /** What the reading was made of: the last frame, probe, reading, heartbeat or contact. */
  facts: Record<string, string | number | null>
  /** Since when it has been in this state, where that is known. */
  since: string | null
  /** True when `since` is only when the platform first read it so — the state may be older. */
  since_is_when_first_read: boolean
}

export interface HealthSummary {
  devices: number
  by_state: Record<HealthState, number>
  by_kind: ({ kind: DeviceKind; label: string; devices: number } & Record<HealthState, number>)[]
}

export interface Health {
  items: Reading[]
  summary: HealthSummary
  as_of: string
  note: string
  /** What the platform does not measure. Shown wherever a reading is. */
  not_measured: string[]
  kinds: { key: DeviceKind; label: string }[]
  states: HealthState[]
}

export interface Down { days: number; known_from: string | null; known_seconds: number; down_seconds: number
                        times_down: number; note: string }

export interface DeviceDetail extends Reading {
  as_of: string
  note: string
  not_measured: string[]
  history: { state: HealthState; reasons: string[]; observed_at: string }[]
  down: Down[]
}

export interface Asset {
  id: string
  asset_code: string
  kind: AssetKind
  kind_label: string
  name: string
  make: string | null
  model: string | null
  serial_number: string | null
  location: string | null
  site_id: string | null
  site_name: string | null
  place_id: string | null
  place_name: string | null
  vendor: string | null
  installed_on: string | null
  warranty_until: string | null
  warranty_days_left: number | null
  warranty: Warranty
  status: AssetStatus
  notes: string | null
  created_by_name: string | null
  updated_by_name: string | null
  updated_at: string
  retired_at: string | null
  retired_by_name: string | null
  retire_reason: string | null
  device_id: string | null
  /** Whether the platform knows it as a device and so has a reading of it. */
  monitored: boolean
  health: Pick<Reading, 'state' | 'reasons' | 'since' | 'since_is_when_first_read'> | null
  /** Why there is no reading, when there is none. */
  not_monitored: string | null
  /** Open work on it. Null for somebody who does not read maintenance. */
  open_orders: number | null
  may: { change: boolean; retire: boolean; restore: boolean }
}

export interface AssetDetail extends Asset {
  work_orders: { id: string; number: string; title: string; kind: string; state: string; due_at: string | null
                 raised_at: string; completed_at: string | null; completion_note: string | null
                 downtime_minutes: number | null }[] | null
  note: string
  not_measured: string[]
}

export interface Register {
  items: Asset[]
  limit: number
  offset: number
  has_more: boolean
  can_manage: boolean
  kinds: { key: AssetKind; label: string; monitored: boolean }[]
  statuses: AssetStatus[]
}

export interface Unregistered {
  kind: DeviceKind
  kind_label: string
  device_id: string
  name: string
  site_id: string | null
  site_name: string | null
  make: string | null
  model: string | null
  serial_number: string | null
  location: string | null
}

export interface AssetBody {
  kind: AssetKind
  name: string
  site_id?: string | null
  device_id?: string | null
  make?: string | null
  model?: string | null
  serial_number?: string | null
  location?: string | null
  vendor?: string | null
  installed_on?: string | null
  warranty_until?: string | null
  status?: Exclude<AssetStatus, 'RETIRED'>
  notes?: string | null
}

export const getHealth = (params: { site_id?: string; kind?: DeviceKind; state?: HealthState } = {}) =>
  apiClient.get<Health>(`${BASE}/health`, { params }).then((r) => r.data)

export const getDevice = (kind: DeviceKind, deviceId: string) =>
  apiClient.get<DeviceDetail>(`${BASE}/health/${kind}/${deviceId}`).then((r) => r.data)

export const getRegister = (params: { site_id?: string; kind?: AssetKind; status?: AssetStatus; warranty?: Warranty
                                      q?: string } = {}) =>
  apiClient.get<Register>(BASE, { params }).then((r) => r.data)

export const getAsset = (id: string) => apiClient.get<AssetDetail>(`${BASE}/${id}`).then((r) => r.data)

export const addAsset = (body: AssetBody) => apiClient.post<Asset>(BASE, body).then((r) => r.data)

/** What is left out stays; what is given as null is cleared. */
export const changeAsset = (id: string, body: Partial<Omit<AssetBody, 'kind'>>) =>
  apiClient.patch<Asset>(`${BASE}/${id}`, body).then((r) => r.data)

export const retireAsset = (id: string, reason: string) =>
  apiClient.post<Asset>(`${BASE}/${id}/retire`, { reason }).then((r) => r.data)

export const restoreAsset = (id: string) => apiClient.post<Asset>(`${BASE}/${id}/restore`).then((r) => r.data)

export const getUnregistered = () =>
  apiClient.get<{ items: Unregistered[] }>(`${BASE}/unregistered`).then((r) => r.data.items)

export const registerDevices = (devices: { kind: DeviceKind; device_id: string }[]) =>
  apiClient.post<{ registered: { id: string; asset_code: string; name: string }[]
                   left: { kind: DeviceKind; device_id: string }[] }>(`${BASE}/register-devices`, { devices })
    .then((r) => r.data)

/** The server's reason, in its own words: a refusal here says what to do instead. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

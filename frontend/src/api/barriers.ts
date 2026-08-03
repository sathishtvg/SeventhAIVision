import { apiClient } from './client'

/** Dedicated access controllers, the ANPR camera's own relay output, a
 * standalone relay board, and a simulator for sites with no hardware yet.
 * Mirrors the vendor CHECK in migration 0078. */
export type BarrierVendor =
  | 'hikvision'
  | 'dahua'
  | 'hikvision_camera_io'
  | 'dahua_camera_io'
  | 'relay'
  | 'simulator'

export type BarrierCommand =
  | 'open'
  | 'close'
  | 'hold_open'
  | 'release_hold'
  | 'emergency_override'

export type BarrierStatus = 'open' | 'closed' | 'held_open' | 'unknown' | 'error'

export const VENDOR_LABELS: Record<BarrierVendor, string> = {
  hikvision: 'Hikvision controller',
  dahua: 'Dahua controller',
  hikvision_camera_io: 'Hikvision camera relay output',
  dahua_camera_io: 'Dahua camera relay output',
  relay: 'Network relay board',
  simulator: 'Simulator (no hardware)',
}

export const COMMAND_LABELS: Record<BarrierCommand, string> = {
  open: 'Open',
  close: 'Close',
  hold_open: 'Hold Open',
  release_hold: 'Release Hold',
  emergency_override: 'Emergency Override',
}

export interface Barrier {
  id: string
  site_id: string | null
  camera_id: string | null
  door_id: string | null
  name: string
  lane_direction: 'entry' | 'exit' | 'bidirectional'
  vendor: BarrierVendor
  host: string | null
  port: number | null
  username: string | null
  relay_channel: number | null
  pulse_ms: number
  auto_open_enabled: boolean
  is_active: boolean
  last_status: BarrierStatus | null
  last_status_at: string | null
  last_error: string | null
  /** The stored device password is never returned by the API — only whether
   * one is configured. */
  has_credentials: boolean
  site_name?: string | null
  camera_name?: string | null
  created_at: string
}

export interface BarrierCommandLog {
  id: string
  command: string
  source: 'operator' | 'decision_engine' | 'schedule' | 'api'
  decision: string | null
  plate_number: string | null
  reason: string | null
  succeeded: boolean | null
  error: string | null
  latency_ms: number | null
  created_at: string
  issued_by_name: string | null
}

export interface BarrierInput {
  name: string
  vendor: BarrierVendor
  site_id?: string | null
  camera_id?: string | null
  lane_direction?: string
  host?: string | null
  port?: number | null
  username?: string | null
  /** Write-only. Omit to leave the stored credential untouched. */
  password?: string | null
  relay_channel?: number | null
  pulse_ms?: number
  auto_open_enabled?: boolean
  is_active?: boolean
}

export interface VendorCapability {
  vendor: BarrierVendor
  commands: BarrierCommand[]
}

export const listBarriers = (siteId?: string) =>
  apiClient
    .get<Barrier[]>('/api/v1/barriers', { params: siteId ? { site_id: siteId } : {} })
    .then((r) => r.data)

export const getVendorCapabilities = () =>
  apiClient
    .get<{ vendors: VendorCapability[] }>('/api/v1/barriers/vendors')
    .then((r) => r.data.vendors)

export const createBarrier = (data: BarrierInput) =>
  apiClient.post<Barrier>('/api/v1/barriers', data).then((r) => r.data)

export const updateBarrier = (id: string, data: Partial<BarrierInput>) =>
  apiClient.put<Barrier>(`/api/v1/barriers/${id}`, data).then((r) => r.data)

export const deleteBarrier = (id: string) =>
  apiClient.delete(`/api/v1/barriers/${id}`).then((r) => r.data)

/** Returns 502 when the device refused or was unreachable — the attempt is
 * still recorded in the command log either way. */
export const issueBarrierCommand = (id: string, command: BarrierCommand, reason?: string) =>
  apiClient
    .post<BarrierCommandLog>(`/api/v1/barriers/${id}/command`, { command, reason })
    .then((r) => r.data)

export const getBarrierStatus = (id: string) =>
  apiClient
    .get<{ reachable: boolean; status: BarrierStatus; error: string | null; latency_ms: number }>(
      `/api/v1/barriers/${id}/status`,
    )
    .then((r) => r.data)

export const listBarrierCommands = (id: string, limit = 50) =>
  apiClient
    .get<BarrierCommandLog[]>(`/api/v1/barriers/${id}/commands`, { params: { limit } })
    .then((r) => r.data)

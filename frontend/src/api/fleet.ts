/**
 * The fleet API (`/api/v1/fleet`): vehicles, their custom fields and groups.
 *
 * A vehicle is archived, never deleted: archiving takes it off lists and the
 * map, keeps its history and can be undone. Every change is in the audit log.
 * What a person may do is said by the server (`may`); the server checks it again.
 */
import { apiClient } from './client'

export type ServiceStatus = 'in_service' | 'maintenance' | 'out_of_service'
export type FieldType = 'text' | 'number' | 'date' | 'select' | 'boolean'
export type CustomValue = string | number | boolean

export interface FleetMay {
  edit: boolean; archive: boolean; manage_fields: boolean; manage_devices: boolean; manage_limits: boolean
  read_history: boolean; export_history: boolean
}
export type TrackingStatus = 'online' | 'stale' | 'offline' | 'not_configured'

export interface FleetVehicle {
  id: string
  name: string
  plate_number: string | null
  plate_key?: string | null
  vehicle_type: string
  type_label: string
  make: string | null
  model: string | null
  color: string | null
  site_id: string | null
  site_name: string | null
  department: string | null
  service_status: ServiceStatus
  service_label: string
  assigned_driver_user_id: string | null
  driver_name: string | null
  driver_phone?: string | null
  notes?: string | null
  custom_fields?: Record<string, CustomValue>
  current_status: string
  last_position_at: string | null
  tracking_enabled?: boolean
  device_identifier?: string | null
  last_fix_at?: string | null
  tracking_status?: TrackingStatus
  tracking_label?: string
  archived: boolean
  archived_at: string | null
  archive_reason: string | null
  created_at: string
  updated_at: string
}

export interface VehicleField {
  id: string
  field_key: string
  label: string
  field_type: FieldType
  options: string[]
  is_required: boolean
  help_text: string | null
  sort_order: number
  is_active: boolean
}

export interface FleetVehicleDetail extends FleetVehicle {
  driver_display: string | null
  driver_user_name: string | null
  archived_by_name: string | null
  groups: { id: string; name: string; site_id: string | null }[]
  fields: VehicleField[]
  may: FleetMay
  archived_note: string
}

export interface VehicleList { items: FleetVehicle[]; total: number; limit: number; offset: number; may: FleetMay }

export interface FleetOptions {
  vehicle_types: { value: string; label: string }[]
  service_statuses: { value: ServiceStatus; label: string }[]
  field_types: FieldType[]
  sites: { id: string; name: string }[]
  drivers: { id: string; full_name: string }[]
  fields: VehicleField[]
  site_required: boolean
  may: FleetMay
}

export interface VehicleHistoryLine { action: string; created_at: string; detail: Record<string, unknown> | null; by_name: string | null }

export interface FleetGroup {
  id: string
  name: string
  description: string | null
  site_id: string | null
  site_name: string | null
  is_active: boolean
  vehicles: number
  created_at: string
}

export interface VehicleInput {
  name?: string
  plate_number?: string | null
  vehicle_type?: string
  make?: string | null
  model?: string | null
  color?: string | null
  site_id?: string | null
  department?: string | null
  assigned_driver_user_id?: string | null
  driver_name?: string | null
  driver_phone?: string | null
  service_status?: ServiceStatus
  notes?: string | null
  custom_fields?: Record<string, CustomValue | null>
}

export interface VehicleQuery {
  q?: string
  site_id?: string
  vehicle_type?: string
  service_status?: string
  group_id?: string
  tracking?: TrackingStatus
  archived?: 'active' | 'archived' | 'all'
  sort?: string
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

const BASE = '/api/v1/fleet'

export const listFleetVehicles = (params: VehicleQuery): Promise<VehicleList> =>
  apiClient.get(`${BASE}/vehicles`, { params }).then((r) => r.data)
export const getFleetVehicle = (id: string): Promise<FleetVehicleDetail> =>
  apiClient.get(`${BASE}/vehicles/${id}`).then((r) => r.data)
export const createFleetVehicle = (body: VehicleInput): Promise<FleetVehicle> =>
  apiClient.post(`${BASE}/vehicles`, body).then((r) => r.data)
export const changeFleetVehicle = (id: string, body: VehicleInput): Promise<FleetVehicle> =>
  apiClient.patch(`${BASE}/vehicles/${id}`, body).then((r) => r.data)
export const archiveFleetVehicle = (id: string, reason: string): Promise<FleetVehicle> =>
  apiClient.post(`${BASE}/vehicles/${id}/archive`, { reason }).then((r) => r.data)
export const restoreFleetVehicle = (id: string): Promise<FleetVehicle> =>
  apiClient.post(`${BASE}/vehicles/${id}/restore`).then((r) => r.data)
export const getVehicleHistory = (id: string): Promise<{ items: VehicleHistoryLine[] }> =>
  apiClient.get(`${BASE}/vehicles/${id}/history`).then((r) => r.data)
export const getFleetOptions = (): Promise<FleetOptions> =>
  apiClient.get(`${BASE}/options`).then((r) => r.data)

export const listVehicleFields = (): Promise<{ items: VehicleField[] }> =>
  apiClient.get(`${BASE}/vehicle-fields`).then((r) => r.data)
export const createVehicleField = (body: Partial<VehicleField>): Promise<VehicleField> =>
  apiClient.post(`${BASE}/vehicle-fields`, body).then((r) => r.data)
export const changeVehicleField = (id: string, body: Partial<VehicleField>): Promise<VehicleField> =>
  apiClient.patch(`${BASE}/vehicle-fields/${id}`, body).then((r) => r.data)
export const setVehicleFieldInUse = (id: string, inUse: boolean): Promise<VehicleField> =>
  apiClient.post(`${BASE}/vehicle-fields/${id}/${inUse ? 'reinstate' : 'retire'}`).then((r) => r.data)

export const listFleetGroups = (includeRetired = false): Promise<{ items: FleetGroup[] }> =>
  apiClient.get(`${BASE}/groups`, { params: includeRetired ? { include_retired: true } : undefined }).then((r) => r.data)
export const createFleetGroup = (body: { name: string; description?: string | null; site_id?: string | null }): Promise<FleetGroup> =>
  apiClient.post(`${BASE}/groups`, body).then((r) => r.data)
export const changeFleetGroup = (id: string, body: { name?: string; description?: string | null; site_id?: string | null }): Promise<FleetGroup> =>
  apiClient.patch(`${BASE}/groups/${id}`, body).then((r) => r.data)
export const retireFleetGroup = (id: string): Promise<FleetGroup> =>
  apiClient.post(`${BASE}/groups/${id}/retire`).then((r) => r.data)
export const getGroupVehicles = (id: string): Promise<{ items: { id: string; name: string; plate_number: string | null; site_id: string | null; archived_at: string | null }[] }> =>
  apiClient.get(`${BASE}/groups/${id}/vehicles`).then((r) => r.data)
export const setGroupVehicles = (id: string, vehicleIds: string[]): Promise<{ added: number; removed: number; vehicles: number }> =>
  apiClient.put(`${BASE}/groups/${id}/vehicles`, { vehicle_ids: vehicleIds }).then((r) => r.data)

/** How a vehicle is named to a person: its plate first, because that is what is on the vehicle. */
export function vehicleLabel(v: { plate_number: string | null; name: string }): string {
  return v.plate_number ? `${v.plate_number} — ${v.name}` : v.name
}

/** The server's reason, in its own words. */
export function fleetError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

/** The audit log's action names, in words. */
export const HISTORY_WORDS: Record<string, string> = {
  'fleet.vehicle.created': 'Registered',
  'fleet.vehicle.changed': 'Changed',
  'fleet.vehicle.archived': 'Archived',
  'fleet.vehicle.restored': 'Restored',
}

/** "Fuel card no." becomes `fuel_card_no`: the key a new field is offered, which the person may change before saving. */
export function fieldKeyOf(label: string): string {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').replace(/^[^a-z]+/, '').slice(0, 50)
}

// ── tracking (phase 2) ──────────────────────────────────────────────────────

export interface DeviceState {
  vehicle_id: string
  archived: boolean
  tracking_enabled: boolean
  tracking_source: string | null
  device_identifier: string | null
  device_assigned_at: string | null
  provider: { id: string | null; name: string; is_active?: boolean } | null
  status: TrackingStatus
  status_label: string
  limits: FleetLimits
  last_fix_at: string | null
  last_received_at: string | null
  last_lat: number | null
  last_lon: number | null
  last_speed: number | null
  last_heading: number | null
  last_accuracy: number | null
  last_ignition: boolean | null
  last_satellites: number | null
  motion: string | null
  rejects: { reason: string; label: string; times: number; first_at: string; last_at: string }[]
  status_changes: { from_status: TrackingStatus; to_status: TrackingStatus; changed_at: string }[]
  builtin_available: boolean
  may_manage: boolean
}
export interface FleetLimits { online_minutes: number; offline_minutes: number }
export interface DeviceInput {
  tracking_enabled?: boolean
  device_identifier?: string | null
  provider_connection_id?: string | null
  register_with_provider?: boolean
}
export interface ProviderConnection {
  id: string
  provider: 'traccar'
  name: string
  base_url: string
  auth_kind: 'token' | 'basic'
  username: string | null
  is_active: boolean
  last_test_at: string | null
  last_test_ok: boolean | null
  last_test_message: string | null
  forward_path: string
  created_at: string
}
export interface ProviderList {
  items: ProviderConnection[]
  builtin: { available: boolean; forwarding: boolean; forward_path: string }
}

export const getVehicleDevice = (id: string): Promise<DeviceState> =>
  apiClient.get(`${BASE}/vehicles/${id}/device`).then((r) => r.data)
export const setVehicleDevice = (id: string, body: DeviceInput): Promise<{ ok: boolean; registered: { ok: boolean; message: string } | null }> =>
  apiClient.put(`${BASE}/vehicles/${id}/device`, body).then((r) => r.data)
export const removeVehicleDevice = (id: string): Promise<{ ok: boolean }> =>
  apiClient.delete(`${BASE}/vehicles/${id}/device`).then((r) => r.data)
export const testVehicleDevice = (id: string): Promise<{ provider: { ok: boolean; message: string; last_update?: string | null }; last_received_at: string | null }> =>
  apiClient.post(`${BASE}/vehicles/${id}/device/test`).then((r) => r.data)

export const listProviders = (): Promise<ProviderList> => apiClient.get(`${BASE}/providers`).then((r) => r.data)
export const createProvider = (body: { name: string; base_url: string; auth_kind: 'token' | 'basic'; username?: string | null; secret: string }):
  Promise<ProviderConnection & { forward_secret: string; forward_secret_note: string }> =>
  apiClient.post(`${BASE}/providers`, body).then((r) => r.data)
export const changeProvider = (id: string, body: Partial<{ name: string; base_url: string; auth_kind: 'token' | 'basic'; username: string | null; secret: string }>): Promise<ProviderConnection> =>
  apiClient.patch(`${BASE}/providers/${id}`, body).then((r) => r.data)
export const testProvider = (id: string): Promise<{ ok: boolean; message: string }> =>
  apiClient.post(`${BASE}/providers/${id}/test`).then((r) => r.data)
export const retireProvider = (id: string): Promise<{ ok: boolean }> =>
  apiClient.post(`${BASE}/providers/${id}/retire`).then((r) => r.data)
export const replaceForwardSecret = (id: string): Promise<{ forward_secret: string; forward_path: string }> =>
  apiClient.post(`${BASE}/providers/${id}/forward-secret`).then((r) => r.data)

export const getFleetLimits = (): Promise<FleetLimits> => apiClient.get(`${BASE}/settings`).then((r) => r.data)
export const setFleetLimits = (body: FleetLimits): Promise<FleetLimits> => apiClient.put(`${BASE}/settings`, body).then((r) => r.data)

export const TRACKING_COLOUR: Record<TrackingStatus, 'success' | 'warning' | 'default' | 'info'> = {
  online: 'success', stale: 'warning', offline: 'default', not_configured: 'info',
}
export const TRACKING_LABEL: Record<TrackingStatus, string> = {
  online: 'Online', stale: 'Stale', offline: 'Offline', not_configured: 'Not configured',
}

// ── the live map (phase 3) ──────────────────────────────────────────────────

/** A vehicle on the live map. `last_lat`/`last_lon` are only there when a fix is. */
export interface MapVehicle {
  id: string
  name: string
  plate_number: string | null
  vehicle_type: string
  type_label: string
  site_id: string | null
  site_name: string | null
  driver_name: string | null
  service_status: ServiceStatus
  tracking_status: TrackingStatus
  status_label: string
  motion: 'moving' | 'idle' | 'offline' | null
  last_lat: number | null
  last_lon: number | null
  last_fix_at: string | null
  last_speed: number | string | null
  last_heading: number | null
  last_accuracy: number | string | null
}
export interface FleetMapAnswer { items: MapVehicle[]; limits: FleetLimits; at: string }

/** Every vehicle the person may see, or - after the live connection says some moved - just those. */
export const getFleetMap = (ids?: string[]): Promise<FleetMapAnswer> =>
  apiClient.get(`${BASE}/map`, { params: ids?.length ? { ids: ids.join(',') } : undefined }).then((r) => r.data)

// ── journey history (phase 4) ───────────────────────────────────────────────

export interface RoutePoint { at: string; lat: number; lon: number; speed: number | null }
export interface JourneyTrip {
  start: string; end: string; minutes: number; distance_km: number; max_speed: number | null; avg_kmh: number | null
  from: { lat: number; lon: number }; to: { lat: number; lon: number }
}
export interface JourneyStop { start: string; end: string; minutes: number; lat: number; lon: number; ignition_off: boolean }
export interface JourneyHistory {
  vehicle: { id: string; name: string; plate_number: string | null }
  start: string
  end: string
  enough: boolean
  positions: number
  left_out: { inaccurate: number; impossible_jump: number }
  route: RoutePoint[]
  route_thinned: boolean
  trips: JourneyTrip[]
  stops: JourneyStop[]
  gaps: { start: string; end: string }[]
  summary: { distance_km: number; driving_minutes: number; stopped_minutes: number }
}
export type JourneyExport = 'trips' | 'stops' | 'positions'

/** Journeys and stops over a range (a week at most), worked out by the server from the stored positions. */
export const getJourneyHistory = (id: string, start: string, end: string): Promise<JourneyHistory> =>
  apiClient.get(`${BASE}/vehicles/${id}/journeys`, { params: { start, end } }).then((r) => r.data)

/** The trips, stops or positions as a CSV file, handed to the browser to save. Every export is in the audit log. */
export async function downloadJourneys(id: string, start: string, end: string, kind: JourneyExport, name: string): Promise<void> {
  const response = await apiClient.get(`${BASE}/vehicles/${id}/journeys.csv`, { params: { start, end, kind }, responseType: 'blob' })
  const href = URL.createObjectURL(response.data as Blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = `${name}-${kind}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(href)
}

export interface RetentionKind {
  key: 'FLEET_POSITIONS' | 'ARCHIVED_VEHICLES'
  label: string
  days: number | null
  set_at: string | null
  counted_from: string
  removes: string
  kept_whatever: string | null
  removed_by: string
}
export interface FleetRetention { kinds: RetentionKind[]; least_days: number; most_days: number; may_set: boolean }

export const getFleetRetention = (): Promise<FleetRetention> => apiClient.get(`${BASE}/retention`).then((r) => r.data)
/** Set or take away a period. A period that already has older records answers 409 with how many, to be said back. */
export const setFleetRetention = (kind: string, days: number | null, alreadyOlder?: number) =>
  apiClient.put(`${BASE}/retention/${kind}`, { days, already_older: alreadyOlder ?? null }).then((r) => r.data)

// ── geofences (phase 5) ──────────────────────────────────────────────────────
// A geofence is a circle or a polygon, of the organisation or one of its sites, applying to chosen vehicle groups
// or else to every vehicle of its site. Its rules say what is alerted. It never opens a barrier, admits anybody
// or confirms who is driving: it records where a tracker said a vehicle was, for a person to look at.

export type GeofenceKind = 'entry' | 'exit' | 'dwell_inside' | 'dwell_outside' | 'overspeed'
export type Severity = 'low' | 'medium' | 'high' | 'critical'
export interface LatLon { lat: number; lon: number }

export interface GeofenceRule {
  kind: GeofenceKind
  minutes: number | null
  speed_kmh: number | null
  severity: Severity
  user_ids: string[]
  role_ids: number[]
  is_active: boolean
}
export interface Geofence {
  id: string
  name: string
  description: string | null
  site_id: string | null
  site_name: string | null
  shape: 'circle' | 'polygon'
  center: LatLon | null
  radius_m: number | null
  polygon: LatLon[]
  is_active: boolean
  archived: boolean
  archived_at: string | null
  archive_reason: string | null
  groups: { id: string; name: string; site_id: string | null }[]
  rules: GeofenceRule[]
  inside_now: number
  may_change?: boolean
  created_at: string
  updated_at: string
}
export interface GeofenceList { items: Geofence[]; may: { manage: boolean; manage_alerts: boolean; organisation_wide: boolean } }
export interface GeofenceOptions {
  sites: { id: string; name: string; latitude: number | null; longitude: number | null; has_boundary: boolean }[]
  groups: { id: string; name: string; site_id: string | null }[]
  people: { id: string; full_name: string; role_id: number; role_name: string }[]
  roles: { id: number; name: string }[]
  kinds: { value: GeofenceKind; label: string }[]
  severities: Severity[]
  limits: { least_radius_m: number; most_radius_m: number; least_points: number; most_points: number }
}
export interface RuleInput {
  kind: GeofenceKind; minutes?: number | null; speed_kmh?: number | null; severity: Severity
  user_ids: string[]; role_ids: number[]; is_active?: boolean
}
export interface GeofenceInput {
  name?: string
  description?: string | null
  site_id?: string | null
  from_site?: boolean
  shape?: { kind: 'circle'; center: LatLon; radius_m: number } | { kind: 'polygon'; points: LatLon[] }
  group_ids?: string[]
  rules?: RuleInput[]
  is_active?: boolean
}
export interface GeofenceEvent {
  id: string
  event_type: 'entry' | 'exit' | 'dwell_inside' | 'dwell_outside' | 'speed_violation' | string
  occurred_at: string
  lat: number | null
  lon: number | null
  speed: number | null
  accuracy: number | null
  between_start: string | null
  between_end: string | null
  alert_id: string | null
  alert_status: string | null
  alert_severity: Severity | null
  vehicle_id: string
  vehicle_name: string
  plate_number: string | null
  geofence_id: string
  geofence_name: string
}
export interface StopAlert { on: boolean; severity: Severity }
export interface StopAlerts { offline: StopAlert; stale: StopAlert }

export const listGeofences = (archived = false): Promise<GeofenceList> =>
  apiClient.get(`${BASE}/geofences`, { params: { archived } }).then((r) => r.data)
export const getGeofenceOptions = (): Promise<GeofenceOptions> => apiClient.get(`${BASE}/geofences/options`).then((r) => r.data)
export const createGeofence = (body: GeofenceInput): Promise<Geofence> => apiClient.post(`${BASE}/geofences`, body).then((r) => r.data)
export const changeGeofence = (id: string, body: GeofenceInput): Promise<Geofence> =>
  apiClient.patch(`${BASE}/geofences/${id}`, body).then((r) => r.data)
export const archiveGeofence = (id: string, reason: string): Promise<Geofence> =>
  apiClient.post(`${BASE}/geofences/${id}/archive`, { reason }).then((r) => r.data)
export const listFleetGeofenceEvents = (params: { geofence_id?: string; vehicle_id?: string; hours?: number } = {}):
  Promise<{ items: GeofenceEvent[] }> => apiClient.get(`${BASE}/geofence-events`, { params }).then((r) => r.data)
export const getStopAlerts = (): Promise<StopAlerts> => apiClient.get(`${BASE}/alert-settings`).then((r) => r.data)
export const setStopAlerts = (body: StopAlerts): Promise<StopAlerts> => apiClient.put(`${BASE}/alert-settings`, body).then((r) => r.data)

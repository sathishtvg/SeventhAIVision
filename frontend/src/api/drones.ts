/**
 * Drone Patrol API — every call the drone screens make, typed to what the
 * backend returns (backend/app/routers/drones.py, drone_planning.py,
 * drone_operations.py). DRONE_PATROL_API.md describes the rules behind them.
 */
import { apiClient } from './client'

// ── Vocabularies ─────────────────────────────────────────────────────────────

export type DroneStatus =
  | 'OFFLINE' | 'STANDBY' | 'READY' | 'PREPARING' | 'MISSION_ACTIVE' | 'RETURNING'
  | 'CHARGING' | 'WARNING' | 'COMMUNICATION_LOST' | 'CRITICAL' | 'MAINTENANCE' | 'DISABLED'
export type ComponentState = 'OK' | 'WARNING' | 'FAULT' | 'UNKNOWN'
export type SessionStatus =
  | 'SCHEDULED' | 'PRECHECK' | 'READY' | 'LAUNCHING' | 'ACTIVE' | 'PAUSED' | 'EVENT_DETECTED'
  | 'RETURNING' | 'COMPLETED' | 'FAILED' | 'ABORTED' | 'CANCELLED' | 'BLOCKED' | 'MISSED'
export type RiskLevel = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export type EventStatus = 'NEW' | 'ACKNOWLEDGED' | 'INVESTIGATING' | 'ESCALATED' | 'RESOLVED' | 'FALSE_POSITIVE'
export type ZoneType =
  | 'NORMAL' | 'RESTRICTED' | 'CRITICAL' | 'VEHICLE_RESTRICTED' | 'PERSON_RESTRICTED'
  | 'NO_ENTRY' | 'SPECIAL_INSPECTION'
export type ZoneShape = 'POLYGON' | 'RECTANGLE' | 'CIRCLE'
export type AlertPolicy = 'NONE' | 'ALERT' | 'INCIDENT'
export type AiModule =
  | 'lpr' | 'face' | 'intrusion' | 'ppe' | 'crowd' | 'fire_smoke' | 'weapon'
  | 'behavior' | 'tampering' | 'abandoned' | 'fall'
export type SyncMode = 'central' | 'local_only' | 'incident_only' | 'scheduled' | 'manual'
export type ScheduleType = 'ONCE' | 'DAILY' | 'WEEKLY' | 'SELECTED_DAYS' | 'SPECIFIC_DATE'
export type CommandKind = 'pause' | 'resume' | 'abort' | 'return-to-home' | 'cancel'

export const IN_FLIGHT: SessionStatus[] = ['PRECHECK', 'READY', 'LAUNCHING', 'ACTIVE', 'PAUSED', 'EVENT_DETECTED', 'RETURNING']
export const AIRBORNE: SessionStatus[] = ['LAUNCHING', 'ACTIVE', 'PAUSED', 'EVENT_DETECTED', 'RETURNING']
export const AI_MODULES: AiModule[] = [
  'intrusion', 'face', 'lpr', 'weapon', 'fire_smoke', 'ppe', 'fall', 'crowd', 'behavior', 'abandoned', 'tampering',
]
export const ZONE_TYPES: ZoneType[] = [
  'NORMAL', 'RESTRICTED', 'CRITICAL', 'VEHICLE_RESTRICTED', 'PERSON_RESTRICTED', 'NO_ENTRY', 'SPECIAL_INSPECTION',
]
export const RISK_LEVELS: RiskLevel[] = ['INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL']

export interface Paged<T> { items: T[]; total: number; limit: number; offset: number; has_more: boolean }
export interface LatLng { lat: number; lng: number }

// ── Fleet ────────────────────────────────────────────────────────────────────

export interface Entitlement {
  licensed: boolean
  /** Why the module is unavailable, in words for the screen; null when licensed. */
  reason: string | null
  expires_at: string | null
  limits: { max_drones: number | null; max_missions: number | null; max_sites: number | null }
  usage: Record<string, number>
}

export interface FleetDashboard {
  fleet: { total: number; online: number; offline: number; disabled: number; needs_attention: number
           battery_warnings: number; maintenance_due: number }
  missions: { active_missions: number; failed_last_24h: number; completed_last_24h: number }
  open_events_by_risk: Record<RiskLevel, number>
}

export interface Drone {
  id: string
  name: string
  code: string
  site_id: string | null
  site_name: string | null
  status: DroneStatus
  battery_level: number | null
  gps_status: ComponentState
  communication_status: ComponentState
  camera_status: ComponentState
  storage_status: ComponentState
  last_heartbeat_at: string | null
  seconds_since_heartbeat: number | null
  heartbeat_timeout_seconds: number
  current_latitude: number | null
  current_longitude: number | null
  current_altitude_m: number | null
  provider_config_id: string | null
  provider_name: string | null
  provider_key: string | null
  edge_gateway_id: string | null
  edge_gateway_name: string | null
  camera_id: string | null
  camera_name: string | null
  manufacturer: string | null
  model: string | null
  serial_number: string | null
  next_maintenance_at: string | null
  active_session_id: string | null
}

export interface DroneInput {
  name?: string
  code?: string
  site_id?: string | null
  provider_config_id?: string | null
  edge_gateway_id?: string | null
  camera_id?: string | null
  manufacturer?: string | null
  model?: string | null
  serial_number?: string | null
  heartbeat_timeout_seconds?: number
}

export interface ProviderField { key: string; label: string; kind: string; secret: boolean; required: boolean
                                 help: string | null; minimum: number | null; maximum: number | null }
export interface ProviderCatalogueItem { key: string; name: string; simulated: boolean; description?: string
                                         fields: ProviderField[] }
export interface ProviderConfig { id: string; name: string; provider_key: string; config: Record<string, unknown>
                                  has_secret: boolean; is_active: boolean; drone_count: number }

export interface Gateway {
  id: string
  site_id: string
  site_name: string
  name: string
  code: string
  status: 'ONLINE' | 'OFFLINE' | 'DEGRADED' | 'UNKNOWN'
  last_seen_at: string | null
  last_sync_at: string | null
  software_version: string | null
  buffer_depth: number | null
  storage_free_pct: number | null
  clock_offset_s: number | null
  health: { problems?: string[] } | null
  credential_prefix: string | null
  is_active: boolean
  drone_count: number
  credential?: string
  credential_note?: string
}

const DRONES = '/api/v1/drones'

export const getEntitlement = () => apiClient.get<Entitlement>(`${DRONES}/entitlement`).then((r) => r.data)
export const getFleetDashboard = () => apiClient.get<FleetDashboard>(`${DRONES}/dashboard`).then((r) => r.data)
export const listDrones = (params: { site_id?: string; status?: string; q?: string; limit?: number } = {}) =>
  apiClient.get<Paged<Drone>>(DRONES, { params: { limit: 200, ...params } }).then((r) => r.data)
export const getDrone = (id: string) => apiClient.get<Drone>(`${DRONES}/${id}`).then((r) => r.data)
export const createDrone = (body: DroneInput) => apiClient.post<Drone>(DRONES, body).then((r) => r.data)
export const updateDrone = (id: string, body: DroneInput) => apiClient.put<Drone>(`${DRONES}/${id}`, body).then((r) => r.data)
export const setDroneEnabled = (id: string, enabled: boolean) =>
  apiClient.post<Drone>(`${DRONES}/${id}/${enabled ? 'enable' : 'disable'}`).then((r) => r.data)

export const getProviderCatalogue = () =>
  apiClient.get<ProviderCatalogueItem[]>(`${DRONES}/providers/catalogue`).then((r) => r.data)
export const listProviders = () => apiClient.get<ProviderConfig[]>(`${DRONES}/providers`).then((r) => r.data)
export const createProvider = (body: { name: string; provider_key: string; settings: Record<string, unknown> }) =>
  apiClient.post<ProviderConfig>(`${DRONES}/providers`, body).then((r) => r.data)

export const listGateways = () => apiClient.get<Gateway[]>(`${DRONES}/edge-gateways`).then((r) => r.data)
export const createGateway = (body: { site_id: string; name: string; code: string }) =>
  apiClient.post<Gateway>(`${DRONES}/edge-gateways`, body).then((r) => r.data)
export const rotateGatewayCredential = (id: string) =>
  apiClient.post<Gateway>(`${DRONES}/edge-gateways/${id}/rotate-credential`).then((r) => r.data)

// ── Planning ─────────────────────────────────────────────────────────────────

export interface Zone {
  id: string
  site_id: string
  name: string
  description: string | null
  zone_type: ZoneType
  shape: ZoneShape
  polygon: LatLng[] | null
  center_latitude: number | null
  center_longitude: number | null
  radius_m: number | null
  severity: RiskLevel
  alert_policy: AlertPolicy
  active_from: string | null
  active_to: string | null
  active_weekdays: number[] | null
  allowed_vehicle_plates: string[] | null
  detection_threshold: number | null
  is_active: boolean
}

export interface ZoneInput {
  site_id?: string
  name?: string
  description?: string | null
  zone_type?: ZoneType
  shape?: ZoneShape
  polygon?: LatLng[] | null
  center_latitude?: number | null
  center_longitude?: number | null
  radius_m?: number | null
  severity?: RiskLevel
  alert_policy?: AlertPolicy
  active_from?: string | null
  active_to?: string | null
  active_weekdays?: number[] | null
  allowed_vehicle_plates?: string[] | null
  detection_threshold?: number | null
  is_active?: boolean
}

export interface ProfileRule { module_type: AiModule; is_enabled: boolean; min_confidence: number | null
                               base_severity: RiskLevel; incident_risk_level: RiskLevel }
export interface Profile { id: string; name: string; description: string | null; min_confidence: number
                           verify_min_seconds: number; is_active: boolean
                           /** Only on the single-profile read; the list gives a count. */
                           rules?: ProfileRule[]; enabled_rule_count?: number; mission_count?: number }

export interface Waypoint {
  id?: string
  sequence?: number
  name: string | null
  latitude: number
  longitude: number
  altitude_m: number | null
  hover_seconds: number
  observe_seconds: number
  snapshot_required: boolean
  security_zone_id?: string | null
  security_zone_name?: string | null
}

export interface Route {
  id: string
  site_id: string
  name: string
  description: string | null
  base_latitude: number | null
  base_longitude: number | null
  default_altitude_m: number
  default_speed_mps: number
  return_to_base: boolean
  is_active: boolean
  /** Only on the single-route read; the list gives waypoint_count. */
  waypoints?: Waypoint[]
  waypoint_count?: number
  summary?: { waypoint_count: number; length_m: number; outside_site_geofence: number[] }
}

export interface Schedule {
  id: string
  mission_id: string
  schedule_type: ScheduleType
  timezone: string
  start_date: string
  end_date: string | null
  launch_time: string
  weekdays: number[]
  specific_dates: string[]
  grace_minutes: number
  enabled: boolean
  next_runs?: { utc: string; local: string; timezone: string }[]
}

export interface Mission {
  id: string
  site_id: string
  site_name: string
  name: string
  description: string | null
  drone_id: string | null
  drone_name: string | null
  drone_status: DroneStatus | null
  route_id: string | null
  route_name: string | null
  security_profile_id: string | null
  profile_name: string | null
  recording_sync_mode: SyncMode | null
  priority: number
  min_battery_pct: number
  max_duration_minutes: number | null
  enabled: boolean
  schedule_count: number
  last_session_status: SessionStatus | null
  last_session_at: string | null
  in_flight: boolean
  next_run: { utc: string; local: string; timezone: string } | null
  schedules?: Schedule[]
  recent_sessions?: Session[]
}

export interface MissionInput {
  site_id?: string
  name?: string
  description?: string | null
  drone_id?: string | null
  route_id?: string | null
  security_profile_id?: string | null
  recording_sync_mode?: SyncMode | null
  priority?: number
  min_battery_pct?: number
  max_duration_minutes?: number | null
  enabled?: boolean
}

export interface ScheduleInput {
  schedule_type: ScheduleType
  timezone: string
  start_date: string
  end_date?: string | null
  launch_time: string
  weekdays?: number[]
  specific_dates?: string[]
  grace_minutes?: number
  enabled?: boolean
}

export interface PreflightCheck { code: string; label: string; passed: boolean; severity: 'BLOCK' | 'WARN'; detail: string }
export interface Preflight {
  passed: boolean
  checks: PreflightCheck[]
  blocking: string[]
  warnings: string[]
  estimate?: { duration_s: number; distance_m: number; battery_needed_pct: number } | null
}

export const listZones = (siteId?: string) =>
  apiClient.get<Zone[]>('/api/v1/drone-zones', { params: siteId ? { site_id: siteId } : {} }).then((r) => r.data)
export const createZone = (body: ZoneInput) => apiClient.post<Zone>('/api/v1/drone-zones', body).then((r) => r.data)
export const updateZone = (id: string, body: ZoneInput) =>
  apiClient.put<Zone>(`/api/v1/drone-zones/${id}`, body).then((r) => r.data)
export const deleteZone = (id: string) => apiClient.delete(`/api/v1/drone-zones/${id}`).then((r) => r.data)

export const listProfiles = () => apiClient.get<Profile[]>('/api/v1/drone-security-profiles').then((r) => r.data)
export const createProfile = (body: Omit<Profile, 'id'>) =>
  apiClient.post<Profile>('/api/v1/drone-security-profiles', body).then((r) => r.data)
export const getProfile = (id: string) =>
  apiClient.get<Profile>(`/api/v1/drone-security-profiles/${id}`).then((r) => r.data)
export const updateProfile = (id: string, body: Partial<Omit<Profile, 'id'>>) =>
  apiClient.put<Profile>(`/api/v1/drone-security-profiles/${id}`, body).then((r) => r.data)

export const listRoutes = (siteId?: string) =>
  apiClient.get<Route[]>('/api/v1/drone-routes', { params: siteId ? { site_id: siteId } : {} }).then((r) => r.data)
export const getRoute = (id: string) => apiClient.get<Route>(`/api/v1/drone-routes/${id}`).then((r) => r.data)
export const createRoute = (body: { site_id: string; name: string; base_latitude?: number | null
                                    base_longitude?: number | null; default_altitude_m?: number
                                    default_speed_mps?: number; return_to_base?: boolean; waypoints: Waypoint[] }) =>
  apiClient.post<Route>('/api/v1/drone-routes', body).then((r) => r.data)
export const updateRoute = (id: string, body: Partial<Pick<Route, 'name' | 'base_latitude' | 'base_longitude'
  | 'default_altitude_m' | 'default_speed_mps' | 'return_to_base'>>) =>
  apiClient.put<Route>(`/api/v1/drone-routes/${id}`, body).then((r) => r.data)
export const replaceWaypoints = (id: string, waypoints: Waypoint[]) =>
  apiClient.put<Route>(`/api/v1/drone-routes/${id}/waypoints`, { waypoints }).then((r) => r.data)

export const listMissions = (params: { site_id?: string; limit?: number } = {}) =>
  apiClient.get<Paged<Mission>>('/api/v1/drone-missions', { params: { limit: 200, ...params } }).then((r) => r.data)
export const getMission = (id: string) => apiClient.get<Mission>(`/api/v1/drone-missions/${id}`).then((r) => r.data)
export const createMission = (body: MissionInput) =>
  apiClient.post<Mission>('/api/v1/drone-missions', body).then((r) => r.data)
export const updateMission = (id: string, body: MissionInput) =>
  apiClient.put<Mission>(`/api/v1/drone-missions/${id}`, body).then((r) => r.data)
export const setMissionEnabled = (id: string, enabled: boolean) =>
  apiClient.patch<Mission>(`/api/v1/drone-missions/${id}/enabled`, null, { params: { enabled } }).then((r) => r.data)
export const getPreflight = (id: string) =>
  apiClient.get<Preflight>(`/api/v1/drone-missions/${id}/preflight`).then((r) => r.data)
export const runMission = (id: string) =>
  apiClient.post<{ session: Session; preflight: Preflight; mission_name: string }>(
    `/api/v1/drone-missions/${id}/run`).then((r) => r.data)
export const createSchedule = (missionId: string, body: ScheduleInput) =>
  apiClient.post<Schedule>(`/api/v1/drone-missions/${missionId}/schedules`, body).then((r) => r.data)
export const deleteSchedule = (id: string) => apiClient.delete(`/api/v1/drone-schedules/${id}`).then((r) => r.data)

// ── Operations ───────────────────────────────────────────────────────────────

export interface Session {
  id: string
  session_number: string
  mission_id: string | null
  mission_name: string | null
  drone_id: string | null
  drone_name: string | null
  route_name: string | null
  profile_name: string | null
  site_id: string
  site_name?: string | null
  status: SessionStatus
  triggered_by: 'SCHEDULE' | 'MANUAL' | 'VERIFY_REQUEST'
  scheduled_for: string | null
  started_at: string | null
  launched_at: string | null
  ended_at: string | null
  blocked_reason: string | null
  failure_reason: string | null
  abort_reason: string | null
  distance_m: number | null
  event_count: number
  incident_count: number
  last_waypoint_sequence: number | null
  edge_gateway_id: string | null
  created_at: string
  waypoints?: (Waypoint & { status: string; reached_at: string | null; departed_at: string | null })[]
  events_by_risk?: Partial<Record<RiskLevel, number>>
  media_count?: number
  config_snapshot?: { route?: { base_latitude: number | null; base_longitude: number | null; return_to_base?: boolean }
                      waypoints?: Waypoint[]; zones?: Zone[]
                      estimate?: { duration_s: number; distance_m: number; battery_needed_pct: number } }
}

export interface TrackPoint {
  recorded_at: string
  latitude: number
  longitude: number
  altitude_m: number | null
  heading_deg: number | null
  speed_mps: number | null
  battery_pct: number | null
  mission_state: string | null
  waypoint_sequence: number | null
}

export interface SessionCommand { id: string; command: string; status: string; reason: string | null
                                  result: string | null; requested_at: string; requested_by_name: string | null }

export const listSessions = (params: { site_id?: string; drone_id?: string; mission_id?: string; status?: string
                                       from?: string; to?: string; limit?: number; offset?: number } = {}) =>
  apiClient.get<Paged<Session>>('/api/v1/drone-patrols', { params: { limit: 50, ...params } }).then((r) => r.data)
export const getSession = (id: string) => apiClient.get<Session>(`/api/v1/drone-patrols/${id}`).then((r) => r.data)
export const getTrack = (id: string) =>
  apiClient.get<{ total_samples: number; points: TrackPoint[] }>(`/api/v1/drone-patrols/${id}/track`).then((r) => r.data)
export const listCommands = (id: string) =>
  apiClient.get<SessionCommand[]>(`/api/v1/drone-patrols/${id}/commands`).then((r) => r.data)
export const sendCommand = (id: string, kind: CommandKind, reason?: string) =>
  apiClient.post(`/api/v1/drone-patrols/${id}/${kind}`, { reason: reason || null }).then((r) => r.data)

export interface DroneEvent {
  id: string
  site_id: string
  site_name: string | null
  session_id: string | null
  session_number: string | null
  mission_name: string | null
  drone_id: string | null
  drone_name: string | null
  drone_code: string | null
  module_type: AiModule
  label: string | null
  detected_at: string
  last_detected_at: string | null
  detection_count: number
  observed_seconds: number | null
  drone_latitude: number | null
  drone_longitude: number | null
  location_method: string
  zone_name: string | null
  zone_type: ZoneType | null
  ai_confidence: number | null
  risk_score: number | null
  risk_level: RiskLevel
  risk_factors: { factor: string; points: number; detail: string }[]
  verification_state: 'UNVERIFIED' | 'OBSERVING' | 'VERIFIED' | 'DISMISSED'
  status: EventStatus
  source: 'CENTRAL' | 'EDGE'
  alert_id: string | null
  incident_id: string | null
  acknowledged_by_name: string | null
  resolved_by_name: string | null
  false_positive_reason: string | null
  attributes: Record<string, unknown>
}

export interface EventDetail extends DroneEvent {
  media: { id: string; media_kind: string; storage_location: string; sync_state: string; captured_at: string
           size_bytes: number | null }[]
  observations: { id: string; source: string; module_type: string; label: string | null
                  ai_confidence: number | null; detected_at: string }[]
  cameras: CctvCamera[]
  incident: { id: string; title: string; severity: string; status: string } | null
  alert: { id: string; title: string; severity: string; status: string } | null
}

export interface CctvCamera {
  camera_id: string
  camera_name: string
  distance_m: number | null
  bearing_deg: number | null
  correlation_method: 'DISTANCE' | 'COVERAGE'
  in_coverage: boolean | null
  corroborates: boolean
  related_detection_count: number
  related_module_type: string | null
  related_detected_at: string | null
  related_alert_id: string | null
  related_alert_severity?: string | null
  camera_online: boolean | null
  streams?: { stream_id: string; status: string; live_path: string; hls_path: string }[]
  playback?: { recording_id: string; offset_s: number; started_at: string; status: string; download_path: string } | null
}

export interface CctvView {
  event_id: string
  location: { latitude: number | null; longitude: number | null; method: string; note: string }
  window: { start: string; end: string; pre_seconds: number; post_seconds: number }
  cameras: CctvCamera[]
  corroborating: string[]
  settled: boolean
}

export interface EventCard {
  event_id: string
  headline: string
  site_name: string | null
  area: string | null
  drone_code: string | null
  drone_name: string | null
  mission_name: string | null
  session_id: string | null
  session_number: string | null
  detected_at_site_time: string
  ai_confidence: number | null
  risk_level: RiskLevel
  risk_score: number | null
  verification_state: string
  status: EventStatus
  latitude: number | null
  longitude: number | null
  incident: { id: string; title: string; severity: string; status: string; incident_ref: string | null
              dispatched_guard_name: string | null; dispatched_at: string | null; guard_arrived_at: string | null } | null
  actions: { action: string; method: string; path: string; permission: string }[]
}

export interface GuardOption { user_id: string; full_name: string; available: boolean; busy_incident_id: string | null
                               distance_m: number | null; position_source: string | null; position_at: string | null
                               position_age_s: number | null }

export interface Verification { id: string; status: string; hold_seconds: number; reason: string | null
                                result: Record<string, unknown>; created_at: string; requested_by_name?: string | null }

export const listEvents = (params: { site_id?: string; session_id?: string; status?: string; open_only?: boolean
                                     risk_level?: string; from?: string; to?: string; limit?: number; offset?: number } = {}) =>
  apiClient.get<Paged<DroneEvent>>('/api/v1/drone-events', { params: { limit: 50, ...params } }).then((r) => r.data)
export const getEvent = (id: string) => apiClient.get<EventDetail>(`/api/v1/drone-events/${id}`).then((r) => r.data)
export const getEventCard = (id: string) => apiClient.get<EventCard>(`/api/v1/drone-events/${id}/card`).then((r) => r.data)
export const getEventCctv = (id: string) => apiClient.get<CctvView>(`/api/v1/drone-events/${id}/cctv`).then((r) => r.data)
export const correlateEvent = (id: string) =>
  apiClient.post<CctvView>(`/api/v1/drone-events/${id}/correlate`).then((r) => r.data)
export const decideEvent = (id: string, decision: 'acknowledge' | 'investigate' | 'escalate' | 'resolve' | 'false-positive',
                            body: { note?: string; reason?: string } = {}) =>
  apiClient.post(`/api/v1/drone-events/${id}/${decision}`, body).then((r) => r.data)
export const openIncident = (id: string, reason?: string) =>
  apiClient.post<{ incident: { id: string; incident_ref: string | null }; created: boolean }>(
    `/api/v1/drone-events/${id}/incident`, { reason: reason || null }).then((r) => r.data)
export const listGuards = (id: string) => apiClient.get<GuardOption[]>(`/api/v1/drone-events/${id}/guards`).then((r) => r.data)
export const dispatchGuard = (id: string, guardUserId?: string, notes?: string) =>
  apiClient.post(`/api/v1/drone-events/${id}/dispatch`, { guard_user_id: guardUserId || null, notes: notes || null })
    .then((r) => r.data)
export const verifyWithDrone = (id: string, holdSeconds: number, reason?: string) =>
  apiClient.post<{ verification: Verification }>(`/api/v1/drone-events/${id}/verify-with-drone`,
    { hold_seconds: holdSeconds, reason: reason || null }).then((r) => r.data)
export const listVerifications = (id: string) =>
  apiClient.get<Verification[]>(`/api/v1/drone-events/${id}/verifications`).then((r) => r.data)

/** Live camera video (the /cameras/{id}/streams/... endpoints) takes the token
 *  in the URL, as the Live Wall does, because <video> cannot send a header. */
export const mediaUrl = (path: string, token: string | null) =>
  `${apiClient.defaults.baseURL ?? ''}${path}${path.includes('?') ? '&' : '?'}token=${token ?? ''}`

/** A drone snapshot or clip. The media endpoint authenticates by header only,
 *  so it is fetched here and handed to <img>/<video> as an object URL. A 409
 *  means the file is held at the site; its message says where. */
export const fetchMediaBlob = (mediaId: string) =>
  apiClient.get<Blob>(`/api/v1/drone-media/${mediaId}/file`, { responseType: 'blob' }).then((r) => r.data)

/** A 409's message from a blob request arrives as a Blob, not JSON. */
export async function blobApiError(err: unknown): Promise<string> {
  const data = (err as { response?: { data?: unknown } })?.response?.data
  if (data instanceof Blob) {
    try {
      const body = JSON.parse(await data.text()) as { detail?: unknown }
      if (typeof body.detail === 'string') return body.detail
    } catch {
      /* not JSON */
    }
  }
  return apiError(err)
}

/** A readable message from an API error: the backend's own reason when it gave one. */
export function apiError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string }
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

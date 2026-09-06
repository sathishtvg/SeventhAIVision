export interface Tenant {
  id: string
  name: string
  slug: string
  subdomain: string | null
  custom_domain: string | null
  timezone: string
  branding: Record<string, string> | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface User {
  id: string
  tenant_id: string
  role_id: number
  email: string
  full_name: string | null
  is_active: boolean
  last_login_at: string | null
  failed_login_count: number
  locked_until: string | null
  created_at: string
  updated_at: string
  // Employee profile fields (ShiftSecure Phase 1) — nullable, filled in incrementally.
  nric_fin: string | null
  date_of_birth: string | null
  nationality: string | null
  phone: string | null
  address: string | null
  work_pass_type: 'citizen' | 'pr' | 'ep' | 'sp' | 'wp' | null
  work_pass_expiry: string | null
  employment_type: 'full_time' | 'part_time' | 'contract' | null
  designation: string | null
  department: string | null
  date_joined: string | null
  bank_name: string | null
  bank_account_number: string | null
  emergency_contact_name: string | null
  emergency_contact_phone: string | null
  hourly_rate: number | null
  daily_rate: number | null
  /** Permanent photo shown before a guard checks in. */
  profile_photo_path?: string | null
  monthly_salary: number | null
  // List-only fields (GET /users grid round) — present only in the list
  // response, not GET /users/{id} or POST /users.
  site_names?: string[]
  documents_total_count?: number
  documents_expiring_count?: number
  documents_expired_count?: number
}

export interface EmployeeDocument {
  id: string
  user_id: string
  document_type: 'passport' | 'work_pass' | 'certification' | 'other'
  document_number: string | null
  issuing_body: string | null
  issue_date: string | null
  expiry_date: string | null
  storage_path: string | null
  notes: string | null
  created_at: string
  updated_at?: string
}

export interface Camera {
  id: string
  tenant_id: string
  name: string
  location: string | null
  latitude: number | null
  longitude: number | null
  ai_modules_enabled: string[]
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface Stream {
  id: string
  camera_id: string
  protocol: string
  url: string
  status: 'online' | 'degraded' | 'offline'
  last_frame_at: string | null
  has_credentials?: boolean
  continuous_recording?: boolean
  created_at: string
  updated_at: string
}

export interface CameraHealthEvent {
  id: string
  event_type: string
  detail: string | null
  occurred_at: string
}

export type AlertSeverity = 'info' | 'low' | 'medium' | 'high' | 'critical'
export type AlertStatus = 'open' | 'acknowledged' | 'resolved' | 'dismissed'

export interface Alert {
  id: string
  detection_id: string | null
  camera_id: string
  module_type: string
  severity: AlertSeverity
  alert_code: string | null
  title: string
  message: string | null
  status: AlertStatus
  acknowledged_by_user_id: string | null
  acknowledged_at: string | null
  acknowledged_by_name: string | null
  acknowledged_via: 'web' | 'mobile' | null
  fp_reason: string | null
  fp_marked_by_user_id: string | null
  fp_marked_at: string | null
  fp_marked_by_name: string | null
  fp_marked_via: 'web' | 'mobile' | null
  assigned_to_user_id: string | null
  assigned_at: string | null
  assigned_to_name: string | null
  assigned_to_email: string | null
  site_name: string | null
  camera_name: string | null
  escalated_at: string | null
  original_severity: string | null
  correlation_id: string | null
  created_at: string
}

export type IncidentSeverity = 'low' | 'medium' | 'high' | 'critical'
export type IncidentStatus = 'open' | 'investigating' | 'resolved' | 'closed'

export interface Incident {
  id: string
  alert_id: string | null
  camera_id: string
  title: string
  description: string | null
  alert_code: string | null
  severity: IncidentSeverity
  status: IncidentStatus
  is_auto_created: boolean
  assigned_to_user_id: string | null
  resolved_at: string | null
  created_at: string
  updated_at: string
}

export interface IncidentNote {
  id: string
  incident_id: string
  author_user_id: string | null
  note: string
  created_at: string
}

export interface Detection {
  id: string
  camera_id: string
  module_type: string
  confidence: number | null
  bounding_box: Record<string, number> | null
  detected_at: string
}

/**
 * Screenshot pointers every detection-event row now carries.
 *
 * All eleven AI modules already wrote an evidence snapshot at detection time;
 * the list endpoints simply never returned a pointer to it. Both are nullable:
 * a plate crop only exists for LPR, and a frame can be absent if the worker's
 * best-effort snapshot write failed without failing the detection itself.
 */
export interface EvidencePointers {
  frame_evidence_id: string | null
  plate_evidence_id: string | null
}

export interface LprEvent extends EvidencePointers {
  detection_id: string
  camera_id: string
  plate_number: string
  plate_confidence: number | null
  direction: string | null
  vehicle_type: string | null
  vehicle_color: string | null
  watchlist_match: 'allow' | 'block' | null
  created_at: string
}

export interface FaceEvent extends EvidencePointers {
  detection_id: string
  camera_id: string
  matched_watchlist_id: string | null
  match_confidence: number | null
  watchlist_match: 'allow' | 'block' | null
  created_at: string
}

export interface IntrusionEvent extends EvidencePointers {
  detection_id: string
  camera_id: string
  zone_id: string
  person_bbox: Record<string, number>
  dwell_time_seconds: number | null
  created_at: string
}

export interface PpeEvent extends EvidencePointers {
  detection_id: string
  camera_id: string
  person_bbox: Record<string, number>
  items_detected: string[]
  items_missing: string[]
  severity: AlertSeverity
  created_at: string
}

export interface CrowdEvent extends EvidencePointers {
  detection_id: string
  camera_id: string
  zone_id: string
  person_count: number
  max_capacity: number
  density_ratio: number
  created_at: string
}

export interface FireSmokeEvent extends EvidencePointers {
  detection_id: string
  camera_id: string
  detection_type: 'fire' | 'smoke'
  confidence: number
  bbox: Record<string, number>
  created_at: string
}

export interface WeaponEvent extends EvidencePointers {
  detection_id: string
  camera_id: string
  weapon_type: string
  confidence: number
  bbox: Record<string, number>
  created_at: string
}

export interface BehaviorEvent extends EvidencePointers {
  detection_id: string
  camera_id: string
  zone_id: string | null
  behavior_type: string
  confidence: number | null
  duration_seconds: number | null
  created_at: string
}

export interface TamperingEvent extends EvidencePointers {
  detection_id: string
  detected_at: string
  camera_id: string
  tampering_type: string
  score: number | null
  reason: string | null
  camera_name: string | null
}

export interface AbandonedObjectEvent extends EvidencePointers {
  detection_id: string
  detected_at: string
  camera_id: string
  object_class: string
  dwell_seconds: number | null
  bbox: Record<string, number> | null
  camera_name: string | null
}

export interface FallEvent extends EvidencePointers {
  detection_id: string
  detected_at: string
  camera_id: string
  fall_confidence: number | null
  person_bbox: Record<string, number> | null
  camera_name: string | null
}

export type NotificationChannelType = 'email' | 'sms' | 'webhook'
export type NotificationLogStatus = 'pending' | 'sent' | 'failed'

export interface NotificationChannel {
  id: string
  tenant_id: string
  name: string
  channel_type: NotificationChannelType
  config: Record<string, unknown>
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface NotificationRule {
  id: string
  tenant_id: string
  channel_id: string
  min_severity: string
  module_types: string[]
  alert_codes: string[]
  trigger_events: string[]
  is_active: boolean
  created_at: string
}

export interface NotificationLog {
  id: string
  tenant_id: string
  alert_id: string
  channel_id: string | null
  channel_type: NotificationChannelType
  status: NotificationLogStatus
  error_detail: string | null
  sent_at: string | null
  created_at: string
}

export interface Evidence {
  id: string
  detection_id: string | null
  incident_id: string | null
  media_type: 'image' | 'video'
  storage_path: string
  checksum_sha256: string | null
  captured_at: string
  /* Context resolved server-side (site/camera/analytic). All optional: a
     capture whose detection has aged out of retention, or one attached to an
     incident rather than a detection, legitimately has no camera or module. */
  capture_kind?: string | null
  module_type?: string | null
  confidence?: number | null
  camera_id?: string | null
  camera_name?: string | null
  camera_location?: string | null
  site_id?: string | null
  site_name?: string | null
  incident_title?: string | null
}

export interface AuditLog {
  id: string
  user_id: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  ip_address: string | null
  detail: Record<string, unknown> | null
  created_at: string
}

/** The ten vehicle categories from migration 0076. `category` is the field
 * admins set; `list_type` below is derived from it by a database trigger and
 * is read-only from the client's point of view. */
export type VehicleCategory =
  | 'whitelist' | 'blacklist' | 'watchlist' | 'vip' | 'staff'
  | 'visitor' | 'contractor' | 'emergency' | 'government' | 'unknown'

export interface WatchlistEntry {
  id: string
  plate_number: string
  /** Derived from `category` by a DB trigger — never send this, it will be
   * overwritten. Kept in the type because the API still returns it and the
   * LPR worker still reads it. */
  list_type: 'allow' | 'block'
  category: VehicleCategory
  owner_name: string | null
  company: string | null
  vehicle_type: string | null
  vehicle_color: string | null
  valid_from: string | null
  valid_to: string | null
  remarks: string | null
  reason: string | null
  is_active: boolean
  expires_at: string | null
  created_at: string
}

export interface FaceWatchlistEntry {
  id: string
  person_name: string
  list_type: 'allow' | 'block'
  is_active: boolean
  expires_at: string | null
  created_at: string
}

export interface CrowdZone {
  id: string
  camera_id: string
  name: string
  polygon: Array<{ x: number; y: number }>
  max_capacity: number
  severity: 'low' | 'medium' | 'high' | 'critical'
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface AnalyticsSummary {
  open_alerts: number
  open_incidents: number
  active_cameras: number
  detections_today: number
  alerts_today: number
  alerts_7d: number
  detections_7d: number
  active_recordings: number
  active_cameras_total?: number
  // Sized by the `days` query param rather than fixed at 7.
  detections_window?: number
  alerts_window?: number
  // Equal-length period immediately before the window, for period-on-period.
  detections_window_prev?: number
  alerts_window_prev?: number
  alerts_window_critical?: number
  alerts_window_unresolved?: number
  // ISO timestamps, null when nothing has ever been recorded.
  last_detection_at?: string | null
  last_alert_at?: string | null
}

export interface AnalyticsCount {
  label: string
  count: number
}

export interface AnalyticsTrendPoint {
  day: string
  count: number
}

export interface TopCamera {
  camera_id: string
  camera_name: string | null
  alert_count: number
}

export interface IncidentResolutionTime {
  resolved_count: number
  avg_hours: number | null
  p95_hours: number | null
}

export interface HeatmapCamera {
  camera_id: string
  camera_name: string
  location: string | null
  latitude: number | null
  longitude: number | null
  site_id: string | null
  site_name: string | null
  stream_status: 'online' | 'degraded' | 'offline'
  total_detections: number
  total_alerts: number
  open_alerts: number
  critical_alerts: number
  high_alerts: number
}

export interface RestrictedZone {
  id: string
  camera_id: string
  name: string
  polygon: Array<{ x: number; y: number }>
  severity: 'low' | 'medium' | 'high' | 'critical'
  is_active: boolean
  bypass_until: string | null
  schedule_enabled: boolean
  schedule_timezone: string
  active_days: number[]
  active_start_time: string
  active_end_time: string
  applies_to_modules: string[]
  is_currently_active: boolean
  created_at: string
}

export interface TenantSetting {
  setting_key: string
  setting_value: number
  updated_by_user_id: string
  updated_at: string
}

export interface SystemVersion {
  version: string
  git_sha: string
  build_date: string
}

export interface Site {
  /** Officers this site is staffed for, per shift type — what the roster
   *  auto-scheduler fills. */
  day_guards_required?: number
  night_guards_required?: number
  id: string
  name: string
  address: string | null
  description: string | null
  latitude: number | null
  longitude: number | null
  geofence_radius_meters: number | null
  /** Drawn boundary, in drawing order. When set it replaces the radius for
   *  attendance check-in checks — see services/geofence.py. */
  geofence_polygon: { lat: number; lng: number }[] | null
  /** Minutes of lateness tolerated at this site; null = tenant default. */
  late_grace_minutes: number | null
  is_active: boolean
  camera_count: number
  client_id: string | null
  bill_rate: number | null
  client_name: string | null
  /** Visitor Management (migration 0077) — VMS is switched on per site and
   * driven by that site's own ANPR cameras. All null/false means the site
   * uses the manual visitor flow, which is the default. */
  vms_enabled: boolean
  entry_lpr_camera_id: string | null
  exit_lpr_camera_id: string | null
  /** null = this site does not meter parking, so nothing can ever overstay. */
  free_parking_minutes: number | null
  created_at: string
  updated_at: string
}

export interface Recording {
  id: string
  camera_id: string
  stream_id: string
  site_id: string | null
  status: 'recording' | 'completed' | 'failed'
  started_at: string
  ended_at: string | null
  file_path: string | null
  file_size_bytes: number | null
  duration_seconds: number | null
  created_at: string
}

export interface ModuleLicense {
  module_type: string
  is_enabled: boolean
  max_cameras: number | null
  licensed_at: string | null
  expires_at: string | null
  notes: string | null
  updated_at: string | null
}

export interface StreamValidationResult {
  valid: boolean
  resolution_w: number | null
  resolution_h: number | null
  fps: number | null
  latency_ms: number | null
  error: string | null
}

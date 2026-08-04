import { apiClient } from './client'

export interface PatrolRoute {
  id: string
  site_id: string
  name: string
  site_name?: string
  checkpoint_count: number
}

export interface PatrolCheckpoint {
  id: string
  route_id: string
  sequence: number
  name: string
  qr_code: string | null
  nfc_tag_id: string | null
}

export interface PatrolSession {
  id: string
  route_id: string
  guard_user_id: string
  status: 'in_progress' | 'completed'
  started_at: string
  completed_at: string | null
  route_name?: string
}

export const getRoutes = (siteId?: string) =>
  apiClient
    .get<PatrolRoute[]>('/api/v1/patrols/routes', { params: { site_id: siteId } })
    .then((r) => r.data)

export const getRouteDetail = (routeId: string) =>
  apiClient
    .get<PatrolRoute & { checkpoints: PatrolCheckpoint[] }>(`/api/v1/patrols/routes/${routeId}`)
    .then((r) => r.data)

export const startSession = (routeId: string, shiftId?: string) =>
  apiClient
    .post<PatrolSession>(`/api/v1/patrols/routes/${routeId}/sessions`, null, {
      params: { shift_id: shiftId },
    })
    .then((r) => r.data)

export const completeSession = (sessionId: string) =>
  apiClient.post(`/api/v1/patrols/sessions/${sessionId}/complete`).then((r) => r.data)

export const getSession = (sessionId: string) =>
  apiClient.get(`/api/v1/patrols/sessions/${sessionId}`).then((r) => r.data)

export const scanCheckpoint = (
  sessionId: string,
  checkpointId: string,
  scanMethod: 'qr' | 'nfc' | 'manual',
  scannedCode?: string,
  latitude?: number,
  longitude?: number,
) =>
  apiClient
    .post(`/api/v1/patrols/sessions/${sessionId}/scan`, {
      checkpoint_id: checkpointId,
      scan_method: scanMethod,
      scanned_code: scannedCode,
      latitude,
      longitude,
    })
    .then((r) => r.data)

// ── Shifts + Handover ─────────────────────────────────────────────────────────

export interface Shift {
  id: string
  guard_user_id: string
  site_id: string | null
  scheduled_start: string
  scheduled_end: string
  actual_start: string | null
  actual_end: string | null
  status: string
  is_late: boolean | null
  late_minutes: number | null
  overtime_minutes: number | null
  is_within_geofence: boolean | null
  on_break: boolean
  guard_name: string | null
  site_name: string | null
}

/**
 * NOTE ON SCOPING: GET /api/v1/shifts is not self-scoped server-side — passing
 * no guard_user_id returns every shift in the tenant. Callers showing a guard
 * "their" schedule must pass their own id explicitly (see getMyShifts). That
 * is client-side scoping, which is a presentation choice and not a security
 * boundary; the endpoint's authorization is a separate open question raised
 * with the team rather than silently narrowed here, because control-room roles
 * legitimately need the full board.
 */
export const getShifts = (params?: { shift_status?: string; guard_user_id?: string }) =>
  apiClient.get<Shift[]>('/api/v1/shifts', { params }).then((r) => r.data)

/** A guard's own schedule. guard_user_id is required, never optional. */
export const getMyShifts = (guardUserId: string, params?: { shift_status?: string }) =>
  apiClient
    .get<Shift[]>('/api/v1/shifts', { params: { ...params, guard_user_id: guardUserId } })
    .then((r) => r.data)

function buildCheckinFormData(photoUri: string): FormData {
  const form = new FormData()
  // React Native's fetch/FormData accepts this {uri,name,type} shape for file
  // fields — not a real Blob, but axios's RN adapter handles it correctly.
  form.append('photo', { uri: photoUri, name: 'checkin.jpg', type: 'image/jpeg' } as any)
  return form
}

export const startShift = (
  shiftId: string,
  photoUri: string,
  isMockLocation: boolean,
  latitude?: number,
  longitude?: number,
) =>
  apiClient
    .post(`/api/v1/shifts/${shiftId}/start`, buildCheckinFormData(photoUri), {
      params: { latitude, longitude, is_mock_location: isMockLocation },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data)

export const endShift = (
  shiftId: string,
  photoUri: string,
  isMockLocation: boolean,
  latitude?: number,
  longitude?: number,
) =>
  apiClient
    .post(`/api/v1/shifts/${shiftId}/end`, buildCheckinFormData(photoUri), {
      params: { latitude, longitude, is_mock_location: isMockLocation },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data)

export const startBreak = (shiftId: string) =>
  apiClient.post(`/api/v1/shifts/${shiftId}/break/start`).then((r) => r.data)

export const endBreak = (shiftId: string) =>
  apiClient.post(`/api/v1/shifts/${shiftId}/break/end`).then((r) => r.data)

export const generateHandover = (shiftId: string, outgoingNotes?: string) =>
  apiClient
    .post(`/api/v1/shifts/${shiftId}/handover`, null, {
      params: { outgoing_notes: outgoingNotes },
    })
    .then((r) => r.data)

export interface ShiftBriefing {
  shift: {
    id: string; site_name: string | null; guard_name: string | null
    scheduled_start: string | null; scheduled_end: string | null; status: string
  }
  active_work_permits: Array<{
    id: string; permit_number: string | null; work_description: string | null
    contractor_name: string; workers_count: number | null; start_at: string; end_at: string
    status: string; safety_briefing_done: boolean
  }>
  expected_visitors: Array<{
    id: string; full_name: string; company: string | null; host_name: string | null
    purpose: string | null; expected_from: string | null; expected_until: string | null
    status: string; visitor_email: string | null
  }>
  open_alerts: Array<{
    id: string; module_type: string; severity: string; title: string
    status: string; created_at: string; camera_name: string | null
  }>
  pending_deliveries: Array<{
    id: string; tracking_number: string | null; carrier: string | null
    sender_company: string | null; recipient_name: string | null; status: string
    expected_at: string | null
  }>
  previous_handover: {
    outgoing_guard_name: string | null; outgoing_notes: string | null
    open_incidents_count: number; open_alerts_count: number; created_at: string
  } | null
  alarm_panels: Array<{
    id: string; name: string; arm_state: string; panel_state: string; is_active: boolean
  }>
  summary: {
    work_permits_count: number; visitors_count: number
    open_alerts_count: number; deliveries_count: number; has_previous_handover: boolean
    panels_armed: number; panels_total: number
  }
}

export const getShiftBriefing = (shiftId: string) =>
  apiClient.get<ShiftBriefing>(`/api/v1/shifts/${shiftId}/briefing`).then((r) => r.data)

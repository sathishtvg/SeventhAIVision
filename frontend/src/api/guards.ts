import { apiClient } from './client'

// ── Shifts ────────────────────────────────────────────────────────────────────

export const getShifts = (params?: { site_id?: string; guard_user_id?: string; shift_status?: string }) =>
  apiClient.get('/api/v1/shifts', { params }).then((r) => r.data)

export const createShift = (data: {
  guard_user_id: string
  site_id: string
  scheduled_start: string
  scheduled_end: string
  notes?: string
}) => apiClient.post('/api/v1/shifts', data).then((r) => r.data)

export const startShift = (shiftId: string, latitude?: number, longitude?: number) =>
  apiClient
    .post(`/api/v1/shifts/${shiftId}/start`, null, { params: { latitude, longitude } })
    .then((r) => r.data)

export const endShift = (shiftId: string, handoverNotes?: string, latitude?: number, longitude?: number) =>
  apiClient
    .post(`/api/v1/shifts/${shiftId}/end`, null, {
      params: { handover_notes: handoverNotes, latitude, longitude },
    })
    .then((r) => r.data)

export const startBreak = (shiftId: string) =>
  apiClient.post(`/api/v1/shifts/${shiftId}/break/start`).then((r) => r.data)

export const endBreak = (shiftId: string) =>
  apiClient.post(`/api/v1/shifts/${shiftId}/break/end`).then((r) => r.data)

export const generateHandover = (shiftId: string, incomingGuardId?: string, outgoingNotes?: string) =>
  apiClient
    .post(`/api/v1/shifts/${shiftId}/handover`, null, {
      params: { incoming_guard_id: incomingGuardId, outgoing_notes: outgoingNotes },
    })
    .then((r) => r.data)

export const getHandover = (shiftId: string) =>
  apiClient.get(`/api/v1/shifts/${shiftId}/handover`).then((r) => r.data)

// ── Patrol Routes & Sessions ──────────────────────────────────────────────────

export const getRoutes = (siteId?: string) =>
  apiClient.get('/api/v1/patrols/routes', { params: { site_id: siteId } }).then((r) => r.data)

export const createRoute = (data: { site_id: string; name: string; description?: string }) =>
  apiClient.post('/api/v1/patrols/routes', data).then((r) => r.data)

export const getRoute = (routeId: string) =>
  apiClient.get(`/api/v1/patrols/routes/${routeId}`).then((r) => r.data)

export const addCheckpoint = (
  routeId: string,
  data: { sequence: number; name: string; latitude?: number; longitude?: number; nfc_tag_id?: string; qr_code?: string }
) => apiClient.post(`/api/v1/patrols/routes/${routeId}/checkpoints`, data).then((r) => r.data)

export const startSession = (routeId: string, shiftId?: string) =>
  apiClient
    .post(`/api/v1/patrols/routes/${routeId}/sessions`, null, { params: { shift_id: shiftId } })
    .then((r) => r.data)

export const completeSession = (sessionId: string) =>
  apiClient.post(`/api/v1/patrols/sessions/${sessionId}/complete`).then((r) => r.data)

// ── DOB ───────────────────────────────────────────────────────────────────────

export const getDobEntries = (params?: {
  site_id?: string
  shift_id?: string
  entry_type?: string
  date_from?: string
  date_until?: string
  limit?: number
}) => apiClient.get('/api/v1/dob', { params }).then((r) => r.data)

export const createDobEntry = (data: {
  entry_type?: string
  body: string
  severity?: string
  site_id?: string
  shift_id?: string
  reference_id?: string
}) => apiClient.post('/api/v1/dob', data).then((r) => r.data)

// ── Visitors ──────────────────────────────────────────────────────────────────

export const getVisitors = (params?: { site_id?: string; is_active?: boolean }) =>
  apiClient.get('/api/v1/visitors', { params }).then((r) => r.data.items)

export const createVisitor = (data: {
  full_name: string
  id_number?: string
  company?: string
  host_name?: string
  purpose?: string
  vehicle_plate?: string
  site_id?: string
  expected_from?: string
  expected_until?: string
}) => apiClient.post('/api/v1/visitors', data).then((r) => r.data)

export const checkinVisitor = (data: {
  visitor_id?: string
  event_type?: string
  badge_number?: string
  notes?: string
  full_name?: string
  site_id?: string
}) => apiClient.post('/api/v1/visitors/checkin', data).then((r) => r.data)

export const getVisitorLogs = (params?: { site_id?: string; visitor_id?: string }) =>
  apiClient.get('/api/v1/visitors/logs', { params }).then((r) => r.data)

// ── SOS ───────────────────────────────────────────────────────────────────────

export const triggerSOS = (data: { latitude?: number; longitude?: number; message?: string }) =>
  apiClient.post('/api/v1/patrols/sos', data).then((r) => r.data)

// ── Dispatch ──────────────────────────────────────────────────────────────────

export const dispatchGuard = (incidentId: string, data: { guard_user_id: string; dispatch_notes?: string }) =>
  apiClient.post(`/api/v1/dispatch/incidents/${incidentId}`, data).then((r) => r.data)

export const guardArrived = (incidentId: string) =>
  apiClient.post(`/api/v1/dispatch/incidents/${incidentId}/arrived`).then((r) => r.data)

// ── 2FA ───────────────────────────────────────────────────────────────────────

export const get2FAStatus = () =>
  apiClient.get('/api/v1/2fa/status').then((r) => r.data)

export const setup2FA = () =>
  apiClient.get('/api/v1/2fa/setup').then((r) => r.data)

export const enable2FA = (totpCode: string) =>
  apiClient.post('/api/v1/2fa/enable', { totp_code: totpCode }).then((r) => r.data)

export const disable2FA = (totpCode: string) =>
  apiClient.delete('/api/v1/2fa', { data: { totp_code: totpCode } }).then((r) => r.data)

export const get2faPolicy = () =>
  apiClient.get<{ required: boolean; grace_hours: number }>('/api/v1/2fa/policy').then((r) => r.data)

export const set2faPolicy = (data: { required: boolean; grace_hours: number }) =>
  apiClient.put<{ required: boolean; grace_hours: number }>('/api/v1/2fa/policy', data).then((r) => r.data)

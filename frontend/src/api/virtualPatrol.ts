import { apiClient } from './client'

const BASE = '/api/v1/virtual-patrol'

export type ScheduleType = 'ONCE' | 'DAILY' | 'WEEKLY'
export type EmailFrequency = 'IMMEDIATE' | 'DAILY' | 'WEEKLY' | 'MONTHLY'
export type QuestionType =
  | 'YES_NO' | 'PASS_FAIL' | 'TEXT' | 'NUMBER' | 'SINGLE_CHOICE' | 'MULTI_CHOICE'
export type FailureAction =
  | 'NONE' | 'CREATE_INCIDENT' | 'RAISE_ALERT' | 'NOTIFY_SUPERVISOR'
export type SessionStatus =
  | 'SCHEDULED' | 'STARTED' | 'IN_PROGRESS' | 'COMPLETED'
  | 'PARTIALLY_COMPLETED' | 'MISSED' | 'CANCELLED' | 'FAILED'

export interface PatrolSchedule {
  id: string
  site_id: string
  site_name?: string
  name: string
  description: string | null
  timezone: string
  schedule_type: ScheduleType
  start_date: string
  end_date: string | null
  patrol_time: string
  /** ISO weekdays, 1 = Monday .. 7 = Sunday. WEEKLY only. */
  weekdays: number[]
  grace_minutes: number
  enabled: boolean
  assigned_user_id: string | null
  email_frequency: EmailFrequency
  camera_count?: number
}

export interface ScheduleCamera {
  id: string
  camera_id: string
  camera_name: string
  location: string | null
  sequence_no: number
  enabled: boolean
  timeout_seconds: number | null
  question_count?: number
}

export interface PatrolQuestion {
  id: string
  schedule_camera_id: string
  question_text: string
  question_type: QuestionType
  is_required: boolean
  sequence_no: number
  enabled: boolean
  options: string[] | null
  failure_action: FailureAction
}

export interface PatrolSession {
  id: string
  patrol_number: string
  /** Copied at creation — the schedule may since have been renamed or deleted. */
  schedule_name: string
  site_name: string
  officer_name: string | null
  scheduled_for: string
  started_at: string | null
  completed_at: string | null
  status: SessionStatus
  camera_count: number
  completed_camera_count: number
  exception_count?: number
}

export interface SessionCamera {
  id: string
  sequence_no: number
  camera_name: string
  location: string | null
  status: string
  snapshot_path: string | null
  snapshot_taken_at: string | null
  snapshot_error: string | null
  officer_notes: string | null
}

export interface SessionQuestion {
  id: string
  question_text: string
  question_type: QuestionType
  is_required: boolean
  sequence_no: number
  options: string[] | null
  failure_action: FailureAction
  answer_text: string | null
  answer_json: unknown
}

// ── Schedules ────────────────────────────────────────────────────────────────

export const listSchedules = (params?: { site_id?: string; enabled?: boolean }) =>
  apiClient.get<PatrolSchedule[]>(`${BASE}/schedules`, { params }).then((r) => r.data)

export const getSchedule = (id: string) =>
  apiClient
    .get<PatrolSchedule & { cameras: ScheduleCamera[] }>(`${BASE}/schedules/${id}`)
    .then((r) => r.data)

export const createSchedule = (body: Partial<PatrolSchedule>) =>
  apiClient.post<PatrolSchedule>(`${BASE}/schedules`, body).then((r) => r.data)

export const updateSchedule = (id: string, body: Partial<PatrolSchedule>) =>
  apiClient.put<PatrolSchedule>(`${BASE}/schedules/${id}`, body).then((r) => r.data)

export const setScheduleEnabled = (id: string, enabled: boolean) =>
  apiClient
    .patch(`${BASE}/schedules/${id}/status`, null, { params: { enabled } })
    .then((r) => r.data)

export const deleteSchedule = (id: string) =>
  apiClient.delete(`${BASE}/schedules/${id}`).then((r) => r.data)

// ── Cameras on a schedule ────────────────────────────────────────────────────

export const listScheduleCameras = (scheduleId: string) =>
  apiClient
    .get<ScheduleCamera[]>(`${BASE}/schedules/${scheduleId}/cameras`)
    .then((r) => r.data)

export const addScheduleCamera = (scheduleId: string, camera_id: string) =>
  apiClient
    .post<ScheduleCamera>(`${BASE}/schedules/${scheduleId}/cameras`, { camera_id })
    .then((r) => r.data)

/** Sends the whole order at once — the sequence constraint is deferred, so the
 *  intermediate states of a reorder are allowed and only the result must be
 *  valid. */
export const reorderScheduleCameras = (
  scheduleId: string,
  items: { schedule_camera_id: string; sequence_no: number }[],
) =>
  apiClient
    .put(`${BASE}/schedules/${scheduleId}/cameras/reorder`, items)
    .then((r) => r.data)

export const removeScheduleCamera = (scheduleId: string, scheduleCameraId: string) =>
  apiClient
    .delete(`${BASE}/schedules/${scheduleId}/cameras/${scheduleCameraId}`)
    .then((r) => r.data)

// ── Questions ────────────────────────────────────────────────────────────────

export const listQuestions = (scheduleCameraId: string) =>
  apiClient
    .get<PatrolQuestion[]>(`${BASE}/schedule-cameras/${scheduleCameraId}/questions`)
    .then((r) => r.data)

export const createQuestion = (
  scheduleCameraId: string,
  body: {
    question_text: string
    question_type: QuestionType
    is_required?: boolean
    options?: string[] | null
    failure_action?: FailureAction
  },
) =>
  apiClient
    .post<PatrolQuestion>(`${BASE}/schedule-cameras/${scheduleCameraId}/questions`, body)
    .then((r) => r.data)

export const deleteQuestion = (questionId: string) =>
  apiClient.delete(`${BASE}/questions/${questionId}`).then((r) => r.data)

// ── Email recipients ─────────────────────────────────────────────────────────

export interface EmailRecipient { id: string; schedule_id: string; email: string }

export const listRecipients = (scheduleId: string) =>
  apiClient
    .get<EmailRecipient[]>(`${BASE}/schedules/${scheduleId}/email-recipients`)
    .then((r) => r.data)

export const addRecipient = (scheduleId: string, email: string) =>
  apiClient
    .post<EmailRecipient>(`${BASE}/schedules/${scheduleId}/email-recipients`, null, {
      params: { email },
    })
    .then((r) => r.data)

export const deleteRecipient = (recipientId: string) =>
  apiClient.delete(`${BASE}/email-recipients/${recipientId}`).then((r) => r.data)

// ── Sessions ─────────────────────────────────────────────────────────────────

export const listSessions = (params?: { site_id?: string; status?: string }) =>
  apiClient.get<PatrolSession[]>(`${BASE}/sessions`, { params }).then((r) => r.data)

export const getSession = (id: string) =>
  apiClient
    .get<PatrolSession & { cameras: SessionCamera[] }>(`${BASE}/sessions/${id}`)
    .then((r) => r.data)

export const myPatrols = () =>
  apiClient.get<PatrolSession[]>(`${BASE}/my-patrols`).then((r) => r.data)

export const startSession = (id: string) =>
  apiClient.post(`${BASE}/sessions/${id}/start`).then((r) => r.data)

export const currentCamera = (sessionId: string) =>
  apiClient
    .get<{ camera: SessionCamera | null; questions?: SessionQuestion[]; message?: string }>(
      `${BASE}/sessions/${sessionId}/current-camera`,
    )
    .then((r) => r.data)

/** Returns ok:false for an unreachable camera rather than throwing — that is an
 *  expected outcome of a patrol which the officer must see and may retry, not a
 *  failed request. */
export const captureSnapshot = (sessionId: string, sessionCameraId: string) =>
  apiClient
    .post<{ ok: boolean; status?: string; error?: string; path?: string }>(
      `${BASE}/sessions/${sessionId}/cameras/${sessionCameraId}/snapshot`,
    )
    .then((r) => r.data)

export const submitAnswers = (
  sessionId: string,
  sessionCameraId: string,
  body: { answers: { session_question_id: string; answer: unknown }[]; officer_notes?: string },
) =>
  apiClient
    .post(`${BASE}/sessions/${sessionId}/cameras/${sessionCameraId}/answers`, body)
    .then((r) => r.data)

export const completeCamera = (sessionId: string, sessionCameraId: string) =>
  apiClient
    .post(`${BASE}/sessions/${sessionId}/cameras/${sessionCameraId}/complete`)
    .then((r) => r.data)

export const completePatrol = (sessionId: string) =>
  apiClient.post(`${BASE}/sessions/${sessionId}/complete`).then((r) => r.data)

/** A PDF link cannot carry an Authorization header — same query-param-JWT
 *  pattern as payslipPdfUrl in api/payroll.ts. */
export const patrolReportPdfUrl = (sessionId: string, token: string | null) =>
  token
    ? `${apiClient.defaults.baseURL}${BASE}/sessions/${sessionId}/report/pdf?token=${token}`
    : null

/** An <img> cannot carry an Authorization header, so the token rides in the
 *  query string — same pattern as evidence images. The server still reads the
 *  tenant FROM the token, so this is authorization, not obscurity. */
export const snapshotImageUrl = (
  sessionId: string,
  sessionCameraId: string,
  token: string | null,
) =>
  token
    ? `${apiClient.defaults.baseURL}${BASE}/sessions/${sessionId}/cameras/${sessionCameraId}/snapshot?token=${token}`
    : null

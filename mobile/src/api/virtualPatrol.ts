/**
 * Virtual patrol, from the phone.
 *
 * THE FEATURE EXISTED EVERYWHERE EXCEPT HERE. The backend has the whole
 * execution flow — my-patrols, start, current-camera, snapshot, answers,
 * complete — and the guard role already carries vpatrol:execute and
 * vpatrol:read. The web app runs patrols; the phone had no idea the feature
 * existed. An officer doing a virtual patrol had to find a desk.
 *
 * Types mirror frontend/src/api/virtualPatrol.ts so the two clients describe
 * the same server in the same words.
 */
import { apiClient } from './client'

const BASE = '/api/v1/virtual-patrol'

/** The server's own values, upper case, copied from the web client. Getting
 *  these wrong is silent: a filter that never matches just shows an empty
 *  screen, which is exactly what the first version of this file did. */
export type SessionStatus =
  | 'SCHEDULED' | 'STARTED' | 'IN_PROGRESS' | 'COMPLETED'
  | 'PARTIALLY_COMPLETED' | 'MISSED' | 'CANCELLED' | 'FAILED'

/** Statuses my-patrols returns — it only ever lists outstanding work. */
export const OUTSTANDING: SessionStatus[] = ['SCHEDULED', 'STARTED', 'IN_PROGRESS']

/** A patrol that has been started and is part-way through. */
export const isRunning = (s: SessionStatus) => s === 'IN_PROGRESS' || s === 'STARTED'
/** The server's types, from services/virtual_patrol.py. 'CHOICE' is not one of
 *  them — an earlier version of this file invented it, and a SINGLE_CHOICE
 *  question would have rendered as a label with no way to answer it. */
export type QuestionType =
  | 'YES_NO' | 'PASS_FAIL' | 'TEXT' | 'NUMBER' | 'SINGLE_CHOICE' | 'MULTI_CHOICE'

/** What validate_answer() accepts for the two verdict types. Booleans are
 *  rejected: the server compares str(answer).upper() to these. */
export const VERDICT_OPTIONS: Record<string, [string, string]> = {
  YES_NO: ['YES', 'NO'],
  PASS_FAIL: ['PASS', 'FAIL'],
}

/** True when the answer counts as a problem reported — mirrors is_exception(). */
export const isNegative = (type: QuestionType, answer: unknown) =>
  (type === 'YES_NO' && answer === 'NO') || (type === 'PASS_FAIL' && answer === 'FAIL')

/** A patrol assigned to the signed-in officer. */
export interface MyPatrol {
  id: string
  patrol_number: string
  schedule_name: string
  scheduled_for: string
  status: SessionStatus
  camera_count: number
  completed_camera_count: number
  site_name?: string | null
}

export interface SessionCamera {
  id: string
  sequence_no: number
  camera_name: string
  location?: string | null
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
  options: string[] | null
  failure_action?: string
}

export interface PatrolSessionDetail extends MyPatrol {
  started_at: string | null
  completed_at: string | null
  cameras: SessionCamera[]
}

/** current-camera returns the next camera needing attention, or a done message. */
export interface CurrentCamera {
  camera: SessionCamera | null
  questions?: SessionQuestion[]
  message?: string
}

export interface AnswerIn {
  session_question_id: string
  answer: unknown
}

/** Only this officer's outstanding patrols: the endpoint filters by the token's
 *  user AND to SCHEDULED/STARTED/IN_PROGRESS, so everything returned is due. */
export const getMyPatrols = () =>
  apiClient.get<MyPatrol[]>(`${BASE}/my-patrols`).then((r) => r.data)

export const getPatrolSession = (sessionId: string) =>
  apiClient.get<PatrolSessionDetail>(`${BASE}/sessions/${sessionId}`).then((r) => r.data)

export const startPatrol = (sessionId: string) =>
  apiClient.post(`${BASE}/sessions/${sessionId}/start`).then((r) => r.data)

export const getCurrentCamera = (sessionId: string) =>
  apiClient.get<CurrentCamera>(`${BASE}/sessions/${sessionId}/current-camera`).then((r) => r.data)

/** Asks the server to grab a frame from the camera. Returns {ok: false, ...}
 *  rather than raising when the camera cannot be reached — a dead camera is a
 *  finding to record, not an error to swallow. */
export const captureSnapshot = (sessionId: string, sessionCameraId: string) =>
  apiClient
    .post<{ ok: boolean; error?: string; status?: string; snapshot_taken_at?: string }>(
      `${BASE}/sessions/${sessionId}/cameras/${sessionCameraId}/snapshot`,
      undefined,
      // Grabbing a frame means opening an RTSP stream server-side, which takes
      // longer than the client's 15s default. A timeout here looked to the
      // officer like the app could not reach the server at all, when the
      // server was still trying.
      { timeout: 60_000 },
    )
    .then((r) => r.data)

/**
 * Report that this camera is not working and move on.
 *
 * The patrol then finishes as PARTIALLY_COMPLETED, not COMPLETED: the camera is
 * recorded as unavailable with the reason, and nobody claims to have seen it.
 */
export const markCameraUnavailable = (
  sessionId: string, sessionCameraId: string, reason?: string,
) =>
  apiClient
    .post(`${BASE}/sessions/${sessionId}/cameras/${sessionCameraId}/unavailable`,
          { reason: reason ?? null })
    .then((r) => r.data)

export const submitAnswers = (
  sessionId: string,
  sessionCameraId: string,
  answers: AnswerIn[],
  officerNotes?: string,
) =>
  apiClient
    .post(`${BASE}/sessions/${sessionId}/cameras/${sessionCameraId}/answers`, {
      answers,
      officer_notes: officerNotes ?? null,
    })
    .then((r) => r.data)

export const completeCamera = (sessionId: string, sessionCameraId: string) =>
  apiClient
    .post(`${BASE}/sessions/${sessionId}/cameras/${sessionCameraId}/complete`)
    .then((r) => r.data)

export const completePatrol = (sessionId: string) =>
  apiClient.post(`${BASE}/sessions/${sessionId}/complete`).then((r) => r.data)

/**
 * URL for a captured frame.
 *
 * The token rides in the query string because an <Image> cannot carry an
 * Authorization header — the server's own design, and it reads the tenant from
 * that token, so it is not a raw path handed to the client. Same mechanism the
 * web client uses.
 */
export const snapshotUrl = (sessionId: string, sessionCameraId: string, accessToken: string) =>
  `${apiClient.defaults.baseURL}${BASE}/sessions/${sessionId}/cameras/${sessionCameraId}` +
  `/snapshot?token=${encodeURIComponent(accessToken)}`

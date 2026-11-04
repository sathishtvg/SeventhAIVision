/**
 * Camera checks and recording gaps API (backend/app/routers/camera_checks.py; Phase 6 of the known limits).
 *
 * A camera is listened to for 10 seconds every 15 minutes: the frame rate that arrived beside the one its own frame
 * times give, the longest stall, the frames missed, how long it took to answer and to send its first picture. A gap
 * is a stretch with no usable recording while it was meant to be recording all the time. The answer time and the
 * first picture are not the delay between the scene and the screen, and the server's note says so.
 */
import { apiClient } from './client'

const BASE = '/api/v1/camera-checks'

export type CheckResult = 'OK' | 'NO_CONNECTION' | 'SIGN_IN_REFUSED' | 'SECOND_CONNECTION_REFUSED' | 'NO_VIDEO'
  | 'TIMED_OUT' | 'FAILED'
export type LimitKey = 'least_rate_pct' | 'most_stall_ms' | 'most_missed_pct' | 'most_answer_ms'
  | 'most_first_picture_ms' | 'most_gap_s'
export type GapKind = 'NOT_RECORDED' | 'EMPTY' | 'FILE_MISSING'

export interface Check {
  checked_at: string
  result: CheckResult
  /** The server's words for the result: "Checked", or why the check could not run. */
  result_text: string
  listened_ms: number | null
  frames: number | null
  stated_rate: number | null
  received_rate: number | null
  longest_stall_ms: number | null
  missed_pct: number | null
  answer_ms: number | null
  first_picture_ms: number | null
  /** The limits it broke when it was made, in the server's words. */
  breaks: { limit: LimitKey; text: string }[]
  /** Older than the server goes by: shown, not judged. */
  stale: boolean
}

export interface CheckedStream {
  stream_id: string
  protocol: string
  stream_status: string
  records_all_the_time: boolean
  /** Why it is not checked, or null when it is. */
  not_checked: string | null
  check: Check | null
}

export interface CheckedCamera {
  camera_id: string
  camera_name: string
  site_id: string | null
  site_name: string | null
  streams: CheckedStream[]
  /** Recording gaps over the limit in the last 24 hours. */
  gaps_24h: { gaps: number; longest_s: number | null; open: boolean }
}

export interface Latest {
  cameras: CheckedCamera[]
  as_of: string
  note: string
  stale_after_s: number
  every_s: number
}

export interface Gap {
  stream_id: string
  kind: GapKind
  kind_text: string
  started_at: string
  /** Null while it is still going on. */
  ended_at: string | null
  seconds: number
  camera_was_offline: boolean
  limit_s: number
  over_limit: boolean
}

export type Limits = Record<LimitKey, number> & { updated_at: string | null; updated_by_name: string | null }

export interface CameraWeek {
  camera: { id: string; name: string; site_id: string | null; site_name: string | null }
  days: number
  checks: (Check & { stream_id: string })[]
  gaps: Gap[]
  limits: Limits
  as_of: string
  note: string
}

export interface LimitSettings extends Limits {
  defaults: Record<LimitKey, number>
  ranges: Record<LimitKey, { least: number; most: number }>
  labels: Record<LimitKey, string>
  may_set: boolean
}

export const LIMIT_KEYS: LimitKey[] = ['least_rate_pct', 'most_stall_ms', 'most_missed_pct', 'most_answer_ms',
  'most_first_picture_ms', 'most_gap_s']

export const getLatestChecks = (params: { site_id?: string; camera_id?: string } = {}) =>
  apiClient.get<Latest>(`${BASE}/latest`, { params }).then((r) => r.data)

export const getCameraWeek = (cameraId: string, days = 7) =>
  apiClient.get<CameraWeek>(`${BASE}/cameras/${cameraId}`, { params: { days } }).then((r) => r.data)

export const getCheckLimits = () => apiClient.get<LimitSettings>(`${BASE}/settings`).then((r) => r.data)

export const setCheckLimits = (body: Record<LimitKey, number>) =>
  apiClient.put<LimitSettings>(`${BASE}/settings`, body).then((r) => r.data)

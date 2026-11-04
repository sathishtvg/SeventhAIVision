/**
 * A camera's latest check and its recording gaps (backend/app/routers/camera_checks.py; Phase 6 of the known
 * limits, 4 November 2026). Read by whoever may see the camera; the seven days and the limits are on the web.
 */
import { apiClient } from './client'

export type CheckResult = 'OK' | 'NO_CONNECTION' | 'SIGN_IN_REFUSED' | 'SECOND_CONNECTION_REFUSED' | 'NO_VIDEO'
  | 'TIMED_OUT' | 'FAILED'

export interface Check {
  checked_at: string
  result: CheckResult
  result_text: string
  stated_rate: number | null
  received_rate: number | null
  longest_stall_ms: number | null
  missed_pct: number | null
  answer_ms: number | null
  first_picture_ms: number | null
  breaks: { limit: string; text: string }[]
  stale: boolean
}

export interface CheckedStream {
  stream_id: string
  not_checked: string | null
  check: Check | null
}

export interface CheckedCamera {
  camera_id: string
  camera_name: string
  streams: CheckedStream[]
  gaps_24h: { gaps: number; longest_s: number | null; open: boolean }
}

export interface Latest {
  cameras: CheckedCamera[]
  note: string
}

export const getLatestChecks = (params: { site_id?: string; camera_id?: string } = {}) =>
  apiClient.get<Latest>('/api/v1/camera-checks/latest', { params }).then((r) => r.data)

import { apiClient } from './client'

export const MAN_DOWN_OUTCOMES = ['false_alarm', 'guard_ok', 'injury', 'other'] as const

export const OUTCOME_LABELS: Record<string, string> = {
  false_alarm: 'False alarm',
  guard_ok: 'Guard was fine',
  injury: 'Injury',
  other: 'Other',
}

export const TRIGGER_LABELS: Record<string, string> = {
  no_motion: 'Stopped moving',
  impact: 'Impact, then still',
  tilt: 'Phone flat and still',
  manual: 'Asked for help',
}

export type ManDownStatus =
  'pending' | 'cancelled' | 'escalated' | 'acknowledged' | 'resolved'

export interface ManDownEvent {
  id: string
  guard_user_id: string
  guard_name: string | null
  employee_code: string | null
  shift_id: string | null
  site_id: string | null
  site_name: string | null
  trigger: string
  detected_at: string
  latitude: number | null
  longitude: number | null
  accuracy_m: number | null
  battery_level: number | null
  device_info: string | null
  status: ManDownStatus
  escalate_at: string
  seconds_remaining: number
  cancelled_at: string | null
  escalated_at: string | null
  /** True when the phone never called back and the sweep escalated it — which
   *  usually means the handset did not survive whatever happened. */
  escalated_by_server: boolean
  acknowledged_at: string | null
  acknowledged_by_name: string | null
  resolved_at: string | null
  resolved_by_name: string | null
  outcome: string | null
  notes: string | null
}

export interface ManDownSettings {
  enabled: boolean
  no_motion_seconds: number
  countdown_seconds: number
  impact_threshold_g: number
  stillness_threshold_mg: number
}

export const listManDown = (params: { site_id?: string; live_only?: boolean } = {}) =>
  apiClient.get<ManDownEvent[]>('/api/v1/man-down', { params }).then((r) => r.data)

export const getManDownSettings = () =>
  apiClient.get<ManDownSettings>('/api/v1/man-down/settings').then((r) => r.data)

export const updateManDownSettings = (data: Partial<ManDownSettings>) =>
  apiClient.put<ManDownSettings>('/api/v1/man-down/settings', data).then((r) => r.data)

export const acknowledgeManDown = (id: string) =>
  apiClient.post(`/api/v1/man-down/${id}/acknowledge`).then((r) => r.data)

export const resolveManDown = (
  id: string,
  data: { outcome: string; notes?: string | null },
) => apiClient.post(`/api/v1/man-down/${id}/resolve`, data).then((r) => r.data)

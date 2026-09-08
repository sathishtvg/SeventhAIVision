/**
 * Man-down — the phone detects, the server decides.
 *
 * The app raises an event and shows a countdown the guard can cancel. The
 * escalation deadline lives on the server and a sweep enforces it, so a
 * handset that shattered on impact, ran flat or lost signal still gets help
 * sent. Calling /escalate when our own timer fires is the fast path, not the
 * only one.
 */
import { apiClient } from './client'

export interface ManDownSettings {
  enabled: boolean
  no_motion_seconds: number
  countdown_seconds: number
  impact_threshold_g: number
  stillness_threshold_mg: number
}

export interface ManDownEvent {
  id: string
  status: 'pending' | 'cancelled' | 'escalated' | 'acknowledged' | 'resolved'
  detected_at: string
  escalate_at: string
  countdown_seconds: number
  already_live: boolean
  seconds_remaining?: number
}

export const getManDownSettings = () =>
  apiClient.get<ManDownSettings>('/api/v1/man-down/settings').then((r) => r.data)

export const raiseManDown = (data: {
  trigger: 'no_motion' | 'impact' | 'tilt' | 'manual'
  shift_id?: string | null
  site_id?: string | null
  latitude?: number | null
  longitude?: number | null
  accuracy_m?: number | null
  battery_level?: number | null
  device_info?: string | null
}) => apiClient.post<ManDownEvent>('/api/v1/man-down', data).then((r) => r.data)

export const cancelManDown = (id: string, notes?: string) =>
  apiClient
    .post(`/api/v1/man-down/${id}/cancel`, { notes: notes || null })
    .then((r) => r.data)

export const escalateManDown = (id: string) =>
  apiClient.post(`/api/v1/man-down/${id}/escalate`).then((r) => r.data)

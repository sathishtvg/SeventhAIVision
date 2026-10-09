/**
 * Privacy zones of a camera (backend/app/routers/pdpa.py, /api/v1/privacy).
 *
 * A zone is a polygon drawn on a camera's picture. What is under it is painted
 * out by the server — of the live view, of what the AI is given, of recordings
 * and of the images a patrol keeps — within about ten seconds of its being
 * drawn. Nothing here masks anything: a zone drawn over the video by a player
 * would mask nothing for anybody holding the stream's address.
 */
import { apiClient } from './client'

export interface ZoneCorner { x: number; y: number }

export interface PrivacyZone {
  id: string
  camera_id: string
  camera_name: string | null
  name: string
  /** Each corner as a share of the picture's width and height. */
  polygon: ZoneCorner[]
  fill_color: string
  is_active: boolean
  created_at: string
  created_by_name: string | null
}

export interface MaskedCameras {
  camera_ids: string[]
  /** How long a zone drawn or deleted takes to be applied everywhere. */
  refresh_seconds: number
}

export const listPrivacyZones = () =>
  apiClient.get<PrivacyZone[]>('/api/v1/privacy/zones').then((r) => r.data)

export const createPrivacyZone = (body: { camera_id: string; name: string; polygon: ZoneCorner[] }) =>
  apiClient.post<PrivacyZone & { applies_within_seconds: number }>('/api/v1/privacy/zones', body).then((r) => r.data)

export const deletePrivacyZone = (zoneId: string) =>
  apiClient.delete<{ deleted: boolean; id: string }>(`/api/v1/privacy/zones/${zoneId}`).then((r) => r.data)

/** Which cameras have a zone now. Such a camera has no HLS view: its live view is the masked one. */
export const getMaskedCameras = () =>
  apiClient.get<MaskedCameras>('/api/v1/privacy/masked-cameras').then((r) => r.data)

/** The server's reason, in its own words. */
export function apiError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string }
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => (x.msg ?? String(x)).replace(/^Value error, /, '')).join('; ')
  return e?.message ?? 'Something went wrong.'
}

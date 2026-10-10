/**
 * Privacy zones of a camera, on the phone (backend/app/routers/pdpa.py).
 *
 * A zone is applied by the server: what is under it is painted out of the live
 * view, of what the AI is given, of recordings and of the images a patrol
 * keeps, within about ten seconds. The phone masks nothing itself - its live
 * view is the server's masked one - and uses the routes the web uses.
 *
 * Drawing and deleting are for whoever holds `privacy:manage`; the server
 * refuses anybody else, and says why.
 */
import { apiClient } from './client'

const BASE = '/api/v1/privacy'

export interface ZoneCorner { x: number; y: number }

export interface PrivacyZone {
  id: string
  camera_id: string
  camera_name: string | null
  name: string
  /** Each corner as a share of the picture's width and height. */
  polygon: ZoneCorner[]
  is_active: boolean
  created_at: string
  created_by_name: string | null
}

/** The zones drawn on one camera, the newest first. */
export const listCameraPrivacyZones = (cameraId: string) =>
  apiClient.get<PrivacyZone[]>(`${BASE}/zones`, { params: { camera_id: cameraId } }).then((r) => r.data)

/** The camera, what the zone covers, and its corners: the server takes nothing else of a point than its x and y. */
export const createPrivacyZone = (zone: { camera_id: string; name: string; polygon: ZoneCorner[] }) =>
  apiClient.post<PrivacyZone>(`${BASE}/zones`, {
    camera_id: zone.camera_id, name: zone.name.trim(), polygon: zone.polygon.map((p) => ({ x: p.x, y: p.y })),
  }).then((r) => r.data)

export const deletePrivacyZone = (zoneId: string) =>
  apiClient.delete<{ deleted: boolean; id: string }>(`${BASE}/zones/${zoneId}`).then((r) => r.data)

/** Which cameras have a zone now: so that a black block in a picture is labelled as meant. */
export const getMaskedCameras = () =>
  apiClient.get<{ camera_ids: string[]; refresh_seconds: number }>(`${BASE}/masked-cameras`).then((r) => r.data)

import { apiClient } from './client'

export type ModuleType =
  | 'lpr' | 'face' | 'intrusion'
  | 'ppe' | 'crowd' | 'fire_smoke' | 'weapon' | 'behavior'

export interface Detection {
  id: string
  camera_id: string
  module_type: ModuleType
  confidence: number | null
  bounding_box: Record<string, number> | null
  raw_metadata: Record<string, unknown> | null
  detected_at: string
}

export interface LprEvent {
  detection_id: string
  detected_at: string
  camera_id: string
  plate_number: string
  plate_confidence: number | null
  direction: string | null
  vehicle_type: string | null
  vehicle_color: string | null
  watchlist_match: 'allow' | 'block' | null
}

export interface FaceEvent {
  detection_id: string
  detected_at: string
  camera_id: string
  matched_watchlist_id: string | null
  match_confidence: number | null
  watchlist_match: 'allow' | 'block' | null
}

export interface IntrusionEvent {
  detection_id: string
  detected_at: string
  camera_id: string
  zone_id: string
  dwell_time_seconds: number | null
}

interface ListParams {
  limit?: number
  offset?: number
  camera_id?: string
  since?: string
}

export const getDetections = (params?: ListParams & { module_type?: ModuleType }) =>
  apiClient
    .get<{ items: Detection[] }>('/api/v1/detections', { params: { limit: 50, ...params } })
    .then((r) => r.data.items)

export const getLprEvents = (params?: ListParams) =>
  apiClient
    .get<{ items: LprEvent[] }>('/api/v1/detections/lpr-events', { params: { limit: 50, ...params } })
    .then((r) => r.data.items)

export const getFaceEvents = (params?: ListParams) =>
  apiClient
    .get<{ items: FaceEvent[] }>('/api/v1/detections/face-events', { params: { limit: 50, ...params } })
    .then((r) => r.data.items)

export const getIntrusionEvents = (params?: ListParams) =>
  apiClient
    .get<{ items: IntrusionEvent[] }>('/api/v1/detections/intrusion-events', { params: { limit: 50, ...params } })
    .then((r) => r.data.items)

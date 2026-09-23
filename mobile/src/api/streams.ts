import { apiClient } from './client'

export type StreamStatus = 'online' | 'degraded' | 'offline'

export interface Stream {
  id: string
  camera_id: string
  protocol: string
  url: string
  status: StreamStatus
  last_frame_at: string | null
  created_at: string
  updated_at: string
}

export interface CameraHealthEvent {
  id: string
  camera_id: string
  event_type: string
  detail: string | null
  occurred_at: string
}

export const getStreams = (cameraId?: string) =>
  apiClient
    .get<Stream[]>(cameraId ? `/api/v1/cameras/${cameraId}/streams` : '/api/v1/streams')
    .then((r) => r.data)

export const getCameraHealth = (cameraId: string, limit = 50) =>
  apiClient
    .get<CameraHealthEvent[]>(`/api/v1/cameras/${cameraId}/health`, { params: { limit } })
    .then((r) => r.data)

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

export const getStream = (streamId: string) =>
  apiClient.get<Stream>(`/api/v1/streams/${streamId}`).then((r) => r.data)

export const createStream = (data: {
  camera_id: string
  protocol?: string
  url: string
}) => apiClient.post<Stream>('/api/v1/streams', data).then((r) => r.data)

export const updateStream = (streamId: string, data: { url?: string; protocol?: string }) =>
  apiClient.put<Stream>(`/api/v1/streams/${streamId}`, data).then((r) => r.data)

export const deleteStream = (streamId: string) =>
  apiClient.delete(`/api/v1/streams/${streamId}`)

export const getCameraHealth = (cameraId: string, limit = 50) =>
  apiClient
    .get<CameraHealthEvent[]>(`/api/v1/cameras/${cameraId}/health`, { params: { limit } })
    .then((r) => r.data)

import { apiClient } from './client'

export interface Camera {
  id: string
  name: string
  location: string | null
  site_id: string | null
  site_name: string | null
  is_active: boolean
  ai_modules_enabled: string[]
}

export interface Stream {
  id: string
  camera_id: string
  protocol: string
  url: string
  status: 'online' | 'degraded' | 'offline'
  last_frame_at: string | null
}

export const getCameras = (params?: { site_id?: string; is_active?: boolean }) =>
  apiClient.get<Camera[]>('/api/v1/cameras', { params }).then((r) => r.data)

export const getStreams = (cameraId?: string) =>
  apiClient
    .get<Stream[]>(cameraId ? `/api/v1/cameras/${cameraId}/streams` : '/api/v1/streams')
    .then((r) => r.data)

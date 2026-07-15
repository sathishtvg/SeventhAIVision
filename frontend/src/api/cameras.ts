import type { Camera, Stream, CameraHealthEvent, StreamValidationResult } from '@/types/api'
import { apiClient } from './client'

// ── Cameras ──────────────────────────────────────────────

export const getCameras = () =>
  apiClient.get<Camera[]>('/api/v1/cameras').then((r) => r.data)

export const getCamera = (id: string) =>
  apiClient.get<Camera>(`/api/v1/cameras/${id}`).then((r) => r.data)

export const createCamera = (data: {
  name: string
  location?: string
  latitude?: number
  longitude?: number
  ai_modules_enabled?: string[]
  site_id?: string
}) => apiClient.post<{ id: string; name: string }>('/api/v1/cameras', data).then((r) => r.data)

export const updateCamera = (id: string, data: {
  name?: string
  location?: string
  latitude?: number
  longitude?: number
  ai_modules_enabled?: string[]
  is_active?: boolean
  site_id?: string
}) => apiClient.put<Camera>(`/api/v1/cameras/${id}`, data).then((r) => r.data)

export const deleteCamera = (id: string) =>
  apiClient.delete(`/api/v1/cameras/${id}`)

// ── Streams ───────────────────────────────────────────────

export const getStreams = (cameraId: string) =>
  apiClient.get<Stream[]>(`/api/v1/cameras/${cameraId}/streams`).then((r) => r.data)

export const createStream = (cameraId: string, data: {
  url: string
  protocol?: string
  username?: string
  password?: string
}) => apiClient.post<Stream>(`/api/v1/cameras/${cameraId}/streams`, data).then((r) => r.data)

export const updateStream = (cameraId: string, streamId: string, data: {
  url?: string
  protocol?: string
  username?: string
  password?: string
  continuous_recording?: boolean
}) => apiClient.put<Stream>(`/api/v1/cameras/${cameraId}/streams/${streamId}`, data).then((r) => r.data)

export const deleteStream = (cameraId: string, streamId: string) =>
  apiClient.delete(`/api/v1/cameras/${cameraId}/streams/${streamId}`)

export const validateStream = (data: { url: string; username?: string; password?: string }) =>
  apiClient.post<StreamValidationResult>('/api/v1/cameras/validate-stream', data).then((r) => r.data)

// ── Health events ─────────────────────────────────────────

export const getCameraHealth = (cameraId: string, limit = 20) =>
  apiClient
    .get<CameraHealthEvent[]>(`/api/v1/cameras/${cameraId}/health`, { params: { limit } })
    .then((r) => r.data)

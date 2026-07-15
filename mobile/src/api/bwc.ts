import { apiClient } from './client'

export interface BwcDashboard {
  total_cameras: number
  available: number
  assigned: number
  recording: number
  low_battery: number
  storage_warning: number
  active_recordings: number
  recordings_today: number
}

export interface BodyCamera {
  id: string
  serial_number: string
  name: string | null
  status: string
  battery_pct: number | null
  storage_used_gb: number | null
  storage_total_gb: number | null
  is_recording: boolean
  assigned_user_id: string | null
  assigned_user_name: string | null
  site_id: string | null
  total_recordings: number
  last_sync_at: string | null
}

export interface BwcRecording {
  id: string
  camera_id: string
  camera_serial: string | null
  trigger: string
  status: string
  duration_seconds: number | null
  file_size_mb: number | null
  incident_id: string | null
  started_at: string
  ended_at: string | null
}

export const getBwcDashboard = () =>
  apiClient.get<BwcDashboard>('/api/v1/bwc/dashboard').then((r) => r.data)

export const getBodyCameras = (params?: { status?: string }) =>
  apiClient.get<BodyCamera[]>('/api/v1/bwc/cameras', { params }).then((r) => r.data)

export const getBwcRecordings = (params?: { camera_id?: string; limit?: number }) =>
  apiClient
    .get<BwcRecording[]>('/api/v1/bwc/recordings', { params: { limit: 50, ...params } })
    .then((r) => r.data)

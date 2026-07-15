import { apiClient } from './client'

export const TRIGGER_TYPES = ['manual', 'pre_event', 'auto_incident', 'panic', 'scheduled'] as const

export interface BodyCamera {
  id: string
  serial_number: string
  model?: string
  firmware_version?: string
  status: 'available' | 'assigned' | 'recording' | 'docked' | 'low_battery' | 'fault' | 'retired'
  battery_pct?: number
  storage_used_gb?: number
  storage_total_gb: number
  assigned_user_id?: string
  assigned_user_name?: string
  assigned_at?: string
  last_docked_at?: string
  last_sync_at?: string
  is_recording: boolean
  notes?: string
  is_active: boolean
  total_recordings?: number
  created_at: string
}

export interface BWCAssignment {
  id: string
  camera_id: string
  user_id: string
  user_name?: string
  assigned_by_name?: string
  assigned_at: string
  returned_at?: string
  notes?: string
}

export interface BWCRecording {
  id: string
  camera_id: string
  serial_number?: string
  user_id?: string
  user_name?: string
  incident_id?: string
  title?: string
  started_at: string
  ended_at?: string
  duration_seconds?: number
  file_size_mb?: number
  trigger_type: string
  latitude?: number
  longitude?: number
  status: 'recording' | 'completed' | 'failed' | 'deleted'
  notes?: string
}

export interface BWCEvent {
  id: string
  camera_id: string
  serial_number?: string
  user_id?: string
  user_name?: string
  event_type: string
  detail?: string
  battery_pct?: number
  occurred_at: string
}

export interface BWCDashboard {
  total_cameras: number
  available: number
  assigned: number
  recording: number
  low_battery: number
  storage_warning: number
  active_recordings: number
  recordings_today: number
}

export async function getBWCDashboard(): Promise<BWCDashboard> {
  const r = await apiClient.get('/api/v1/bwc/dashboard')
  return r.data
}

export async function listCameras(status?: string): Promise<BodyCamera[]> {
  const r = await apiClient.get('/api/v1/bwc/cameras', { params: status ? { status } : {} })
  return r.data
}

export async function registerCamera(data: Partial<BodyCamera>): Promise<BodyCamera> {
  const r = await apiClient.post('/api/v1/bwc/cameras', data)
  return r.data
}

export async function assignCamera(cameraId: string, userId: string, notes?: string) {
  const r = await apiClient.post(`/api/v1/bwc/cameras/${cameraId}/assign`, { user_id: userId, notes })
  return r.data
}

export async function unassignCamera(cameraId: string, notes?: string) {
  const r = await apiClient.post(`/api/v1/bwc/cameras/${cameraId}/unassign`, { notes })
  return r.data
}

export async function startRecording(cameraId: string, data?: {
  trigger_type?: string; title?: string; incident_id?: string
  latitude?: number; longitude?: number; notes?: string
}): Promise<BWCRecording> {
  const r = await apiClient.post(`/api/v1/bwc/cameras/${cameraId}/recordings/start`, data ?? {})
  return r.data
}

export async function stopRecording(cameraId: string, recordingId: string, data?: {
  duration_seconds?: number; file_size_mb?: number; notes?: string
}) {
  const r = await apiClient.post(`/api/v1/bwc/cameras/${cameraId}/recordings/${recordingId}/stop`, data ?? {})
  return r.data
}

export interface BWCRecordingsPage { items: BWCRecording[]; has_more: boolean }

export async function listRecordings(params?: {
  camera_id?: string; user_id?: string; status?: string; incident_id?: string; limit?: number; offset?: number
}): Promise<BWCRecordingsPage> {
  const r = await apiClient.get('/api/v1/bwc/recordings', { params })
  return r.data
}

export async function linkRecordingToIncident(recordingId: string, incidentId: string | null): Promise<{ ok: boolean; recording_id: string; incident_id: string | null }> {
  const r = await apiClient.put(`/api/v1/bwc/recordings/${recordingId}/link-incident`, { incident_id: incidentId })
  return r.data
}

export async function listBWCEvents(params?: {
  camera_id?: string; event_type?: string
}): Promise<BWCEvent[]> {
  const r = await apiClient.get('/api/v1/bwc/events', { params })
  return r.data
}

export async function updateTelemetry(cameraId: string, battery_pct: number, storage_used_gb?: number) {
  const r = await apiClient.put(`/api/v1/bwc/cameras/${cameraId}/telemetry`, { battery_pct, storage_used_gb })
  return r.data
}

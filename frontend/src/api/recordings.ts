import type { Recording } from '@/types/api'
import { apiClient } from './client'

export const startRecording = (cameraId: string, streamId: string) =>
  apiClient
    .post<{ recording_id: string; status: string }>(`/api/v1/cameras/${cameraId}/streams/${streamId}/recordings/start`)
    .then((r) => r.data)

export const stopRecording = (cameraId: string, streamId: string, recordingId: string) =>
  apiClient
    .post(`/api/v1/cameras/${cameraId}/streams/${streamId}/recordings/${recordingId}/stop`)
    .then((r) => r.data)

export const listRecordings = (cameraId: string, streamId: string) =>
  apiClient
    .get<Recording[]>(`/api/v1/cameras/${cameraId}/streams/${streamId}/recordings`)
    .then((r) => r.data)

export const downloadRecordingUrl = (recordingId: string) =>
  `${apiClient.defaults.baseURL}/api/v1/cameras/recordings/${recordingId}/download`

export const listAllRecordings = (params?: { status_filter?: string; site_id?: string; limit?: number }) =>
  apiClient
    .get<Recording[]>('/api/v1/recordings', { params })
    .then((r) => r.data)

// ── Playback timeline (Gap 85) ───────────────────────────────────────────────

export interface TimelineSegment {
  id: string
  status: string
  started_at: string
  ended_at: string | null
  file_size_bytes: number | null
  duration_seconds: number | null
  is_active: boolean
}

export interface TimelineAlert {
  id: string
  severity: string
  title: string
  module_type: string
  created_at: string
}

export interface RecordingTimeline {
  camera_id: string
  camera_name: string
  date: string
  segments: TimelineSegment[]
  alerts: TimelineAlert[]
}

export const getRecordingTimeline = (cameraId: string, date: string) =>
  apiClient
    .get<RecordingTimeline>('/api/v1/recordings/timeline', {
      params: { camera_id: cameraId, date },
    })
    .then((r) => r.data)

export const playRecordingUrl = (recordingId: string, token: string) =>
  `${apiClient.defaults.baseURL}/api/v1/recordings/${recordingId}/play?token=${token}`

export const listAllStreams = (params?: { status_filter?: string; site_id?: string }) =>
  apiClient
    .get<Array<{
      id: string; camera_id: string; protocol: string; url: string; status: string;
      last_frame_at: string | null; camera_name: string; location: string | null;
      camera_active: boolean; site_id: string | null; site_name: string | null;
    }>>('/api/v1/streams', { params })
    .then((r) => r.data)

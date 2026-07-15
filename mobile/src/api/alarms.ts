import { apiClient } from './client'

export interface AlarmPanel {
  id: string
  name: string
  site_id: string | null
  model: string | null
  serial_number: string | null
  protocol: string
  arm_status: 'disarmed' | 'armed_away' | 'armed_stay' | 'armed_night' | 'triggered' | 'alarm' | 'fault'
  is_active: boolean
  last_event_at: string | null
  zone_count: number | null
  created_at: string
}

export interface AlarmEvent {
  id: string
  panel_id: string
  panel_name: string | null
  event_type: string
  zone_id: string | null
  zone_name: string | null
  severity: string
  raw_payload: Record<string, unknown> | null
  occurred_at: string
}

export interface AlarmZone {
  id: string
  panel_id: string
  zone_number: number
  name: string
  zone_type: string
  status: 'normal' | 'open' | 'tamper' | 'fault' | 'bypassed'
  linked_camera_id: string | null
}

export const getAlarmPanels = (params?: { site_id?: string }) =>
  apiClient.get<AlarmPanel[]>('/api/v1/alarms/panels', { params }).then((r) => r.data)

export const getAlarmEvents = (params?: { panel_id?: string; severity?: string; limit?: number }) =>
  apiClient
    .get<AlarmEvent[]>('/api/v1/alarms/events', { params: { limit: 50, ...params } })
    .then((r) => r.data)

export const getAlarmZones = (panelId: string) =>
  apiClient.get<AlarmZone[]>(`/api/v1/alarms/panels/${panelId}/zones`).then((r) => r.data)

export const armPanel = (panelId: string, mode: string) =>
  apiClient.post(`/api/v1/alarms/panels/${panelId}/arm`, { mode }).then((r) => r.data)

export const disarmPanel = (panelId: string) =>
  apiClient.post(`/api/v1/alarms/panels/${panelId}/disarm`, {}).then((r) => r.data)

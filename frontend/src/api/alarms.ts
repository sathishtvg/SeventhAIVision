import { apiClient } from './client'

export interface AlarmPanel {
  id: string
  site_id?: string
  site_name?: string
  name: string
  model?: string
  serial_number?: string
  protocol: 'webhook' | 'contact_id_tcp' | 'mqtt' | 'sdk'
  host?: string
  port?: number
  api_key?: string
  status: 'unknown' | 'online' | 'offline' | 'fault'
  arm_state: 'disarmed' | 'armed_away' | 'armed_stay' | 'armed_night' | 'alarm'
  last_contact_at?: string
  notes?: string
  is_active: boolean
  created_at: string
  total_zones?: number
  alarm_zones?: number
}

export interface AlarmZone {
  id: string
  panel_id: string
  zone_number: number
  name: string
  zone_type: string
  current_state: 'normal' | 'alarm' | 'tamper' | 'fault' | 'bypass' | 'open' | 'restored'
  linked_camera_id?: string
  camera_name?: string
  is_active: boolean
}

export interface AlarmEvent {
  id: string
  event_type: string
  severity: 'info' | 'low' | 'medium' | 'high' | 'critical'
  description?: string
  zone_number?: number
  zone_name?: string
  occurred_at: string
  alert_id?: string
  panel_name?: string
  site_name?: string
}

export interface AlarmDashboard {
  panel_summary: {
    total_panels: number
    online: number
    offline: number
    in_alarm: number
    armed: number
    disarmed: number
  }
  zone_summary: {
    total_zones: number
    zones_in_alarm: number
    zones_tampered: number
    zones_bypassed: number
    zones_normal: number
  }
  event_summary: {
    events_24h: number
    alarms_24h: number
    alarms_1h: number
  }
  recent_alarms: Array<{
    id: string
    event_type: string
    severity: string
    description?: string
    occurred_at: string
    panel_name: string
    site_name?: string
    zone_name?: string
    zone_number?: number
  }>
  panels: AlarmPanel[]
}

export async function getAlarmDashboard(): Promise<AlarmDashboard> {
  const r = await apiClient.get('/api/v1/alarms/dashboard')
  return r.data
}

export async function listPanels(): Promise<AlarmPanel[]> {
  const r = await apiClient.get('/api/v1/alarms/panels')
  return r.data
}

export async function createPanel(data: {
  name: string
  site_id?: string
  model?: string
  serial_number?: string
  protocol?: string
  host?: string
  port?: number
  notes?: string
}): Promise<AlarmPanel> {
  const r = await apiClient.post('/api/v1/alarms/panels', data)
  return r.data
}

export async function getPanel(id: string): Promise<AlarmPanel & { zones: AlarmZone[]; recent_events: AlarmEvent[] }> {
  const r = await apiClient.get(`/api/v1/alarms/panels/${id}`)
  return r.data
}

export async function armPanel(id: string, mode: 'away' | 'stay' | 'night') {
  const r = await apiClient.put(`/api/v1/alarms/panels/${id}/arm`, { mode })
  return r.data
}

export async function disarmPanel(id: string) {
  const r = await apiClient.put(`/api/v1/alarms/panels/${id}/disarm`)
  return r.data
}

export async function createZone(panelId: string, data: {
  zone_number: number
  name: string
  zone_type?: string
  linked_camera_id?: string
}): Promise<AlarmZone> {
  const r = await apiClient.post(`/api/v1/alarms/panels/${panelId}/zones`, data)
  return r.data
}

export async function bypassZone(zoneId: string) {
  const r = await apiClient.put(`/api/v1/alarms/zones/${zoneId}/bypass`)
  return r.data
}

export async function rotatePanelKey(panelId: string): Promise<{ panel_id: string; api_key: string; warning: string }> {
  const r = await apiClient.post(`/api/v1/alarms/panels/${panelId}/rotate-key`)
  return r.data
}

export async function listEvents(params?: {
  panel_id?: string
  event_type?: string
  severity?: string
  limit?: number
}): Promise<AlarmEvent[]> {
  const r = await apiClient.get('/api/v1/alarms/events', { params })
  return r.data
}

import { apiClient } from './client'

export interface IoTDevice {
  id: string
  name: string
  device_type: string
  protocol: string | null
  site_id: string | null
  location: string | null
  status: 'online' | 'offline' | 'error' | 'unknown'
  battery_pct: number | null
  firmware_version: string | null
  last_seen_at: string | null
  is_active: boolean
  created_at: string
}

export interface IoTReading {
  id: string
  device_id: string
  device_name: string | null
  metric: string
  value: number
  unit: string | null
  is_alert: boolean
  recorded_at: string
}

export interface IoTDashboard {
  total_devices: number
  online: number
  offline: number
  error: number
  alerts_today: number
  devices: IoTDevice[]
}

export const getIoTDashboard = (params?: { site_id?: string }) =>
  apiClient.get<IoTDashboard>('/api/v1/iot/dashboard', { params }).then((r) => r.data)

export const getIoTDevices = (params?: { status?: string; device_type?: string; site_id?: string }) =>
  apiClient.get<IoTDevice[]>('/api/v1/iot/devices', { params }).then((r) => r.data)

export const getIoTReadings = (params?: { device_id?: string; metric?: string; limit?: number }) =>
  apiClient
    .get<IoTReading[]>('/api/v1/iot/readings', { params: { limit: 50, ...params } })
    .then((r) => r.data)

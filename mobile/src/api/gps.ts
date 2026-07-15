import { apiClient } from './client'

export interface GpsVehicle {
  id: string
  name: string
  plate_number: string | null
  vehicle_type: string
  color: string | null
  driver_name: string | null
  driver_user_id: string | null
  site_id: string | null
  is_active: boolean
  status: 'moving' | 'idle' | 'offline'
  last_lat: number | null
  last_lon: number | null
  last_speed_kmh: number | null
  last_seen_at: string | null
}

export interface GpsPosition {
  id: string
  vehicle_id: string
  latitude: number
  longitude: number
  speed_kmh: number | null
  heading_deg: number | null
  recorded_at: string
}

export interface GpsDashboard {
  total: number
  moving: number
  idle: number
  offline: number
  vehicles: GpsVehicle[]
}

export const getGpsDashboard = (params?: { site_id?: string }) =>
  apiClient.get<GpsDashboard>('/api/v1/gps/dashboard', { params }).then((r) => r.data)

export const getVehicles = (params?: { status?: string; limit?: number }) =>
  apiClient.get<GpsVehicle[]>('/api/v1/gps/vehicles', { params: { limit: 50, ...params } }).then((r) => r.data)

export const getVehicleTrack = (vehicleId: string, params?: { hours?: number }) =>
  apiClient
    .get<GpsPosition[]>(`/api/v1/gps/vehicles/${vehicleId}/track`, { params: { hours: 4, ...params } })
    .then((r) => r.data)

import { apiClient } from './client'

export const VEHICLE_TYPES = [
  { value: 'patrol_car',  label: 'Patrol Car',  icon: '🚔' },
  { value: 'motorcycle',  label: 'Motorcycle',  icon: '🏍️' },
  { value: 'van',         label: 'Van',         icon: '🚐' },
  { value: 'truck',       label: 'Truck',       icon: '🚚' },
  { value: 'golf_cart',   label: 'Golf Cart',   icon: '🛺' },
  { value: 'bicycle',     label: 'Bicycle',     icon: '🚲' },
  { value: 'boat',        label: 'Boat',        icon: '⛵' },
  { value: 'custom',      label: 'Custom',      icon: '🚗' },
] as const

export interface Vehicle {
  id: string
  tenant_id: string
  site_id: string | null
  name: string
  plate_number: string | null
  vehicle_type: string
  make: string | null
  model: string | null
  color: string | null
  assigned_driver_user_id: string | null
  driver_name: string | null
  site_name: string | null
  is_active: boolean
  last_position_at: string | null
  last_lat: number | null
  last_lon: number | null
  last_speed: number | null
  current_status: 'moving' | 'idle' | 'offline'
  created_at: string
  updated_at: string
}

export interface VehiclePosition {
  lat: number
  lon: number
  speed: number | null
  heading: number | null
  recorded_at: string
}

export interface Geofence {
  id: string
  tenant_id: string
  site_id: string | null
  site_name: string | null
  name: string
  description: string | null
  polygon: Array<{ lat: number; lon: number }>
  alert_on_entry: boolean
  alert_on_exit: boolean
  speed_limit: number | null
  is_active: boolean
  created_at: string
}

export interface GeofenceEvent {
  id: string
  vehicle_id: string
  vehicle_name: string
  plate_number: string | null
  geofence_id: string
  geofence_name: string
  event_type: 'entry' | 'exit' | 'speed_violation'
  lat: number | null
  lon: number | null
  speed: number | null
  occurred_at: string
}

export interface VehicleJourney {
  id: string
  vehicle_id: string
  start_lat: number | null
  start_lon: number | null
  end_lat: number | null
  end_lon: number | null
  start_at: string
  end_at: string | null
  distance_km: number
  max_speed: number | null
  avg_speed: number | null
  status: 'active' | 'completed'
}

export interface GPSDashboard {
  summary: { total: number; moving: number; idle: number; offline: number }
  vehicles: (Vehicle & { active_journey: number })[]
  recent_events: GeofenceEvent[]
}

export interface VehicleCreate {
  name: string
  vehicle_type: string
  plate_number?: string
  make?: string
  model?: string
  color?: string
  site_id?: string
  assigned_driver_user_id?: string
}

export const getGPSDashboard = (site_id?: string): Promise<GPSDashboard> =>
  apiClient.get('/api/v1/gps/dashboard', { params: site_id ? { site_id } : undefined }).then(r => r.data)

export const listVehicles = (params?: { site_id?: string; status?: string }): Promise<Vehicle[]> =>
  apiClient.get('/api/v1/gps/vehicles', { params }).then(r => r.data)

export const createVehicle = (body: VehicleCreate): Promise<Vehicle> =>
  apiClient.post('/api/v1/gps/vehicles', body).then(r => r.data)

export const updateVehicle = (id: string, body: Partial<VehicleCreate & { is_active: boolean }>): Promise<{ ok: boolean }> =>
  apiClient.put(`/api/v1/gps/vehicles/${id}`, body).then(r => r.data)

export const getVehiclePositions = (id: string, hours = 8): Promise<VehiclePosition[]> =>
  apiClient.get(`/api/v1/gps/vehicles/${id}/positions`, { params: { hours } }).then(r => r.data)

export const getVehicleJourneys = (id: string): Promise<VehicleJourney[]> =>
  apiClient.get(`/api/v1/gps/vehicles/${id}/journeys`).then(r => r.data)

export const listGeofences = (): Promise<Geofence[]> =>
  apiClient.get('/api/v1/gps/geofences').then(r => r.data)

export const createGeofence = (body: Partial<Geofence> & { name: string; polygon: Array<{ lat: number; lon: number }> }): Promise<Geofence> =>
  apiClient.post('/api/v1/gps/geofences', body).then(r => r.data)

export const listGeofenceEvents = (params?: { vehicle_id?: string; hours?: number }): Promise<GeofenceEvent[]> =>
  apiClient.get('/api/v1/gps/geofence-events', { params }).then(r => r.data)

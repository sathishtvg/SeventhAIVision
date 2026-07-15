import { apiClient } from './client'

export const ZONE_TYPES = ['regular', 'handicap', 'ev', 'vip', 'motorcycle', 'loading'] as const
export const VEHICLE_TYPES = ['car', 'motorcycle', 'truck', 'van', 'bicycle'] as const

export interface CarPark {
  id: string
  tenant_id: string
  site_id?: string
  site_name?: string
  name: string
  description?: string
  total_capacity: number
  levels: number
  address?: string
  is_active: boolean
  available_bays?: number
  occupied_bays?: number
  bay_count?: number
  created_at: string
}

export interface ParkingZone {
  id: string
  car_park_id: string
  name: string
  zone_type: string
  level: number
  capacity: number
  is_active: boolean
  bay_count?: number
  available?: number
  occupied?: number
}

export interface ParkingBay {
  id: string
  zone_id: string
  car_park_id: string
  bay_number: string
  status: 'available' | 'occupied' | 'reserved' | 'blocked'
  vehicle_plate?: string
  occupied_since?: string
  notes?: string
  zone_name?: string
  zone_type?: string
}

export interface ParkingRate {
  id: string
  car_park_id: string
  zone_type: string
  rate_name: string
  first_hour_rate: number
  subsequent_rate: number
  daily_max_rate?: number
  currency: string
  is_active: boolean
}

export interface ParkingSession {
  id: string
  car_park_id: string
  zone_id?: string
  bay_id?: string
  vehicle_plate?: string
  vehicle_type: string
  entry_at: string
  exit_at?: string
  duration_minutes?: number
  fee_amount?: number
  currency: string
  payment_status: 'unpaid' | 'paid' | 'waived' | 'void'
  payment_method?: string
  status: 'active' | 'completed' | 'overstay' | 'disputed'
  carpark_name?: string
  zone_name?: string
  bay_number?: string
}

export interface ParkingDashboard {
  total_carparks: number
  total_capacity: number
  available_bays: number
  occupied_bays: number
  reserved_bays: number
  active_sessions: number
  revenue_today: number
  overstay_count: number
}

export async function getParkingDashboard(): Promise<ParkingDashboard> {
  const r = await apiClient.get('/api/v1/parking/dashboard')
  return r.data
}

export async function listCarParks(): Promise<CarPark[]> {
  const r = await apiClient.get('/api/v1/carparks')
  return r.data
}

export async function createCarPark(data: Partial<CarPark>): Promise<CarPark> {
  const r = await apiClient.post('/api/v1/carparks', data)
  return r.data
}

export async function getCarParkOccupancy(id: string) {
  const r = await apiClient.get(`/api/v1/carparks/${id}/occupancy`)
  return r.data as { zones: ParkingZone[]; bays: ParkingBay[] }
}

export async function createZone(carparkId: string, data: Partial<ParkingZone>): Promise<ParkingZone> {
  const r = await apiClient.post(`/api/v1/carparks/${carparkId}/zones`, data)
  return r.data
}

export async function listBays(carparkId: string, zoneId?: string): Promise<ParkingBay[]> {
  const r = await apiClient.get(`/api/v1/carparks/${carparkId}/bays`, { params: zoneId ? { zone_id: zoneId } : {} })
  return r.data
}

export async function updateBayStatus(bayId: string, status: string, notes?: string) {
  const r = await apiClient.put(`/api/v1/parking/bays/${bayId}`, { status, notes })
  return r.data
}

export async function listRates(carparkId: string): Promise<ParkingRate[]> {
  const r = await apiClient.get(`/api/v1/carparks/${carparkId}/rates`)
  return r.data
}

export async function createRate(carparkId: string, data: Partial<ParkingRate>): Promise<ParkingRate> {
  const r = await apiClient.post(`/api/v1/carparks/${carparkId}/rates`, data)
  return r.data
}

export interface ParkingSessionsPage { items: ParkingSession[]; has_more: boolean }

export async function listSessions(params?: {
  status?: string; carpark_id?: string; plate?: string; limit?: number; offset?: number
}): Promise<ParkingSessionsPage> {
  const r = await apiClient.get('/api/v1/parking/sessions', { params })
  return r.data
}

export async function logEntry(data: {
  car_park_id: string; zone_id?: string; bay_id?: string
  vehicle_plate?: string; vehicle_type?: string; operator_notes?: string
}): Promise<ParkingSession> {
  const r = await apiClient.post('/api/v1/parking/sessions/entry', data)
  return r.data
}

export async function logExit(sessionId: string, data?: {
  payment_status?: string; payment_method?: string; exit_camera_id?: string
}) {
  const r = await apiClient.put(`/api/v1/parking/sessions/${sessionId}/exit`, data ?? {})
  return r.data as { duration_minutes: number; fee_amount: number; ok: boolean }
}

export async function updatePayment(sessionId: string, payment_status: string, payment_method?: string) {
  const r = await apiClient.put(`/api/v1/parking/sessions/${sessionId}/payment`, { payment_status, payment_method })
  return r.data
}

// ── Parking LPR Camera configuration (migration 0035) ─────────────────────────

export interface LprCameraConfig {
  id: string
  camera_id: string
  car_park_id: string
  trigger_type: 'entry' | 'exit' | 'both'
  default_zone_id: string | null
  is_active: boolean
  notes: string | null
  created_at: string
  camera_name: string
  car_park_name: string
  default_zone_name: string | null
}

export async function listLprCameras(): Promise<LprCameraConfig[]> {
  const r = await apiClient.get('/api/v1/parking/lpr-cameras')
  return r.data
}

export async function createLprCamera(data: {
  camera_id: string
  car_park_id: string
  trigger_type: 'entry' | 'exit' | 'both'
  default_zone_id?: string
  notes?: string
}): Promise<LprCameraConfig> {
  const r = await apiClient.post('/api/v1/parking/lpr-cameras', data)
  return r.data
}

export async function deleteLprCamera(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/parking/lpr-cameras/${id}`)
}

export interface LprTriggeredSession {
  id: string
  vehicle_plate: string | null
  entry_at: string
  exit_at: string | null
  status: string
  duration_minutes: number | null
  fee_amount: number | null
  payment_status: string
  lpr_detection_id: string | null
  car_park_name: string
  zone_name: string | null
}

export async function listLprTriggeredSessions(params?: {
  limit?: number; offset?: number
}): Promise<LprTriggeredSession[]> {
  const r = await apiClient.get('/api/v1/parking/sessions/lpr-triggered', { params })
  return r.data
}

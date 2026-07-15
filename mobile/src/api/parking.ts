import { apiClient } from './client'

export interface CarPark {
  id: string
  name: string
  location: string | null
  site_id: string | null
  total_bays: number
  is_active: boolean
  created_at: string
}

export interface ParkingZone {
  id: string
  car_park_id: string
  name: string
  zone_type: string
  total_bays: number
  occupied: number
  is_active: boolean
}

export interface ParkingSession {
  id: string
  car_park_id: string
  car_park_name: string | null
  zone_id: string | null
  vehicle_plate: string
  vehicle_type: string | null
  bay_number: string | null
  status: 'active' | 'completed' | 'overstay' | 'reserved'
  entry_at: string
  exit_at: string | null
  duration_minutes: number | null
  fee_amount: number | null
}

export interface ParkingOccupancy {
  zones: ParkingZone[]
  total_bays: number
  occupancy_pct: number
}

export const getCarParks = () =>
  apiClient.get<CarPark[]>('/api/v1/parking/car-parks').then((r) => r.data)

export const getParkingOccupancy = (carParkId: string) =>
  apiClient.get<ParkingOccupancy>(`/api/v1/parking/car-parks/${carParkId}/occupancy`).then((r) => r.data)

export const getParkingSessions = (params?: { car_park_id?: string; status?: string; limit?: number }) =>
  apiClient
    .get<ParkingSession[]>('/api/v1/parking/sessions', { params: { limit: 50, ...params } })
    .then((r) => r.data)

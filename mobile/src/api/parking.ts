/**
 * Car parks, their bays, and who is parked.
 *
 * THE PATH WAS INVENTED AND SO WAS THE SHAPE. This client asked for
 * /api/v1/parking/car-parks; the route is /api/v1/carparks, one word, without
 * the parking prefix. It also described a car park with a `location` and
 * `total_bays`, where the table has `address` and `total_capacity`. The request
 * answered 404 and the screen said "No sessions", which reads as an empty car
 * park rather than a screen that cannot ask.
 *
 * OCCUPANCY IS NO LONGER A REQUEST PER CARD. There was a
 * /car-parks/{id}/occupancy call — also invented — fired once per car park from
 * inside the list row, so ten car parks meant ten more requests. The list
 * endpoint already counts bays per car park, so the number is computed from the
 * row that is already in hand.
 */
import { apiClient } from './client'

export interface CarPark {
  id: string
  name: string
  description: string | null
  address: string | null
  site_id: string | null
  site_name: string | null
  total_capacity: number
  levels: number
  is_active: boolean
  /** Counted by the server per car park. */
  bay_count: number
  available_bays: number
  occupied_bays: number
  created_at: string
}

export interface ParkingZone {
  id: string
  car_park_id: string
  name: string
  zone_type: string | null
  bay_count: number
  available: number
  occupied: number
  is_active: boolean
}

/** A car park with its zones — the detail endpoint. */
export interface CarParkDetail extends CarPark {
  zones: ParkingZone[]
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

export interface ParkingDashboard {
  total_carparks: number
  total_capacity: number
  available_bays: number
  occupied_bays: number
  reserved_bays: number
  active_sessions: number
  revenue_today: number | null
  overstay_count: number
}

/**
 * How full a car park is, as a percentage.
 *
 * Pure and exported for the test. Bays counted is the honest denominator:
 * total_capacity is what the car park was declared to hold, and when no bays
 * have been created yet dividing by it reports 0% for a car park whose state
 * is simply unknown. Returns null in that case so the bar can be left off
 * rather than drawn empty.
 */
export function occupancyPct(park: Pick<CarPark, 'bay_count' | 'occupied_bays'>): number | null {
  if (!park.bay_count) return null
  return Math.round((park.occupied_bays / park.bay_count) * 100)
}

export const getParkingDashboard = () =>
  apiClient.get<ParkingDashboard>('/api/v1/parking/dashboard').then((r) => r.data)

export const getCarParks = () =>
  apiClient.get<CarPark[]>('/api/v1/carparks').then((r) => r.data)

export const getCarPark = (carParkId: string) =>
  apiClient.get<CarParkDetail>(`/api/v1/carparks/${carParkId}`).then((r) => r.data)

export const getParkingSessions = (params?: { car_park_id?: string; status?: string; limit?: number }) =>
  apiClient
    .get<ParkingSession[]>('/api/v1/parking/sessions', { params: { limit: 50, ...params } })
    .then((r) => r.data)

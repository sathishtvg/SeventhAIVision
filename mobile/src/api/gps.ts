import { apiClient } from './client'

/** Whether a vehicle is being heard from, worked out by the server from its last fix (docs/FLEET_GPS_PROVIDERS.md). */
export type TrackingStatus = 'online' | 'stale' | 'offline' | 'not_configured'

/** A fleet vehicle as /api/v1/gps/vehicles sends it. (This used to name `status`, `last_speed_kmh` and
 *  `last_seen_at`, which the server never sent: the screen showed no status, speed or time.) */
export interface GpsVehicle {
  id: string
  name: string
  plate_number: string | null
  vehicle_type: string
  color: string | null
  driver_name: string | null
  site_id: string | null
  site_name: string | null
  is_active: boolean
  current_status: 'moving' | 'idle' | 'offline'
  tracking_status: TrackingStatus
  last_lat: number | null
  last_lon: number | null
  last_speed: number | string | null
  last_fix_at: string | null
  last_position_at: string | null
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

export const TRACKING_LABEL: Record<TrackingStatus, string> = {
  online: 'Online', stale: 'Stale', offline: 'Offline', not_configured: 'Not configured',
}

/** The vehicles the person may see (the server keeps to their sites). Not archived ones. */
export const getVehicles = () =>
  apiClient.get<GpsVehicle[]>('/api/v1/gps/vehicles').then((r) => r.data)

/** How many of `vehicles` are in each tracking status. */
export function countByTracking(vehicles: GpsVehicle[]): Record<TrackingStatus, number> {
  const out: Record<TrackingStatus, number> = { online: 0, stale: 0, offline: 0, not_configured: 0 }
  for (const v of vehicles) out[v.tracking_status ?? 'not_configured'] += 1
  return out
}

export const getVehicleTrack = (vehicleId: string, params?: { hours?: number }) =>
  apiClient
    .get<GpsPosition[]>(`/api/v1/gps/vehicles/${vehicleId}/positions`, { params: { hours: 4, ...params } })
    .then((r) => r.data)

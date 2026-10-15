/**
 * The fleet map's rules, kept apart from Leaflet so they can be read and tested.
 *
 * A vehicle's status is the server's rule, worked out again here against the
 * clock - so a vehicle that stops reporting turns Stale, then Offline, on the
 * screen, whether or not anything was fetched in between: Online under the
 * organisation's "online" minutes since its last fix, Stale under its
 * "offline" minutes, then Offline. A vehicle with tracking off or no device is
 * Not configured, and is listed but never put on the map. A vehicle is never
 * drawn without a fix: no place is made up.
 */
import type { MapVehicle, TrackingStatus } from '@/api/fleet'

export interface Limits { online_minutes: number; offline_minutes: number }

export function statusNow(v: Pick<MapVehicle, 'tracking_status' | 'last_fix_at'>, limits: Limits, now: number): TrackingStatus {
  if (v.tracking_status === 'not_configured') return 'not_configured'
  if (!v.last_fix_at) return 'offline'
  const minutes = (now - new Date(v.last_fix_at).getTime()) / 60_000
  if (minutes < limits.online_minutes) return 'online'
  if (minutes < limits.offline_minutes) return 'stale'
  return 'offline'
}

/** On the map: a vehicle that is configured and has a place. */
export function drawn(v: MapVehicle): v is MapVehicle & { last_lat: number; last_lon: number } {
  return v.tracking_status !== 'not_configured' && v.last_lat != null && v.last_lon != null && !!v.last_fix_at
}

const GREEN = '#22c55e', AMBER = '#f59e0b', GREY = '#94a3b8'

/** Online is solid; Stale is faint; Offline is a hollow, dashed ring at the last known place. */
export const STATUS_STYLE: Record<Exclude<TrackingStatus, 'not_configured'>, { color: string; fillColor: string; fillOpacity: number; opacity: number; dashArray?: string }> = {
  online: { color: '#ffffff', fillColor: GREEN, fillOpacity: 0.95, opacity: 0.9 },
  stale: { color: AMBER, fillColor: AMBER, fillOpacity: 0.45, opacity: 0.55 },
  offline: { color: GREY, fillColor: GREY, fillOpacity: 0, opacity: 0.8, dashArray: '3 3' },
}

/** Where a short line from the vehicle in the direction it is heading ends: about `metres` ahead. */
export function headingEnd(lat: number, lon: number, heading: number, metres = 45): [number, number] {
  const rad = (heading * Math.PI) / 180
  const dLat = (metres * Math.cos(rad)) / 111_320
  const dLon = (metres * Math.sin(rad)) / (111_320 * Math.cos((lat * Math.PI) / 180))
  return [lat + dLat, lon + dLon]
}

/** The rows a refetch of some vehicles brought, put in place of the old ones; the rest are kept as they were. */
export function merge(all: MapVehicle[], fresh: MapVehicle[]): MapVehicle[] {
  const byId = new Map(fresh.map((v) => [v.id, v]))
  const kept = all.map((v) => byId.get(v.id) ?? v)
  const added = fresh.filter((v) => !all.some((a) => a.id === v.id))
  return [...kept, ...added]
}

export interface MapFilter { q: string; site: string; type: string; statuses: TrackingStatus[] }

export function matches(v: MapVehicle, f: MapFilter, status: TrackingStatus): boolean {
  if (f.statuses.length && !f.statuses.includes(status)) return false
  if (f.site && v.site_id !== f.site) return false
  if (f.type && v.vehicle_type !== f.type) return false
  const q = f.q.trim().toLowerCase().replace(/[\s-]/g, '')
  if (!q) return true
  return [v.plate_number, v.name, v.driver_name, v.site_name].some((s) => (s ?? '').toLowerCase().replace(/[\s-]/g, '').includes(q))
}

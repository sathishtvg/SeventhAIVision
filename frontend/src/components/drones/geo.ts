import type { LatLng, Waypoint, Zone, ZoneShape } from '@/api/drones'

/** Great-circle distance in metres, as the backend's geofence.haversine_meters. */
export function haversine(a: LatLng, b: LatLng): number {
  const R = 6_371_000
  const toRad = (d: number) => (d * Math.PI) / 180
  const dLat = toRad(b.lat - a.lat)
  const dLng = toRad(b.lng - a.lng)
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

export function formatDistance(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`
}

export function routeLengthM(base: LatLng | null, wps: Waypoint[], returnToBase: boolean): number {
  const pts: LatLng[] = []
  if (base) pts.push(base)
  wps.forEach((w) => pts.push({ lat: w.latitude, lng: w.longitude }))
  if (base && returnToBase && wps.length) pts.push(base)
  let d = 0
  for (let i = 1; i < pts.length; i++) d += haversine(pts[i - 1], pts[i])
  return d
}

// ── Zone drafts (the zone being drawn) ──────────────────────────────────────

export interface ZoneDraft {
  shape: ZoneShape
  points: LatLng[]          // polygon corners, or two rectangle corners
  center: LatLng | null     // circle
  radius_m: number | null   // circle
}

export const emptyDraft = (shape: ZoneShape): ZoneDraft => ({ shape, points: [], center: null, radius_m: null })

export function draftFromZone(z: Zone): ZoneDraft {
  if (z.shape === 'CIRCLE') {
    return { shape: 'CIRCLE', points: [], radius_m: z.radius_m,
             center: z.center_latitude != null && z.center_longitude != null
               ? { lat: z.center_latitude, lng: z.center_longitude } : null }
  }
  return { shape: z.shape, points: z.polygon ?? [], center: null, radius_m: null }
}

/** Whether the draft is a shape the server will accept. */
export function draftComplete(d: ZoneDraft): boolean {
  if (d.shape === 'CIRCLE') return !!d.center && !!d.radius_m && d.radius_m > 0
  if (d.shape === 'RECTANGLE') return d.points.length === 2 || d.points.length === 4
  return d.points.length >= 3
}

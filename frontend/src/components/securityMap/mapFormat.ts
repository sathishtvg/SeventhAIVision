/**
 * Security map — the words and colours the map screens share.
 */
import type { Feature, LayerKey, PlaceKind } from '@/api/siteMap'

/** The same tiles the site map uses: configurable, so that an installation
 *  with no internet points at a tile server inside its own network. */
export const TILE_URL =
  import.meta.env.VITE_MAP_TILE_URL ?? 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
export const TILE_ATTRIBUTION =
  import.meta.env.VITE_MAP_TILE_ATTRIBUTION ?? '&copy; OpenStreetMap contributors'
/** Where the map opens before it knows where anything is. */
export const DEFAULT_CENTRE: [number, number] = [1.3521, 103.8198]

export const PLACE_LABEL: Record<PlaceKind, string> = {
  BUILDING: 'Building', FLOOR: 'Floor', GATE: 'Gate', ACCESS_POINT: 'Access point',
  EMERGENCY_POINT: 'Emergency point', ASSEMBLY_POINT: 'Assembly point', ZONE: 'Zone', PARKING: 'Parking',
  OTHER: 'Other',
}

/** The layers that are on when the map opens: what an officer looks for first. */
export const ON_AT_FIRST: LayerKey[] = ['SITE', 'CAMERA', 'GUARD', 'INCIDENT', 'SITUATION', 'DRONE', 'PLACE']

const RED = '#ff4560', AMBER = '#f59e0b', GREEN = '#22c55e', BLUE = '#38bdf8', GREY = '#94a3b8', VIOLET = '#a78bfa'

const BY_STATE: Record<string, string> = {
  critical: RED, high: RED, emergency: RED, offline: RED, alert: RED, fault: RED,
  medium: AMBER, busy: AMBER, degraded: AMBER, attention: AMBER, warning: AMBER,
  low: GREEN, available: GREEN, online: GREEN, ok: GREEN, ready: GREEN, scanned: GREEN,
  info: BLUE, mission_active: BLUE, returning: BLUE, preparing: BLUE,
}
const BY_LAYER: Partial<Record<LayerKey, string>> = { PLACE: VIOLET, DRONE_ZONE: BLUE, DRONE: BLUE, CHECKPOINT: GREY }

/** The colour a feature is drawn in: by what state it is in, else by what it is. */
export function colourOf(feature: Pick<Feature, 'layer' | 'state'>): string {
  return BY_STATE[(feature.state ?? '').toLowerCase()] ?? BY_LAYER[feature.layer] ?? GREY
}

/** How big a feature's mark is: the things an officer acts on are the biggest. */
export function sizeOf(layer: LayerKey): number {
  return layer === 'INCIDENT' || layer === 'SITUATION' ? 11 : layer === 'GUARD' || layer === 'DRONE' ? 9
    : layer === 'SITE' ? 13 : 7
}

export const pretty = (s: string | null | undefined) =>
  (s ?? '').replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase())

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** How long ago a position was recorded, in the words a person would use. */
export function ago(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return 'no position recorded'
  if (seconds < 90) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 90) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`
}

export function distance(metres: number | null | undefined): string {
  if (metres === null || metres === undefined) return 'distance not known'
  return metres < 1000 ? `${metres} m` : `${(metres / 1000).toFixed(1)} km`
}

/** The second line under a feature's name: what state it is in, and since when. */
export function about(feature: Feature): string {
  const d = feature.detail as Record<string, string | number | boolean | null | undefined>
  switch (feature.layer) {
    case 'GUARD':
      return `${pretty(feature.state)} · ${d.position_source ?? 'no position'}, ${ago(d.position_age_s as number | null)}`
    case 'CAMERA': return `Stream ${feature.state ?? 'unknown'}`
    case 'SITE': return `${d.cameras_online ?? 0} of ${d.cameras ?? 0} cameras online`
    case 'INCIDENT': return `${pretty(feature.state)} · ${pretty(d.status as string)} · at ${d.position_from}`
    case 'ALERT': return `${pretty(feature.state)} · ${pretty(d.kind as string)}`
    case 'SITUATION': return `Risk ${pretty(d.risk_level as string) || '—'} · ${pretty(d.stands as string)}`
    case 'DRONE': return `${pretty(feature.state)}${d.battery_level != null ? ` · battery ${d.battery_level}%` : ''}`
    case 'CHECKPOINT': return `${d.route_name} · ${feature.at ? `last scanned ${fmt(feature.at)}` : 'never scanned'}`
    case 'PLACE':
      return [PLACE_LABEL[d.kind as PlaceKind], d.parent_name && `in ${d.parent_name}`,
              d.level != null && `level ${d.level}`,
              d.last_door_event && `door last ${pretty(d.last_door_event as string).toLowerCase()}`]
        .filter(Boolean).join(' · ')
    case 'DRONE_ZONE': return `Drone zone · ${pretty(feature.state)}`
    default: return ''
  }
}

/** Features at the same spot, so that twenty incidents at one camera are one mark with a count. */
export function gathered(features: Feature[]): Feature[][] {
  const groups = new Map<string, Feature[]>()
  for (const f of features) {
    if (f.latitude === null || f.longitude === null) continue
    const key = `${f.layer}:${f.latitude.toFixed(5)}:${f.longitude.toFixed(5)}`
    groups.set(key, [...(groups.get(key) ?? []), f])
  }
  return [...groups.values()]
}

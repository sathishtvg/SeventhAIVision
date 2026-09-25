/**
 * Map editing for the drone module, on react-leaflet — no drawing plugin.
 *
 *   ZoneLayer        existing security zones, read-only, coloured by type
 *   RouteEditor      click the map to add waypoints, drag them to move, drag
 *                    the base; the path follows the order
 *   ZoneDrawer       draw a polygon (click the corners), a rectangle (two
 *                    opposite corners) or a circle (centre, then the edge);
 *                    corners stay draggable until saved
 */
import { useMemo } from 'react'
import L from 'leaflet'
import { Circle, Marker, Polygon, Polyline, Tooltip, useMapEvents } from 'react-leaflet'
import type { LatLng, Waypoint, Zone } from '@/api/drones'
import { ZONE_COLOR, numberIcon, pretty } from './droneFormat'
import { haversine } from './geo'
import type { ZoneDraft } from './geo'

export function ZoneLayer({ zones, highlight }: { zones: Zone[]; highlight?: string | null }) {
  return (
    <>
      {zones.filter((z) => z.is_active !== false).map((z) => {
        const color = ZONE_COLOR[z.zone_type] ?? '#8ea0b8'
        const weight = z.id === highlight ? 4 : 2
        const tip = <Tooltip sticky>{z.name} · {pretty(z.zone_type)}</Tooltip>
        if (z.shape === 'CIRCLE' && z.center_latitude != null && z.center_longitude != null && z.radius_m) {
          return (
            <Circle key={z.id} center={[z.center_latitude, z.center_longitude]} radius={z.radius_m}
                    pathOptions={{ color, weight, fillOpacity: 0.12, dashArray: '6 4' }}>{tip}</Circle>
          )
        }
        const pts = (z.polygon ?? []).map((p) => [p.lat, p.lng] as [number, number])
        if (pts.length < 3) return null
        return (
          <Polygon key={z.id} positions={pts} pathOptions={{ color, weight, fillOpacity: 0.12, dashArray: '6 4' }}>
            {tip}
          </Polygon>
        )
      })}
    </>
  )
}

function ClickCatcher({ onClick }: { onClick: (p: LatLng) => void }) {
  useMapEvents({ click: (e) => onClick({ lat: e.latlng.lat, lng: e.latlng.lng }) })
  return null
}

// ── Route ────────────────────────────────────────────────────────────────────

export interface RouteEditorProps {
  base: LatLng | null
  waypoints: Waypoint[]
  returnToBase: boolean
  editable: boolean
  selected: number | null
  onSelect: (i: number | null) => void
  onChange: (next: Waypoint[]) => void
  onBaseChange: (p: LatLng) => void
  /** When set, a map click places the base instead of adding a waypoint. */
  placingBase?: boolean
}

export function RouteEditor(p: RouteEditorProps) {
  const path = useMemo(() => {
    const pts: [number, number][] = []
    if (p.base) pts.push([p.base.lat, p.base.lng])
    p.waypoints.forEach((w) => pts.push([w.latitude, w.longitude]))
    if (p.base && p.returnToBase && p.waypoints.length) pts.push([p.base.lat, p.base.lng])
    return pts
  }, [p.base, p.waypoints, p.returnToBase])

  const move = (i: number, ll: L.LatLng) =>
    p.onChange(p.waypoints.map((w, j) => (j === i ? { ...w, latitude: ll.lat, longitude: ll.lng } : w)))

  return (
    <>
      {p.editable && (
        <ClickCatcher onClick={(ll) => {
          if (p.placingBase) { p.onBaseChange(ll); return }
          p.onChange([...p.waypoints, { name: null, latitude: ll.lat, longitude: ll.lng, altitude_m: null,
                                        hover_seconds: 0, observe_seconds: 0, snapshot_required: false }])
          p.onSelect(p.waypoints.length)
        }} />
      )}
      {path.length > 1 && (
        <Polyline positions={path} pathOptions={{ color: '#4f8cff', weight: 3, dashArray: p.editable ? '8 6' : undefined }} />
      )}
      {p.base && (
        <Marker position={[p.base.lat, p.base.lng]} icon={numberIcon('H', '#00c48c', 28)} draggable={p.editable}
                eventHandlers={{ dragend: (e) => { const ll = (e.target as L.Marker).getLatLng(); p.onBaseChange({ lat: ll.lat, lng: ll.lng }) } }}>
          <Tooltip>Launch and landing point</Tooltip>
        </Marker>
      )}
      {p.waypoints.map((w, i) => (
        <Marker key={i} position={[w.latitude, w.longitude]}
                icon={numberIcon(String(i + 1), i === p.selected ? '#ff7a45' : '#4f8cff')}
                draggable={p.editable}
                eventHandlers={{
                  click: () => p.onSelect(i),
                  dragend: (e) => move(i, (e.target as L.Marker).getLatLng()),
                }}>
          <Tooltip>
            {w.name || `Waypoint ${i + 1}`}
            {w.hover_seconds ? ` · hover ${w.hover_seconds}s` : ''}
            {w.snapshot_required ? ' · snapshot' : ''}
          </Tooltip>
        </Marker>
      ))}
    </>
  )
}

// ── Zones ────────────────────────────────────────────────────────────────────

function rectCorners(a: LatLng, b: LatLng): [number, number][] {
  return [[a.lat, a.lng], [a.lat, b.lng], [b.lat, b.lng], [b.lat, a.lng]]
}

export function ZoneDrawer({ draft, onChange, color = '#ffb020' }: { draft: ZoneDraft; onChange: (d: ZoneDraft) => void
                                                                  color?: string }) {
  const add = (ll: LatLng) => {
    if (draft.shape === 'CIRCLE') {
      if (!draft.center) onChange({ ...draft, center: ll })
      else onChange({ ...draft, radius_m: Math.round(haversine(draft.center, ll)) })
      return
    }
    if (draft.shape === 'RECTANGLE') {
      onChange({ ...draft, points: draft.points.length >= 2 ? [ll] : [...draft.points, ll] })
      return
    }
    onChange({ ...draft, points: [...draft.points, ll] })
  }
  const drag = (i: number, ll: L.LatLng) =>
    onChange({ ...draft, points: draft.points.map((p, j) => (j === i ? { lat: ll.lat, lng: ll.lng } : p)) })

  let shape = null
  if (draft.shape === 'CIRCLE' && draft.center && draft.radius_m) {
    shape = <Circle center={[draft.center.lat, draft.center.lng]} radius={draft.radius_m}
                    pathOptions={{ color, weight: 3, fillOpacity: 0.18 }} />
  } else if (draft.shape === 'RECTANGLE' && draft.points.length === 2) {
    shape = <Polygon positions={rectCorners(draft.points[0], draft.points[1])} pathOptions={{ color, weight: 3, fillOpacity: 0.18 }} />
  } else if (draft.points.length >= 2) {
    const pts = draft.points.map((p) => [p.lat, p.lng] as [number, number])
    shape = draft.points.length >= 3
      ? <Polygon positions={pts} pathOptions={{ color, weight: 3, fillOpacity: 0.18 }} />
      : <Polyline positions={pts} pathOptions={{ color, weight: 3 }} />
  }
  return (
    <>
      <ClickCatcher onClick={add} />
      {shape}
      {draft.points.map((p, i) => (
        <Marker key={i} position={[p.lat, p.lng]} icon={numberIcon(String(i + 1), color, 20)} draggable
                eventHandlers={{ dragend: (e) => drag(i, (e.target as L.Marker).getLatLng()) }} />
      ))}
      {draft.center && (
        <Marker position={[draft.center.lat, draft.center.lng]} icon={numberIcon('C', color, 22)} draggable
                eventHandlers={{ dragend: (e) => { const ll = (e.target as L.Marker).getLatLng()
                                                   onChange({ ...draft, center: { lat: ll.lat, lng: ll.lng } }) } }} />
      )}
    </>
  )
}

/**
 * Geofences on the map: each circle or polygon with its name, and - while one is being drawn - the draft. A click
 * on the map sets a circle's centre or adds a polygon's next corner. Nothing here is a live position.
 */
import { useEffect, useRef } from 'react'
import L from 'leaflet'
import { Circle, CircleMarker, MapContainer, Polygon, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { Box } from '@mui/material'
import { DEFAULT_CENTRE, TILE_ATTRIBUTION, TILE_URL } from '@/components/securityMap/mapFormat'
import type { Geofence, LatLon } from '@/api/fleet'
import type { Draft } from './geofenceFormat'

const FENCE = { color: '#6C63FF', weight: 2, fillOpacity: 0.12 }
const OFF = { color: '#9e9e9e', weight: 1, dashArray: '4 4', fillOpacity: 0.05 }
const DRAFT = { color: '#f59e0b', weight: 3, fillOpacity: 0.18 }

function pointsOf(fences: Geofence[], draft?: Draft | null): [number, number][] {
  const out: [number, number][] = []
  for (const g of fences) {
    if (g.shape === 'circle' && g.center) out.push([g.center.lat, g.center.lon])
    else g.polygon.forEach((p) => out.push([p.lat, p.lon]))
  }
  if (draft?.kind === 'circle' && draft.center) out.push([draft.center.lat, draft.center.lon])
  if (draft?.kind === 'polygon') draft.points.forEach((p) => out.push([p.lat, p.lon]))
  return out
}

function Fit({ points, focus }: { points: [number, number][]; focus: string }) {
  const map = useMap()
  const fitted = useRef('')
  useEffect(() => {
    if (!points.length || fitted.current === focus) return
    fitted.current = focus
    if (points.length === 1) map.setView(points[0], 16)
    else map.fitBounds(L.latLngBounds(points), { padding: [30, 30], maxZoom: 17 })
  }, [map, points, focus])
  return null
}

function Picker({ onPick }: { onPick: (p: LatLon) => void }) {
  useMapEvents({ click: (e) => onPick({ lat: e.latlng.lat, lon: e.latlng.lng }) })
  return null
}

export function GeofenceMap({ fences, draft, onPick, focus = 'all', height = 420 }: {
  fences: Geofence[]
  draft?: Draft | null
  onPick?: (p: LatLon) => void
  /** Changes when the map should fit again: a different geofence opened, or the list loaded. */
  focus?: string
  height?: number | string
}) {
  const points = pointsOf(fences, draft)
  const radius = draft?.kind === 'circle' ? Number(draft.radius) : NaN
  return (
    <Box sx={{ height, borderRadius: 2, overflow: 'hidden', border: 1, borderColor: 'divider', cursor: onPick ? 'crosshair' : undefined }}
         data-testid="geofence-map">
      <MapContainer center={DEFAULT_CENTRE} zoom={12} style={{ height: '100%', width: '100%' }} scrollWheelZoom>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
        <Fit points={points} focus={`${focus}|${points.length > 0}`} />
        {onPick && <Picker onPick={onPick} />}
        {fences.map((g) => {
          const style = g.is_active ? FENCE : OFF
          return g.shape === 'circle' && g.center && g.radius_m ? (
            <Circle key={g.id} center={[g.center.lat, g.center.lon]} radius={g.radius_m} pathOptions={style}>
              <Tooltip>{g.name}</Tooltip>
            </Circle>
          ) : (
            <Polygon key={g.id} positions={g.polygon.map((p) => [p.lat, p.lon] as [number, number])} pathOptions={style}>
              <Tooltip>{g.name}</Tooltip>
            </Polygon>
          )
        })}
        {draft?.kind === 'circle' && draft.center && (
          <>
            <CircleMarker center={[draft.center.lat, draft.center.lon]} radius={5} pathOptions={{ ...DRAFT, fillOpacity: 1 }} />
            {Number.isFinite(radius) && radius > 0 && <Circle center={[draft.center.lat, draft.center.lon]} radius={radius} pathOptions={DRAFT} />}
          </>
        )}
        {draft?.kind === 'polygon' && draft.points.length > 0 && (
          <>
            {draft.points.length > 2 && <Polygon positions={draft.points.map((p) => [p.lat, p.lon] as [number, number])} pathOptions={DRAFT} />}
            {draft.points.map((p, i) => (
              <CircleMarker key={`${p.lat},${p.lon},${i}`} center={[p.lat, p.lon]} radius={5} pathOptions={{ ...DRAFT, fillOpacity: 1 }}>
                <Tooltip>Corner {i + 1}</Tooltip>
              </CircleMarker>
            ))}
          </>
        )}
      </MapContainer>
    </Box>
  )
}

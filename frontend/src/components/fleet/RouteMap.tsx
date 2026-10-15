/**
 * A vehicle's route over a range: the line through its kept positions (thinned
 * by the server for drawing), where it stopped and for how long, where it
 * started and ended, and - while playing back - where it was at the moment on
 * the slider.
 */
import { useEffect, useRef } from 'react'
import L from 'leaflet'
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { Box } from '@mui/material'
import { DEFAULT_CENTRE, TILE_ATTRIBUTION, TILE_URL } from '@/components/securityMap/mapFormat'
import type { JourneyStop, RoutePoint } from '@/api/fleet'
import { minutesText } from './journeyFormat'

function Fit({ points }: { points: [number, number][] }) {
  const map = useMap()
  const fitted = useRef('')
  useEffect(() => {
    const key = points.length ? `${points[0].join()}|${points[points.length - 1].join()}|${points.length}` : ''
    if (!key || fitted.current === key) return
    fitted.current = key
    if (points.length === 1) map.setView(points[0], 15)
    else map.fitBounds(L.latLngBounds(points), { padding: [30, 30], maxZoom: 16 })
  }, [map, points])
  return null
}

export function RouteMap({ route, stops, at, height = 420 }: { route: RoutePoint[]; stops: JourneyStop[]; at: RoutePoint | null; height?: number }) {
  const line = route.map((p) => [p.lat, p.lon] as [number, number])
  return (
    <Box sx={{ height, borderRadius: 2, overflow: 'hidden', border: 1, borderColor: 'divider' }} data-testid="route-map">
      <MapContainer center={DEFAULT_CENTRE} zoom={12} style={{ height: '100%', width: '100%' }} scrollWheelZoom>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
        <Fit points={line} />
        {line.length > 1 && <Polyline positions={line} pathOptions={{ color: '#6C63FF', weight: 4, opacity: 0.85 }} />}
        {stops.map((s) => (
          <CircleMarker key={s.start} center={[s.lat, s.lon]} radius={7}
                        pathOptions={{ color: '#ffffff', fillColor: '#38bdf8', fillOpacity: 0.9, weight: 2 }}>
            <Tooltip>Stopped {minutesText(s.minutes)}{s.ignition_off ? ', ignition off' : ''} from {new Date(s.start).toLocaleTimeString()}</Tooltip>
          </CircleMarker>
        ))}
        {route.length > 0 && (
          <CircleMarker center={line[0]} radius={6} pathOptions={{ color: '#22c55e', fillColor: '#22c55e', fillOpacity: 1 }}>
            <Tooltip>Start {new Date(route[0].at).toLocaleString()}</Tooltip>
          </CircleMarker>
        )}
        {route.length > 1 && (
          <CircleMarker center={line[line.length - 1]} radius={6} pathOptions={{ color: '#ff4560', fillColor: '#ff4560', fillOpacity: 1 }}>
            <Tooltip>End {new Date(route[route.length - 1].at).toLocaleString()}</Tooltip>
          </CircleMarker>
        )}
        {at && (
          <CircleMarker center={[at.lat, at.lon]} radius={9} pathOptions={{ color: '#ffffff', fillColor: '#f59e0b', fillOpacity: 1, weight: 3 }}>
            <Tooltip permanent direction="top" offset={[0, -8]}>
              {new Date(at.at).toLocaleTimeString()}{at.speed != null ? ` · ${Math.round(at.speed)} km/h` : ''}
            </Tooltip>
          </CircleMarker>
        )}
      </MapContainer>
    </Box>
  )
}

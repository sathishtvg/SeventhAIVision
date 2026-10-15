/**
 * The fleet on a street map: each vehicle that has reported, at its last fix,
 * in its status - Online solid, Stale faint, Offline a hollow dashed ring at
 * the last known place - with a short line the way it is heading when that is
 * reported, and its registration number beside it.
 *
 * The tiles are the platform's own setting (VITE_MAP_TILE_URL), the same as
 * every other map here. The status is worked out against the clock as the map
 * is looked at (`statusNow`), so a vehicle that stops reporting goes faint
 * without anything being fetched. A vehicle with no fix is not drawn.
 */
import { Fragment, useEffect, useRef } from 'react'
import L from 'leaflet'
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { Box } from '@mui/material'
import { ageText } from '@/components/states'
import { useNow } from '@/motion'
import { DEFAULT_CENTRE, TILE_ATTRIBUTION, TILE_URL } from '@/components/securityMap/mapFormat'
import type { MapVehicle } from '@/api/fleet'
import { STATUS_STYLE, drawn, headingEnd, statusNow, type Limits } from './fleetMapFormat'

function FitOnce({ points }: { points: [number, number][] }) {
  const map = useMap()
  const done = useRef(false)
  useEffect(() => {
    if (done.current || points.length === 0) return
    done.current = true
    if (points.length === 1) map.setView(points[0], 15)
    else map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 16 })
  }, [map, points])
  return null
}

function Focus({ at }: { at: [number, number] | null }) {
  const map = useMap()
  useEffect(() => { if (at) map.setView(at, Math.max(map.getZoom?.() ?? 15, 16)) }, [map, at])
  return null
}

interface Props {
  vehicles: MapVehicle[]
  limits: Limits
  height?: number | string
  selectedId?: string | null
  /** Centre on the selected vehicle; changes each time "Focus" is asked for. */
  focusTick?: number
  onSelect?: (id: string) => void
  /** Registration numbers beside the markers (off on a small map). */
  labels?: boolean
}

export function FleetMap({ vehicles, limits, height = 380, selectedId, focusTick = 0, onSelect, labels = true }: Props) {
  const now = useNow(10_000)
  const shown = vehicles.filter(drawn)
  const points = shown.map((v) => [v.last_lat, v.last_lon] as [number, number])
  const selected = shown.find((v) => v.id === selectedId)
  const focusAt = selected && focusTick ? ([selected.last_lat, selected.last_lon] as [number, number]) : null
  return (
    <Box sx={{ height, borderRadius: 2, overflow: 'hidden', border: 1, borderColor: 'divider' }} data-testid="fleet-map">
      <MapContainer center={DEFAULT_CENTRE} zoom={12} style={{ height: '100%', width: '100%' }} scrollWheelZoom>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
        <FitOnce points={points} />
        <Focus key={focusTick} at={focusAt} />
        {shown.map((v) => {
          const status = statusNow(v, limits, now)
          if (status === 'not_configured') return null
          const style = STATUS_STYLE[status]
          const isSelected = v.id === selectedId
          const heading = status === 'online' && v.last_heading != null
            ? headingEnd(v.last_lat, v.last_lon, v.last_heading) : null
          return (
            <Fragment key={v.id}>
              {heading && <Polyline positions={[[v.last_lat, v.last_lon], heading]} pathOptions={{ color: style.fillColor, weight: 3, opacity: 0.9 }} />}
              <CircleMarker center={[v.last_lat, v.last_lon]} radius={isSelected ? 10 : 7}
                            pathOptions={{ ...style, weight: isSelected ? 3 : 2 }}
                            eventHandlers={{ click: () => onSelect?.(v.id) }}>
                <Tooltip direction="right" offset={[8, 0]} permanent={labels} opacity={0.9}>
                  {v.plate_number ?? v.name}
                  {status !== 'online' && ` · ${status === 'stale' ? 'Stale' : 'Last known'}, ${ageText(now - new Date(v.last_fix_at!).getTime())}`}
                </Tooltip>
              </CircleMarker>
            </Fragment>
          )
        })}
      </MapContainer>
    </Box>
  )
}

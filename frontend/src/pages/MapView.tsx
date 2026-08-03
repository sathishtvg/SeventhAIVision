/**
 * GIS map — every site plotted with live camera health, for the
 * one-operator-watching-many-sites case the platform exists to serve.
 *
 * WHY LEAFLET AND NOT MAPBOX/GOOGLE
 * Both of those require a per-deployment API key. This product ships on-prem
 * to ports, industrial parks and government sites; making every install
 * register for a commercial mapping account (and reach the internet to
 * validate it) is an operational burden the customer did not ask for.
 * Leaflet is MIT, needs no key, and points at whatever tile server you give
 * it — including one running inside the customer's own network.
 *
 * TILES ARE CONFIGURABLE FOR EXACTLY THAT REASON. The default is public
 * OpenStreetMap, which needs outbound internet. An air-gapped site sets
 * VITE_MAP_TILE_URL to a local tile server and everything else keeps working.
 *
 * DATA: /command-centre/overview already computed per-site camera health for
 * the Command Centre; this reuses it rather than adding a second endpoint
 * with its own subtly-different definition of "online".
 */
import { useMemo } from 'react'
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import {
  Box, Typography, Chip, Button, Alert, Skeleton, Divider,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import VideocamIcon from '@mui/icons-material/Videocam'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { apiClient } from '@/api/client'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'

const TILE_URL =
  import.meta.env.VITE_MAP_TILE_URL ?? 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const TILE_ATTRIBUTION =
  import.meta.env.VITE_MAP_TILE_ATTRIBUTION ?? '&copy; OpenStreetMap contributors'

interface MapSite {
  id: string
  name: string
  address: string | null
  latitude: number | null
  longitude: number | null
  cameras_online: number
  cameras_offline: number
  cameras_degraded: number
  cameras_total: number
  active_alerts: number
  critical_alerts: number
}

/** Health drives the marker colour. Deliberately three states, not a gradient:
 * an operator scanning a wall of markers needs "fine / needs a look / down"
 * readable at a glance, not a shade they have to interpret. */
function healthOf(s: MapSite): { color: string; label: string } {
  if (s.cameras_total === 0) return { color: '#7A8195', label: 'No cameras' }
  if (s.cameras_online === 0) return { color: '#FF4560', label: 'All cameras down' }
  if (s.cameras_offline > 0 || s.cameras_degraded > 0)
    return { color: '#FF9800', label: 'Partially degraded' }
  return { color: '#00E396', label: 'All cameras online' }
}

/** Fit the viewport to the plotted sites once they load. Without this the map
 * opens on a hardcoded centre, which is wrong for every customer but one. */
function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap()
  useMemo(() => {
    if (points.length === 0) return
    if (points.length === 1) {
      map.setView(points[0], 15)
    } else {
      map.fitBounds(points, { padding: [48, 48] })
    }
  }, [points, map])
  return null
}

export function MapViewPage() {
  const navigate = useNavigate()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['cc-overview'],
    queryFn: () => apiClient.get('/api/v1/command-centre/overview').then((r) => r.data),
  })

  const sites: MapSite[] = data?.sites ?? []
  const plotted = sites.filter((s) => s.latitude != null && s.longitude != null)
  const unplotted = sites.filter((s) => s.latitude == null || s.longitude == null)
  const points = plotted.map((s) => [s.latitude!, s.longitude!] as [number, number])

  const totals = useMemo(() => ({
    online: sites.reduce((a, s) => a + s.cameras_online, 0),
    down: sites.reduce((a, s) => a + s.cameras_offline, 0),
    degraded: sites.reduce((a, s) => a + s.cameras_degraded, 0),
  }), [sites])

  return (
    <Box>
      <PageHeader
        title="Site Map"
        subtitle="Every site plotted with live camera health"
      />

      {isError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Could not load site data. The map may be incomplete.
        </Alert>
      )}

      <Stack direction="row" spacing={1} sx={{ mb: 2, flexWrap: 'wrap', alignItems: 'center' }}>
        <Chip icon={<VideocamIcon />} label={`${totals.online} online`}
          sx={{ bgcolor: '#00E39622', color: '#00E396', fontWeight: 600 }} />
        {totals.degraded > 0 && (
          <Chip label={`${totals.degraded} degraded`}
            sx={{ bgcolor: '#FF980022', color: '#FF9800', fontWeight: 600 }} />
        )}
        {totals.down > 0 && (
          <Chip label={`${totals.down} down`}
            sx={{ bgcolor: '#FF456022', color: '#FF4560', fontWeight: 600 }} />
        )}
        <Box sx={{ flex: 1 }} />
        <Typography variant="caption" color="text.secondary">
          {plotted.length} of {sites.length} sites plotted
        </Typography>
      </Stack>

      {isLoading ? (
        <Skeleton variant="rounded" height={520} />
      ) : (
        <GlassCard sx={{ p: 0, overflow: 'hidden' }}>
          <Box sx={{
            height: 520,
            // Leaflet's own popup/control chrome is light-themed by default and
            // reads as a bright rectangle on this dark UI; tint it to match.
            '& .leaflet-container': { background: '#0B0F19' },
            '& .leaflet-popup-content-wrapper, & .leaflet-popup-tip': {
              background: '#141A28', color: '#E6E9EF',
            },
            '& .leaflet-bar a': { background: '#141A28', color: '#E6E9EF', borderColor: '#2A3346' },
          }}>
            <MapContainer
              center={[1.3521, 103.8198]}
              zoom={11}
              style={{ height: '100%', width: '100%' }}
              scrollWheelZoom
            >
              <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
              <FitBounds points={points} />
              {plotted.map((s) => {
                const health = healthOf(s)
                return (
                  <CircleMarker
                    key={s.id}
                    center={[s.latitude!, s.longitude!]}
                    radius={10 + Math.min(s.cameras_total, 10)}
                    pathOptions={{
                      color: health.color,
                      fillColor: health.color,
                      fillOpacity: 0.35,
                      weight: 2,
                    }}
                  >
                    <Popup>
                      <Typography variant="subtitle2" fontWeight={700}>{s.name}</Typography>
                      {s.address && (
                        <Typography variant="caption" sx={{ display: 'block', mb: 0.5 }}>
                          {s.address}
                        </Typography>
                      )}
                      <Typography variant="caption" sx={{ color: health.color, fontWeight: 600 }}>
                        {health.label}
                      </Typography>
                      <Divider sx={{ my: 0.75 }} />
                      <Typography variant="caption" sx={{ display: 'block' }}>
                        Cameras: {s.cameras_online} online
                        {s.cameras_degraded > 0 && `, ${s.cameras_degraded} degraded`}
                        {s.cameras_offline > 0 && `, ${s.cameras_offline} down`}
                        {' '}of {s.cameras_total}
                      </Typography>
                      <Typography variant="caption" sx={{ display: 'block', mb: 1 }}>
                        Open alerts: {s.active_alerts}
                        {s.critical_alerts > 0 && ` (${s.critical_alerts} critical)`}
                      </Typography>
                      <Stack direction="row" spacing={1}>
                        <Button size="small" variant="contained"
                          onClick={() => navigate('/live')}>Live Wall</Button>
                        <Button size="small" variant="outlined"
                          onClick={() => navigate(`/alerts?site_id=${s.id}`)}>Alerts</Button>
                      </Stack>
                    </Popup>
                  </CircleMarker>
                )
              })}
            </MapContainer>
          </Box>
        </GlassCard>
      )}

      {/* Sites without coordinates are listed rather than silently omitted —
          an operator must not conclude a site is fine just because it isn't
          on the map. */}
      {unplotted.length > 0 && (
        <GlassCard sx={{ mt: 2, p: 2 }}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 1 }}>
            <WarningAmberIcon fontSize="small" color="warning" />
            <Typography variant="subtitle2">
              {unplotted.length} site{unplotted.length === 1 ? '' : 's'} not on the map
            </Typography>
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
            These have no coordinates set. Add latitude and longitude on the Sites page to plot them.
          </Typography>
          <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
            {unplotted.map((s) => {
              const health = healthOf(s)
              return (
                <Chip
                  key={s.id}
                  label={`${s.name} — ${s.cameras_online}/${s.cameras_total}`}
                  size="small"
                  variant="outlined"
                  sx={{ color: health.color, borderColor: health.color }}
                  onClick={() => navigate('/sites')}
                />
              )
            })}
          </Stack>
        </GlassCard>
      )}
    </Box>
  )
}

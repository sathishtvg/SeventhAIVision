/**
 * GIS map — every site plotted with live camera health AND live manning,
 * for the one-operator-watching-many-sites case the platform exists to serve.
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
 * WHY LABELLED MARKERS AND NOT PLAIN CIRCLES
 * This page used bare CircleMarkers: identical green dots, with the site name
 * reachable only by clicking one. On a city-wide map that is a field of
 * anonymous dots — an operator could not tell which site was which, let alone
 * which needed attention, without clicking every one. Markers now carry the
 * site name, a status word and the guard count as always-visible text, so the
 * map answers "which site needs me?" at a glance instead of on interrogation.
 *
 * DATA: /command-centre/overview already computed per-site camera health and
 * today's roster; this reuses it rather than adding a second endpoint with
 * its own subtly-different definition of "online" or "on duty".
 */
import { useEffect, useMemo } from 'react'
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import {
  Box, Typography, Chip, Button, Alert, Skeleton, Divider, IconButton, Tooltip,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import VideocamIcon from '@mui/icons-material/Videocam'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import FullscreenIcon from '@mui/icons-material/Fullscreen'
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import PhoneIcon from '@mui/icons-material/Phone'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { apiClient } from '@/api/client'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { openInNewWindow } from '@/lib/popoutWindow'
import { useKioskToggle } from '@/hooks/useKioskToggle'
import { useFocusModeStore } from '@/store/focusMode'

const TILE_URL =
  import.meta.env.VITE_MAP_TILE_URL ?? 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const TILE_ATTRIBUTION =
  import.meta.env.VITE_MAP_TILE_ATTRIBUTION ?? '&copy; OpenStreetMap contributors'

/** One entry of today's roster at a site, as returned by /command-centre/overview. */
interface RosterEntry {
  shift_id: string
  guard_name: string | null
  guard_phone: string | null
  live_status: 'checked_in' | 'on_break' | 'late' | 'not_started' | 'checked_out'
  is_overdue: boolean
  late_minutes: number | null
  scheduled_start: string | null
  scheduled_end: string | null
  actual_start: string | null
}

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
  roster?: RosterEntry[]
}

/* ── Status vocabulary ──────────────────────────────────────────────────────
 * Severity is an ordered scale, not a set of colours, because the marker has
 * to combine two independent signals (cameras and manning) into one colour.
 * Ranking them makes "show the worse of the two" a max() rather than a pile
 * of nested conditionals.                                                    */
const OK = 0, INFO = 1, WARN = 2, BAD = 3
type Severity = typeof OK | typeof INFO | typeof WARN | typeof BAD

const SEVERITY_COLOR: Record<Severity, string> = {
  [OK]:   '#00E396',
  [INFO]: '#7A8195',
  [WARN]: '#FF9800',
  [BAD]:  '#FF4560',
}

/** Per-guard presentation. Mirrors the live_status ladder the attendance
 * monitor uses so the two screens never disagree about a guard's state. */
const GUARD_STATUS: Record<string, { label: string; color: string }> = {
  checked_in:  { label: 'On duty',   color: '#00E396' },
  on_break:    { label: 'On break',  color: '#6C63FF' },
  late:        { label: 'Late',      color: '#FF9800' },
  checked_out: { label: 'Off',       color: '#7A8195' },
  not_started: { label: 'Scheduled', color: '#7A8195' },
}

function guardPresentation(g: RosterEntry): { label: string; color: string } {
  // Overdue is the one state the backend's status ladder cannot express on
  // its own: is_late is only written at check-in, so a guard who never turns
  // up stays 'not_started' forever — the absence that matters most would
  // otherwise render as the calmest label on the board.
  if (g.is_overdue && g.live_status === 'not_started') {
    return { label: 'No show', color: '#FF4560' }
  }
  if (g.live_status === 'late' && g.late_minutes) {
    return { label: `Late ${g.late_minutes}m`, color: '#FF9800' }
  }
  return GUARD_STATUS[g.live_status] ?? { label: g.live_status, color: '#7A8195' }
}

/** Camera health. Deliberately three states, not a gradient: an operator
 * scanning a wall of markers needs "fine / needs a look / down" readable at a
 * glance, not a shade they have to interpret. */
function healthOf(s: MapSite): { severity: Severity; label: string } {
  if (s.cameras_total === 0) return { severity: INFO, label: 'No cameras' }
  if (s.cameras_online === 0) return { severity: BAD, label: 'All cameras down' }
  if (s.cameras_offline > 0 || s.cameras_degraded > 0)
    return { severity: WARN, label: 'Cameras degraded' }
  return { severity: OK, label: 'Cameras online' }
}

/** Manning, derived from today's roster. */
function staffingOf(s: MapSite): { severity: Severity; label: string; present: number } {
  const roster = s.roster ?? []
  const present = roster.filter(
    (g) => g.live_status === 'checked_in' || g.live_status === 'on_break',
  ).length
  const missing = roster.filter(
    (g) => g.is_overdue || g.live_status === 'late',
  ).length

  if (roster.length === 0) return { severity: INFO, label: 'No shifts', present: 0 }
  // Nobody here and someone should be: the single most important thing this
  // map can say. Ranked above a camera fault deliberately — a dark camera at a
  // manned site is a maintenance ticket, an unmanned site is an incident.
  if (present === 0 && missing > 0) return { severity: BAD, label: 'Unmanned', present }
  if (missing > 0) return { severity: WARN, label: `${missing} not in`, present }
  if (present > 0) return { severity: OK, label: `${present} on duty`, present }
  const anyUpcoming = roster.some((g) => g.live_status === 'not_started')
  return { severity: INFO, label: anyUpcoming ? 'Shift upcoming' : 'Shift ended', present }
}

/** Site names are customer-entered and go into marker HTML, so they must be
 * escaped. Leaflet's divIcon takes a raw HTML string — interpolating an
 * unescaped name would make a site called `<img onerror=...>` executable. */
function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!
  ))
}

/** The marker itself: a coloured pin plus an always-visible label carrying
 * the site name, its worst status and who is on. Built as a divIcon rather
 * than a CircleMarker + Tooltip because a permanent Leaflet tooltip is a
 * second positioned layer that drifts from its marker while zooming. */
function buildIcon(site: MapSite, severity: Severity, statusLine: string): L.DivIcon {
  const color = SEVERITY_COLOR[severity]
  const urgent = severity === BAD
  return L.divIcon({
    className: 'site-marker',
    // Anchored bottom-centre on the pin tip so the label sits above the point
    // it describes and never covers it.
    iconSize: [0, 0],
    iconAnchor: [0, 0],
    html: `
      <div class="site-marker__wrap">
        <div class="site-marker__label" style="border-color:${color}">
          <span class="site-marker__name">${escapeHtml(site.name)}</span>
          <span class="site-marker__status" style="color:${color}">${escapeHtml(statusLine)}</span>
        </div>
        <div class="site-marker__pin${urgent ? ' site-marker__pin--urgent' : ''}"
             style="background:${color}"></div>
      </div>`,
  })
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
      map.fitBounds(points, { padding: [64, 64] })
    }
  }, [points, map])
  return null
}

/** Leaflet sizes its canvas on mount; entering focus mode changes the
 * container height afterwards, leaving grey tiles until something forces a
 * recalculation. The delay lets the height transition finish first.
 *
 * useEffect, not useMemo: this schedules a timer and must cancel it on
 * unmount. useMemo never invokes the function it returns, so a cleanup
 * written there is silently dead — the timer outlives the component and
 * fires invalidateSize() against a torn-down map, which throws on
 * `_leaflet_pos`. Observed exactly that in the console before this fix.
 */
function ResizeOnFocusChange({ focus }: { focus: boolean }) {
  const map = useMap()
  useEffect(() => {
    const t = setTimeout(() => map.invalidateSize(), 260)
    return () => clearTimeout(t)
  }, [focus, map])
  return null
}

function timeOnly(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export function MapViewPage() {
  const navigate = useNavigate()
  const { kiosk, toggleKiosk } = useKioskToggle()
  const isFocus = useFocusModeStore((s) => s.isFocusMode)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['cc-overview'],
    queryFn: () => apiClient.get('/api/v1/command-centre/overview').then((r) => r.data),
    refetchInterval: 30_000,
  })

  const sites: MapSite[] = data?.sites ?? []
  const plotted = sites.filter((s) => s.latitude != null && s.longitude != null)
  const unplotted = sites.filter((s) => s.latitude == null || s.longitude == null)
  const points = plotted.map((s) => [s.latitude!, s.longitude!] as [number, number])

  const totals = useMemo(() => {
    let onDuty = 0, missing = 0, unmanned = 0
    for (const s of sites) {
      const st = staffingOf(s)
      onDuty += st.present
      missing += (s.roster ?? []).filter((g) => g.is_overdue || g.live_status === 'late').length
      if (st.severity === BAD) unmanned += 1
    }
    return {
      online: sites.reduce((a, s) => a + s.cameras_online, 0),
      down: sites.reduce((a, s) => a + s.cameras_offline, 0),
      degraded: sites.reduce((a, s) => a + s.cameras_degraded, 0),
      onDuty, missing, unmanned,
    }
  }, [sites])

  return (
    <Box>
      {!isFocus && (
        <PageHeader pageKey="map"
        />
      )}

      {isError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Could not load site data. The map may be incomplete.
        </Alert>
      )}

      <Stack direction="row" spacing={1} sx={{ mb: 2, flexWrap: 'wrap', alignItems: 'center' }}>
        <Chip icon={<VideocamIcon />} label={`${totals.online} cameras online`}
          sx={{ bgcolor: '#00E39622', color: '#00E396', fontWeight: 600 }} />
        {totals.degraded > 0 && (
          <Chip label={`${totals.degraded} degraded`}
            sx={{ bgcolor: '#FF980022', color: '#FF9800', fontWeight: 600 }} />
        )}
        {totals.down > 0 && (
          <Chip label={`${totals.down} down`}
            sx={{ bgcolor: '#FF456022', color: '#FF4560', fontWeight: 600 }} />
        )}
        <Chip label={`${totals.onDuty} guards on duty`}
          sx={{ bgcolor: '#6C63FF22', color: '#6C63FF', fontWeight: 600 }} />
        {totals.missing > 0 && (
          <Chip label={`${totals.missing} not checked in`}
            sx={{ bgcolor: '#FF456022', color: '#FF4560', fontWeight: 600 }} />
        )}
        {totals.unmanned > 0 && (
          <Chip label={`${totals.unmanned} site${totals.unmanned === 1 ? '' : 's'} unmanned`}
            sx={{ bgcolor: '#FF4560', color: '#fff', fontWeight: 700 }} />
        )}

        <Box sx={{ flex: 1 }} />

        <Typography variant="caption" color="text.secondary">
          {plotted.length} of {sites.length} plotted
        </Typography>
        <Tooltip title="Open the map in its own window">
          <IconButton
            size="small"
            aria-label="Open map in a new window"
            onClick={() => openInNewWindow('/map', { fullscreen: true })}
          >
            <OpenInNewIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Button
          size="small"
          variant="outlined"
          startIcon={kiosk ? <FullscreenExitIcon /> : <FullscreenIcon />}
          onClick={toggleKiosk}
        >
          {kiosk ? 'Exit Full Screen' : 'Full Screen'}
        </Button>
      </Stack>

      {isLoading ? (
        <Skeleton variant="rounded" height={560} />
      ) : (
        <GlassCard sx={{ p: 0, overflow: 'hidden' }}>
          <Box sx={{
            // In focus mode the map is the whole point of the screen, so it
            // takes the viewport rather than a fixed slab with dead space
            // under it — the same top-down sizing the Live Wall needed.
            height: isFocus ? 'calc(100vh - 96px)' : 560,
            transition: 'height 0.25s ease',
            '& .leaflet-container': { background: '#0B0F19' },
            '& .leaflet-popup-content-wrapper, & .leaflet-popup-tip': {
              background: '#141A28', color: '#E6E9EF',
            },
            '& .leaflet-popup-content': { margin: '10px 12px', minWidth: 230 },
            '& .leaflet-bar a': { background: '#141A28', color: '#E6E9EF', borderColor: '#2A3346' },

            // ── Marker styling ───────────────────────────────────────────
            // divIcon content is unstyled by default; these rules are what
            // turn it into a readable pin + label.
            '& .site-marker__wrap': {
              position: 'relative',
              transform: 'translate(-50%, -100%)',
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              pointerEvents: 'none',
            },
            '& .site-marker__label': {
              pointerEvents: 'auto',
              background: 'rgba(11,15,25,0.92)',
              border: '1.5px solid',
              borderRadius: '8px',
              padding: '3px 8px',
              marginBottom: '3px',
              whiteSpace: 'nowrap',
              boxShadow: '0 3px 10px rgba(0,0,0,0.55)',
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              lineHeight: 1.25,
            },
            '& .site-marker__name': {
              color: '#F2F4F8', fontSize: '0.76rem', fontWeight: 700,
            },
            '& .site-marker__status': { fontSize: '0.64rem', fontWeight: 600 },
            '& .site-marker__pin': {
              pointerEvents: 'auto',
              width: 13, height: 13, borderRadius: '50%',
              border: '2.5px solid rgba(11,15,25,0.9)',
              boxShadow: '0 0 0 2px currentColor',
            },
            // Unmanned / all-cameras-down sites pulse so they are noticed
            // without the operator hunting for them. Motion is suppressed
            // for users who ask for reduced motion.
            '& .site-marker__pin--urgent': { animation: 'siteMarkerPulse 1.6s ease-in-out infinite' },
            '@keyframes siteMarkerPulse': {
              '0%, 100%': { transform: 'scale(1)',    opacity: 1 },
              '50%':      { transform: 'scale(1.45)', opacity: 0.65 },
            },
            '@media (prefers-reduced-motion: reduce)': {
              '& .site-marker__pin--urgent': { animation: 'none' },
            },
          }}>
            <MapContainer
              center={[1.3521, 103.8198]}
              zoom={11}
              style={{ height: '100%', width: '100%' }}
              scrollWheelZoom
            >
              <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
              <FitBounds points={points} />
              <ResizeOnFocusChange focus={isFocus} />

              {plotted.map((s) => {
                const health = healthOf(s)
                const staffing = staffingOf(s)
                const severity = Math.max(health.severity, staffing.severity) as Severity
                // The label shows whichever signal is driving the colour, so
                // the marker never says "3 on duty" while glowing red for a
                // camera fault.
                const statusLine = severity === health.severity && severity !== staffing.severity
                  ? health.label
                  : staffing.label
                const roster = s.roster ?? []

                return (
                  <Marker
                    key={s.id}
                    position={[s.latitude!, s.longitude!]}
                    icon={buildIcon(s, severity, statusLine)}
                  >
                    <Popup>
                      <Typography variant="subtitle2" fontWeight={700}>{s.name}</Typography>
                      {s.address && (
                        <Typography variant="caption" sx={{ display: 'block', mb: 0.5 }}>
                          {s.address}
                        </Typography>
                      )}

                      <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', mb: 0.5 }}>
                        <Chip size="small" label={health.label}
                          sx={{ height: 18, fontSize: '0.62rem',
                                color: SEVERITY_COLOR[health.severity],
                                border: `1px solid ${SEVERITY_COLOR[health.severity]}` }} />
                        <Chip size="small" label={staffing.label}
                          sx={{ height: 18, fontSize: '0.62rem',
                                color: SEVERITY_COLOR[staffing.severity],
                                border: `1px solid ${SEVERITY_COLOR[staffing.severity]}` }} />
                      </Stack>

                      <Typography variant="caption" sx={{ display: 'block' }}>
                        Cameras: {s.cameras_online} online
                        {s.cameras_degraded > 0 && `, ${s.cameras_degraded} degraded`}
                        {s.cameras_offline > 0 && `, ${s.cameras_offline} down`}
                        {' '}of {s.cameras_total}
                      </Typography>
                      <Typography variant="caption" sx={{ display: 'block' }}>
                        Open alerts: {s.active_alerts}
                        {s.critical_alerts > 0 && ` (${s.critical_alerts} critical)`}
                      </Typography>

                      <Divider sx={{ my: 0.75 }} />

                      <Typography variant="caption" sx={{ fontWeight: 700, display: 'block', mb: 0.25 }}>
                        Guards today ({roster.length})
                      </Typography>
                      {roster.length === 0 ? (
                        <Typography variant="caption" sx={{ color: 'text.disabled' }}>
                          No shifts scheduled
                        </Typography>
                      ) : (
                        <Box sx={{ maxHeight: 132, overflowY: 'auto', mb: 1 }}>
                          {roster.map((g) => {
                            const p = guardPresentation(g)
                            return (
                              <Stack key={g.shift_id} direction="row" spacing={0.75}
                                sx={{ alignItems: 'center', py: 0.25 }}>
                                <Box sx={{ width: 7, height: 7, borderRadius: '50%',
                                           background: p.color, flexShrink: 0 }} />
                                <Typography variant="caption" sx={{ flex: 1, minWidth: 0 }} noWrap>
                                  {g.guard_name ?? 'Unassigned'}
                                </Typography>
                                {/* The scheduled window is shown for every
                                    guard so a check-in that is days stale is
                                    visible as such instead of being trusted. */}
                                <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.6rem' }}>
                                  {timeOnly(g.scheduled_start)}–{timeOnly(g.scheduled_end)}
                                </Typography>
                                <Typography variant="caption"
                                  sx={{ color: p.color, fontWeight: 700, fontSize: '0.62rem' }}>
                                  {p.label}
                                </Typography>
                                {g.guard_phone && (
                                  <IconButton size="small" href={`tel:${g.guard_phone}`}
                                    aria-label={`Call ${g.guard_name ?? 'guard'}`}
                                    sx={{ p: 0.25 }}>
                                    <PhoneIcon sx={{ fontSize: 13 }} />
                                  </IconButton>
                                )}
                              </Stack>
                            )
                          })}
                        </Box>
                      )}

                      <Stack direction="row" spacing={1}>
                        <Button size="small" variant="contained"
                          onClick={() => navigate('/live')}>Live Wall</Button>
                        <Button size="small" variant="outlined"
                          onClick={() => navigate(`/alerts?site_id=${s.id}`)}>Alerts</Button>
                      </Stack>
                    </Popup>
                  </Marker>
                )
              })}
            </MapContainer>
          </Box>
        </GlassCard>
      )}

      {/* Sites without coordinates are listed rather than silently omitted —
          an operator must not conclude a site is fine just because it isn't
          on the map. */}
      {!isFocus && unplotted.length > 0 && (
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
              const sev = Math.max(healthOf(s).severity, staffingOf(s).severity) as Severity
              return (
                <Chip
                  key={s.id}
                  label={`${s.name} — ${s.cameras_online}/${s.cameras_total} cams, ${staffingOf(s).label}`}
                  size="small"
                  variant="outlined"
                  sx={{ color: SEVERITY_COLOR[sev], borderColor: SEVERITY_COLOR[sev] }}
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

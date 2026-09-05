/**
 * Activity Heatmap — where is activity concentrated, and what needs attention.
 *
 * This used to plot cameras onto an SVG by latitude/longitude. That could not
 * work: coordinates are recorded per SITE, not per camera, so every camera at
 * a site resolves to the identical point. Marina Bay Tower's three cameras all
 * sit on 1.282, 103.8549 — the projection stacked their labels into an
 * unreadable smear, and the only way to read a value was to hover one blob at
 * a time while the numbers rendered off the bottom of the page.
 *
 * Real geography already has a home in the GIS Map page (/map), which is built
 * for it. So this page groups by site instead of projecting. Grouping cannot
 * collide by construction, every number is on screen without hovering, and the
 * ranking answers the question an operator actually opens this page with:
 * which cameras are producing the alerts, and which ones have stopped
 * reporting.
 */
import { useMemo, useState } from 'react'
import {
  Box,
  Typography,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Chip,
  Button,
  LinearProgress,
  CircularProgress,
  Alert,
  Tooltip,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import {
  Error as CriticalIcon,
  VideocamOff as OfflineIcon,
} from '@mui/icons-material'
import { useQuery } from '@tanstack/react-query'
import { getHeatmapData } from '@/api/analytics'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { fadeUpSx, useCountUp } from '@/lib/motion'
import type { HeatmapCamera } from '@/types/api'

const TIME_OPTIONS = [
  { label: 'Last 1h', value: 1 },
  { label: 'Last 6h', value: 6 },
  { label: 'Last 24h', value: 24 },
  { label: 'Last 7d', value: 168 },
  { label: 'Last 30d', value: 720 },
]

const MODULE_OPTIONS = [
  'All Modules', 'lpr', 'face', 'intrusion', 'ppe', 'crowd', 'fire_smoke', 'weapon', 'behavior',
  'tampering', 'abandoned', 'fall',
]

/** Colour by the most severe thing happening, so scanning a column of these
 *  ranks urgency without reading a legend. */
function severityColour(cam: HeatmapCamera): string {
  if (cam.critical_alerts > 0) return '#FF4560'
  if (cam.high_alerts > 0) return '#FF9800'
  if (cam.open_alerts > 0) return '#FFC107'
  if (cam.total_detections > 0) return '#00D9C0'
  return '#6C63FF'
}

function statusColour(status: string): string {
  if (status === 'online') return '#00E396'
  if (status === 'degraded') return '#FF9800'
  return '#FF4560'
}

function KpiCard({ label, value, colour, sub }: {
  label: string; value: number; colour: string; sub?: string
}) {
  const animated = useCountUp(value)
  return (
    <GlassCard sx={{ p: 2.5, flex: 1, minWidth: 150 }}>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 1 }}>
        {label}
      </Typography>
      <Typography variant="h4" sx={{ fontWeight: 700, color: colour, mt: 0.5 }}>{animated}</Typography>
      {sub && <Typography variant="caption" color="text.secondary">{sub}</Typography>}
    </GlassCard>
  )
}

/** One camera, with every figure on screen. No hover required — the previous
 *  version hid all of this behind a mouseover on a 12px circle. */
function CameraRow({ cam, max }: { cam: HeatmapCamera; max: number }) {
  const colour = severityColour(cam)
  const pct = max > 0 ? (cam.total_detections / max) * 100 : 0
  return (
    <Box sx={{ py: 0.75, borderTop: '1px solid rgba(255,255,255,0.05)' }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 0.4 }}>
        <Tooltip title={`Stream ${cam.stream_status}`}>
          <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: statusColour(cam.stream_status), flexShrink: 0 }} />
        </Tooltip>
        <Typography variant="body2" sx={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {cam.camera_name}
        </Typography>
        {cam.critical_alerts > 0 && (
          <Chip size="small" color="error" label={`${cam.critical_alerts} critical`} sx={{ height: 18, fontSize: '0.62rem' }} />
        )}
        {cam.open_alerts > 0 && cam.critical_alerts === 0 && (
          <Chip size="small" color="warning" variant="outlined" label={`${cam.open_alerts} open`} sx={{ height: 18, fontSize: '0.62rem' }} />
        )}
        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 120, textAlign: 'right' }}>
          {cam.total_detections.toLocaleString()} det · {cam.total_alerts} alerts
        </Typography>
      </Stack>
      <LinearProgress
        variant="determinate"
        value={Math.min(pct, 100)}
        sx={{
          height: 4, borderRadius: 2, bgcolor: 'rgba(255,255,255,0.06)',
          '& .MuiLinearProgress-bar': { bgcolor: colour, borderRadius: 2 },
        }}
      />
    </Box>
  )
}

export default function Heatmap() {
  const [hours, setHours] = useState(24)
  const [moduleType, setModuleType] = useState('All Modules')
  const [siteId, setSiteId] = useState('')

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const { data: cameras = [], isLoading, isError } = useQuery({
    queryKey: ['heatmap', hours, moduleType, siteId],
    queryFn: () => getHeatmapData(hours, moduleType === 'All Modules' ? undefined : moduleType, siteId || undefined),
  })

  const totalDetections = cameras.reduce((s, c) => s + c.total_detections, 0)
  const totalAlerts = cameras.reduce((s, c) => s + c.total_alerts, 0)
  const criticalCount = cameras.reduce((s, c) => s + c.critical_alerts, 0)
  const offline = cameras.filter((c) => c.stream_status !== 'online')
  const maxDetections = Math.max(...cameras.map((c) => c.total_detections), 1)

  // Group by site. This is what removes the label collisions: co-located
  // cameras become sibling rows instead of overlapping points.
  const bySite = useMemo(() => {
    const map = new Map<string, HeatmapCamera[]>()
    for (const cam of cameras) {
      const key = cam.site_name ?? 'Unassigned'
      const list = map.get(key)
      if (list) list.push(cam)
      else map.set(key, [cam])
    }
    return [...map.entries()]
      .map(([site, cams]) => ({
        site,
        cams: [...cams].sort((a, b) => b.total_detections - a.total_detections),
        detections: cams.reduce((s, c) => s + c.total_detections, 0),
        alerts: cams.reduce((s, c) => s + c.total_alerts, 0),
        critical: cams.reduce((s, c) => s + c.critical_alerts, 0),
        offline: cams.filter((c) => c.stream_status !== 'online').length,
      }))
      .sort((a, b) => b.alerts - a.alerts || b.detections - a.detections)
  }, [cameras])

  const hotspots = useMemo(
    () => [...cameras].filter((c) => c.total_alerts > 0).sort((a, b) => b.total_alerts - a.total_alerts).slice(0, 8),
    [cameras],
  )
  const maxHotspot = hotspots.length ? hotspots[0].total_alerts : 1
  const windowLabel = TIME_OPTIONS.find((o) => o.value === hours)?.label ?? `${hours}h`

  return (
    <Box>
      <PageHeader pageKey="heatmap" />

      <Stack direction="row" spacing={2} sx={{ mb: 3, alignItems: 'center', flexWrap: 'wrap' }}>
        <FormControl size="small" sx={{ minWidth: 150 }}>
          <InputLabel>Time Period</InputLabel>
          <Select value={hours} label="Time Period" onChange={(e) => setHours(Number(e.target.value))}>
            {TIME_OPTIONS.map((o) => <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 170 }}>
          <InputLabel>Module</InputLabel>
          <Select value={moduleType} label="Module" onChange={(e) => setModuleType(e.target.value)}>
            {MODULE_OPTIONS.map((m) => <MenuItem key={m} value={m}>{m === 'All Modules' ? m : m.toUpperCase()}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 170 }}>
          <InputLabel>Site</InputLabel>
          <Select value={siteId} label="Site" onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">All Sites</MenuItem>
            {sites.map((s: any) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>

      {isLoading && <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}><CircularProgress /></Box>}
      {isError && <Alert severity="error">Failed to load heatmap data.</Alert>}

      {!isLoading && !isError && (
        <>
          <Stack direction="row" spacing={2} sx={{ mb: 3, flexWrap: 'wrap' }}>
            <Box sx={{ ...fadeUpSx(0), display: 'flex', flex: 1, minWidth: 150 }}>
              <KpiCard label="Detections" value={totalDetections} colour="#6C63FF" sub={windowLabel} />
            </Box>
            <Box sx={{ ...fadeUpSx(1), display: 'flex', flex: 1, minWidth: 150 }}>
              <KpiCard label="Alerts" value={totalAlerts} colour="#FF9800" sub={windowLabel} />
            </Box>
            <Box sx={{ ...fadeUpSx(2), display: 'flex', flex: 1, minWidth: 150 }}>
              <KpiCard label="Critical" value={criticalCount} colour="#FF4560" sub="needs response" />
            </Box>
            <Box sx={{ ...fadeUpSx(3), display: 'flex', flex: 1, minWidth: 150 }}>
              <KpiCard label="Cameras Down" value={offline.length} colour={offline.length ? '#FF4560' : '#00E396'}
                       sub={`of ${cameras.length} active`} />
            </Box>
          </Stack>

          {/* A window with nothing in it is the most common thing an operator
              sees here, and "0" alone reads as broken. Say which window is
              empty and offer the one that is not, rather than leaving them to
              guess the filter is at fault. */}
          {cameras.length > 0 && totalDetections === 0 && totalAlerts === 0 && (
            <Alert
              severity="info"
              sx={{ mb: 3 }}
              action={hours < 720 && (
                <Button color="inherit" size="small" onClick={() => setHours(720)}>Try last 30 days</Button>
              )}
            >
              No detections or alerts in the {windowLabel.toLowerCase()} for these filters. The cameras below are
              configured and their stream status is current — this window is simply empty.
            </Alert>
          )}

          {offline.length > 0 && (
            <Alert severity="warning" icon={<OfflineIcon />} sx={{ mb: 3 }}>
              <strong>{offline.length} camera{offline.length === 1 ? '' : 's'} not reporting</strong>
              {' — '}
              {offline.map((c) => `${c.camera_name}${c.site_name ? ` (${c.site_name})` : ''}`).join(', ')}.
              A camera that is down produces no detections, so any quiet figure above may be under-counting.
            </Alert>
          )}

          <Stack direction={{ xs: 'column', lg: 'row' }} spacing={3} sx={{ alignItems: 'flex-start' }}>
            {/* Sites, ranked by alert load */}
            <Box sx={{ flex: 2, width: '100%' }}>
              {bySite.length === 0 ? (
                <GlassCard sx={{ p: 4 }}>
                  <Typography color="text.secondary" align="center">No cameras match the selected filters.</Typography>
                </GlassCard>
              ) : (
                <Stack spacing={2}>
                  {bySite.map((group, i) => (
                    <GlassCard key={group.site} sx={{ p: 2.5, ...fadeUpSx(i) }}>
                      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 1 }}>
                        <Typography variant="subtitle1" sx={{ fontWeight: 700, flex: 1 }}>{group.site}</Typography>
                        {group.critical > 0 && (
                          <Chip size="small" color="error" icon={<CriticalIcon sx={{ fontSize: 14 }} />}
                                label={`${group.critical} critical`} sx={{ height: 20 }} />
                        )}
                        {group.offline > 0 && (
                          <Chip size="small" color="error" variant="outlined"
                                label={`${group.offline} down`} sx={{ height: 20 }} />
                        )}
                        <Typography variant="caption" color="text.secondary">
                          {group.cams.length} camera{group.cams.length === 1 ? '' : 's'} ·{' '}
                          {group.detections.toLocaleString()} detections · {group.alerts} alerts
                        </Typography>
                      </Stack>
                      {group.cams.map((cam) => (
                        <CameraRow key={cam.camera_id} cam={cam} max={maxDetections} />
                      ))}
                    </GlassCard>
                  ))}
                </Stack>
              )}
            </Box>

            {/* Hotspots — the ranking that used to sit below the fold */}
            <Box sx={{ flex: 1, width: '100%', position: { lg: 'sticky' }, top: { lg: 16 } }}>
              <GlassCard sx={{ p: 2.5 }}>
                <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1.5 }}>
                  Alert Hotspots · {windowLabel}
                </Typography>
                {hotspots.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No camera raised an alert in this window.
                  </Typography>
                ) : (
                  <Stack spacing={1.5}>
                    {hotspots.map((cam, idx) => (
                      <Box key={cam.camera_id}>
                        <Stack direction="row" spacing={1} sx={{ alignItems: 'baseline' }}>
                          <Typography variant="caption" color="text.disabled" sx={{ width: 14 }}>{idx + 1}</Typography>
                          <Typography variant="body2" sx={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {cam.camera_name}
                          </Typography>
                          <Typography variant="body2" sx={{ fontWeight: 700, color: severityColour(cam) }}>
                            {cam.total_alerts}
                          </Typography>
                        </Stack>
                        <Typography variant="caption" color="text.secondary" sx={{ ml: 2.5 }}>
                          {cam.site_name ?? 'Unassigned'}
                          {cam.open_alerts > 0 ? ` · ${cam.open_alerts} still open` : ' · all handled'}
                        </Typography>
                        <LinearProgress
                          variant="determinate"
                          value={(cam.total_alerts / maxHotspot) * 100}
                          sx={{
                            mt: 0.5, height: 4, borderRadius: 2, bgcolor: 'rgba(255,255,255,0.06)',
                            '& .MuiLinearProgress-bar': { bgcolor: severityColour(cam), borderRadius: 2 },
                          }}
                        />
                      </Box>
                    ))}
                  </Stack>
                )}
              </GlassCard>
            </Box>
          </Stack>
        </>
      )}
    </Box>
  )
}

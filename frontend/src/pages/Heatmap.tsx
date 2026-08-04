import React, { useMemo, useState, useRef } from 'react'
import {
  Box,
  Typography,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Chip,
  Paper,
  Tooltip,
  Divider,
  List,
  ListItem,
  ListItemText,
  ListItemSecondaryAction,
  CircularProgress,
  Alert,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import {
  ThermostatAuto as HeatmapIcon,
  FiberManualRecord as DotIcon,
  Warning as WarningIcon,
  Error as CriticalIcon,
} from '@mui/icons-material'
import { useQuery } from '@tanstack/react-query'
import { getHeatmapData } from '@/api/analytics'
import { getSites } from '@/api/sites'
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

// ── colour helpers ──────────────────────────────────────────────────────────

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

// Normalise lat/lng coords to SVG canvas [0,1] space.
function normaliseCoords(cameras: HeatmapCamera[], w: number, h: number) {
  const withGeo = cameras.filter((c) => c.latitude != null && c.longitude != null)
  if (withGeo.length < 2) return null

  const lats = withGeo.map((c) => c.latitude!)
  const lngs = withGeo.map((c) => c.longitude!)
  const minLat = Math.min(...lats), maxLat = Math.max(...lats)
  const minLng = Math.min(...lngs), maxLng = Math.max(...lngs)
  const pad = 60

  return (cam: HeatmapCamera) => {
    if (cam.latitude == null || cam.longitude == null) return null
    const x = pad + ((cam.longitude - minLng) / Math.max(maxLng - minLng, 0.001)) * (w - pad * 2)
    const y = h - pad - ((cam.latitude - minLat) / Math.max(maxLat - minLat, 0.001)) * (h - pad * 2)
    return { x, y }
  }
}

// Grid layout fallback for cameras without geo coordinates.
function gridLayout(cameras: HeatmapCamera[], w: number, h: number) {
  const cols = Math.ceil(Math.sqrt(cameras.length))
  const cellW = (w - 80) / Math.max(cols, 1)
  const cellH = (h - 80) / Math.max(Math.ceil(cameras.length / cols), 1)
  return (idx: number) => ({
    x: 40 + (idx % cols) * cellW + cellW / 2,
    y: 40 + Math.floor(idx / cols) * cellH + cellH / 2,
  })
}

// ── SVG canvas ──────────────────────────────────────────────────────────────

interface CanvasProps {
  cameras: HeatmapCamera[]
  maxDetections: number
  onHover: (cam: HeatmapCamera | null) => void
}

const HeatmapCanvas: React.FC<CanvasProps> = ({ cameras, maxDetections, onHover }) => {
  const W = 760
  const H = 440

  const toPos = useMemo(() => {
    const geoFn = normaliseCoords(cameras, W, H)
    if (geoFn) {
      const fallback = gridLayout(cameras, W, H)
      return (cam: HeatmapCamera, idx: number) => geoFn(cam) ?? fallback(idx)
    }
    const fallback = gridLayout(cameras, W, H)
    return (_cam: HeatmapCamera, idx: number) => fallback(idx)
  }, [cameras])

  const minR = 12, maxR = 36

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      style={{ width: '100%', height: '100%', maxHeight: 440 }}
    >
      <defs>
        <filter id="blur-heat">
          <feGaussianBlur stdDeviation="14" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
        <filter id="glow">
          <feGaussianBlur stdDeviation="4" result="glow" />
          <feMerge><feMergeNode in="glow" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>

      {/* heat blobs underneath */}
      {cameras.map((cam, idx) => {
        const pos = toPos(cam, idx)
        const r = minR + ((cam.total_detections / Math.max(maxDetections, 1)) * (maxR - minR))
        const col = severityColour(cam)
        return (
          <circle
            key={`heat-${cam.camera_id}`}
            cx={pos.x} cy={pos.y} r={r * 2.2}
            fill={col} opacity={0.12}
            filter="url(#blur-heat)"
          />
        )
      })}

      {/* camera nodes */}
      {cameras.map((cam, idx) => {
        const pos = toPos(cam, idx)
        const r = minR + ((cam.total_detections / Math.max(maxDetections, 1)) * (maxR - minR))
        const col = severityColour(cam)
        const sCol = statusColour(cam.stream_status)

        return (
          <g key={cam.camera_id}>
            {cam.critical_alerts > 0 && (
              <circle cx={pos.x} cy={pos.y} r={r + 8} fill="none" stroke="#FF4560" strokeWidth={2} opacity={0.6}>
                <animate attributeName="r" values={`${r + 6};${r + 14};${r + 6}`} dur="1.5s" repeatCount="indefinite" />
                <animate attributeName="opacity" values="0.6;0.1;0.6" dur="1.5s" repeatCount="indefinite" />
              </circle>
            )}
            <circle
              cx={pos.x} cy={pos.y} r={r}
              fill={col} opacity={0.85}
              filter="url(#glow)"
              style={{ cursor: 'pointer' }}
              onMouseEnter={() => onHover(cam)}
              onMouseLeave={() => onHover(null)}
            />
            {/* status dot */}
            <circle cx={pos.x + r * 0.6} cy={pos.y - r * 0.6} r={5} fill={sCol} />
            {/* label */}
            <text
              x={pos.x} y={pos.y + r + 14}
              textAnchor="middle"
              fill="rgba(255,255,255,0.7)"
              fontSize={10}
              style={{ pointerEvents: 'none', userSelect: 'none' }}
            >
              {cam.camera_name.length > 16 ? cam.camera_name.slice(0, 14) + '…' : cam.camera_name}
            </text>
            {cam.total_detections > 0 && (
              <text
                x={pos.x} y={pos.y + 4}
                textAnchor="middle"
                fill="white"
                fontSize={Math.max(9, r * 0.55)}
                fontWeight="bold"
                style={{ pointerEvents: 'none', userSelect: 'none' }}
              >
                {cam.total_detections > 999 ? '999+' : cam.total_detections}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function Heatmap() {
  const [hours, setHours] = useState(24)
  const [moduleType, setModuleType] = useState('All Modules')
  const [siteId, setSiteId] = useState('')
  const [hoveredCam, setHoveredCam] = useState<HeatmapCamera | null>(null)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const { data: cameras = [], isLoading, isError } = useQuery({
    queryKey: ['heatmap', hours, moduleType, siteId],
    queryFn: () => getHeatmapData(
      hours,
      moduleType === 'All Modules' ? undefined : moduleType,
      siteId || undefined,
    ),
    refetchInterval: 30_000,
  })

  const maxDetections = useMemo(() => Math.max(...cameras.map((c) => c.total_detections), 1), [cameras])
  const topCameras = useMemo(
    () => [...cameras].sort((a, b) => b.total_alerts - a.total_alerts).slice(0, 8),
    [cameras],
  )

  const totalDetections = cameras.reduce((s, c) => s + c.total_detections, 0)
  const totalAlerts = cameras.reduce((s, c) => s + c.total_alerts, 0)
  const criticalCount = cameras.reduce((s, c) => s + c.critical_alerts, 0)

  return (
    <Box sx={{ p: 3 }}>
      {/* header */}
      <Stack direction="row" alignItems="center" spacing={1.5} mb={3}>
        <HeatmapIcon sx={{ color: 'primary.main', fontSize: 28 }} />
        <Typography variant="h5" fontWeight={700}>
          Activity Heatmap
        </Typography>
      </Stack>

      {/* filters */}
      <Stack direction="row" spacing={2} mb={3} flexWrap="wrap">
        <FormControl size="small" sx={{ minWidth: 130 }}>
          <InputLabel>Time Period</InputLabel>
          <Select value={hours} label="Time Period" onChange={(e) => setHours(Number(e.target.value))}>
            {TIME_OPTIONS.map((o) => (
              <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" sx={{ minWidth: 150 }}>
          <InputLabel>Module</InputLabel>
          <Select value={moduleType} label="Module" onChange={(e) => setModuleType(e.target.value)}>
            {MODULE_OPTIONS.map((m) => (
              <MenuItem key={m} value={m}>{m === 'All Modules' ? m : m.replace('_', ' ')}</MenuItem>
            ))}
          </Select>
        </FormControl>

        {sites.length > 0 && (
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <InputLabel>Site</InputLabel>
            <Select value={siteId} label="Site" onChange={(e) => setSiteId(e.target.value)}>
              <MenuItem value="">All Sites</MenuItem>
              {sites.map((s: { id: string; name: string }) => (
                <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
              ))}
            </Select>
          </FormControl>
        )}

        <Stack direction="row" spacing={1} alignItems="center" sx={{ ml: 'auto' }}>
          <Chip label={`${totalDetections.toLocaleString()} detections`} size="small" color="primary" variant="outlined" />
          <Chip label={`${totalAlerts} alerts`} size="small" color="warning" variant="outlined" />
          {criticalCount > 0 && (
            <Chip label={`${criticalCount} critical`} size="small" color="error" icon={<CriticalIcon />} />
          )}
        </Stack>
      </Stack>

      {isLoading && <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}><CircularProgress /></Box>}
      {isError && <Alert severity="error">Failed to load heatmap data.</Alert>}

      {!isLoading && !isError && (
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={3}>
          {/* canvas area */}
          <Paper
            sx={{
              flex: 1,
              minHeight: 460,
              p: 2,
              background: 'rgba(8,8,24,0.6)',
              backdropFilter: 'blur(16px)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 2,
              display: 'flex',
              flexDirection: 'column',
              gap: 1,
            }}
          >
            {cameras.length === 0 ? (
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1 }}>
                <Typography color="text.secondary">No camera data for the selected filters</Typography>
              </Box>
            ) : (
              <>
                <HeatmapCanvas cameras={cameras} maxDetections={maxDetections} onHover={setHoveredCam} />

                {/* tooltip card */}
                {hoveredCam && (
                  <Paper
                    sx={{
                      p: 1.5,
                      background: 'rgba(20,20,40,0.95)',
                      border: '1px solid rgba(108,99,255,0.4)',
                      borderRadius: 1,
                      minWidth: 200,
                    }}
                  >
                    <Typography fontWeight={700} fontSize={13}>{hoveredCam.camera_name}</Typography>
                    {hoveredCam.site_name && (
                      <Typography fontSize={11} color="text.secondary">{hoveredCam.site_name}</Typography>
                    )}
                    {hoveredCam.location && (
                      <Typography fontSize={11} color="text.secondary">{hoveredCam.location}</Typography>
                    )}
                    <Divider sx={{ my: 0.75 }} />
                    <Stack direction="row" spacing={2}>
                      <Box>
                        <Typography fontSize={11} color="text.secondary">Detections</Typography>
                        <Typography fontSize={14} fontWeight={700} color="primary.main">{hoveredCam.total_detections}</Typography>
                      </Box>
                      <Box>
                        <Typography fontSize={11} color="text.secondary">Alerts</Typography>
                        <Typography fontSize={14} fontWeight={700} color="warning.main">{hoveredCam.total_alerts}</Typography>
                      </Box>
                      <Box>
                        <Typography fontSize={11} color="text.secondary">Open</Typography>
                        <Typography fontSize={14} fontWeight={700} color="error.main">{hoveredCam.open_alerts}</Typography>
                      </Box>
                    </Stack>
                    <Box mt={0.5}>
                      <DotIcon sx={{ fontSize: 10, color: statusColour(hoveredCam.stream_status), mr: 0.5 }} />
                      <Typography component="span" fontSize={11} color="text.secondary">
                        {hoveredCam.stream_status}
                      </Typography>
                    </Box>
                  </Paper>
                )}
              </>
            )}
          </Paper>

          {/* sidebar */}
          <Paper
            sx={{
              width: { xs: '100%', md: 260 },
              p: 2,
              background: 'rgba(8,8,24,0.6)',
              backdropFilter: 'blur(16px)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 2,
              display: 'flex',
              flexDirection: 'column',
              gap: 2,
            }}
          >
            {/* legend */}
            <Box>
              <Typography variant="subtitle2" fontWeight={700} gutterBottom>Legend</Typography>
              <Stack spacing={0.75}>
                {[
                  { col: '#FF4560', label: 'Critical alerts active' },
                  { col: '#FF9800', label: 'High alerts active' },
                  { col: '#FFC107', label: 'Open alerts' },
                  { col: '#00D9C0', label: 'Activity — no open alerts' },
                  { col: '#6C63FF', label: 'Online — no detections' },
                ].map(({ col, label }) => (
                  <Stack key={label} direction="row" spacing={1} alignItems="center">
                    <Box sx={{ width: 12, height: 12, borderRadius: '50%', bgcolor: col, flexShrink: 0 }} />
                    <Typography fontSize={11} color="text.secondary">{label}</Typography>
                  </Stack>
                ))}
              </Stack>
              <Divider sx={{ my: 1.5 }} />
              <Typography fontSize={11} color="text.secondary" mb={0.5}>Circle size = detection volume</Typography>
              <Typography fontSize={11} color="text.secondary">Pulsing ring = critical alert</Typography>
            </Box>

            <Divider />

            {/* top cameras */}
            <Box>
              <Typography variant="subtitle2" fontWeight={700} gutterBottom>
                Top Cameras by Alerts
              </Typography>
              <List dense disablePadding>
                {topCameras.map((cam, idx) => (
                  <ListItem key={cam.camera_id} disableGutters sx={{ py: 0.3 }}>
                    <ListItemText
                      primary={
                        <Typography fontSize={12} noWrap>
                          <Typography component="span" fontSize={11} color="text.secondary" mr={0.5}>
                            {idx + 1}.
                          </Typography>
                          {cam.camera_name}
                        </Typography>
                      }
                      secondary={cam.site_name ?? cam.location ?? '—'}
                      slotProps={{ secondary: { fontSize: 10, color: 'text.secondary' } }}
                    />
                    <ListItemSecondaryAction>
                      <Stack direction="row" spacing={0.5} alignItems="center">
                        {cam.critical_alerts > 0 && (
                          <Chip
                            label={cam.critical_alerts}
                            size="small"
                            color="error"
                            sx={{ height: 16, fontSize: 9, px: 0 }}
                          />
                        )}
                        <Chip
                          label={cam.total_alerts}
                          size="small"
                          color={cam.open_alerts > 0 ? 'warning' : 'default'}
                          variant="outlined"
                          sx={{ height: 16, fontSize: 9 }}
                        />
                      </Stack>
                    </ListItemSecondaryAction>
                  </ListItem>
                ))}
                {topCameras.length === 0 && (
                  <Typography fontSize={12} color="text.secondary" mt={1}>
                    No alert activity in this period.
                  </Typography>
                )}
              </List>
            </Box>
          </Paper>
        </Stack>
      )}
    </Box>
  )
}

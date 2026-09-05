import React, { useState, useEffect, useRef } from 'react'
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Grid,
  IconButton,
  LinearProgress,
  MenuItem,
  Select,
  Skeleton,
  Tooltip,
  Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import { useAuthStore } from '@/store/auth'
import { fadeUpSx, useCountUp } from '@/lib/motion'
import { openLiveWallWindow } from '@/lib/liveWallWindow'
import { openInNewWindow } from '@/lib/popoutWindow'
import { useKioskToggle } from '@/hooks/useKioskToggle'
import FullscreenIcon from '@mui/icons-material/Fullscreen'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import LiveTvIcon from '@mui/icons-material/LiveTv'
import NotificationsIcon from '@mui/icons-material/Notifications'
import ReportProblemIcon from '@mui/icons-material/ReportProblem'
import VideocamIcon from '@mui/icons-material/Videocam'
import SearchIcon from '@mui/icons-material/Search'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import CircleIcon from '@mui/icons-material/Circle'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import HelpOutlineIcon from '@mui/icons-material/Help'
import EmailIcon from '@mui/icons-material/Email'
import SmsIcon from '@mui/icons-material/Sms'
import WebhookIcon from '@mui/icons-material/Link'
import TimerIcon from '@mui/icons-material/Timer'
import { useQuery } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { SeverityChip } from '@/components/common/SeverityChip'
import { getSites } from '@/api/sites'
import { getAlerts } from '@/api/alerts'
import {
  getSummary, getAlertsBySeverity, getAlertsByModule,
  getDetectionsTrend, getTopCameras, getIncidentResolutionTime,
} from '@/api/analytics'
import { getChannels } from '@/api/notifications'
import { listAllRecordings, listAllStreams } from '@/api/recordings'
import { apiClient } from '@/api/client'
import { useWebSocket } from '@/hooks/useWebSocket'
import type { AlertSeverity } from '@/types/api'
import { getIoTDashboard } from '@/api/iot'
import { getParkingDashboard } from '@/api/parking'
import { getBWCDashboard } from '@/api/bwc'
import { getGPSDashboard } from '@/api/gps'
import { getAlarmDashboard } from '@/api/alarms'

// ── KPI Card ────────────────────────────────────────────────
function KpiCard({ label, value, icon, color, sublabel }: {
  label: string; value: number | undefined; icon: React.ReactNode; color: string; sublabel?: string
}) {
  const hexToRgb = (hex: string) => {
    const m = hex.replace('#', '').match(/.{2}/g)
    return m ? m.map((v) => parseInt(v, 16)).join(',') : '108,99,255'
  }
  const rgb = hexToRgb(color)
  const animatedValue = useCountUp(value)

  return (
    <GlassCard variant="glow" sx={{
      p: 2.5,
      position: 'relative',
      overflow: 'hidden',
      borderColor: `rgba(${rgb},0.18)`,
      '&:hover': { borderColor: `rgba(${rgb},0.35)` },
      '&::before': {
        content: '""',
        position: 'absolute',
        top: 0, left: 0, right: 0,
        height: '2px',
        background: `linear-gradient(90deg, transparent 0%, ${color} 50%, transparent 100%)`,
        opacity: 0.7,
      },
    }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
        <Box sx={{ minWidth: 0 }}>
          <Typography sx={{
            color: `rgba(${rgb},0.8)`,
            textTransform: 'uppercase',
            letterSpacing: '0.1em',
            fontSize: '0.62rem',
            fontWeight: 700,
            mb: 0.75,
          }}>
            {label}
          </Typography>
          {value === undefined ? (
            <Skeleton width={60} height={44} sx={{ bgcolor: `rgba(${rgb},0.08)` }} />
          ) : (
            <Typography sx={{
              fontWeight: 800,
              lineHeight: 1.1,
              fontSize: '2rem',
              color,
              fontFamily: '"Fira Code", monospace',
              letterSpacing: '-0.02em',
            }}>
              {animatedValue.toLocaleString()}
            </Typography>
          )}
          {sublabel && (
            <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.65rem', mt: 0.25, display: 'block' }}>
              {sublabel}
            </Typography>
          )}
        </Box>
        <Box
          sx={{
            width: 44,
            height: 44,
            borderRadius: '12px',
            background: `rgba(${rgb},0.12)`,
            border: `1px solid rgba(${rgb},0.22)`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            color,
            boxShadow: `0 0 16px rgba(${rgb},0.2)`,
            '& svg': { fontSize: 22 },
          }}
        >
          {icon}
        </Box>
      </Box>
    </GlassCard>
  )
}

// ── Sparkline ───────────────────────────────────────────────
function Sparkline({ data, color = '#6C63FF', height = 40 }: {
  data: { day: string; count: number }[]; color?: string; height?: number
}) {
  if (!data || data.length === 0) return <Skeleton height={height} width="100%" />
  const max = Math.max(...data.map((d) => d.count), 1)
  const w = 100 / data.length
  return (
    <svg width="100%" height={height} style={{ display: 'block' }}>
      {data.map((d, i) => {
        const barH = (d.count / max) * (height - 4)
        return (
          <rect key={i} x={`${i * w + 1}%`} y={height - barH - 2}
            width={`${w - 2}%`} height={barH} rx={2} fill={color} opacity={0.7} />
        )
      })}
    </svg>
  )
}

// ── System health dot ────────────────────────────────────────
function HealthDot({ status }: { status: string | undefined }) {
  if (!status) return <HelpOutlineIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
  if (status === 'healthy') return <CheckCircleIcon sx={{ fontSize: 14, color: 'success.main' }} />
  if (status === 'offline' || status.startsWith('unhealthy')) return <ErrorIcon sx={{ fontSize: 14, color: 'error.main' }} />
  return <CircleIcon sx={{ fontSize: 14, color: 'warning.main' }} />
}

// ── Live events feed ─────────────────────────────────────────
interface LiveEvent {
  id: string; event_type: string; severity?: string; title: string
  module?: string; site_name?: string; ts: number
}

function LiveEventsFeed() {
  const { lastMessage } = useWebSocket()
  const [events, setEvents] = useState<LiveEvent[]>([])
  const idRef = useRef(0)

  useEffect(() => {
    if (!lastMessage) return
    try {
      const evt = JSON.parse(lastMessage)
      setEvents((prev) => [{
        id: String(idRef.current++),
        event_type: evt.event_type,
        severity: evt.payload?.severity,
        title: evt.payload?.title ?? evt.event_type,
        module: evt.payload?.module_type,
        site_name: evt.payload?.site_name,
        ts: Date.now(),
      }, ...prev].slice(0, 30))
    } catch {}
  }, [lastMessage])

  return (
    <GlassCard sx={{ p: 2.5, height: '100%' }}>
      <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>Live Events</Typography>
      <Box sx={{ overflowY: 'auto', maxHeight: 340 }}>
        {events.length === 0 ? (
          <Typography variant="caption" color="text.disabled">Waiting for events…</Typography>
        ) : events.map((e) => (
          <Box key={e.id} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.75, borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
            {e.severity
              ? <SeverityChip severity={e.severity as AlertSeverity} />
              : <Chip label={e.event_type.replace('_', ' ')} size="small" variant="outlined" sx={{ fontSize: '0.6rem', height: 18 }} />}
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="caption" noWrap sx={{ display: "block" }}>{e.title}</Typography>
              <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.62rem' }}>
                {e.site_name && `${e.site_name} · `}{new Date(e.ts).toLocaleTimeString()}
              </Typography>
            </Box>
            {e.module && <Chip label={e.module} size="small" variant="outlined" sx={{ fontSize: '0.6rem', height: 16 }} />}
          </Box>
        ))}
      </Box>
    </GlassCard>
  )
}

// ── Camera status grid (stream-based) ─────────────────────────
function CameraStatusGrid({ siteFilter }: { siteFilter: string }) {
  const { data: streams = [], isLoading } = useQuery({
    queryKey: ['all-streams', siteFilter],
    queryFn: () => listAllStreams(siteFilter ? { site_id: siteFilter } : undefined),
    refetchInterval: 30_000,
  })

  const statusColor = (s: string) =>
    s === 'online' ? '#00E396' : s === 'degraded' ? '#FF9800' : '#ff4560'

  return (
    <GlassCard sx={{ p: 2.5 }}>
      <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>
        Camera Status
        <Chip label={`${streams.length}`} size="small" sx={{ ml: 1, height: 18, fontSize: '0.68rem' }} />
      </Typography>
      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 1, maxHeight: 240, overflowY: 'auto' }}>
        {isLoading
          ? Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} height={60} />)
          : streams.map((s) => (
            <Tooltip key={s.id} title={`${s.status}${s.last_frame_at ? ` · ${new Date(s.last_frame_at).toLocaleTimeString()}` : ''}`}>
              <Box sx={{ p: 1, borderRadius: 1, border: '1px solid', borderColor: `${statusColor(s.status)}33`, bgcolor: `${statusColor(s.status)}08` }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <Box sx={{ width: 7, height: 7, borderRadius: '50%', bgcolor: statusColor(s.status), flexShrink: 0 }} />
                  <Typography variant="caption" noWrap sx={{ fontWeight: 600 }}>{s.camera_name}</Typography>
                </Box>
                {s.site_name && (
                  <Typography variant="caption" color="text.disabled" noWrap sx={{ fontSize: '0.62rem', display: "block" }}>{s.site_name}</Typography>
                )}
              </Box>
            </Tooltip>
          ))}
        {!isLoading && streams.length === 0 && <Typography variant="caption" color="text.disabled">No cameras</Typography>}
      </Box>
    </GlassCard>
  )
}

// ── Alert severity breakdown ──────────────────────────────────
const SEVERITY_COLORS: Record<string, string> = {
  critical: '#FF4560', high: '#FF6B35', medium: '#FF9800', low: '#00D9C0', info: '#6C63FF',
}

function AlertSeverityBreakdown() {
  const { data = [] } = useQuery({
    queryKey: ['alerts-by-severity'],
    queryFn: () => getAlertsBySeverity(7),
    refetchInterval: 60_000,
  })

  const total = data.reduce((s, d) => s + d.count, 0)

  return (
    <GlassCard sx={{ p: 2.5 }}>
      <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>Alert Severity (7 days)</Typography>
      {data.length === 0 ? (
        <Typography variant="caption" color="text.disabled">No alerts</Typography>
      ) : (
        <Stack spacing={0.75}>
          {data.map((d) => (
            <Box key={d.label}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.25 }}>
                <Typography variant="caption" sx={{ textTransform: 'capitalize' }}>{d.label}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {d.count} ({total > 0 ? Math.round((d.count / total) * 100) : 0}%)
                </Typography>
              </Box>
              <LinearProgress
                variant="determinate"
                value={total > 0 ? (d.count / total) * 100 : 0}
                sx={{
                  height: 6, borderRadius: 1,
                  bgcolor: 'rgba(255,255,255,0.07)',
                  '& .MuiLinearProgress-bar': { bgcolor: SEVERITY_COLORS[d.label] ?? '#6C63FF', borderRadius: 1 },
                }}
              />
            </Box>
          ))}
        </Stack>
      )}
    </GlassCard>
  )
}

// ── Active recordings panel ───────────────────────────────────
function ActiveRecordingsPanel({ siteFilter }: { siteFilter: string }) {
  const { data: recordings = [] } = useQuery({
    queryKey: ['all-recordings', 'recording', siteFilter],
    queryFn: () => listAllRecordings({ status_filter: 'recording', site_id: siteFilter || undefined }),
    refetchInterval: 15_000,
  })

  const elapsed = (startedAt: string) => {
    const secs = Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)
    const m = Math.floor(secs / 60), s = secs % 60
    return `${m}:${String(s).padStart(2, '0')}`
  }

  return (
    <GlassCard sx={{ p: 2.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <FiberManualRecordIcon sx={{ color: 'error.main', fontSize: 14 }} />
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Active Recordings</Typography>
        <Chip label={recordings.length} size="small" color="error" variant="outlined" sx={{ height: 18, fontSize: '0.62rem' }} />
      </Box>
      {recordings.length === 0 ? (
        <Typography variant="caption" color="text.disabled">No active recordings</Typography>
      ) : (
        <Stack spacing={0.5}>
          {recordings.map((r: any) => (
            <Box key={r.id} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Box sx={{ width: 6, height: 6, borderRadius: '50%', bgcolor: 'error.main', animation: 'pulse 1.5s infinite' }} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="caption" noWrap sx={{ fontWeight: 500 }}>{r.camera_name ?? r.camera_id}</Typography>
                {r.site_name && (
                  <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.62rem', display: "block" }}>{r.site_name}</Typography>
                )}
              </Box>
              <Typography variant="caption" color="error.light" sx={{ fontFamily: 'monospace' }}>
                {elapsed(r.started_at)}
              </Typography>
            </Box>
          ))}
        </Stack>
      )}
    </GlassCard>
  )
}

// ── Site health cards ─────────────────────────────────────────
function SiteHealthCards() {
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const { data: streams = [] } = useQuery({
    queryKey: ['all-streams'],
    queryFn: () => listAllStreams(),
    refetchInterval: 30_000,
  })

  if ((sites as any[]).length === 0) return null

  return (
    <GlassCard sx={{ p: 2.5 }}>
      <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>Site Health</Typography>
      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 1 }}>
        {(sites as any[]).map((site) => {
          const siteStreams = (streams as any[]).filter((s) => s.site_id === site.id)
          const online = siteStreams.filter((s) => s.status === 'online').length
          const total = siteStreams.length
          const allOnline = total > 0 && online === total
          const someOnline = online > 0 && online < total
          const color = allOnline ? '#00E396' : someOnline ? '#FF9800' : '#ff4560'
          return (
            <Box key={site.id} sx={{ p: 1, borderRadius: 1, border: '1px solid', borderColor: `${color}33`, bgcolor: `${color}08` }}>
              <Typography variant="caption" noWrap sx={{ fontWeight: 700, display: "block" }}>{site.name}</Typography>
              <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.62rem' }}>
                {online}/{total} online
              </Typography>
            </Box>
          )
        })}
      </Box>
    </GlassCard>
  )
}

// ── Notification channels status ──────────────────────────────
const CHANNEL_ICONS: Record<string, React.ReactNode> = {
  email: <EmailIcon sx={{ fontSize: 14 }} />,
  sms: <SmsIcon sx={{ fontSize: 14 }} />,
  webhook: <WebhookIcon sx={{ fontSize: 14 }} />,
}

function NotificationChannelsStatus() {
  const { data: channels = [] } = useQuery({
    queryKey: ['notification-channels'],
    queryFn: getChannels,
    refetchInterval: 120_000,
  })

  if ((channels as any[]).length === 0) return null

  return (
    <GlassCard sx={{ p: 2.5 }}>
      <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>Notification Channels</Typography>
      <Stack spacing={0.5}>
        {(channels as any[]).map((ch) => (
          <Box key={ch.id} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Box sx={{ color: ch.is_active ? 'success.main' : 'text.disabled' }}>
              {CHANNEL_ICONS[ch.channel_type] ?? <WebhookIcon sx={{ fontSize: 14 }} />}
            </Box>
            <Typography variant="caption" sx={{ flex: 1 }} noWrap>{ch.name}</Typography>
            <Chip
              label={ch.is_active ? 'active' : 'inactive'}
              size="small"
              color={ch.is_active ? 'success' : 'default'}
              variant="outlined"
              sx={{ height: 16, fontSize: '0.58rem' }}
            />
          </Box>
        ))}
      </Stack>
    </GlassCard>
  )
}

// ── Incident resolution metrics ───────────────────────────────
function IncidentResolutionPanel() {
  const { data } = useQuery({
    queryKey: ['incident-resolution'],
    queryFn: () => getIncidentResolutionTime(30),
    refetchInterval: 120_000,
  })

  const fmt = (minutes: number | null | undefined) => {
    if (!minutes) return '—'
    if (minutes < 60) return `${Math.round(minutes)}m`
    return `${Math.round(minutes / 60)}h ${Math.round(minutes % 60)}m`
  }

  return (
    <GlassCard sx={{ p: 2.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <TimerIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Incident Resolution (30 days)</Typography>
      </Box>
      {!data ? (
        <Skeleton height={60} />
      ) : (
        <Stack direction="row" spacing={3}>
          {[
            { label: 'Avg', value: fmt((data as any).avg_minutes) },
            { label: 'Median', value: fmt((data as any).median_minutes) },
            { label: 'P90', value: fmt((data as any).p90_minutes) },
            { label: 'Total', value: (data as any).total_resolved ?? 0 },
          ].map((m) => (
            <Box key={m.label} sx={{ textAlign: 'center' }}>
              <Typography variant="h6" color="secondary.main" sx={{ fontWeight: 700 }}>{m.value}</Typography>
              <Typography variant="caption" color="text.disabled">{m.label}</Typography>
            </Box>
          ))}
        </Stack>
      )}
    </GlassCard>
  )
}

// ── System health strip ───────────────────────────────────────
function SystemHealth() {
  const { data: health, isLoading } = useQuery({
    queryKey: ['system-health'],
    queryFn: () => apiClient.get('/api/v1/system/health').then((r) => r.data),
    refetchInterval: 60_000,
  })

  const CORE = ['api', 'postgres', 'redis']
  const WORKERS = ['lpr', 'face', 'intrusion', 'ppe', 'crowd', 'fire_smoke', 'weapon', 'behavior', 'tampering', 'abandoned', 'fall']

  if (isLoading) return <Skeleton height={40} />

  const services = health?.services ?? {}

  return (
    <GlassCard sx={{ p: 1.5 }}>
      <Stack direction="row" spacing={2} flexWrap="wrap" alignItems="center">
        <Typography variant="caption" color="text.secondary" sx={{ mr: 1, fontWeight: 600 }}>System</Typography>
        {CORE.map((svc) => (
          <Tooltip key={svc} title={`${svc}: ${services[svc] ?? 'unknown'}`}>
            <Stack direction="row" spacing={0.5} alignItems="center" sx={{ cursor: 'default' }}>
              <HealthDot status={services[svc]} />
              <Typography variant="caption" sx={{ textTransform: 'uppercase', fontSize: '0.62rem' }}>{svc}</Typography>
            </Stack>
          </Tooltip>
        ))}
        <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.1)' }} />
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>AI Workers</Typography>
        {WORKERS.map((mod) => {
          const s = services[`worker_${mod}`]
          return (
            <Tooltip key={mod} title={`${mod}: ${s ?? 'unknown'}`}>
              <Stack direction="row" spacing={0.5} alignItems="center" sx={{ cursor: 'default' }}>
                <HealthDot status={s} />
                <Typography variant="caption" sx={{ fontSize: '0.62rem' }}>{mod.replace('_', '/')}</Typography>
              </Stack>
            </Tooltip>
          )
        })}
      </Stack>
    </GlassCard>
  )
}

// ── Operations Overview ───────────────────────────────────────
function OperationsOverview() {
  const { data: iotData } = useQuery({
    queryKey: ['iot-dashboard'],
    queryFn: () => getIoTDashboard(),
    refetchInterval: 60_000,
  })
  const { data: parkingData } = useQuery({
    queryKey: ['parking-dashboard'],
    queryFn: () => getParkingDashboard(),
    refetchInterval: 60_000,
  })
  const { data: bwcData } = useQuery({
    queryKey: ['bwc-dashboard'],
    queryFn: () => getBWCDashboard(),
    refetchInterval: 60_000,
  })
  const { data: gpsData } = useQuery({
    queryKey: ['gps-dashboard'],
    queryFn: () => getGPSDashboard(),
    refetchInterval: 30_000,
  })
  const { data: alarmData } = useQuery({
    queryKey: ['alarm-dashboard'],
    queryFn: () => getAlarmDashboard(),
    refetchInterval: 30_000,
  })

  const modules = [
    {
      key: 'iot',
      label: 'IoT Sensors',
      color: '#00D9C0',
      loading: !iotData,
      stats: [
        { label: 'Normal',      value: iotData?.summary.normal,      color: '#00E396' },
        { label: 'Warning',     value: iotData?.summary.warning,     color: '#FF9800' },
        { label: 'Critical',    value: iotData?.summary.critical,    color: '#FF4560' },
        { label: 'Offline',     value: iotData?.summary.offline,     color: '#888' },
        { label: 'Open Alerts', value: iotData?.summary.open_alerts, color: '#FF4560', wide: true },
      ],
    },
    {
      key: 'parking',
      label: 'Parking',
      color: '#2196F3',
      loading: !parkingData,
      stats: [
        { label: 'Available',      value: parkingData?.available_bays,  color: '#00E396' },
        { label: 'Occupied',       value: parkingData?.occupied_bays,   color: '#6C63FF' },
        { label: 'Active Sessions',value: parkingData?.active_sessions, color: '#00D9C0', wide: true },
        { label: 'Overstay',       value: parkingData?.overstay_count,  color: '#FF9800', wide: true },
      ],
    },
    {
      key: 'bwc',
      label: 'Body-Worn Cameras',
      color: '#9C27B0',
      loading: !bwcData,
      stats: [
        { label: 'Available',    value: bwcData?.available,        color: '#00E396' },
        { label: 'Assigned',     value: bwcData?.assigned,         color: '#6C63FF' },
        { label: 'Recording',    value: bwcData?.recording,        color: '#FF4560' },
        { label: 'Low Battery',  value: bwcData?.low_battery,      color: '#FF9800' },
        { label: 'Today',        value: bwcData?.recordings_today, color: '#00D9C0', wide: true },
      ],
    },
    {
      key: 'gps',
      label: 'GPS Fleet',
      color: '#FF9800',
      loading: !gpsData,
      stats: [
        { label: 'Moving',  value: gpsData?.summary.moving,  color: '#00E396' },
        { label: 'Idle',    value: gpsData?.summary.idle,    color: '#FF9800' },
        { label: 'Offline', value: gpsData?.summary.offline, color: '#888' },
        { label: 'Total',   value: gpsData?.summary.total,   color: '#6C63FF' },
      ],
    },
    {
      key: 'alarms',
      label: 'Alarms',
      color: '#FF4560',
      loading: !alarmData,
      stats: [
        { label: 'Armed',       value: alarmData?.panel_summary.armed,             color: '#00E396' },
        { label: 'Disarmed',    value: alarmData?.panel_summary.disarmed,          color: '#888' },
        { label: 'In Alarm',    value: alarmData?.panel_summary.in_alarm,          color: '#FF4560' },
        { label: 'Zones Alarm', value: alarmData?.zone_summary.zones_in_alarm,     color: '#FF4560' },
        { label: 'Tampered',    value: alarmData?.zone_summary.zones_tampered,     color: '#FF9800', wide: true },
      ],
    },
  ]

  return (
    <Box sx={{
      display: 'grid',
      gridTemplateColumns: { xs: 'repeat(2, 1fr)', sm: 'repeat(3, 1fr)', md: 'repeat(5, 1fr)' },
      gap: 1.5,
      mb: 2.5,
    }}>
      {modules.map((mod) => (
        <GlassCard key={mod.key} sx={{
          p: 2,
          position: 'relative',
          overflow: 'hidden',
          borderColor: `${mod.color}22`,
          '&:hover': { borderColor: `${mod.color}44` },
          '&::before': {
            content: '""',
            position: 'absolute',
            top: 0, left: 0, right: 0,
            height: '2px',
            background: `linear-gradient(90deg, transparent 0%, ${mod.color} 50%, transparent 100%)`,
            opacity: 0.8,
          },
        }}>
          <Typography sx={{
            color: mod.color,
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            fontSize: '0.6rem',
            fontWeight: 700,
            mb: 1.5,
          }}>
            {mod.label}
          </Typography>
          {mod.loading ? (
            <Stack spacing={0.5}>
              <Skeleton height={18} />
              <Skeleton height={18} width="70%" />
            </Stack>
          ) : (
            <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0.75 }}>
              {mod.stats.map((stat) => (
                <Box key={stat.label} sx={'wide' in stat ? { gridColumn: '1 / -1' } : {}}>
                  <Typography sx={{ fontSize: '0.57rem', color: 'text.disabled', lineHeight: 1.3 }}>
                    {stat.label}
                  </Typography>
                  <Typography sx={{
                    fontSize: '1.1rem',
                    fontWeight: 700,
                    color: (stat.value ?? 0) > 0 ? stat.color : 'rgba(255,255,255,0.25)',
                    fontFamily: '"Fira Code", monospace',
                    lineHeight: 1.2,
                  }}>
                    {stat.value ?? 0}
                  </Typography>
                </Box>
              ))}
            </Box>
          )}
        </GlassCard>
      ))}
    </Box>
  )
}

// ── Main Dashboard ────────────────────────────────────────────
export default function Dashboard() {
  // Client role (7) gets a simplified portal view instead of the full operational dashboard
  const roleId = useAuthStore((s) => s.user?.roleId)
  if (roleId === 7) {
    const ClientPortal = React.lazy(() => import('./ClientPortal'))
    return (
      <React.Suspense fallback={<Box sx={{ p: 3 }}><CircularProgress /></Box>}>
        <ClientPortal />
      </React.Suspense>
    )
  }

  const [siteFilter, setSiteFilter] = useState('')
  // Declared alongside the page's other hooks, i.e. after the role-7 early
  // return above — consistent with every existing hook in this component.
  const { kiosk, toggleKiosk } = useKioskToggle()
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const { data: summary } = useQuery({
    queryKey: ['analytics-summary', siteFilter],
    queryFn: () => getSummary(siteFilter || undefined),
    refetchInterval: 30_000,
  })

  const { data: recentAlerts } = useQuery({
    queryKey: ['alerts', 'open', siteFilter, ''],
    queryFn: () => getAlerts('open', siteFilter || undefined, undefined, 8),
    refetchInterval: 30_000,
  })

  const { data: moduleData = [] } = useQuery({
    queryKey: ['analytics-by-module'],
    queryFn: () => getAlertsByModule(7),
    refetchInterval: 60_000,
  })

  const { data: trend = [] } = useQuery({
    queryKey: ['analytics-trend'],
    queryFn: () => getDetectionsTrend(7),
    refetchInterval: 60_000,
  })

  const { data: topCameras = [] } = useQuery({
    queryKey: ['top-cameras'],
    queryFn: () => getTopCameras(30, 5),
    refetchInterval: 60_000,
  })

  return (
    <Box sx={{ p: 0 }}>
      {/* Header + site selector */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flex: 1 }}>
          <Box sx={{
            width: 3, height: 28, borderRadius: 2,
            background: (t) => `linear-gradient(180deg, ${t.palette.primary.main} 0%, ${t.palette.secondary.main} 100%)`,
            boxShadow: (t) => t.palette.mode === 'dark' ? `0 0 12px ${t.palette.primary.main}b3` : 'none',
            flexShrink: 0,
          }} />
          {/* Same theme-derived treatment as PageHeader: the first gradient
              stop must be the theme's own text colour, or this title turns
              near-white-on-white the moment light mode is on. */}
          <Typography variant="h5" sx={{
            fontWeight: 800,
            letterSpacing: '-0.02em',
            color: 'text.primary',
          }}>
            Dashboard
          </Typography>
        </Box>
        {(sites as any[]).length > 0 && (
          <Select size="small" value={siteFilter} onChange={(e) => setSiteFilter(e.target.value)}
            displayEmpty sx={{ minWidth: 160 }}>
            <MenuItem value="">All Sites</MenuItem>
            {(sites as any[]).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </Select>
        )}
        {summary?.active_recordings != null && summary.active_recordings > 0 && (
          <Chip
            icon={<FiberManualRecordIcon sx={{ color: 'red !important', fontSize: '0.75rem !important' }} />}
            label={`${summary.active_recordings} recording`}
            color="error" size="small"
          />
        )}
        <Tooltip title="Open Live Wall in a new window — keep watching this Dashboard while monitoring cameras on another screen">
          <Button
            variant="outlined"
            size="small"
            startIcon={<LiveTvIcon />}
            onClick={() => openLiveWallWindow()}
          >
            Live Wall
          </Button>
        </Tooltip>
        <Tooltip title="Send the Dashboard to another monitor — opens its own window already in full screen (Esc to leave full screen there)">
          <IconButton size="small" onClick={() => openInNewWindow('/', { fullscreen: true })}>
            <OpenInNewIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        {/* Enter-only, like the other control-room pages — AppShell's
            focus-mode strip owns Back and Exit once full screen. */}
        {!kiosk && (
          <Tooltip title="Full screen for continuous monitoring">
            <Button
              size="small"
              variant="outlined"
              startIcon={<FullscreenIcon />}
              onClick={toggleKiosk}
            >
              Full Screen
            </Button>
          </Tooltip>
        )}
      </Box>

      {/* KPI Row */}
      <Box sx={{ display: 'flex', gap: 2, mb: 2.5, flexWrap: 'wrap' }}>
        {[
          { label: 'Open Alerts',       value: summary?.open_alerts,               icon: <NotificationsIcon />,       color: '#FF4560', sub: 'active'  },
          { label: 'Open Incidents',    value: summary?.open_incidents,             icon: <ReportProblemIcon />,       color: '#FF9800', sub: 'active'  },
          { label: 'Cameras Active',    value: summary?.active_cameras,             icon: <VideocamIcon />,            color: '#00E396', sub: 'online'  },
          { label: 'Detections Today',  value: summary?.detections_today,           icon: <SearchIcon />,              color: '#6C63FF', sub: 'today'   },
          { label: 'Active Recordings', value: summary?.active_recordings ?? 0,     icon: <FiberManualRecordIcon />,   color: '#FF4560', sub: 'streams' },
        ].map((kpi, i) => (
          <Box
            key={kpi.label}
            sx={{ flex: '1 1 180px', minWidth: 0, ...fadeUpSx(i) }}
          >
            <KpiCard label={kpi.label} value={kpi.value} icon={kpi.icon} color={kpi.color} sublabel={kpi.sub} />
          </Box>
        ))}
      </Box>

      {/* Operations Overview — IoT / Parking / BWC / GPS / Alarms */}
      <OperationsOverview />

      {/* Site health cards */}
      <Box sx={{ mb: 2 }}>
        <SiteHealthCards />
      </Box>

      {/* Main 3-column layout */}
      <Grid container spacing={2} sx={{ mb: 2 }}>
        {/* Left — camera grid + recent alerts + active recordings */}
        <Grid size={{ xs: 12, md: 4 }}>
          <Stack spacing={2}>
            <CameraStatusGrid siteFilter={siteFilter} />
            <ActiveRecordingsPanel siteFilter={siteFilter} />
            <GlassCard sx={{ p: 2.5 }}>
              <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>Recent Open Alerts</Typography>
              {!recentAlerts ? (
                <Skeleton height={120} />
              ) : !recentAlerts.items?.length ? (
                <Typography variant="caption" color="text.disabled">No open alerts</Typography>
              ) : (
                <Stack spacing={0.5}>
                  {(recentAlerts.items as any[]).slice(0, 6).map((a) => (
                    <Box key={a.id} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <SeverityChip severity={a.severity as AlertSeverity} />
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography variant="caption" noWrap sx={{ fontWeight: 500 }}>{a.title}</Typography>
                        {a.site_name && (
                          <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.62rem', display: "block" }}>
                            {a.site_name}
                          </Typography>
                        )}
                      </Box>
                    </Box>
                  ))}
                </Stack>
              )}
            </GlassCard>
          </Stack>
        </Grid>

        {/* Middle — analytics + severity breakdown + resolution */}
        <Grid size={{ xs: 12, md: 4 }}>
          <Stack spacing={2}>
            <GlassCard sx={{ p: 2.5 }}>
              <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>Detections — 7 Days</Typography>
              <Sparkline data={trend as any[]} color="#6C63FF" height={60} />
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 0.5 }}>
                <Typography variant="caption" color="text.disabled">
                  {(trend as any[])[0]?.day ? new Date((trend as any[])[0].day).toLocaleDateString('en', { weekday: 'short' }) : ''}
                </Typography>
                <Typography variant="caption" color="text.disabled">Today</Typography>
              </Box>
            </GlassCard>

            <AlertSeverityBreakdown />

            <GlassCard sx={{ p: 2.5 }}>
              <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>Alerts by Module (7 days)</Typography>
              {(moduleData as any[]).length === 0 ? (
                <Typography variant="caption" color="text.disabled">No data</Typography>
              ) : (
                <Stack spacing={0.75}>
                  {(moduleData as any[]).slice(0, 6).map((m: any) => {
                    const maxCount = Math.max(...(moduleData as any[]).map((d: any) => d.count), 1)
                    const pct = (m.count / maxCount) * 100
                    return (
                      <Box key={m.label}>
                        <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.25 }}>
                          <Typography variant="caption">{m.label}</Typography>
                          <Typography variant="caption" color="text.secondary">{m.count}</Typography>
                        </Box>
                        <Box sx={{ height: 6, bgcolor: 'rgba(255,255,255,0.07)', borderRadius: 1, overflow: 'hidden' }}>
                          <Box sx={{ height: '100%', width: `${pct}%`, bgcolor: 'primary.main', borderRadius: 1, transition: 'width 0.5s' }} />
                        </Box>
                      </Box>
                    )
                  })}
                </Stack>
              )}
            </GlassCard>

            <GlassCard sx={{ p: 2.5 }}>
              <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>Top Cameras by Alerts (30 days)</Typography>
              {(topCameras as any[]).length === 0 ? (
                <Typography variant="caption" color="text.disabled">No data</Typography>
              ) : (
                <Stack spacing={0.5}>
                  {(topCameras as any[]).slice(0, 5).map((cam: any, i: number) => (
                    <Box key={cam.camera_id} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Typography variant="caption" color="text.disabled" sx={{ width: 14 }}>{i + 1}</Typography>
                      <Typography variant="caption" sx={{ flex: 1 }} noWrap>{cam.camera_name}</Typography>
                      <Chip label={cam.alert_count} size="small" color="error" variant="outlined" sx={{ height: 18, fontSize: '0.62rem' }} />
                    </Box>
                  ))}
                </Stack>
              )}
            </GlassCard>
          </Stack>
        </Grid>

        {/* Right — live events + notification channels + incident resolution */}
        <Grid size={{ xs: 12, md: 4 }}>
          <Stack spacing={2}>
            <LiveEventsFeed />
            <NotificationChannelsStatus />
            <IncidentResolutionPanel />
          </Stack>
        </Grid>
      </Grid>

      {/* System health strip */}
      <SystemHealth />
    </Box>
  )
}

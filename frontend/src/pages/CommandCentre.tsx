import React, { useState, useEffect } from 'react'
import {
  Box, Typography, Grid, Paper, Chip, Divider, CircularProgress,
  Tooltip, IconButton, Button,
} from '@mui/material'
import LiveTvIcon from '@mui/icons-material/LiveTv'
import VideocamIcon from '@mui/icons-material/Videocam'
import VideocamOffIcon from '@mui/icons-material/VideocamOff'
import SecurityIcon from '@mui/icons-material/Security'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import ErrorIcon from '@mui/icons-material/Error'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import RefreshIcon from '@mui/icons-material/Refresh'
import PersonIcon from '@mui/icons-material/Person'
import AccessTimeIcon from '@mui/icons-material/AccessTime'
import MonitorIcon from '@mui/icons-material/Monitor'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import FullscreenIcon from '@mui/icons-material/Fullscreen'
import TaskAltIcon from '@mui/icons-material/TaskAlt'
import DirectionsCarIcon from '@mui/icons-material/DirectionsCar'
import { getSites } from '@/api/sites'
import { useNavigate } from 'react-router-dom'
import { fadeUpSx, useCountUp } from '@/lib/motion'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getCCOverview } from '@/api/commandCentre'
import { AlertResponseDialog } from '@/components/common/AlertResponseDialog'
import { openLiveWallWindow } from '@/lib/liveWallWindow'
import { openInNewWindow } from '@/lib/popoutWindow'
import { useKioskToggle } from '@/hooks/useKioskToggle'
import type { SiteStatus, RecentAlert, GuardStatus } from '@/api/commandCentre'

// ── helpers ──────────────────────────────────────────────────────────────────

function relativeTime(iso: string | null): string {
  if (!iso) return 'N/A'
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  return `${Math.floor(diff / 3600)}h ago`
}

function siteHealth(site: SiteStatus): 'critical' | 'warning' | 'ok' {
  if (site.critical_alerts > 0 || site.cameras_offline > 0) return 'critical'
  if (site.high_alerts > 0 || site.cameras_degraded > 0) return 'warning'
  return 'ok'
}

const HEALTH = {
  critical: { border: '#FF4560', bg: 'rgba(255,69,96,0.08)',  glow: 'rgba(255,69,96,0.25)',  icon: <ErrorIcon sx={{ color: '#FF4560', fontSize: 16 }} />,        label: 'CRITICAL' },
  warning:  { border: '#FF9800', bg: 'rgba(255,152,0,0.08)',   glow: 'rgba(255,152,0,0.2)',   icon: <WarningAmberIcon sx={{ color: '#FF9800', fontSize: 16 }} />, label: 'WARNING'  },
  ok:       { border: '#00E396', bg: 'rgba(0,227,150,0.06)',   glow: 'rgba(0,227,150,0.15)',  icon: <CheckCircleIcon sx={{ color: '#00E396', fontSize: 16 }} />,  label: 'NORMAL'   },
}

const SEV_COLOR: Record<string, string> = {
  critical: '#FF4560',
  high:     '#FF9800',
  medium:   '#FFC107',
  low:      '#00E396',
  info:     '#6C63FF',
}

const MODULE_LABEL: Record<string, string> = {
  lpr: 'LPR', face: 'Face', intrusion: 'Intrusion', ppe: 'PPE',
  crowd: 'Crowd', fire_smoke: 'Fire/Smoke', weapon: 'Weapon',
  behavior: 'Behavior', tampering: 'Tamper', abandoned: 'Abandoned', fall: 'Fall',
}

// ── subcomponents ─────────────────────────────────────────────────────────────

function KpiCard({ label, value, icon, color, sub, onClick }: {
  label: string; value: number; icon: React.ReactNode; color: string; sub?: string; onClick?: () => void
}) {
  const animatedValue = useCountUp(value)
  return (
    <Paper
      onClick={onClick}
      sx={{
        flex: 1,
        p: 2,
        display: 'flex',
        flexDirection: 'column',
        gap: 0.5,
        background: `linear-gradient(135deg, ${color}18 0%, transparent 100%)`,
        border: `1px solid ${color}40`,
        borderRadius: 2,
        position: 'relative',
        overflow: 'hidden',
        cursor: onClick ? 'pointer' : 'default',
        transition: 'transform 0.15s, box-shadow 0.15s',
        ...(onClick && { '&:hover': { transform: 'translateY(-2px)', boxShadow: `0 4px 16px ${color}30` } }),
        '&::after': {
          content: '""',
          position: 'absolute',
          top: 0, right: 0,
          width: 80, height: 80,
          background: `radial-gradient(circle at top right, ${color}22 0%, transparent 70%)`,
          pointerEvents: 'none',
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, color: color }}>
        {icon}
        <Typography variant="caption" sx={{ fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', fontSize: '0.63rem', color: 'text.secondary' }}>
          {label}
        </Typography>
      </Box>
      <Typography variant="h4" sx={{ fontWeight: 800, lineHeight: 1, color: color }}>
        {animatedValue}
      </Typography>
      {sub && (
        <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.64rem' }}>
          {sub}
        </Typography>
      )}
    </Paper>
  )
}

function SiteCard({ site, onClick }: { site: SiteStatus; onClick: () => void }) {
  const health = siteHealth(site)
  const h = HEALTH[health]

  return (
    <Paper
      onClick={onClick}
      sx={{
        p: 2,
        height: '100%',
        background: h.bg,
        border: `1px solid ${h.border}55`,
        borderRadius: 2.5,
        boxShadow: `0 0 18px ${h.glow}`,
        cursor: 'pointer',
        transition: 'box-shadow 0.3s, transform 0.15s',
        '&:hover': { boxShadow: `0 0 28px ${h.glow}`, transform: 'translateY(-2px)' },
      }}
    >
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
        {h.icon}
        <Typography variant="subtitle2" sx={{ fontWeight: 700, flex: 1 }} noWrap>
          {site.name}
        </Typography>
        <Chip
          label={h.label}
          size="small"
          sx={{
            height: 18,
            fontSize: '0.58rem',
            fontWeight: 700,
            letterSpacing: '0.06em',
            bgcolor: `${h.border}22`,
            color: h.border,
            border: `1px solid ${h.border}55`,
          }}
        />
      </Box>

      {/* Camera bar */}
      <Box sx={{ mb: 1.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
          <VideocamIcon sx={{ fontSize: 13, color: 'text.secondary' }} />
          <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.68rem' }}>
            Cameras — {site.cameras_total} total
          </Typography>
        </Box>
        {/* Segmented bar */}
        {site.cameras_total > 0 ? (
          <Box sx={{ display: 'flex', height: 6, borderRadius: 1, overflow: 'hidden', gap: '1px', bgcolor: 'rgba(255,255,255,0.05)' }}>
            {site.cameras_online > 0 && (
              <Tooltip title={`${site.cameras_online} online`}>
                <Box sx={{ flex: site.cameras_online, bgcolor: '#00E396', borderRadius: '3px 0 0 3px' }} />
              </Tooltip>
            )}
            {site.cameras_degraded > 0 && (
              <Tooltip title={`${site.cameras_degraded} degraded`}>
                <Box sx={{ flex: site.cameras_degraded, bgcolor: '#FF9800' }} />
              </Tooltip>
            )}
            {site.cameras_offline > 0 && (
              <Tooltip title={`${site.cameras_offline} offline`}>
                <Box sx={{ flex: site.cameras_offline, bgcolor: '#FF4560', borderRadius: '0 3px 3px 0' }} />
              </Tooltip>
            )}
          </Box>
        ) : (
          <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.62rem' }}>No cameras assigned</Typography>
        )}
        <Box sx={{ display: 'flex', gap: 1, mt: 0.5 }}>
          {site.cameras_online > 0 && <Typography variant="caption" sx={{ color: '#00E396', fontSize: '0.62rem' }}>{site.cameras_online} online</Typography>}
          {site.cameras_degraded > 0 && <Typography variant="caption" sx={{ color: '#FF9800', fontSize: '0.62rem' }}>{site.cameras_degraded} degraded</Typography>}
          {site.cameras_offline > 0 && <Typography variant="caption" sx={{ color: '#FF4560', fontSize: '0.62rem' }}>{site.cameras_offline} offline</Typography>}
        </Box>
      </Box>

      <Divider sx={{ borderColor: `${h.border}25`, mb: 1.25 }} />

      {/* Alert counts */}
      <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', mb: 1.25 }}>
        {site.active_alerts === 0 ? (
          <Typography variant="caption" sx={{ color: '#00E396', fontSize: '0.68rem', fontWeight: 600 }}>
            No active alerts
          </Typography>
        ) : (
          <>
            {site.critical_alerts > 0 && (
              <Chip size="small" icon={<ErrorIcon style={{ fontSize: 11 }} />} label={`${site.critical_alerts} Critical`}
                sx={{ height: 20, fontSize: '0.62rem', bgcolor: 'rgba(255,69,96,0.18)', color: '#FF4560', border: '1px solid rgba(255,69,96,0.4)' }} />
            )}
            {site.high_alerts > 0 && (
              <Chip size="small" label={`${site.high_alerts} High`}
                sx={{ height: 20, fontSize: '0.62rem', bgcolor: 'rgba(255,152,0,0.18)', color: '#FF9800', border: '1px solid rgba(255,152,0,0.4)' }} />
            )}
            {site.medium_alerts > 0 && (
              <Chip size="small" label={`${site.medium_alerts} Medium`}
                sx={{ height: 20, fontSize: '0.62rem', bgcolor: 'rgba(255,193,7,0.15)', color: '#FFC107', border: '1px solid rgba(255,193,7,0.35)' }} />
            )}
          </>
        )}
      </Box>

      {/* Guards */}
      {site.guards.length > 0 ? (
        <Box>
          <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.63rem', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            Guards on duty ({site.guards.length})
          </Typography>
          {site.guards.slice(0, 3).map((g) => (
            <Box key={g.shift_id} sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mt: 0.5 }}>
              <PersonIcon sx={{ fontSize: 11, color: 'text.secondary' }} />
              <Typography variant="caption" sx={{ flex: 1, fontSize: '0.68rem' }} noWrap>{g.guard_name}</Typography>
              <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.6rem', flexShrink: 0 }}>
                {g.last_checkpoint_at ? relativeTime(g.last_checkpoint_at) : '—'}
              </Typography>
            </Box>
          ))}
          {site.guards.length > 3 && (
            <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.62rem', mt: 0.5 }}>
              +{site.guards.length - 3} more
            </Typography>
          )}
        </Box>
      ) : (
        <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.65rem' }}>No guards on duty</Typography>
      )}
    </Paper>
  )
}

function AlertFeed({ alerts, onSelect }: { alerts: RecentAlert[]; onSelect: (alert: RecentAlert) => void }) {
  return (
    <Paper sx={{ p: 1.5, height: '100%', display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Typography variant="caption" sx={{ fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase', color: 'text.secondary', fontSize: '0.63rem' }}>
        Live Alerts — Last 4h
      </Typography>
      <Box sx={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 0.75 }}>
        {alerts.length === 0 ? (
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 80 }}>
            <Typography variant="caption" sx={{ color: 'text.disabled' }}>No critical/high alerts</Typography>
          </Box>
        ) : alerts.map((a) => (
          <Box
            key={a.id}
            onClick={() => onSelect(a)}
            sx={{
              p: 1,
              borderRadius: 1.5,
              bgcolor: `${SEV_COLOR[a.severity] ?? '#6C63FF'}0f`,
              border: `1px solid ${SEV_COLOR[a.severity] ?? '#6C63FF'}30`,
              cursor: 'pointer',
              transition: 'background-color 0.15s',
              '&:hover': { bgcolor: `${SEV_COLOR[a.severity] ?? '#6C63FF'}22` },
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.25 }}>
              <Box sx={{ width: 6, height: 6, borderRadius: '50%', bgcolor: SEV_COLOR[a.severity] ?? '#6C63FF', flexShrink: 0 }} />
              <Typography variant="caption" sx={{ flex: 1, fontWeight: 600, fontSize: '0.7rem', lineHeight: 1.3 }} noWrap>
                {a.title}
              </Typography>
              <Chip
                label={MODULE_LABEL[a.module_type] ?? a.module_type}
                size="small"
                sx={{ height: 16, fontSize: '0.55rem', bgcolor: 'rgba(108,99,255,0.15)', color: '#9B8FFF' }}
              />
            </Box>
            <Box sx={{ display: 'flex', gap: 1, pl: 1.5 }}>
              <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.62rem' }} noWrap>
                {a.site_name ?? '—'} · {a.camera_name}
              </Typography>
              <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.6rem', flexShrink: 0 }}>
                {relativeTime(a.created_at)}
              </Typography>
            </Box>
          </Box>
        ))}
      </Box>
    </Paper>
  )
}

function GuardsBoard({ guards }: { guards: GuardStatus[] }) {
  return (
    <Paper sx={{ p: 1.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Typography variant="caption" sx={{ fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase', color: 'text.secondary', fontSize: '0.63rem' }}>
        Guards on Duty ({guards.length})
      </Typography>
      {guards.length === 0 ? (
        <Typography variant="caption" sx={{ color: 'text.disabled', textAlign: 'center', py: 2 }}>No active shifts</Typography>
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
          {guards.map((g) => (
            <Box key={g.shift_id} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.5, borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
              <Box
                sx={{
                  width: 28, height: 28, borderRadius: '8px', flexShrink: 0,
                  background: 'linear-gradient(135deg, #6C63FF, #00D9C0)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '0.7rem', fontWeight: 700, color: '#fff',
                }}
              >
                {g.guard_name.charAt(0).toUpperCase()}
              </Box>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="caption" sx={{ fontWeight: 600, fontSize: '0.72rem', display: 'block' }} noWrap>
                  {g.guard_name}
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <AccessTimeIcon sx={{ fontSize: 10, color: 'text.disabled' }} />
                  <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.6rem' }}>
                    {g.last_checkpoint_at ? `Chkpt ${relativeTime(g.last_checkpoint_at)}` : 'No scan yet'}
                  </Typography>
                </Box>
              </Box>
              <Box
                sx={{
                  width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                  bgcolor: g.last_checkpoint_at && (Date.now() - new Date(g.last_checkpoint_at).getTime()) < 30 * 60 * 1000
                    ? '#00E396' : '#FF9800',
                  boxShadow: '0 0 5px currentcolor',
                }}
              />
            </Box>
          ))}
        </Box>
      )}
    </Paper>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────

export default function CommandCentre() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [now, setNow] = useState(new Date())
  const [selectedAlert, setSelectedAlert] = useState<RecentAlert | null>(null)
  const { kiosk, toggleKiosk } = useKioskToggle()
  const { data, isLoading, isFetching, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['cc-overview'],
    queryFn: getCCOverview,
    refetchInterval: 30_000,
  })
  // Same gate as Guard Ops: only offer the gatehouse board when a site this
  // user can see actually runs visitor management. Shares the ['sites'] cache
  // key with every other page, so this costs no extra request in practice.
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const hasVms = (sites ?? []).some((s) => s.vms_enabled)

  // Update clock every second
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  const s = data?.summary
  const lastUpdate = dataUpdatedAt ? new Date(dataUpdatedAt) : null

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 2, minHeight: 0 }}>

      {/* ── Header ── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: kiosk ? 1.25 : 2, flexShrink: 0 }}>
        <MonitorIcon sx={{ color: 'primary.main', fontSize: kiosk ? 22 : 28 }} />
        {/* Full screen the clock moves up beside the title instead of sitting
            under it — the one-line header the rest of the app gets from
            PageHeader, and the date is the closest thing this page has to a
            tagline. */}
        <Box sx={{
          flex: 1,
          minWidth: 0,
          ...(kiosk ? { display: 'flex', alignItems: 'baseline', gap: 1.25, flexWrap: 'wrap' } : null),
        }}>
          <Typography variant={kiosk ? 'h6' : 'h5'} sx={{
            fontWeight: 800,
            letterSpacing: '-0.01em',
            lineHeight: 1.2,
            ...(kiosk ? { fontSize: '1.05rem' } : null),
          }}>
            Command &amp; Control Centre
          </Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.65rem' }}>
            {now.toLocaleDateString('en-SG', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
            &nbsp;&mdash;&nbsp;
            {now.toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {lastUpdate && (
            <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.63rem' }}>
              Updated {relativeTime(lastUpdate.toISOString())}
            </Typography>
          )}
          <Tooltip title="Refresh now">
            <IconButton size="small" onClick={() => refetch()} disabled={isFetching}>
              <RefreshIcon sx={{ fontSize: 16, animation: isFetching ? 'spin 1s linear infinite' : 'none' }} />
            </IconButton>
          </Tooltip>
          {/* Every screen an operator runs full-screen on another monitor,
              reachable from the one page they sit on all shift. Live Wall keeps
              its dedicated helper (it restores a saved layout); the rest go
              through the generic route opener. Data-driven so a new
              full-screen page is one line, not another copy-pasted button. */}
          <Tooltip title="Open Live Wall in a new window — keep this Command Centre visible while monitoring cameras on another screen">
            <Button variant="outlined" size="small" startIcon={<LiveTvIcon />} onClick={() => openLiveWallWindow()}>
              Live Wall
            </Button>
          </Tooltip>
          {([
            { label: 'Attendance', path: '/attendance', icon: <AccessTimeIcon />,
              hint: 'Open the live attendance board on another screen — who is on, late, or still to arrive' },
            { label: 'Action Center', path: '/action-center', icon: <TaskAltIcon />,
              hint: 'Open the duty board on another screen — what needs attention right now' },
            ...(hasVms
              ? [{ label: 'VMS', path: '/vms-onsite', icon: <DirectionsCarIcon />,
                   hint: 'Open the gatehouse board on another screen — everyone on site, with arrival time and parking expiry' }]
              : []),
          ]).map((s) => (
            <Tooltip key={s.path} title={s.hint}>
              <Button
                variant="outlined"
                size="small"
                startIcon={s.icon}
                onClick={() => openInNewWindow(s.path, { fullscreen: true })}
              >
                {s.label}
              </Button>
            </Tooltip>
          ))}
          <Tooltip title="Send Command Centre to another monitor — opens its own window already in full screen (Esc to leave full screen there)">
            <IconButton size="small" onClick={() => openInNewWindow('/command-centre', { fullscreen: true })}>
              <OpenInNewIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          {/* Enter-only. Once full screen, AppShell's focus-mode strip owns Back
              and Exit — a second exit button here landed under the strip and
              read as two overlapping controls in the top-right corner. */}
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
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1 }}>
          <CircularProgress size={40} />
        </Box>
      ) : (
        <>
          {/* ── KPI row ── */}
          <Box sx={{
            display: 'flex', gap: 1.5, flexShrink: 0, flexWrap: 'wrap',
            ...Object.fromEntries(Array.from({ length: 6 }, (_, i) => [`& > *:nth-of-type(${i + 1})`, fadeUpSx(i)])),
          }}>
            <KpiCard label="Sites"         value={s?.total_sites    ?? 0} color="#6C63FF" icon={<MonitorIcon sx={{ fontSize: 16 }} />}        sub="active" onClick={() => navigate('/sites')} />
            <KpiCard label="Cameras Online" value={s?.cameras_online  ?? 0} color="#00E396" icon={<VideocamIcon sx={{ fontSize: 16 }} />}       sub={`of ${(s?.cameras_online ?? 0) + (s?.cameras_offline ?? 0) + (s?.cameras_degraded ?? 0)} total`} onClick={() => navigate('/cameras')} />
            <KpiCard label="Offline"        value={s?.cameras_offline ?? 0} color="#FF4560" icon={<VideocamOffIcon sx={{ fontSize: 16 }} />}    sub="cameras" onClick={() => navigate('/cameras')} />
            <KpiCard label="Active Alerts"  value={s?.active_alerts   ?? 0} color="#FF9800" icon={<WarningAmberIcon sx={{ fontSize: 16 }} />}   sub="open + acknowledged" onClick={() => navigate('/alerts')} />
            <KpiCard label="Critical"       value={s?.critical_alerts ?? 0} color="#FF4560" icon={<ErrorIcon sx={{ fontSize: 16 }} />}          sub="severity" onClick={() => navigate('/alerts')} />
            <KpiCard label="Guards on Duty" value={s?.guards_on_duty  ?? 0} color="#00D9C0" icon={<SecurityIcon sx={{ fontSize: 16 }} />}       sub="active shifts" onClick={() => navigate('/roster')} />
          </Box>

          {/* ── Main area ── */}
          <Box sx={{ flex: 1, display: 'flex', gap: 2, minHeight: 0, overflow: 'hidden' }}>

            {/* Left — site grid */}
            <Box sx={{ flex: 1, overflowY: 'auto', minWidth: 0,
              '&::-webkit-scrollbar': { width: '3px' },
              '&::-webkit-scrollbar-thumb': { background: 'rgba(108,99,255,0.3)', borderRadius: '4px' },
            }}>
              {!data?.sites?.length ? (
                <Paper sx={{ p: 4, textAlign: 'center' }}>
                  <Typography variant="body2" color="text.secondary">No sites configured yet.</Typography>
                </Paper>
              ) : (
                <Grid container spacing={1.5}>
                  {data.sites.map((site, i) => (
                    <Grid
                      size={{ xs: 12, sm: 6, md: 4 }}
                      key={site.id}
                      sx={fadeUpSx(i, { stepMs: 40, durationS: 0.35 })}
                    >
                      <SiteCard site={site} onClick={() => navigate(`/alerts?site_id=${site.id}`)} />
                    </Grid>
                  ))}
                </Grid>
              )}
            </Box>

            {/* Right — alert feed + guards */}
            <Box sx={{ width: 300, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 1.5, minHeight: 0 }}>
              <Box sx={{ flex: 1, minHeight: 0 }}>
                <AlertFeed alerts={data?.recent_alerts ?? []} onSelect={setSelectedAlert} />
              </Box>
              <GuardsBoard guards={data?.guards ?? []} />
            </Box>
          </Box>
        </>
      )}

      {selectedAlert && (
        <AlertResponseDialog
          alert={selectedAlert}
          onClose={() => setSelectedAlert(null)}
          onResolved={() => {
            qc.invalidateQueries({ queryKey: ['cc-overview'] })
            setSelectedAlert(null)
          }}
        />
      )}

      {/* spinning animation keyframe */}
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </Box>
  )
}

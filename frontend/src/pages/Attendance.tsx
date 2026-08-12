/**
 * Live Attendance — the command office's board.
 *
 * WHAT THIS SCREEN IS FOR
 *   One officer watching every site at once, answering three questions in
 *   this order: is anything wrong → who and where → can I reach them. The
 *   layout follows that order top to bottom: company totals, then a legend
 *   and filters, then a card per site holding a card per guard.
 *
 * WHY CARDS AND NOT A TABLE
 *   A table is for reading rows one at a time. This screen is scanned, not
 *   read — an officer should catch a red card in peripheral vision across a
 *   wall-mounted monitor without parsing any text. Cards give each guard a
 *   fixed-size, colour-carrying target; sites give the eye somewhere to stop.
 *
 * EVERY ROSTERED GUARD STAYS ON THE BOARD
 *   Including the ones who never checked in — an absence is the single most
 *   important thing here, and a row that only appears on check-in can never
 *   show it.
 *
 * COLOUR IS NEVER THE ONLY SIGNAL
 *   Each status carries an icon and a written label as well. Roughly one man
 *   in twelve has some colour-vision deficiency, and a security board that
 *   only works for the other eleven is not a security board.
 *
 * THE SERVER OWNS "LATE"
 *   Status and the grace period both arrive computed (monitor_status,
 *   grace_minutes). This page previously re-derived overdue from a hardcoded
 *   5-minute guess, which could disagree with the violation the backend had
 *   already written against the same shift.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Box, Typography, Chip, Select, MenuItem, FormControl, InputLabel, Skeleton,
  Button, Divider, IconButton, Tooltip, Avatar, Dialog, DialogTitle,
  DialogContent, TextField, InputAdornment, Badge,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AccessTimeIcon from '@mui/icons-material/AccessTime'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import EventBusyIcon from '@mui/icons-material/EventBusy'
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty'
import PauseCircleOutlineIcon from '@mui/icons-material/PauseCircleOutlined'
import ScheduleIcon from '@mui/icons-material/Schedule'
import LogoutIcon from '@mui/icons-material/Logout'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import SearchIcon from '@mui/icons-material/Search'
import CloseIcon from '@mui/icons-material/Close'
import CheckIcon from '@mui/icons-material/Check'
import FullscreenIcon from '@mui/icons-material/Fullscreen'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import PhoneInTalkIcon from '@mui/icons-material/PhoneInTalk'
import PersonIcon from '@mui/icons-material/Person'
import PlaceIcon from '@mui/icons-material/Place'
import GpsOffIcon from '@mui/icons-material/GpsOff'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getLiveAttendance, getGuardRecentAttendance, listCorrections,
  approveCorrection, rejectCorrection, checkinPhotoUrl,
  type LiveAttendanceShift, type LiveAttendanceSite, type MonitorStatus,
} from '@/api/attendance'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { fadeUpSx, useCountUp } from '@/lib/motion'
import { openAttendanceWindow } from '@/lib/attendanceWindow'
import { useAuthStore } from '@/store/auth'
import { useKioskToggle } from '@/hooks/useKioskToggle'

// ── Status vocabulary ────────────────────────────────────────────────────────

interface StatusMeta {
  label: string
  color: string
  icon: React.ReactNode
  /** Pulses and counts toward the site's alert badge. */
  attention?: boolean
  help: string
}

const STATUS_META: Record<MonitorStatus, StatusMeta> = {
  on_time: {
    label: 'On Time', color: '#00E396', icon: <CheckCircleIcon sx={{ fontSize: 14 }} />,
    help: 'Checked in within the grace period',
  },
  late: {
    label: 'Late', color: '#FF9800', icon: <AccessTimeIcon sx={{ fontSize: 14 }} />,
    attention: true, help: 'Checked in, but after the grace period',
  },
  on_break: {
    label: 'On Break', color: '#6C63FF', icon: <PauseCircleOutlineIcon sx={{ fontSize: 14 }} />,
    help: 'On duty, currently on a recorded break',
  },
  not_reported: {
    label: 'Not Reported', color: '#FF4560', icon: <ErrorIcon sx={{ fontSize: 14 }} />,
    attention: true, help: 'Duty started and the grace period has passed with no check-in',
  },
  awaiting: {
    label: 'Due Now', color: '#FFC107', icon: <HourglassEmptyIcon sx={{ fontSize: 14 }} />,
    help: 'Duty has started — still inside the grace period',
  },
  not_yet_on_duty: {
    label: 'Not Yet On Duty', color: '#8B92A8', icon: <ScheduleIcon sx={{ fontSize: 14 }} />,
    help: 'Rostered later today; nothing expected yet',
  },
  on_leave: {
    label: 'Approved Leave', color: '#00D9C0', icon: <EventBusyIcon sx={{ fontSize: 14 }} />,
    help: 'Excused — approved leave covers today',
  },
  checked_out: {
    label: 'Checked Out', color: '#5A6178', icon: <LogoutIcon sx={{ fontSize: 14 }} />,
    help: 'Shift completed',
  },
}

const EMPLOYMENT_META: Record<string, { label: string; color: string }> = {
  full_time: { label: 'Permanent', color: '#00D9C0' },
  part_time: { label: 'Part-Time', color: '#6C63FF' },
  contract:  { label: 'Day-Basis', color: '#FF9800' },
}

function employmentMeta(t: string | null) {
  return (t && EMPLOYMENT_META[t]) || { label: 'Type not set', color: '#5A6178' }
}

// ── Small helpers ────────────────────────────────────────────────────────────

const fmtTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '—'

const initials = (name: string | null) =>
  (name ?? '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?'

function hexToRgb(hex: string) {
  const h = hex.replace('#', '')
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)].join(',')
}

/** "just now" / "3 min ago" — a stale board must look stale. */
function relativeAge(iso: string | undefined, nowMs: number) {
  if (!iso) return '—'
  const secs = Math.max(0, Math.round((nowMs - new Date(iso).getTime()) / 1000))
  if (secs < 20) return 'just now'
  if (secs < 90) return `${secs}s ago`
  return `${Math.round(secs / 60)} min ago`
}

// ── Presentational pieces ────────────────────────────────────────────────────

/** Colour + icon + words. Never colour alone. */
function StatusBadge({ status, size = 'sm' }: { status: MonitorStatus; size?: 'sm' | 'md' }) {
  const m = STATUS_META[status]
  return (
    <Box
      sx={{
        display: 'inline-flex', alignItems: 'center', gap: 0.5,
        px: size === 'md' ? 1.25 : 0.75, py: size === 'md' ? 0.5 : 0.25,
        borderRadius: '999px', flexShrink: 0,
        background: `rgba(${hexToRgb(m.color)},0.16)`,
        border: `1px solid rgba(${hexToRgb(m.color)},0.5)`,
        color: m.color,
        fontSize: size === 'md' ? '0.74rem' : '0.66rem',
        fontWeight: 700, lineHeight: 1.2, whiteSpace: 'nowrap',
      }}
    >
      {m.icon}{m.label}
    </Box>
  )
}

function EmploymentBadge({ type }: { type: string | null }) {
  const m = employmentMeta(type)
  return (
    <Box
      sx={{
        display: 'inline-block', px: 0.75, py: 0.15, borderRadius: '4px',
        fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.04em',
        textTransform: 'uppercase', whiteSpace: 'nowrap',
        color: m.color, border: `1px solid rgba(${hexToRgb(m.color)},0.45)`,
        background: `rgba(${hexToRgb(m.color)},0.10)`,
      }}
    >
      {m.label}
    </Box>
  )
}

/** One company-wide figure. Clickable — opens the drill-down. */
function StatTile({ label, value, color, icon, active, onClick, index }: {
  label: string; value: number; color: string; icon: React.ReactNode
  active: boolean; onClick: () => void; index: number
}) {
  const shown = useCountUp(value)
  return (
    <GlassCard
      onClick={onClick}
      sx={{
        p: 1.5, cursor: 'pointer', minWidth: 0,
        border: `1px solid rgba(${hexToRgb(color)},${active ? 0.9 : 0.28})`,
        background: `linear-gradient(135deg, rgba(${hexToRgb(color)},${active ? 0.22 : 0.09}) 0%, transparent 100%)`,
        transition: 'transform 0.16s, border-color 0.16s, background 0.16s',
        '&:hover': { transform: 'translateY(-2px)', borderColor: `rgba(${hexToRgb(color)},0.85)` },
        ...fadeUpSx(index),
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.25 }}>
        <Box sx={{ color, display: 'flex' }}>{icon}</Box>
        <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.66rem', lineHeight: 1.1 }}>
          {label}
        </Typography>
      </Stack>
      <Typography sx={{ fontSize: '1.9rem', fontWeight: 800, lineHeight: 1, color }}>
        {shown}
      </Typography>
    </GlassCard>
  )
}

/** One guard. Fixed size so a wall of them scans evenly. */
function GuardCard({ g, token, onOpen }: {
  g: LiveAttendanceShift; token: string | null; onOpen: () => void
}) {
  const m = STATUS_META[g.monitor_status]
  const photo = g.check_in_photo_path ? checkinPhotoUrl(g.id, 'check_in', token) : null
  return (
    <Box
      onClick={onOpen}
      sx={{
        p: 1.25, borderRadius: '12px', cursor: 'pointer', minWidth: 0,
        background: `linear-gradient(135deg, rgba(${hexToRgb(m.color)},0.10) 0%, rgba(255,255,255,0.02) 100%)`,
        border: `1px solid rgba(${hexToRgb(m.color)},0.45)`,
        // Left spine repeats the status colour as a shape, which survives
        // being seen from across a room better than a small badge does.
        borderLeft: `4px solid ${m.color}`,
        transition: 'transform 0.15s, box-shadow 0.15s',
        '&:hover': { transform: 'translateY(-2px)', boxShadow: `0 6px 20px rgba(${hexToRgb(m.color)},0.28)` },
        ...(m.attention ? {
          animation: 'attnPulse 2.4s ease-in-out infinite',
          '@keyframes attnPulse': {
            '0%,100%': { boxShadow: `0 0 0 0 rgba(${hexToRgb(m.color)},0.0)` },
            '50%':     { boxShadow: `0 0 14px 2px rgba(${hexToRgb(m.color)},0.45)` },
          },
          '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
        } : {}),
      }}
    >
      <Stack direction="row" spacing={1.25} alignItems="flex-start">
        <Avatar
          src={photo ?? undefined}
          sx={{ width: 42, height: 42, flexShrink: 0, bgcolor: `rgba(${hexToRgb(m.color)},0.25)`,
                color: m.color, fontSize: '0.85rem', fontWeight: 700 }}
        >
          {initials(g.guard_name)}
        </Avatar>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="body2" noWrap sx={{ fontWeight: 700 }} title={g.guard_name ?? undefined}>
            {g.guard_name ?? 'Unassigned'}
          </Typography>
          <Typography variant="caption" noWrap sx={{ display: 'block', color: 'text.secondary', fontSize: '0.66rem' }}>
            {g.guard_phone || 'No contact number'}
          </Typography>
          <Stack direction="row" spacing={0.5} sx={{ mt: 0.5, flexWrap: 'wrap', gap: 0.5 }}>
            <StatusBadge status={g.monitor_status} />
            <EmploymentBadge type={g.employment_type} />
          </Stack>
        </Box>
      </Stack>

      <Divider sx={{ my: 1, borderColor: 'rgba(255,255,255,0.07)' }} />

      <Stack direction="row" justifyContent="space-between" sx={{ fontSize: '0.66rem' }}>
        <Box>
          <Typography variant="caption" sx={{ color: 'text.disabled', display: 'block', fontSize: '0.58rem' }}>
            ROSTERED
          </Typography>
          <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
            {fmtTime(g.scheduled_start)}–{fmtTime(g.scheduled_end)}
          </Typography>
        </Box>
        <Box sx={{ textAlign: 'right' }}>
          <Typography variant="caption" sx={{ color: 'text.disabled', display: 'block', fontSize: '0.58rem' }}>
            CHECK-IN
          </Typography>
          <Typography variant="caption" sx={{ fontFamily: 'monospace', color: g.actual_start ? m.color : 'text.disabled' }}>
            {g.actual_start ? fmtTime(g.actual_start) : 'not received'}
          </Typography>
        </Box>
      </Stack>

      {/* Only surfaced when there is something to say — a card that always
          carries a warning row teaches people to ignore the warning row. */}
      {(g.is_late || g.check_in_is_mock_location || g.is_within_geofence === false) && (
        <Stack direction="row" spacing={0.5} sx={{ mt: 0.75, flexWrap: 'wrap', gap: 0.5 }}>
          {g.is_late && g.late_minutes != null && (
            <Chip size="small" label={`${g.late_minutes}m late`} sx={{ height: 17, fontSize: '0.58rem' }} color="warning" variant="outlined" />
          )}
          {g.is_within_geofence === false && (
            <Tooltip title="Checked in outside the site geofence">
              <Chip size="small" icon={<PlaceIcon sx={{ fontSize: 11 }} />} label="Off-site"
                    sx={{ height: 17, fontSize: '0.58rem' }} color="warning" variant="outlined" />
            </Tooltip>
          )}
          {g.check_in_is_mock_location && (
            <Tooltip title="Mock GPS reported by the device">
              <Chip size="small" icon={<GpsOffIcon sx={{ fontSize: 11 }} />} label="Mock GPS"
                    sx={{ height: 17, fontSize: '0.58rem' }} color="error" variant="outlined" />
            </Tooltip>
          )}
        </Stack>
      )}
    </Box>
  )
}

/** One site: a compact scoreboard, then its guards. */
function SiteCard({ site, token, onOpenGuard, index }: {
  site: LiveAttendanceSite; token: string | null
  onOpenGuard: (g: LiveAttendanceShift) => void; index: number
}) {
  const c = site.counts
  const problems = c.not_reported + c.late
  // "Unusually low attendance" made concrete: over half the roster missing,
  // on a site rostering enough people for that to mean anything.
  const thin = c.rostered >= 3 && c.not_reported / c.rostered > 0.5

  return (
    <GlassCard sx={{ p: 1.75, ...fadeUpSx(index) }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.25, flexWrap: 'wrap' }}>
        <Badge
          color="error" badgeContent={problems} invisible={problems === 0}
          sx={{ '& .MuiBadge-badge': { fontSize: '0.6rem', height: 16, minWidth: 16 } }}
        >
          <Typography variant="subtitle2" sx={{ fontWeight: 800, pr: problems ? 1 : 0 }}>
            {site.site_name}
          </Typography>
        </Badge>
        <Box sx={{ flex: 1 }} />
        <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', gap: 0.5 }}>
          <Chip size="small" variant="outlined" label={`${c.rostered} rostered`} sx={{ height: 19, fontSize: '0.62rem' }} />
          <Chip size="small" variant="outlined" color="success" label={`${c.checked_in} in`} sx={{ height: 19, fontSize: '0.62rem' }} />
          {c.late > 0 && <Chip size="small" variant="outlined" color="warning" label={`${c.late} late`} sx={{ height: 19, fontSize: '0.62rem' }} />}
          {c.not_reported > 0 && <Chip size="small" variant="outlined" color="error" label={`${c.not_reported} missing`} sx={{ height: 19, fontSize: '0.62rem' }} />}
          {c.on_leave > 0 && <Chip size="small" variant="outlined" label={`${c.on_leave} leave`} sx={{ height: 19, fontSize: '0.62rem' }} />}
          {c.not_yet_on_duty > 0 && <Chip size="small" variant="outlined" label={`${c.not_yet_on_duty} later`} sx={{ height: 19, fontSize: '0.62rem' }} />}
        </Stack>
      </Stack>

      {thin && (
        <Stack direction="row" spacing={0.75} alignItems="center"
               sx={{ mb: 1.25, px: 1, py: 0.6, borderRadius: 1,
                     background: 'rgba(255,69,96,0.12)', border: '1px solid rgba(255,69,96,0.4)' }}>
          <WarningAmberIcon sx={{ fontSize: 15, color: '#FF4560' }} />
          <Typography variant="caption" sx={{ color: '#FF4560', fontWeight: 700 }}>
            {c.not_reported} of {c.rostered} rostered guards have not reported
          </Typography>
        </Stack>
      )}

      <Box sx={{ display: 'grid', gap: 1.25,
                 gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>
        {site.guards.map((g) => (
          <GuardCard key={g.id} g={g} token={token} onOpen={() => onOpenGuard(g)} />
        ))}
      </Box>
    </GlassCard>
  )
}

// ── Dialogs ──────────────────────────────────────────────────────────────────

/** Drill-down behind a company statistic, with the site split. */
function StatDetailDialog({ open, title, guards, token, onClose, onOpenGuard }: {
  open: boolean; title: string; guards: LiveAttendanceShift[]
  token: string | null; onClose: () => void; onOpenGuard: (g: LiveAttendanceShift) => void
}) {
  const bySite = useMemo(() => {
    const m = new Map<string, LiveAttendanceShift[]>()
    guards.forEach((g) => {
      const k = g.site_name ?? 'Unassigned site'
      m.set(k, [...(m.get(k) ?? []), g])
    })
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [guards])

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Box sx={{ flex: 1 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>{title}</Typography>
          <Typography variant="caption" color="text.secondary">
            {guards.length} guard{guards.length === 1 ? '' : 's'} across {bySite.length} site{bySite.length === 1 ? '' : 's'}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close"><CloseIcon fontSize="small" /></IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {guards.length === 0 ? (
          <Typography color="text.secondary" sx={{ py: 3, textAlign: 'center' }}>
            No guards in this category right now.
          </Typography>
        ) : bySite.map(([siteName, rows]) => (
          <Box key={siteName} sx={{ mb: 2.5 }}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="caption" sx={{ fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                {siteName}
              </Typography>
              <Chip size="small" label={rows.length} sx={{ height: 17, fontSize: '0.6rem' }} />
            </Stack>
            <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>
              {rows.map((g) => (
                <GuardCard key={g.id} g={g} token={token} onOpen={() => onOpenGuard(g)} />
              ))}
            </Box>
          </Box>
        ))}
      </DialogContent>
    </Dialog>
  )
}

function Detail({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'baseline', py: 0.45 }}>
      <Typography variant="caption" sx={{ minWidth: 118, flexShrink: 0, color: 'text.secondary',
                                          textTransform: 'uppercase', letterSpacing: '0.06em',
                                          fontSize: '0.61rem', fontWeight: 700 }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ minWidth: 0, wordBreak: 'break-word' }}>{value}</Typography>
    </Box>
  )
}

/** Everything needed to verify one guard by hand, and a way to ring them. */
function GuardDetailDialog({ g, token, onClose }: {
  g: LiveAttendanceShift | null; token: string | null; onClose: () => void
}) {
  const { data: recent = [] } = useQuery({
    queryKey: ['guard-recent', g?.guard_user_id],
    queryFn: () => getGuardRecentAttendance(g!.guard_user_id),
    enabled: Boolean(g?.guard_user_id),
  })
  if (!g) return null
  const m = STATUS_META[g.monitor_status]
  const photo = g.check_in_photo_path ? checkinPhotoUrl(g.id, 'check_in', token) : null

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 800 }} noWrap>{g.guard_name ?? 'Unassigned'}</Typography>
          <Typography variant="caption" color="text.secondary">{g.designation || 'Security Guard'}</Typography>
        </Box>
        <StatusBadge status={g.monitor_status} size="md" />
        <IconButton size="small" onClick={onClose} aria-label="Close"><CloseIcon fontSize="small" /></IconButton>
      </DialogTitle>
      <DialogContent dividers>
        <Box sx={{ display: 'flex', gap: 2.5, flexDirection: { xs: 'column', sm: 'row' } }}>
          <Box sx={{ flexShrink: 0, textAlign: 'center' }}>
            <Avatar
              src={photo ?? undefined}
              variant="rounded"
              sx={{ width: 168, height: 168, mx: 'auto', fontSize: '3rem', fontWeight: 700,
                    bgcolor: `rgba(${hexToRgb(m.color)},0.22)`, color: m.color,
                    border: `2px solid rgba(${hexToRgb(m.color)},0.5)` }}
            >
              {initials(g.guard_name)}
            </Avatar>
            <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 0.75 }}>
              {photo ? 'Check-in selfie' : 'No check-in photo yet'}
            </Typography>
            {g.guard_phone && (
              <Button
                fullWidth size="small" variant="contained" startIcon={<PhoneInTalkIcon />}
                href={`tel:${g.guard_phone}`} sx={{ mt: 1.25 }}
              >
                Call
              </Button>
            )}
          </Box>

          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Detail label="Contact" value={g.guard_phone || '—'} />
            <Detail label="Guard ID" value={<Box component="span" sx={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>{g.guard_user_id}</Box>} />
            <Detail label="Employment" value={<EmploymentBadge type={g.employment_type} />} />
            <Detail label="Site" value={g.site_name ?? 'Unassigned'} />
            <Detail label="Shift" value={g.shift_type ? g.shift_type[0].toUpperCase() + g.shift_type.slice(1) : 'Not set'} />
            <Divider sx={{ my: 1, borderColor: 'rgba(255,255,255,0.08)' }} />
            <Detail label="Rostered" value={`${fmtTime(g.scheduled_start)} – ${fmtTime(g.scheduled_end)}`} />
            <Detail label="Checked in" value={g.actual_start ? fmtTime(g.actual_start) : 'Not received'} />
            <Detail label="Checked out" value={g.actual_end ? fmtTime(g.actual_end) : '—'} />
            {g.is_late && <Detail label="Late by" value={`${g.late_minutes ?? '?'} minutes`} />}
            {g.on_leave && <Detail label="Leave" value={g.leave_reason || 'Approved leave'} />}
            <Divider sx={{ my: 1, borderColor: 'rgba(255,255,255,0.08)' }} />
            {/* There is no separate "source" column — attendance arrives from
                the guard's mobile app, so the honest thing to show is the
                evidence that came with it. */}
            <Detail
              label="Check-in source"
              value={g.actual_start
                ? `Mobile app${g.check_in_photo_path ? ' · selfie' : ''}${
                    g.is_within_geofence === true ? ' · inside geofence'
                    : g.is_within_geofence === false ? ' · OUTSIDE geofence' : ''}${
                    g.check_in_is_mock_location ? ' · MOCK GPS' : ''}`
                : 'No check-in received'}
            />
            {g.check_in_liveness_score != null && (
              <Detail label="Liveness" value={g.check_in_liveness_score.toFixed(2)} />
            )}
          </Box>
        </Box>

        <Divider sx={{ my: 2, borderColor: 'rgba(255,255,255,0.08)' }} />
        <Typography variant="caption" sx={{ fontWeight: 800, letterSpacing: '0.08em',
                                            textTransform: 'uppercase', color: 'text.secondary' }}>
          Recent attendance
        </Typography>
        {recent.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>No earlier shifts recorded.</Typography>
        ) : (
          <Stack spacing={0.5} sx={{ mt: 1 }}>
            {recent.map((r) => (
              <Stack key={r.id} direction="row" spacing={1.5} alignItems="center"
                     sx={{ px: 1, py: 0.5, borderRadius: 1, background: 'rgba(255,255,255,0.03)' }}>
                <Typography variant="caption" sx={{ minWidth: 92, fontFamily: 'monospace' }}>
                  {new Date(r.scheduled_start).toLocaleDateString()}
                </Typography>
                <Typography variant="caption" sx={{ flex: 1, color: 'text.secondary' }} noWrap>
                  {r.site_name ?? '—'}
                </Typography>
                <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                  {fmtTime(r.scheduled_start)} → {r.actual_start ? fmtTime(r.actual_start) : 'no show'}
                </Typography>
                {r.is_late
                  ? <Chip size="small" color="warning" variant="outlined" label={`${r.late_minutes ?? '?'}m late`} sx={{ height: 17, fontSize: '0.58rem' }} />
                  : r.actual_start
                    ? <Chip size="small" color="success" variant="outlined" label="on time" sx={{ height: 17, fontSize: '0.58rem' }} />
                    : <Chip size="small" color="error" variant="outlined" label="missed" sx={{ height: 17, fontSize: '0.58rem' }} />}
              </Stack>
            ))}
          </Stack>
        )}
      </DialogContent>
    </Dialog>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

type StatKey = 'rostered' | 'on_duty' | 'checked_in' | 'reported' | 'late'
  | 'not_reported' | 'not_yet_on_duty' | 'on_leave'

/** Which guards sit behind each headline figure. Defined once so the tile and
 *  its drill-down can never disagree about what the number meant. */
const STAT_FILTER: Record<StatKey, (g: LiveAttendanceShift) => boolean> = {
  rostered:        () => true,
  on_duty:         (g) => g.status === 'active',
  checked_in:      (g) => g.actual_start != null && g.actual_end == null,
  reported:        (g) => g.actual_start != null,
  late:            (g) => g.is_late,
  not_reported:    (g) => g.monitor_status === 'not_reported',
  not_yet_on_duty: (g) => g.monitor_status === 'not_yet_on_duty',
  on_leave:        (g) => g.monitor_status === 'on_leave',
}

const STAT_TILES: { key: StatKey; label: string; color: string; icon: React.ReactNode }[] = [
  { key: 'rostered',        label: 'Total Rostered',   color: '#6C63FF', icon: <PersonIcon sx={{ fontSize: 16 }} /> },
  { key: 'on_duty',         label: 'Currently On Duty', color: '#00E396', icon: <CheckCircleIcon sx={{ fontSize: 16 }} /> },
  { key: 'checked_in',      label: 'Checked In',       color: '#00D9C0', icon: <CheckIcon sx={{ fontSize: 16 }} /> },
  { key: 'reported',        label: 'Reported Today',   color: '#2196F3', icon: <CheckCircleIcon sx={{ fontSize: 16 }} /> },
  { key: 'late',            label: 'Late to Work',     color: '#FF9800', icon: <AccessTimeIcon sx={{ fontSize: 16 }} /> },
  { key: 'not_reported',    label: 'Not Reported',     color: '#FF4560', icon: <ErrorIcon sx={{ fontSize: 16 }} /> },
  { key: 'not_yet_on_duty', label: 'Not Yet On Duty',  color: '#8B92A8', icon: <ScheduleIcon sx={{ fontSize: 16 }} /> },
  { key: 'on_leave',        label: 'Approved Leave',   color: '#00D9C0', icon: <EventBusyIcon sx={{ fontSize: 16 }} /> },
]

export function AttendancePage() {
  const qc = useQueryClient()
  const token = useAuthStore((s) => s.accessToken)
  const { kiosk, toggleKiosk } = useKioskToggle()

  const [statusFilter, setStatusFilter] = useState<MonitorStatus | ''>('')
  const [employmentFilter, setEmploymentFilter] = useState('')
  const [shiftFilter, setShiftFilter] = useState('')
  const [siteFilter, setSiteFilter] = useState('')
  const [search, setSearch] = useState('')
  const [statDrill, setStatDrill] = useState<StatKey | null>(null)
  const [selectedGuard, setSelectedGuard] = useState<LiveAttendanceShift | null>(null)

  const { data: board, isLoading } = useQuery({
    queryKey: ['attendance-live'],
    queryFn: () => getLiveAttendance(),
    // WebSocket invalidation is the primary path; this is the safety net for a
    // dropped socket, so the board self-heals instead of silently freezing.
    refetchInterval: 60_000,
  })

  const { data: corrections = [] } = useQuery({
    queryKey: ['corrections', 'pending'],
    queryFn: () => listCorrections('pending'),
  })

  // Re-render on a slow tick purely so "Last updated" ages visibly.
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 15_000)
    return () => clearInterval(t)
  }, [])

  const { mutate: approve } = useMutation({
    mutationFn: (id: string) => approveCorrection(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['corrections'] }); qc.invalidateQueries({ queryKey: ['attendance-live'] }) },
  })
  const { mutate: reject } = useMutation({
    mutationFn: (id: string) => rejectCorrection(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['corrections'] }),
  })

  const matches = (g: LiveAttendanceShift) => {
    if (statusFilter && g.monitor_status !== statusFilter) return false
    if (employmentFilter && g.employment_type !== employmentFilter) return false
    if (shiftFilter && g.shift_type !== shiftFilter) return false
    if (siteFilter && String(g.site_id ?? '') !== siteFilter) return false
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      const hay = `${g.guard_name ?? ''} ${g.guard_phone ?? ''}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  }

  const filteredSites = useMemo(() => {
    if (!board) return []
    return board.sites
      .map((s) => ({ ...s, guards: s.guards.filter(matches) }))
      .filter((s) => s.guards.length > 0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [board, statusFilter, employmentFilter, shiftFilter, siteFilter, search])

  const anyFilter = Boolean(statusFilter || employmentFilter || shiftFilter || siteFilter || search)
  const clearFilters = () => {
    setStatusFilter(''); setEmploymentFilter(''); setShiftFilter(''); setSiteFilter(''); setSearch('')
  }

  const drillGuards = statDrill && board ? board.shifts.filter(STAT_FILTER[statDrill]) : []
  const drillLabel = STAT_TILES.find((t) => t.key === statDrill)?.label ?? ''

  return (
    <Box sx={{ p: kiosk ? 1.5 : 3 }}>
      <Stack direction="row" alignItems="flex-start" sx={{ mb: 1 }}>
        <Box sx={{ flex: 1 }}>
          <PageHeader title="Live Attendance" subtitle="Company-wide guard attendance, updating as check-ins arrive" />
        </Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <Tooltip title="Last time this board heard from the server">
            <Chip
              size="small" variant="outlined"
              label={`Updated ${relativeAge(board?.generated_at, nowMs)}`}
              sx={{ height: 24, fontSize: '0.66rem' }}
            />
          </Tooltip>
          {!kiosk && (
            <Tooltip title="Full Screen">
              <IconButton size="small" onClick={toggleKiosk}><FullscreenIcon fontSize="small" /></IconButton>
            </Tooltip>
          )}
          <Tooltip title="Open in a separate monitor window">
            <IconButton size="small" onClick={() => openAttendanceWindow()}><OpenInNewIcon fontSize="small" /></IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      {/* ── Company statistics ─────────────────────────────────────────── */}
      <Box sx={{ display: 'grid', gap: 1.25, mb: 2,
                 gridTemplateColumns: 'repeat(auto-fit, minmax(148px, 1fr))' }}>
        {STAT_TILES.map((t, i) => (
          <StatTile
            key={t.key} label={t.label} value={board?.summary[t.key] ?? 0}
            color={t.color} icon={t.icon} index={i}
            active={statDrill === t.key}
            onClick={() => setStatDrill(t.key)}
          />
        ))}
      </Box>

      {/* ── Legend + filters ───────────────────────────────────────────── */}
      <GlassCard sx={{ p: 1.5, mb: 2 }}>
        <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1, mb: 1.5 }}>
          {(Object.keys(STATUS_META) as MonitorStatus[]).map((s) => (
            <Tooltip key={s} title={STATUS_META[s].help}>
              <Box
                onClick={() => setStatusFilter(statusFilter === s ? '' : s)}
                sx={{ cursor: 'pointer', opacity: statusFilter && statusFilter !== s ? 0.4 : 1,
                       transition: 'opacity 0.15s' }}
              >
                <StatusBadge status={s} />
              </Box>
            </Tooltip>
          ))}
        </Stack>

        <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1 }}>
          <TextField
            size="small" placeholder="Search name or number"
            value={search} onChange={(e) => setSearch(e.target.value)}
            sx={{ minWidth: 210 }}
            slotProps={{ input: { startAdornment: (
              <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
            ) } }}
          />
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Site</InputLabel>
            <Select label="Site" value={siteFilter} onChange={(e) => setSiteFilter(e.target.value)}>
              <MenuItem value="">All sites</MenuItem>
              {(board?.sites ?? []).map((s) => (
                <MenuItem key={String(s.site_id)} value={String(s.site_id ?? '')}>{s.site_name}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Status</InputLabel>
            <Select label="Status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as MonitorStatus | '')}>
              <MenuItem value="">All statuses</MenuItem>
              {(Object.keys(STATUS_META) as MonitorStatus[]).map((s) => (
                <MenuItem key={s} value={s}>{STATUS_META[s].label}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Employment</InputLabel>
            <Select label="Employment" value={employmentFilter} onChange={(e) => setEmploymentFilter(e.target.value)}>
              <MenuItem value="">All types</MenuItem>
              {Object.entries(EMPLOYMENT_META).map(([k, v]) => (
                <MenuItem key={k} value={k}>{v.label}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 130 }}>
            <InputLabel>Shift</InputLabel>
            <Select label="Shift" value={shiftFilter} onChange={(e) => setShiftFilter(e.target.value)}>
              <MenuItem value="">All shifts</MenuItem>
              <MenuItem value="day">Day</MenuItem>
              <MenuItem value="night">Night</MenuItem>
              <MenuItem value="split">Split</MenuItem>
            </Select>
          </FormControl>
          {anyFilter && (
            <Button size="small" variant="outlined" onClick={clearFilters}>Show All</Button>
          )}
        </Stack>
      </GlassCard>

      {/* ── Site grid ──────────────────────────────────────────────────── */}
      {isLoading ? (
        <GlassCard sx={{ p: 2 }}><Skeleton height={120} /></GlassCard>
      ) : filteredSites.length === 0 ? (
        <GlassCard sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="text.secondary">
            {anyFilter ? 'No guards match these filters.' : 'No guards rostered today.'}
          </Typography>
          {anyFilter && <Button size="small" sx={{ mt: 1 }} onClick={clearFilters}>Show All</Button>}
        </GlassCard>
      ) : (
        <Stack spacing={2}>
          {filteredSites.map((s, i) => (
            <SiteCard key={String(s.site_id) + s.site_name} site={s} token={token}
                      onOpenGuard={setSelectedGuard} index={i} />
          ))}
        </Stack>
      )}

      {/* ── Correction queue (unchanged behaviour) ─────────────────────── */}
      <PermissionGuard permission="attendance:manage">
        {corrections.length > 0 && (
          <GlassCard sx={{ p: 2, mt: 2 }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 800, mb: 1 }}>
              Pending attendance corrections ({corrections.length})
            </Typography>
            <Stack spacing={0.75}>
              {corrections.map((c) => (
                <Stack key={c.id} direction="row" spacing={1.5} alignItems="center"
                       sx={{ px: 1, py: 0.75, borderRadius: 1, background: 'rgba(255,255,255,0.03)' }}>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{c.guard_name}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {c.site_name ?? '—'} · {c.reason}
                    </Typography>
                  </Box>
                  <Tooltip title="Approve"><IconButton size="small" color="success" onClick={() => approve(c.id)}><CheckIcon fontSize="small" /></IconButton></Tooltip>
                  <Tooltip title="Reject"><IconButton size="small" color="error" onClick={() => reject(c.id)}><CloseIcon fontSize="small" /></IconButton></Tooltip>
                </Stack>
              ))}
            </Stack>
          </GlassCard>
        )}
      </PermissionGuard>

      <StatDetailDialog
        open={statDrill !== null} title={drillLabel} guards={drillGuards} token={token}
        onClose={() => setStatDrill(null)} onOpenGuard={(g) => { setStatDrill(null); setSelectedGuard(g) }}
      />
      <GuardDetailDialog g={selectedGuard} token={token} onClose={() => setSelectedGuard(null)} />
    </Box>
  )
}

export default AttendancePage

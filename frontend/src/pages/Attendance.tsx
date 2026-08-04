/**
 * Attendance (ShiftSecure Phase 2A) — live check-in/out monitor across all
 * sites, refreshed in real time via WebSocket (attendance_status_changed),
 * plus the correction-request approval queue.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Box,
  Typography,
  Chip,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Skeleton,
  Button,
  Divider,
  IconButton,
  Tooltip,
  Avatar,
  Dialog,
  DialogContent,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import LoginIcon from '@mui/icons-material/Login'
import PauseCircleOutlineIcon from '@mui/icons-material/PauseCircleOutlined'
import AccessTimeIcon from '@mui/icons-material/AccessTime'
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty'
import CheckIcon from '@mui/icons-material/Check'
import CloseIcon from '@mui/icons-material/Close'
import FullscreenIcon from '@mui/icons-material/Fullscreen'
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import PersonIcon from '@mui/icons-material/Person'
import PhoneIcon from '@mui/icons-material/Phone'
import PhoneInTalkIcon from '@mui/icons-material/PhoneInTalk'
import NotificationsActiveIcon from '@mui/icons-material/NotificationsActive'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getLiveAttendance, listCorrections, approveCorrection, rejectCorrection,
  checkinPhotoUrl, type LiveAttendanceShift,
} from '@/api/attendance'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { fadeUpSx, useCountUp } from '@/lib/motion'
import { openAttendanceWindow } from '@/lib/attendanceWindow'
import { useAuthStore } from '@/store/auth'
import { useFocusModeStore } from '@/store/focusMode'

// Small buffer past scheduled_start before a not-yet-checked-in guard escalates
// from calm "Scheduled" to alarming "Not Checked In".
const OVERDUE_GRACE_MIN = 5

type RowState = {
  label: string
  color: string
  pulse: boolean       // pulsing glow — draws the eye to something needing action
  attention: boolean   // guard should be contacted (late, or overdue-not-checked-in)
  sub?: string         // secondary note, e.g. "was 12m late"
}

/**
 * Derive the display state for one shift row. Richer than the raw live_status:
 * distinguishes on-time vs. checked-in-late (keeps the green "present" signal
 * but preserves the lateness fact, which the raw status silently drops once
 * active), and splits not_started into calm "Scheduled" (before start) vs.
 * alarming "Not Checked In" (past start + grace).
 */
function deriveRowState(sh: LiveAttendanceShift): RowState {
  const start = sh.scheduled_start ? new Date(sh.scheduled_start).getTime() : null
  const overdue = start != null && Date.now() > start + OVERDUE_GRACE_MIN * 60_000
  switch (sh.live_status) {
    case 'checked_in':
      return sh.is_late
        ? { label: 'Checked In', color: '#00E396', pulse: false, attention: false, sub: sh.late_minutes ? `was ${sh.late_minutes}m late` : 'was late' }
        : { label: 'On Time', color: '#00E396', pulse: false, attention: false }
    case 'on_break':
      return { label: 'On Break', color: '#6C63FF', pulse: false, attention: false }
    case 'checked_out':
      return { label: 'Checked Out', color: '#8B92A8', pulse: false, attention: false }
    case 'late':
      return { label: 'Late', color: '#FF9800', pulse: true, attention: true, sub: sh.late_minutes ? `${sh.late_minutes}m late` : undefined }
    case 'not_started':
    default:
      return overdue
        ? { label: 'Not Checked In', color: '#FF4560', pulse: true, attention: true }
        : { label: 'Scheduled', color: '#8B92A8', pulse: false, attention: false }
  }
}

function overdueLabel(sh: LiveAttendanceShift): string {
  if (sh.late_minutes) return `${sh.late_minutes}m late`
  const start = sh.scheduled_start ? new Date(sh.scheduled_start).getTime() : null
  if (start == null) return 'overdue'
  const mins = Math.max(0, Math.floor((Date.now() - start) / 60_000))
  return mins > 0 ? `${mins}m overdue` : 'due now'
}

function hexToRgb(hex: string) {
  const m = hex.replace('#', '').match(/.{2}/g)
  return m ? m.map((v) => parseInt(v, 16)).join(',') : '108,99,255'
}

function KpiCard({ label, value, icon, color }: {
  label: string; value: number | undefined; icon: React.ReactNode; color: string
}) {
  const rgb = hexToRgb(color)
  const animatedValue = useCountUp(value)
  return (
    <GlassCard variant="glow" sx={{
      p: 2.5, position: 'relative', overflow: 'hidden',
      borderColor: `rgba(${rgb},0.18)`,
      '&:hover': { borderColor: `rgba(${rgb},0.35)` },
      '&::before': {
        content: '""', position: 'absolute', top: 0, left: 0, right: 0, height: '2px',
        background: `linear-gradient(90deg, transparent 0%, ${color} 50%, transparent 100%)`,
        opacity: 0.7,
      },
    }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
        <Box sx={{ minWidth: 0 }}>
          <Typography sx={{
            color: `rgba(${rgb},0.8)`, textTransform: 'uppercase', letterSpacing: '0.1em',
            fontSize: '0.62rem', fontWeight: 700, mb: 0.75,
          }}>
            {label}
          </Typography>
          {value === undefined ? (
            <Skeleton width={60} height={44} sx={{ bgcolor: `rgba(${rgb},0.08)` }} />
          ) : (
            <Typography sx={{
              fontWeight: 800, lineHeight: 1.1, fontSize: '2rem', color,
              fontFamily: '"Fira Code", monospace', letterSpacing: '-0.02em',
            }}>
              {animatedValue.toLocaleString()}
            </Typography>
          )}
        </Box>
        <Box sx={{
          width: 44, height: 44, borderRadius: '12px', background: `rgba(${rgb},0.12)`,
          border: `1px solid rgba(${rgb},0.22)`, display: 'flex', alignItems: 'center',
          justifyContent: 'center', flexShrink: 0, color, boxShadow: `0 0 16px rgba(${rgb},0.2)`,
          '& svg': { fontSize: 22 },
        }}>
          {icon}
        </Box>
      </Box>
    </GlassCard>
  )
}

function StatusChip({ state }: { state: RowState }) {
  const rgb = hexToRgb(state.color)
  return (
    <Stack direction="row" spacing={0.75} alignItems="center" sx={{ flexShrink: 0 }}>
      {state.sub && (
        <Typography variant="caption" sx={{ color: '#FF9800', fontWeight: 600, whiteSpace: 'nowrap' }}>
          {state.sub}
        </Typography>
      )}
      <Chip
        label={state.label}
        size="small"
        sx={{
          color: state.color,
          backgroundColor: `rgba(${rgb},0.14)`,
          border: `1px solid rgba(${rgb},0.3)`,
          fontWeight: 700,
          transition: 'background-color 0.25s, color 0.25s, border-color 0.25s',
          ...(state.pulse && { animation: 'att-pulse 1.6s ease-in-out infinite' }),
          '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
        }}
      />
    </Stack>
  )
}

const fmtTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '—'

function PhotoThumb({ url, label, onClick }: { url: string | null; label: string; onClick: () => void }) {
  if (!url) {
    return (
      <Avatar sx={{ width: 32, height: 32, bgcolor: 'rgba(255,255,255,0.08)' }}>
        <PersonIcon fontSize="small" sx={{ color: 'rgba(255,255,255,0.3)' }} />
      </Avatar>
    )
  }
  return (
    <Tooltip title={label}>
      <Avatar
        src={url}
        onClick={onClick}
        sx={{ width: 32, height: 32, cursor: 'pointer', border: '1px solid rgba(255,255,255,0.15)' }}
      />
    </Tooltip>
  )
}

export function AttendancePage() {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState('')
  const [kiosk, setKiosk] = useState(false)
  const setFocusMode = useFocusModeStore((s) => s.setFocusMode)
  const accessToken = useAuthStore((s) => s.accessToken)
  const [enlargedPhoto, setEnlargedPhoto] = useState<{ url: string; label: string } | null>(null)

  // Full-screen: same pattern as Live Wall — Electron gets true kiosk mode,
  // browsers get the Fullscreen API; both flip focus-mode so AppShell hides
  // its own sidebar/topbar (fullscreening the window alone doesn't hide it).
  // Branches on our own `kiosk` state, not document.fullscreenElement — if
  // requestFullscreen() is ever rejected (missing user gesture, browser
  // policy), fullscreenElement stays null while kiosk is already true, and
  // keying off fullscreenElement would re-enter instead of exit, leaving
  // the user stuck with no way to bring the chrome back via this button.
  const toggleKiosk = async () => {
    if (window.electronAPI?.setKiosk) {
      const now = await window.electronAPI.setKiosk(!kiosk)
      setKiosk(now)
      setFocusMode(now)
      return
    }
    if (kiosk) {
      setKiosk(false)
      setFocusMode(false)
      if (document.fullscreenElement) {
        try { await document.exitFullscreen() } catch { /* already exiting */ }
      }
      return
    }
    setKiosk(true)
    setFocusMode(true)
    try { await document.documentElement.requestFullscreen() } catch { /* focus mode still applies */ }
  }

  useEffect(() => {
    const sync = () => {
      if (!document.fullscreenElement) { setKiosk(false); setFocusMode(false) }
    }
    document.addEventListener('fullscreenchange', sync)
    return () => document.removeEventListener('fullscreenchange', sync)
  }, [setFocusMode])

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const { data: live, isLoading } = useQuery({
    queryKey: ['attendance-live', siteId],
    queryFn: () => getLiveAttendance(siteId || undefined),
    refetchInterval: 60_000, // fallback poll; WS invalidation is the primary refresh path
  })
  const { data: corrections = [] } = useQuery({
    queryKey: ['attendance-corrections', 'pending'],
    queryFn: () => listCorrections('pending'),
  })

  const { mutate: approve } = useMutation({
    mutationFn: (id: string) => approveCorrection(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['attendance-corrections'] })
      qc.invalidateQueries({ queryKey: ['attendance-live'] })
    },
  })
  const { mutate: reject } = useMutation({
    mutationFn: (id: string) => rejectCorrection(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['attendance-corrections'] }),
  })

  const bySite = useMemo(() => {
    const map = new Map<string, LiveAttendanceShift[]>()
    for (const sh of live?.shifts ?? []) {
      const site = sh.site_name ?? 'No site'
      if (!map.has(site)) map.set(site, [])
      map.get(site)!.push(sh)
    }
    return map
  }, [live])

  // Guards a command-centre officer should contact right now — late, or past
  // their scheduled start without checking in. Auto-clears as they check in
  // (the WS refresh re-derives this on every attendance_status_changed).
  const attentionList = useMemo(
    () => (live?.shifts ?? []).filter((sh) => deriveRowState(sh).attention),
    [live],
  )

  // Flash a green ring on a row the moment it transitions into checked_in, so a
  // control-room officer catches the check-in even glancing away. Compares the
  // previous vs. current live_status per shift id across WS-driven refreshes.
  const prevStatusRef = useRef<Map<string, string>>(new Map())
  const [flashIds, setFlashIds] = useState<Set<string>>(new Set())
  useEffect(() => {
    const prev = prevStatusRef.current
    const justCheckedIn: string[] = []
    for (const sh of live?.shifts ?? []) {
      const before = prev.get(sh.id)
      if (before && before !== 'checked_in' && sh.live_status === 'checked_in') {
        justCheckedIn.push(sh.id)
      }
      prev.set(sh.id, sh.live_status)
    }
    if (justCheckedIn.length === 0) return
    setFlashIds((s) => new Set([...s, ...justCheckedIn]))
    const t = setTimeout(() => {
      setFlashIds((s) => {
        const next = new Set(s)
        justCheckedIn.forEach((id) => next.delete(id))
        return next
      })
    }, 1800)
    return () => clearTimeout(t)
  }, [live])

  return (
    <Box>
      <PageHeader title="Attendance" subtitle="Live check-in/out monitoring across all sites" />

      {/* KPI Row */}
      <Box sx={{ display: 'flex', gap: 2, mb: 2.5, flexWrap: 'wrap' }}>
        {[
          { label: 'Checked In',  value: live?.summary.checked_in,  icon: <LoginIcon />,              color: '#00E396' },
          { label: 'On Break',    value: live?.summary.on_break,    icon: <PauseCircleOutlineIcon />, color: '#6C63FF' },
          { label: 'Late',        value: live?.summary.late,        icon: <AccessTimeIcon />,          color: '#FF9800' },
          { label: 'Not Started', value: live?.summary.not_started, icon: <HourglassEmptyIcon />,      color: '#8B92A8' },
        ].map((kpi, i) => (
          <Box key={kpi.label} sx={{ flex: '1 1 180px', minWidth: 0, ...fadeUpSx(i) }}>
            <KpiCard label={kpi.label} value={kpi.value} icon={kpi.icon} color={kpi.color} />
          </Box>
        ))}
      </Box>

      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2, flexWrap: 'wrap' }}>
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel>Site</InputLabel>
          <Select value={siteId} label="Site" onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">All Sites</MenuItem>
            {(sites as any[]).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </Select>
        </FormControl>
        <Tooltip title="Open this monitor in a new window — drag it to another monitor for multi-screen control-room use">
          <IconButton size="small" onClick={() => openAttendanceWindow()}>
            <OpenInNewIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={kiosk ? 'Exit full screen (Esc)' : 'Enter full screen for continuous monitoring'}>
          <Button
            size="small"
            variant={kiosk ? 'contained' : 'outlined'}
            startIcon={kiosk ? <FullscreenExitIcon /> : <FullscreenIcon />}
            onClick={toggleKiosk}
          >
            {kiosk ? 'Exit Full Screen' : 'Full Screen'}
          </Button>
        </Tooltip>
      </Stack>

      {/* Action Required — guards a command-centre officer should contact now.
          Reminder-first: surfaces who to call; the officer acts. Auto-clears
          as each guard checks in (re-derived on every WS refresh). */}
      {attentionList.length > 0 && (
        <GlassCard
          sx={{
            p: 2, mb: 2.5,
            border: '1px solid rgba(255,69,96,0.35)',
            background: 'linear-gradient(135deg, rgba(255,69,96,0.10) 0%, transparent 60%)',
            animation: 'att-attn-glow 2.4s ease-in-out infinite',
            '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
          }}
        >
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
            <NotificationsActiveIcon sx={{ color: '#FF4560', fontSize: 20 }} />
            <Typography variant="subtitle2" fontWeight={800} sx={{ color: '#FF4560' }}>
              Action Required — {attentionList.length} {attentionList.length === 1 ? 'guard needs' : 'guards need'} contact
            </Typography>
          </Stack>
          <Stack spacing={1}>
            {attentionList.map((sh) => (
              <Box key={sh.id} sx={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1.5,
                py: 1, px: 1.5, borderRadius: '10px', backgroundColor: 'rgba(255,255,255,0.04)',
              }}>
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" fontWeight={700} noWrap>
                    {sh.guard_name ?? 'Unassigned'}
                    <Typography component="span" variant="caption" sx={{ color: '#FF4560', fontWeight: 700, ml: 1 }}>
                      {overdueLabel(sh)}
                    </Typography>
                  </Typography>
                  <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
                    {sh.site_name ?? 'No site'} · shift {fmtTime(sh.scheduled_start)}–{fmtTime(sh.scheduled_end)}
                  </Typography>
                </Box>
                {sh.guard_phone ? (
                  <Button
                    component="a"
                    href={`tel:${sh.guard_phone}`}
                    size="small"
                    variant="contained"
                    color="error"
                    startIcon={<PhoneInTalkIcon />}
                    sx={{ flexShrink: 0, whiteSpace: 'nowrap' }}
                  >
                    Call {sh.guard_phone}
                  </Button>
                ) : (
                  <Chip size="small" label="No number on file" variant="outlined" sx={{ flexShrink: 0 }} />
                )}
              </Box>
            ))}
          </Stack>
        </GlassCard>
      )}

      {/* Live list, grouped by site */}
      <Stack spacing={2} sx={{ mb: 3 }}>
        {isLoading ? (
          <GlassCard sx={{ p: 2 }}><Skeleton height={80} /></GlassCard>
        ) : bySite.size === 0 ? (
          <GlassCard sx={{ p: 3, textAlign: 'center' }}>
            <Typography color="text.secondary">No shifts scheduled today.</Typography>
          </GlassCard>
        ) : Array.from(bySite.entries()).map(([site, rows], i) => (
          <GlassCard key={site} sx={{ p: 2, ...fadeUpSx(i) }}>
            <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>{site}</Typography>
            <Stack spacing={1}>
              {rows.map((sh) => {
                const state = deriveRowState(sh)
                const rgb = hexToRgb(state.color)
                return (
                <Box key={sh.id} sx={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  py: 1, px: 1.5, borderRadius: '10px',
                  backgroundColor: 'rgba(255,255,255,0.04)',
                  borderLeft: `3px solid rgba(${rgb},0.7)`,
                  transition: 'background-color 0.3s, box-shadow 0.3s',
                  ...(flashIds.has(sh.id) && { animation: 'att-flash 1.8s ease-out' }),
                  '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
                }}>
                  <Stack direction="row" spacing={1.5} alignItems="center" sx={{ minWidth: 0 }}>
                    <Stack direction="row" spacing={-0.75}>
                      <PhotoThumb
                        url={sh.check_in_photo_path ? checkinPhotoUrl(sh.id, 'check_in', accessToken) : null}
                        label={`${sh.guard_name ?? 'Guard'} — check-in`}
                        onClick={() => {
                          const url = checkinPhotoUrl(sh.id, 'check_in', accessToken)
                          if (url) setEnlargedPhoto({ url, label: `${sh.guard_name ?? 'Guard'} — check-in` })
                        }}
                      />
                      {sh.check_out_photo_path && (
                        <PhotoThumb
                          url={checkinPhotoUrl(sh.id, 'check_out', accessToken)}
                          label={`${sh.guard_name ?? 'Guard'} — check-out`}
                          onClick={() => {
                            const url = checkinPhotoUrl(sh.id, 'check_out', accessToken)
                            if (url) setEnlargedPhoto({ url, label: `${sh.guard_name ?? 'Guard'} — check-out` })
                          }}
                        />
                      )}
                    </Stack>
                    <Box sx={{ minWidth: 0 }}>
                      <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap">
                        <Typography variant="body2" fontWeight={600}>{sh.guard_name ?? 'Unassigned'}</Typography>
                        {sh.guard_phone && (
                          <Typography
                            component="a"
                            href={`tel:${sh.guard_phone}`}
                            variant="caption"
                            onClick={(e) => e.stopPropagation()}
                            sx={{
                              display: 'inline-flex', alignItems: 'center', gap: 0.4,
                              color: 'text.secondary', textDecoration: 'none',
                              '&:hover': { color: 'primary.main' },
                            }}
                          >
                            <PhoneIcon sx={{ fontSize: 13 }} />
                            {sh.guard_phone}
                          </Typography>
                        )}
                      </Stack>
                      <Typography variant="caption" color="text.secondary">
                        {fmtTime(sh.scheduled_start)}–{fmtTime(sh.scheduled_end)}
                        {sh.actual_start && ` · in ${fmtTime(sh.actual_start)}`}
                        {sh.actual_end && ` · out ${fmtTime(sh.actual_end)}`}
                        {sh.late_minutes ? ` · ${sh.late_minutes}m late` : ''}
                        {sh.overtime_minutes ? ` · ${sh.overtime_minutes}m OT` : ''}
                      </Typography>
                    </Box>
                  </Stack>
                  <StatusChip state={state} />
                </Box>
                )
              })}
            </Stack>
          </GlassCard>
        ))}
      </Stack>

      {/* Correction requests */}
      <PermissionGuard permission="attendance:manage">
        <GlassCard sx={{ p: 2 }}>
          <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>
            Correction Requests
          </Typography>
          {corrections.length === 0 ? (
            <Typography variant="body2" color="text.secondary">No pending correction requests.</Typography>
          ) : (
            <Stack divider={<Divider />} spacing={1.5}>
              {corrections.map((c) => (
                <Box key={c.id} sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2 }}>
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={600}>
                      {c.guard_name} — {c.site_name ?? 'No site'}
                    </Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                      Shift {fmtTime(c.scheduled_start)}–{fmtTime(c.scheduled_end)} · requests{' '}
                      {c.requested_check_in && `check-in ${fmtTime(c.requested_check_in)}`}
                      {c.requested_check_in && c.requested_check_out && ', '}
                      {c.requested_check_out && `check-out ${fmtTime(c.requested_check_out)}`}
                    </Typography>
                    <Typography variant="caption" sx={{ fontStyle: 'italic', color: 'text.secondary' }}>
                      "{c.reason}"
                    </Typography>
                  </Box>
                  <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
                    <Button size="small" color="error" variant="outlined" startIcon={<CloseIcon />}
                            onClick={() => reject(c.id)}>
                      Reject
                    </Button>
                    <Button size="small" color="success" variant="contained" startIcon={<CheckIcon />}
                            onClick={() => approve(c.id)}>
                      Approve
                    </Button>
                  </Stack>
                </Box>
              ))}
            </Stack>
          )}
        </GlassCard>
      </PermissionGuard>

      <Dialog open={!!enlargedPhoto} onClose={() => setEnlargedPhoto(null)} maxWidth="xs">
        {enlargedPhoto && (
          <DialogContent sx={{ p: 0 }}>
            <img src={enlargedPhoto.url} alt={enlargedPhoto.label} style={{ width: '100%', display: 'block' }} />
            <Typography variant="caption" sx={{ display: 'block', p: 1.5, textAlign: 'center' }}>
              {enlargedPhoto.label}
            </Typography>
          </DialogContent>
        )}
      </Dialog>

      <style>{`
        @keyframes att-pulse {
          0%, 100% { box-shadow: 0 0 0 0 currentColor; opacity: 1; }
          50%      { box-shadow: 0 0 0 3px transparent; opacity: 0.55; }
        }
        @keyframes att-flash {
          0%   { box-shadow: 0 0 0 0 rgba(0,227,150,0.0);  background-color: rgba(0,227,150,0.28); }
          30%  { box-shadow: 0 0 14px 2px rgba(0,227,150,0.55); }
          100% { box-shadow: 0 0 0 0 rgba(0,227,150,0.0);  background-color: rgba(255,255,255,0.04); }
        }
        @keyframes att-attn-glow {
          0%, 100% { box-shadow: 0 0 0 0 rgba(255,69,96,0.0); }
          50%      { box-shadow: 0 0 20px -2px rgba(255,69,96,0.35); }
        }
      `}</style>
    </Box>
  )
}

export default AttendancePage

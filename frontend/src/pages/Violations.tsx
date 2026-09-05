/**
 * Violations (ShiftSecure Phase 3) — auto-detected (no-show, late check-in,
 * geofence failure, early departure) and manually-logged guard conduct
 * issues, with a rolling per-guard points summary. Refreshed in real time
 * via WebSocket (violation_created).
 */
import { useMemo, useState } from 'react'
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
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  IconButton,
  Tooltip,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import PaidIcon from '@mui/icons-material/Paid'
import GroupIcon from '@mui/icons-material/Group'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import AddIcon from '@mui/icons-material/Add'
import CloseIcon from '@mui/icons-material/Close'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  listViolations, createViolation, reviewViolation, getViolationsSummary,
  type Violation, type ViolationType, type ViolationStatus,
} from '@/api/violations'
import { getUsers } from '@/api/users'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { fadeUpSx, useCountUp } from '@/lib/motion'

const GUARD_ROLES = new Set([3, 4, 5, 8])

const TYPE_META: Record<ViolationType, { label: string; color: string }> = {
  no_show: { label: 'No Show', color: '#FF4560' },
  late_checkin: { label: 'Late Check-in', color: '#FF9800' },
  geofence_failure: { label: 'Geofence Failure', color: '#FFC107' },
  early_departure: { label: 'Early Departure', color: '#6C63FF' },
  manual: { label: 'Manual Entry', color: '#8B92A8' },
}

const STATUS_META: Record<ViolationStatus, { label: string; color: string }> = {
  open: { label: 'Open', color: '#FF4560' },
  acknowledged: { label: 'Acknowledged', color: '#00E396' },
  disputed: { label: 'Disputed', color: '#FF9800' },
  waived: { label: 'Waived', color: '#8B92A8' },
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

function TypeChip({ type }: { type: ViolationType }) {
  const meta = TYPE_META[type]
  const rgb = hexToRgb(meta.color)
  return (
    <Chip label={meta.label} size="small" sx={{
      color: meta.color, backgroundColor: `rgba(${rgb},0.14)`, border: `1px solid rgba(${rgb},0.3)`, fontWeight: 700,
    }} />
  )
}

function StatusChip({ status }: { status: ViolationStatus }) {
  const meta = STATUS_META[status]
  const rgb = hexToRgb(meta.color)
  return (
    <Chip label={meta.label} size="small" sx={{
      color: meta.color, backgroundColor: `rgba(${rgb},0.14)`, border: `1px solid rgba(${rgb},0.3)`, fontWeight: 700,
      transition: 'background-color 0.25s, color 0.25s, border-color 0.25s',
    }} />
  )
}

function pointsColor(points: number) {
  if (points >= 25) return '#FF4560'
  if (points >= 10) return '#FF9800'
  return '#00E396'
}

const fmtDateTime = (iso: string) => new Date(iso).toLocaleString([], {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
})

function LogViolationDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => getUsers() })
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const guards = (users as any[]).filter((u) => GUARD_ROLES.has(u.role_id) && u.is_active)

  const [guardId, setGuardId] = useState('')
  const [type, setType] = useState<ViolationType>('manual')
  const [points, setPoints] = useState('5')
  const [siteId, setSiteId] = useState('')
  const [description, setDescription] = useState('')

  const { mutate: submit, isPending } = useMutation({
    mutationFn: () => createViolation({
      guard_user_id: guardId,
      violation_type: type,
      description: description || undefined,
      points: points ? Number(points) : undefined,
      site_id: siteId || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['violations'] })
      qc.invalidateQueries({ queryKey: ['violations-summary'] })
      handleClose()
    },
  })

  const handleClose = () => {
    setGuardId(''); setType('manual'); setPoints('5'); setSiteId(''); setDescription('')
    onClose()
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="xs" fullWidth>
      <DialogTitle>Log Violation</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <FormControl size="small" fullWidth required>
            <InputLabel>Guard</InputLabel>
            <Select value={guardId} label="Guard" onChange={(e) => setGuardId(e.target.value)}>
              {guards.map((g) => <MenuItem key={g.id} value={g.id}>{g.full_name ?? g.email}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" fullWidth>
            <InputLabel>Type</InputLabel>
            <Select value={type} label="Type" onChange={(e) => setType(e.target.value as ViolationType)}>
              {Object.entries(TYPE_META).map(([k, m]) => <MenuItem key={k} value={k}>{m.label}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField
            size="small" label="Points" type="number" value={points}
            onChange={(e) => setPoints(e.target.value)} fullWidth
          />
          <FormControl size="small" fullWidth>
            <InputLabel>Site (optional)</InputLabel>
            <Select value={siteId} label="Site (optional)" onChange={(e) => setSiteId(e.target.value)}>
              <MenuItem value="">None</MenuItem>
              {(sites as any[]).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField
            size="small" label="Description" multiline minRows={2} fullWidth
            value={description} onChange={(e) => setDescription(e.target.value)}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button variant="contained" disabled={!guardId || isPending} onClick={() => submit()}>
          Log Violation
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function ReviewDialog({ violation, onClose }: { violation: Violation | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [notes, setNotes] = useState('')

  const { mutate: review, isPending } = useMutation({
    mutationFn: (status: 'acknowledged' | 'disputed' | 'waived') =>
      reviewViolation(violation!.id, status, notes || undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['violations'] })
      qc.invalidateQueries({ queryKey: ['violations-summary'] })
      setNotes('')
      onClose()
    },
  })

  return (
    <Dialog open={!!violation} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        Review Violation
        <IconButton size="small" onClick={onClose}><CloseIcon fontSize="small" /></IconButton>
      </DialogTitle>
      {violation && (
        <DialogContent>
          <Stack spacing={1.5}>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
              <TypeChip type={violation.violation_type} />
              <StatusChip status={violation.status} />
              {violation.is_auto_generated && (
                <Chip size="small" label="Auto" variant="outlined" sx={{ fontSize: '0.7rem' }} />
              )}
            </Box>
            <Typography variant="body2" fontWeight={600}>{violation.guard_name}</Typography>
            <Typography variant="caption" color="text.secondary">
              {fmtDateTime(violation.occurred_at)} {violation.site_name ? `· ${violation.site_name}` : ''} · {violation.points} pts
            </Typography>
            {violation.description && (
              <Typography variant="body2" sx={{ fontStyle: 'italic' }}>"{violation.description}"</Typography>
            )}
            <TextField
              size="small" label="Review notes (optional)" multiline minRows={2} fullWidth
              value={notes} onChange={(e) => setNotes(e.target.value)}
            />
          </Stack>
        </DialogContent>
      )}
      <DialogActions>
        <Button color="warning" disabled={isPending} onClick={() => review('disputed')}>Dispute</Button>
        <Button color="inherit" disabled={isPending} onClick={() => review('waived')}>Waive</Button>
        <Button variant="contained" color="success" disabled={isPending} onClick={() => review('acknowledged')}>
          Acknowledge
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export function ViolationsPage() {
  const [siteId, setSiteId] = useState('')
  const [guardId, setGuardId] = useState('')
  const [type, setType] = useState('')
  const [status, setStatus] = useState('')
  const [logOpen, setLogOpen] = useState(false)
  const [reviewing, setReviewing] = useState<Violation | null>(null)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => getUsers() })
  const guards = (users as any[]).filter((u) => GUARD_ROLES.has(u.role_id) && u.is_active)

  const filters = {
    site_id: siteId || undefined,
    guard_user_id: guardId || undefined,
    violation_type: type || undefined,
    violation_status: status || undefined,
  }
  const { data: violations = [], isLoading } = useQuery({
    queryKey: ['violations', filters],
    queryFn: () => listViolations(filters),
  })
  const { data: summary = [] } = useQuery({
    queryKey: ['violations-summary'],
    queryFn: () => getViolationsSummary(),
  })

  const kpis = useMemo(() => {
    const monthStart = new Date(); monthStart.setDate(1); monthStart.setHours(0, 0, 0, 0)
    const openCount = violations.filter((v) => v.status === 'open').length
    const monthPoints = violations
      .filter((v) => new Date(v.occurred_at) >= monthStart && v.status !== 'waived')
      .reduce((sum, v) => sum + v.points, 0)
    const autoCount = violations.filter((v) => v.is_auto_generated).length
    return { openCount, monthPoints, guardsFlagged: summary.length, autoCount, manualCount: violations.length - autoCount }
  }, [violations, summary])

  return (
    <Box>
      <PageHeader pageKey="violations" />

      <Box sx={{ display: 'flex', gap: 2, mb: 2.5, flexWrap: 'wrap' }}>
        {[
          { label: 'Open Violations', value: kpis.openCount, icon: <WarningAmberIcon />, color: '#FF4560' },
          { label: "This Month's Points", value: kpis.monthPoints, icon: <PaidIcon />, color: '#FF9800' },
          { label: 'Guards Flagged', value: kpis.guardsFlagged, icon: <GroupIcon />, color: '#6C63FF' },
          { label: 'Auto-Detected', value: kpis.autoCount, icon: <AutoAwesomeIcon />, color: '#00E396' },
        ].map((kpi, i) => (
          <Box key={kpi.label} sx={{ flex: '1 1 180px', minWidth: 0, ...fadeUpSx(i) }}>
            <KpiCard label={kpi.label} value={kpi.value} icon={kpi.icon} color={kpi.color} />
          </Box>
        ))}
      </Box>

      {/* Points leaderboard */}
      {summary.length > 0 && (
        <GlassCard sx={{ p: 2, mb: 2.5 }}>
          <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>Points Leaderboard (90 days)</Typography>
          <Stack direction="row" spacing={1.5} sx={{ flexWrap: 'wrap' }}>
            {summary.slice(0, 8).map((row) => {
              const pts = row.total_points ?? 0
              const color = pointsColor(pts)
              const rgb = hexToRgb(color)
              return (
                <Box key={row.guard_user_id} sx={{
                  px: 1.5, py: 1, borderRadius: '10px', backgroundColor: `rgba(${rgb},0.08)`,
                  border: `1px solid rgba(${rgb},0.2)`, minWidth: 140,
                }}>
                  <Typography variant="body2" fontWeight={600} noWrap>{row.guard_name}</Typography>
                  <Typography variant="caption" sx={{ color, fontWeight: 700 }}>
                    {pts} pts · {row.violation_count} violations
                  </Typography>
                </Box>
              )
            })}
          </Stack>
        </GlassCard>
      )}

      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2, flexWrap: 'wrap' }}>
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel>Site</InputLabel>
          <Select value={siteId} label="Site" onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">All Sites</MenuItem>
            {(sites as any[]).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel>Guard</InputLabel>
          <Select value={guardId} label="Guard" onChange={(e) => setGuardId(e.target.value)}>
            <MenuItem value="">All Guards</MenuItem>
            {guards.map((g) => <MenuItem key={g.id} value={g.id}>{g.full_name ?? g.email}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel>Type</InputLabel>
          <Select value={type} label="Type" onChange={(e) => setType(e.target.value)}>
            <MenuItem value="">All Types</MenuItem>
            {Object.entries(TYPE_META).map(([k, m]) => <MenuItem key={k} value={k}>{m.label}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel>Status</InputLabel>
          <Select value={status} label="Status" onChange={(e) => setStatus(e.target.value)}>
            <MenuItem value="">All Statuses</MenuItem>
            {Object.entries(STATUS_META).map(([k, m]) => <MenuItem key={k} value={k}>{m.label}</MenuItem>)}
          </Select>
        </FormControl>
        <Box sx={{ flexGrow: 1 }} />
        <PermissionGuard permission="violation:manage">
          <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setLogOpen(true)}>
            Log Violation
          </Button>
        </PermissionGuard>
      </Stack>

      <GlassCard sx={{ p: 2 }}>
        {isLoading ? (
          <Skeleton height={80} />
        ) : violations.length === 0 ? (
          <Typography color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>No violations found.</Typography>
        ) : (
          <Stack divider={<Divider />} spacing={1.5}>
            {violations.map((v) => (
              <PermissionGuard key={v.id} permission="violation:manage" fallback={
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, py: 0.5 }}>
                  <ViolationRow v={v} />
                </Box>
              }>
                <Box
                  sx={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, py: 0.5,
                    cursor: 'pointer', borderRadius: '8px', '&:hover': { backgroundColor: 'rgba(255,255,255,0.04)' },
                  }}
                  onClick={() => setReviewing(v)}
                >
                  <ViolationRow v={v} />
                </Box>
              </PermissionGuard>
            ))}
          </Stack>
        )}
      </GlassCard>

      <ReviewDialog violation={reviewing} onClose={() => setReviewing(null)} />
      <LogViolationDialog open={logOpen} onClose={() => setLogOpen(false)} />
    </Box>
  )
}

function ViolationRow({ v }: { v: Violation }) {
  return (
    <>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ minWidth: 0 }}>
        <TypeChip type={v.violation_type} />
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="body2" fontWeight={600}>{v.guard_name}</Typography>
          <Typography variant="caption" color="text.secondary">
            {fmtDateTime(v.occurred_at)}{v.site_name ? ` · ${v.site_name}` : ''} · {v.points} pts
            {v.is_auto_generated ? ' · auto' : ` · logged by ${v.reported_by_name ?? '—'}`}
          </Typography>
        </Box>
      </Stack>
      <StatusChip status={v.status} />
    </>
  )
}

export default ViolationsPage

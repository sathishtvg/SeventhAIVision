/**
 * Leave Management (ShiftSecure Phase 4) — leave types, guard-submitted
 * requests with an approval workflow, and per-guard balances. Approval
 * syncs into guard_leave_blocks so the roster auto-scheduler picks it up
 * automatically. Refreshed in real time via WebSocket (leave_status_changed).
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
  Tabs,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Switch,
  FormControlLabel,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import EventBusyIcon from '@mui/icons-material/EventBusy'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import TodayIcon from '@mui/icons-material/Today'
import AddIcon from '@mui/icons-material/Add'
import CloseIcon from '@mui/icons-material/Close'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getLeaveTypes, createLeaveType, updateLeaveType, deleteLeaveType,
  listLeaveRequests, createLeaveRequest, approveLeaveRequest, rejectLeaveRequest,
  cancelLeaveRequest, getLeaveBalances,
  type LeaveRequest, type LeaveRequestStatus, type LeaveType,
} from '@/api/leave'
import { getUsers } from '@/api/users'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { usePermission } from '@/hooks/usePermission'
import { fadeUpSx, useCountUp } from '@/lib/motion'
import { useAuthStore } from '@/store/auth'

const GUARD_ROLES = new Set([3, 4, 5, 8])
const _GUARD_ONLY_ROLES = new Set([4, 5])

const STATUS_META: Record<LeaveRequestStatus, { label: string; color: string }> = {
  pending: { label: 'Pending', color: '#FF9800' },
  approved: { label: 'Approved', color: '#00E396' },
  rejected: { label: 'Rejected', color: '#FF4560' },
  cancelled: { label: 'Cancelled', color: '#8B92A8' },
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

function StatusChip({ status }: { status: LeaveRequestStatus }) {
  const meta = STATUS_META[status]
  const rgb = hexToRgb(meta.color)
  return (
    <Chip label={meta.label} size="small" sx={{
      color: meta.color, backgroundColor: `rgba(${rgb},0.14)`, border: `1px solid rgba(${rgb},0.3)`, fontWeight: 700,
      transition: 'background-color 0.25s, color 0.25s, border-color 0.25s',
    }} />
  )
}

const fmtDate = (iso: string) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })

function RequestLeaveDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const user = useAuthStore((s) => s.user)
  const isGuardOnly = user ? _GUARD_ONLY_ROLES.has(user.roleId) : false
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => getUsers() })
  const { data: types = [] } = useQuery({ queryKey: ['leave-types'], queryFn: () => getLeaveTypes() })
  const guards = (users as any[]).filter((u) => GUARD_ROLES.has(u.role_id) && u.is_active)

  const [guardId, setGuardId] = useState('')
  const [typeId, setTypeId] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [reason, setReason] = useState('')

  const selectedType = types.find((t) => t.id === typeId)
  const { data: balance } = useQuery({
    queryKey: ['leave-balances-preview', guardId || user?.id],
    queryFn: () => getLeaveBalances(guardId || user?.id),
    enabled: !!(guardId || user?.id),
  })
  const typeBalance = balance?.find((b) => b.leave_type_id === typeId)

  const { mutate: submit, isPending } = useMutation({
    mutationFn: () => createLeaveRequest({
      guard_user_id: isGuardOnly ? user!.id : guardId,
      leave_type_id: typeId,
      start_date: startDate,
      end_date: endDate,
      reason: reason || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['leave-requests'] })
      qc.invalidateQueries({ queryKey: ['leave-balances'] })
      handleClose()
    },
  })

  const handleClose = () => {
    setGuardId(''); setTypeId(''); setStartDate(''); setEndDate(''); setReason('')
    onClose()
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="xs" fullWidth>
      <DialogTitle>Request Leave</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {!isGuardOnly && (
            <FormControl size="small" fullWidth required>
              <InputLabel>Guard</InputLabel>
              <Select value={guardId} label="Guard" onChange={(e) => setGuardId(e.target.value)}>
                {guards.map((g) => <MenuItem key={g.id} value={g.id}>{g.full_name ?? g.email}</MenuItem>)}
              </Select>
            </FormControl>
          )}
          <FormControl size="small" fullWidth required>
            <InputLabel>Leave Type</InputLabel>
            <Select value={typeId} label="Leave Type" onChange={(e) => setTypeId(e.target.value)}>
              {types.map((t) => <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>)}
            </Select>
          </FormControl>
          {typeBalance && (
            <Typography variant="caption" color="text.secondary">
              Balance: {typeBalance.remaining_days} of {typeBalance.entitled_days} days remaining
            </Typography>
          )}
          {selectedType?.requires_document && (
            <Typography variant="caption" sx={{ color: '#FF9800' }}>
              This leave type typically requires a supporting document — you can attach one after submitting.
            </Typography>
          )}
          <TextField
            size="small" label="Start Date" type="date" fullWidth
            slotProps={{ inputLabel: { shrink: true } }} value={startDate} onChange={(e) => setStartDate(e.target.value)}
          />
          <TextField
            size="small" label="End Date" type="date" fullWidth
            slotProps={{ inputLabel: { shrink: true } }} value={endDate} onChange={(e) => setEndDate(e.target.value)}
          />
          <TextField
            size="small" label="Reason (optional)" multiline minRows={2} fullWidth
            value={reason} onChange={(e) => setReason(e.target.value)}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={(!isGuardOnly && !guardId) || !typeId || !startDate || !endDate || isPending}
          onClick={() => submit()}
        >
          Submit Request
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function ReviewDialog({ leaveRequest, onClose }: { leaveRequest: LeaveRequest | null; onClose: () => void }) {
  const qc = useQueryClient()
  const canManage = usePermission('leave:manage')
  const user = useAuthStore((s) => s.user)
  const isOwn = leaveRequest && user ? leaveRequest.guard_user_id === user.id : false
  const [notes, setNotes] = useState('')
  const [affectedShifts, setAffectedShifts] = useState<{ id: string; scheduled_start: string; site_name: string | null }[]>([])

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['leave-requests'] })
    qc.invalidateQueries({ queryKey: ['leave-balances'] })
  }

  const { mutate: approve, isPending: approving } = useMutation({
    mutationFn: () => approveLeaveRequest(leaveRequest!.id),
    onSuccess: (data) => {
      invalidate()
      if (data.affected_shifts?.length) {
        setAffectedShifts(data.affected_shifts)
      } else {
        setNotes(''); onClose()
      }
    },
  })
  const { mutate: reject, isPending: rejecting } = useMutation({
    mutationFn: () => rejectLeaveRequest(leaveRequest!.id, notes || undefined),
    onSuccess: () => { invalidate(); setNotes(''); onClose() },
  })
  const { mutate: cancel, isPending: cancelling } = useMutation({
    mutationFn: () => cancelLeaveRequest(leaveRequest!.id),
    onSuccess: () => { invalidate(); setNotes(''); onClose() },
  })

  const handleClose = () => { setAffectedShifts([]); setNotes(''); onClose() }
  const pending = approving || rejecting || cancelling

  return (
    <Dialog open={!!leaveRequest} onClose={handleClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        Leave Request
        <IconButton size="small" onClick={handleClose}><CloseIcon fontSize="small" /></IconButton>
      </DialogTitle>
      {leaveRequest && (
        <DialogContent>
          <Stack spacing={1.5}>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
              <Chip size="small" label={leaveRequest.leave_type_name} variant="outlined" />
              <StatusChip status={leaveRequest.status} />
            </Box>
            <Typography variant="body2" fontWeight={600}>{leaveRequest.guard_name}</Typography>
            <Typography variant="caption" color="text.secondary">
              {fmtDate(leaveRequest.start_date)} – {fmtDate(leaveRequest.end_date)} · {leaveRequest.days_count} day(s)
            </Typography>
            {leaveRequest.reason && (
              <Typography variant="body2" sx={{ fontStyle: 'italic' }}>"{leaveRequest.reason}"</Typography>
            )}
            {affectedShifts.length > 0 && (
              <Box sx={{ p: 1.5, borderRadius: '8px', backgroundColor: 'rgba(255,152,0,0.1)', border: '1px solid rgba(255,152,0,0.3)' }}>
                <Typography variant="caption" sx={{ color: '#FF9800', fontWeight: 700, display: 'block', mb: 0.5 }}>
                  {affectedShifts.length} published shift(s) need reassignment
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  See Roster to reassign these shifts to another guard.
                </Typography>
              </Box>
            )}
            {leaveRequest.status === 'pending' && canManage && (
              <TextField
                size="small" label="Review notes (optional)" multiline minRows={2} fullWidth
                value={notes} onChange={(e) => setNotes(e.target.value)}
              />
            )}
          </Stack>
        </DialogContent>
      )}
      <DialogActions>
        {leaveRequest?.status === 'pending' && canManage && (
          <>
            <Button color="error" disabled={pending} onClick={() => reject()}>Reject</Button>
            <Button variant="contained" color="success" disabled={pending} onClick={() => approve()}>Approve</Button>
          </>
        )}
        {(leaveRequest?.status === 'pending' || leaveRequest?.status === 'approved') && (canManage || isOwn) && (
          <Button color="warning" disabled={pending} onClick={() => cancel()}>Cancel Request</Button>
        )}
      </DialogActions>
    </Dialog>
  )
}

function RequestsTab() {
  const [guardId, setGuardId] = useState('')
  const [typeId, setTypeId] = useState('')
  const [status, setStatus] = useState('')
  const [requestOpen, setRequestOpen] = useState(false)
  const [reviewing, setReviewing] = useState<LeaveRequest | null>(null)

  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => getUsers() })
  const { data: types = [] } = useQuery({ queryKey: ['leave-types'], queryFn: () => getLeaveTypes() })
  const guards = (users as any[]).filter((u) => GUARD_ROLES.has(u.role_id) && u.is_active)

  const filters = {
    guard_user_id: guardId || undefined,
    leave_type_id: typeId || undefined,
    request_status: status || undefined,
  }
  const { data: requests = [], isLoading } = useQuery({
    queryKey: ['leave-requests', filters],
    queryFn: () => listLeaveRequests(filters),
  })

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2, flexWrap: 'wrap' }}>
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel>Guard</InputLabel>
          <Select value={guardId} label="Guard" onChange={(e) => setGuardId(e.target.value)}>
            <MenuItem value="">All Guards</MenuItem>
            {guards.map((g) => <MenuItem key={g.id} value={g.id}>{g.full_name ?? g.email}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel>Type</InputLabel>
          <Select value={typeId} label="Type" onChange={(e) => setTypeId(e.target.value)}>
            <MenuItem value="">All Types</MenuItem>
            {types.map((t) => <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>)}
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
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setRequestOpen(true)}>
          Request Leave
        </Button>
      </Stack>

      {isLoading ? (
        <Skeleton height={80} />
      ) : requests.length === 0 ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>No leave requests found.</Typography>
      ) : (
        <Stack divider={<Divider />} spacing={1.5}>
          {requests.map((r) => (
            <Box
              key={r.id}
              onClick={() => setReviewing(r)}
              sx={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, py: 0.5,
                cursor: 'pointer', borderRadius: '8px', '&:hover': { backgroundColor: 'rgba(255,255,255,0.04)' },
              }}
            >
              <Stack direction="row" spacing={1.5} alignItems="center" sx={{ minWidth: 0 }}>
                <Chip size="small" label={r.leave_type_name} variant="outlined" />
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" fontWeight={600}>{r.guard_name}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {fmtDate(r.start_date)} – {fmtDate(r.end_date)} · {r.days_count} day(s)
                  </Typography>
                </Box>
              </Stack>
              <StatusChip status={r.status} />
            </Box>
          ))}
        </Stack>
      )}

      <ReviewDialog leaveRequest={reviewing} onClose={() => setReviewing(null)} />
      <RequestLeaveDialog open={requestOpen} onClose={() => setRequestOpen(false)} />
    </Box>
  )
}

function BalancesTab() {
  const user = useAuthStore((s) => s.user)
  const canManage = usePermission('leave:manage')
  const isGuardOnly = user ? _GUARD_ONLY_ROLES.has(user.roleId) : false
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => getUsers() })
  const guards = (users as any[]).filter((u) => GUARD_ROLES.has(u.role_id) && u.is_active)

  const [guardId, setGuardId] = useState(isGuardOnly ? (user?.id ?? '') : '')
  const [year, setYear] = useState(new Date().getFullYear())
  const effectiveGuard = isGuardOnly ? user?.id : (guardId || undefined)

  const { data: balances = [], isLoading } = useQuery({
    queryKey: ['leave-balances', effectiveGuard, year],
    queryFn: () => getLeaveBalances(effectiveGuard, year),
    enabled: !!effectiveGuard,
  })

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2, flexWrap: 'wrap' }}>
        {!isGuardOnly && (
          <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel>Guard</InputLabel>
            <Select value={guardId} label="Guard" onChange={(e) => setGuardId(e.target.value)}>
              <MenuItem value="">Select a guard…</MenuItem>
              {guards.map((g) => <MenuItem key={g.id} value={g.id}>{g.full_name ?? g.email}</MenuItem>)}
            </Select>
          </FormControl>
        )}
        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>Year</InputLabel>
          <Select value={year} label="Year" onChange={(e) => setYear(Number(e.target.value))}>
            {[year - 1, year, year + 1].map((y) => <MenuItem key={y} value={y}>{y}</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>

      {!effectiveGuard ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>Select a guard to view their leave balances.</Typography>
      ) : isLoading ? (
        <Skeleton height={80} />
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Leave Type</TableCell>
                <TableCell align="right">Entitled</TableCell>
                <TableCell align="right">Used</TableCell>
                <TableCell align="right">Remaining</TableCell>
                {canManage && <TableCell align="right">Actions</TableCell>}
              </TableRow>
            </TableHead>
            <TableBody>
              {balances.map((b) => (
                <TableRow key={b.leave_type_id}>
                  <TableCell>{b.name}</TableCell>
                  <TableCell align="right">{b.entitled_days}</TableCell>
                  <TableCell align="right">{b.used_days}</TableCell>
                  <TableCell align="right" sx={{ color: b.remaining_days < 2 ? '#FF9800' : undefined, fontWeight: 700 }}>
                    {b.remaining_days}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  )
}

function LeaveTypeDialog({ open, onClose, editing }: { open: boolean; onClose: () => void; editing: LeaveType | null }) {
  const qc = useQueryClient()
  const [name, setName] = useState(editing?.name ?? '')
  const [days, setDays] = useState(String(editing?.default_annual_days ?? 0))
  const [requiresDoc, setRequiresDoc] = useState(editing?.requires_document ?? false)

  const { mutate: save, isPending } = useMutation({
    mutationFn: () => editing
      ? updateLeaveType(editing.id, { name, default_annual_days: Number(days), requires_document: requiresDoc })
      : createLeaveType({ name, default_annual_days: Number(days), requires_document: requiresDoc }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['leave-types'] })
      onClose()
    },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{editing ? 'Edit Leave Type' : 'Add Leave Type'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField size="small" label="Name" fullWidth value={name} onChange={(e) => setName(e.target.value)} />
          <TextField
            size="small" label="Default Annual Days" type="number" fullWidth
            value={days} onChange={(e) => setDays(e.target.value)}
          />
          <FormControlLabel
            control={<Switch checked={requiresDoc} onChange={(e) => setRequiresDoc(e.target.checked)} />}
            label="Requires supporting document"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!name || isPending} onClick={() => save()}>Save</Button>
      </DialogActions>
    </Dialog>
  )
}

function LeaveTypesTab() {
  const qc = useQueryClient()
  const { data: types = [], isLoading } = useQuery({ queryKey: ['leave-types'], queryFn: () => getLeaveTypes() })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<LeaveType | null>(null)

  const { mutate: deactivate } = useMutation({
    mutationFn: (id: string) => deleteLeaveType(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['leave-types'] }),
  })

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 2 }}>
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => { setEditing(null); setDialogOpen(true) }}>
          Add Leave Type
        </Button>
      </Stack>
      {isLoading ? (
        <Skeleton height={80} />
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell align="right">Default Annual Days</TableCell>
                <TableCell align="center">Requires Document</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {types.map((t) => (
                <TableRow key={t.id}>
                  <TableCell>{t.name}</TableCell>
                  <TableCell align="right">{t.default_annual_days}</TableCell>
                  <TableCell align="center">{t.requires_document ? 'Yes' : 'No'}</TableCell>
                  <TableCell align="right">
                    <Button size="small" onClick={() => { setEditing(t); setDialogOpen(true) }}>Edit</Button>
                    <Button size="small" color="error" onClick={() => deactivate(t.id)}>Deactivate</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <LeaveTypeDialog open={dialogOpen} onClose={() => setDialogOpen(false)} editing={editing} />
    </Box>
  )
}

export function LeavePage() {
  const [tab, setTab] = useState(0)
  const canManage = usePermission('leave:manage')

  const { data: allRequests = [] } = useQuery({
    queryKey: ['leave-requests', {}],
    queryFn: () => listLeaveRequests(),
  })

  const kpis = useMemo(() => {
    const monthStart = new Date(); monthStart.setDate(1); monthStart.setHours(0, 0, 0, 0)
    const today = new Date().toISOString().slice(0, 10)
    const pending = allRequests.filter((r) => r.status === 'pending').length
    const approvedThisMonth = allRequests.filter(
      (r) => r.status === 'approved' && r.reviewed_at && new Date(r.reviewed_at) >= monthStart
    ).length
    const onLeaveToday = allRequests.filter(
      (r) => r.status === 'approved' && r.start_date <= today && r.end_date >= today
    ).length
    return { pending, approvedThisMonth, onLeaveToday }
  }, [allRequests])

  return (
    <Box>
      <PageHeader title="Leave" subtitle="Leave requests, approvals, and per-guard balances" />

      <Box sx={{ display: 'flex', gap: 2, mb: 2.5, flexWrap: 'wrap' }}>
        {[
          { label: 'Pending Requests', value: kpis.pending, icon: <EventBusyIcon />, color: '#FF9800' },
          { label: 'Approved This Month', value: kpis.approvedThisMonth, icon: <CheckCircleIcon />, color: '#00E396' },
          { label: 'On Leave Today', value: kpis.onLeaveToday, icon: <TodayIcon />, color: '#6C63FF' },
        ].map((kpi, i) => (
          <Box key={kpi.label} sx={{ flex: '1 1 180px', minWidth: 0, ...fadeUpSx(i) }}>
            <KpiCard label={kpi.label} value={kpi.value} icon={kpi.icon} color={kpi.color} />
          </Box>
        ))}
      </Box>

      <GlassCard>
        <Box sx={{ borderBottom: 1, borderColor: 'rgba(255,255,255,0.1)' }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab label="Requests" />
            <Tab label="Balances" />
            {canManage && <Tab label="Leave Types" />}
          </Tabs>
        </Box>
        {tab === 0 && <RequestsTab />}
        {tab === 1 && <BalancesTab />}
        {tab === 2 && canManage && (
          <PermissionGuard permission="leave:manage">
            <LeaveTypesTab />
          </PermissionGuard>
        )}
      </GlassCard>
    </Box>
  )
}

export default LeavePage

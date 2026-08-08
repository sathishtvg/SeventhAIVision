import { useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  Grid,
  InputLabel,
  LinearProgress,
  MenuItem,
  Paper,
  Select,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import AddIcon from '@mui/icons-material/Add'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import CancelIcon from '@mui/icons-material/Cancel'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import AccessTimeIcon from '@mui/icons-material/AccessTime'
import RouteIcon from '@mui/icons-material/Route'
import EventRepeatIcon from '@mui/icons-material/EventRepeat'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getComplianceDashboard,
  listSchedules,
  listOccurrences,
  createSchedule,
  generateOccurrences,
  getComplianceReport,
  resolveOccurrence,
  type TourSchedule,
  type TourOccurrence,
} from '@/api/compliance'
import { apiClient } from '@/api/client'

// Assignable tour guards — Supervisor/Operator/Security Guard only. A raw
// `role_id >= 4` range check would also sweep in Viewer(6), Client(7), and
// Manager(8), none of whom should show up as a patrol-tour assignee.
const GUARD_ROLES = new Set([3, 4, 5])

// ── Status helpers ────────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, 'success' | 'error' | 'warning' | 'info' | 'default'> = {
  completed: 'success',
  missed: 'error',
  late: 'warning',
  incomplete: 'warning',
  pending: 'default',
}

function StatusChip({ status }: { status: string }) {
  return (
    <Chip
      size="small"
      label={status.charAt(0).toUpperCase() + status.slice(1)}
      color={STATUS_COLOR[status] ?? 'default'}
    />
  )
}

function ScoreBar({ score }: { score?: number | null }) {
  if (score == null) return <Typography variant="body2" color="text.secondary">—</Typography>
  const color = score >= 90 ? '#00E396' : score >= 70 ? '#FF9800' : '#FF4560'
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <Box sx={{ flex: 1 }}>
        <LinearProgress
          variant="determinate"
          value={score}
          sx={{
            height: 6, borderRadius: 3,
            bgcolor: 'rgba(255,255,255,0.1)',
            '& .MuiLinearProgress-bar': { bgcolor: color, borderRadius: 3 },
          }}
        />
      </Box>
      <Typography variant="body2" sx={{ color, minWidth: 38 }}>
        {score}%
      </Typography>
    </Box>
  )
}

function KpiCard({ label, value, sub, icon, color }: {
  label: string; value: number | string; sub?: string; icon: React.ReactNode; color?: string
}) {
  return (
    <Paper sx={{
      p: 2.5,
      background: 'rgba(255,255,255,0.04)',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: 2,
    }}>
      <Stack direction="row" alignItems="flex-start" justifyContent="space-between">
        <Box>
          <Typography variant="body2" color="text.secondary">{label}</Typography>
          <Typography variant="h4" fontWeight={700} sx={{ color: color ?? 'text.primary' }}>
            {value}
          </Typography>
          {sub && <Typography variant="caption" color="text.secondary">{sub}</Typography>}
        </Box>
        <Box sx={{ color: color ?? 'primary.main', opacity: 0.8, mt: 0.5 }}>{icon}</Box>
      </Stack>
    </Paper>
  )
}

// ── Create Schedule Dialog ─────────────────────────────────────────────────────

function CreateScheduleDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    name: '', route_id: '', recurrence: 'daily', scheduled_time: '08:00',
    window_minutes: 30, assigned_guard_user_id: '',
  })

  const { data: routes = [] } = useQuery({
    queryKey: ['patrol-routes'],
    queryFn: async () => {
      const r = await apiClient.get('/api/v1/patrols/routes')
      return r.data as Array<{ id: string; name: string; site_name?: string }>
    },
    enabled: open,
  })

  const { data: guards = [] } = useQuery({
    queryKey: ['users-guards'],
    queryFn: async () => {
      const r = await apiClient.get('/api/v1/users')
      return r.data as Array<{ id: string; full_name: string; role_id: number }>
    },
    enabled: open,
  })

  const mutation = useMutation({
    mutationFn: () => createSchedule({
      ...form,
      window_minutes: Number(form.window_minutes),
      assigned_guard_user_id: form.assigned_guard_user_id || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tour-schedules'] })
      qc.invalidateQueries({ queryKey: ['compliance-dashboard'] })
      onClose()
    },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Create Tour Schedule</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField label="Schedule Name" value={form.name} required
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          <FormControl fullWidth>
            <InputLabel>Patrol Route</InputLabel>
            <Select value={form.route_id} label="Patrol Route"
              onChange={e => setForm(f => ({ ...f, route_id: e.target.value }))}>
              {routes.map(r => (
                <MenuItem key={r.id} value={r.id}>
                  {r.name}{r.site_name ? ` — ${r.site_name}` : ''}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl fullWidth>
            <InputLabel>Recurrence</InputLabel>
            <Select value={form.recurrence} label="Recurrence"
              onChange={e => setForm(f => ({ ...f, recurrence: e.target.value }))}>
              <MenuItem value="daily">Daily</MenuItem>
              <MenuItem value="weekdays">Weekdays (Mon–Fri)</MenuItem>
              <MenuItem value="weekends">Weekends (Sat–Sun)</MenuItem>
            </Select>
          </FormControl>
          <TextField label="Scheduled Time (UTC)" value={form.scheduled_time} type="time"
            onChange={e => setForm(f => ({ ...f, scheduled_time: e.target.value }))}
            slotProps={{ inputLabel: { shrink: true } }} />
          <TextField label="Window (minutes)" value={form.window_minutes} type="number"
            inputProps={{ min: 5, max: 120 }}
            onChange={e => setForm(f => ({ ...f, window_minutes: Number(e.target.value) }))}
            helperText="Grace period: tour must start within this many minutes of scheduled time" />
          <FormControl fullWidth>
            <InputLabel>Assigned Guard (optional)</InputLabel>
            <Select value={form.assigned_guard_user_id} label="Assigned Guard (optional)"
              onChange={e => setForm(f => ({ ...f, assigned_guard_user_id: e.target.value }))}>
              <MenuItem value="">— Unassigned —</MenuItem>
              {guards.filter(g => GUARD_ROLES.has(g.role_id)).map(g => (
                <MenuItem key={g.id} value={g.id}>{g.full_name}</MenuItem>
              ))}
            </Select>
          </FormControl>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.name || !form.route_id || mutation.isPending}
          onClick={() => mutation.mutate()}>
          {mutation.isPending ? 'Creating…' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Tab: Dashboard ─────────────────────────────────────────────────────────────

function DashboardTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['compliance-dashboard'],
    queryFn: getComplianceDashboard,
    refetchInterval: 60_000,
  })

  if (isLoading) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}><CircularProgress /></Box>
  if (error || !data) return <Alert severity="error">Failed to load compliance dashboard.</Alert>

  const completedToday = data.completed_today ?? 0
  const toursToday = (data.tours_today ?? 0) + completedToday + (data.missed_today ?? 0)

  return (
    <Stack spacing={3}>
      <Grid container spacing={2}>
        <Grid size={{ xs: 6, md: 3 }}>
          <KpiCard label="Tours Scheduled Today" value={toursToday}
            icon={<EventRepeatIcon />} />
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <KpiCard label="Completed Today" value={completedToday}
            icon={<CheckCircleIcon />} color="#00E396" />
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <KpiCard label="Missed Today" value={data.missed_today ?? 0}
            icon={<CancelIcon />}
            color={data.missed_today ? '#FF4560' : undefined} />
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <KpiCard label="7-Day Compliance"
            value={data.compliance_rate_7d != null ? `${data.compliance_rate_7d}%` : '—'}
            sub={`Avg score: ${data.avg_score_7d ?? '—'}%`}
            icon={<RouteIcon />}
            color={data.compliance_rate_7d != null
              ? data.compliance_rate_7d >= 90 ? '#00E396'
                : data.compliance_rate_7d >= 70 ? '#FF9800' : '#FF4560'
              : undefined} />
        </Grid>
      </Grid>

      {/* 7-day daily bar chart */}
      {data.daily_trend.length > 0 && (
        <Paper sx={{ p: 2.5, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2 }}>
          <Typography variant="subtitle2" gutterBottom>7-Day Daily Trend</Typography>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-end', height: 80, mt: 1 }}>
            {data.daily_trend.map((d) => {
              const pct = d.total > 0 ? (d.completed / d.total) * 100 : 0
              const barColor = pct >= 90 ? '#00E396' : pct >= 70 ? '#FF9800' : '#FF4560'
              return (
                <Tooltip key={d.day} title={`${d.day}: ${d.completed}/${d.total} (${pct.toFixed(0)}%)`}>
                  <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0.5 }}>
                    <Box sx={{
                      width: '100%', bgcolor: barColor,
                      height: `${Math.max(pct, 4)}%`,
                      borderRadius: '2px 2px 0 0', opacity: 0.85,
                      minHeight: 4,
                    }} />
                    <Typography variant="caption" sx={{ fontSize: 9, color: 'text.secondary' }}>
                      {new Date(d.day).toLocaleDateString('en-SG', { weekday: 'short' })}
                    </Typography>
                  </Box>
                </Tooltip>
              )
            })}
          </Box>
        </Paper>
      )}

      {/* Recent missed tours */}
      {data.recent_missed.length > 0 && (
        <Paper sx={{ p: 2.5, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Recent Missed Tours</Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Schedule</TableCell>
                <TableCell>Route</TableCell>
                <TableCell>Assigned Guard</TableCell>
                <TableCell>Scheduled At</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.recent_missed.map((m) => (
                <TableRow key={m.id} hover>
                  <TableCell>{m.schedule_name}</TableCell>
                  <TableCell>{m.route_name}</TableCell>
                  <TableCell>{m.assigned_guard ?? '—'}</TableCell>
                  <TableCell sx={{ fontSize: 12 }}>
                    {new Date(m.scheduled_at).toLocaleString('en-SG')}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}
    </Stack>
  )
}

// ── Tab: Schedules ─────────────────────────────────────────────────────────────

function SchedulesTab() {
  const qc = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)

  const { data: schedules = [], isLoading } = useQuery({
    queryKey: ['tour-schedules'],
    queryFn: () => listSchedules(),
    refetchInterval: 30_000,
  })

  const genMutation = useMutation({
    mutationFn: (id: string) => generateOccurrences(id, 7),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tour-occurrences'] }),
  })

  const recurrenceLabel: Record<string, string> = {
    daily: 'Daily', weekdays: 'Weekdays', weekends: 'Weekends', custom: 'Custom',
  }

  return (
    <Stack spacing={2}>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setDialogOpen(true)}>
          New Schedule
        </Button>
      </Box>

      {isLoading
        ? <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}><CircularProgress /></Box>
        : schedules.length === 0
          ? <Alert severity="info">No tour schedules yet. Create one to start tracking compliance.</Alert>
          : (
            <Paper sx={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell>Route / Site</TableCell>
                    <TableCell>Recurrence</TableCell>
                    <TableCell>Time (UTC)</TableCell>
                    <TableCell>Window</TableCell>
                    <TableCell>Guard</TableCell>
                    <TableCell>30d Rate</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {schedules.map((s: TourSchedule) => (
                    <TableRow key={s.id} hover>
                      <TableCell sx={{ fontWeight: 500 }}>{s.name}</TableCell>
                      <TableCell>
                        <Box>{s.route_name}</Box>
                        {s.site_name && <Typography variant="caption" color="text.secondary">{s.site_name}</Typography>}
                      </TableCell>
                      <TableCell>{recurrenceLabel[s.recurrence] ?? s.recurrence}</TableCell>
                      <TableCell>{String(s.scheduled_time).slice(0, 5)}</TableCell>
                      <TableCell>{s.window_minutes}m</TableCell>
                      <TableCell>{s.assigned_guard_name ?? <Typography variant="caption" color="text.secondary">Unassigned</Typography>}</TableCell>
                      <TableCell sx={{ minWidth: 110 }}>
                        <ScoreBar score={s.compliance_rate_30d} />
                      </TableCell>
                      <TableCell>
                        <Chip size="small" label={s.is_active ? 'Active' : 'Inactive'}
                          color={s.is_active ? 'success' : 'default'} />
                      </TableCell>
                      <TableCell>
                        <Button size="small" variant="outlined"
                          disabled={genMutation.isPending}
                          onClick={() => genMutation.mutate(s.id)}
                          title="Generate occurrence records for next 7 days">
                          Generate 7d
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Paper>
          )
      }

      <CreateScheduleDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
    </Stack>
  )
}

// ── Tab: Occurrences ──────────────────────────────────────────────────────────

function OccurrencesTab() {
  const qc = useQueryClient()
  const [dateFrom, setDateFrom] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 7)
    return d.toISOString().slice(0, 10)
  })
  const [dateTo, setDateTo] = useState(() => new Date().toISOString().slice(0, 10))
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedOcc, setSelectedOcc] = useState<TourOccurrence | null>(null)
  const [resolveNotes, setResolveNotes] = useState('')

  const { data: occurrences = [], isLoading } = useQuery({
    queryKey: ['tour-occurrences', dateFrom, dateTo, statusFilter],
    queryFn: () => listOccurrences({
      date_from: dateFrom,
      date_to: dateTo,
      status: statusFilter || undefined,
      limit: 100,
    }),
  })

  const resolveMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      resolveOccurrence(id, { status, notes: resolveNotes }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tour-occurrences'] })
      qc.invalidateQueries({ queryKey: ['compliance-dashboard'] })
      setSelectedOcc(null)
      setResolveNotes('')
    },
  })

  // Status is a closed set and moves to the rail. The date range stays: two
  // date pickers are not a chip list, and a report you scope by date is one
  // you change the dates on constantly.
  const filterGroups: FilterGroup[] = [{
    key: 'status',
    label: 'Status',
    value: statusFilter,
    onChange: setStatusFilter,
    options: [
      { value: '', label: 'All' },
      ...['pending', 'completed', 'missed', 'late', 'incomplete'].map((v) => ({
        value: v, label: v.charAt(0).toUpperCase() + v.slice(1),
      })),
    ],
  }]

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Stack spacing={2} sx={{ flex: 1, minWidth: 0 }}>
      <Stack direction="row" spacing={2} flexWrap="wrap">
        <TextField label="From" type="date" size="small" value={dateFrom}
          onChange={e => setDateFrom(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
        <TextField label="To" type="date" size="small" value={dateTo}
          onChange={e => setDateTo(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
      </Stack>

      {isLoading
        ? <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}><CircularProgress /></Box>
        : occurrences.length === 0
          ? <Alert severity="info">No occurrences found for selected filters. Use "Generate 7d" on a schedule to create records.</Alert>
          : (
            <Paper sx={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Scheduled At</TableCell>
                    <TableCell>Schedule</TableCell>
                    <TableCell>Route</TableCell>
                    <TableCell>Guard</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Compliance</TableCell>
                    <TableCell>Checkpoints</TableCell>
                    <TableCell>Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {occurrences.map((o: TourOccurrence) => (
                    <TableRow key={o.id} hover>
                      <TableCell sx={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                        {new Date(o.scheduled_at).toLocaleString('en-SG')}
                      </TableCell>
                      <TableCell sx={{ fontWeight: 500 }}>{o.schedule_name}</TableCell>
                      <TableCell>
                        <Box>{o.route_name}</Box>
                        {o.site_name && <Typography variant="caption" color="text.secondary">{o.site_name}</Typography>}
                      </TableCell>
                      <TableCell>{o.assigned_guard_name ?? '—'}</TableCell>
                      <TableCell><StatusChip status={o.status} /></TableCell>
                      <TableCell sx={{ minWidth: 110 }}>
                        <ScoreBar score={o.compliance_score} />
                      </TableCell>
                      <TableCell>
                        {o.total_checkpoints != null
                          ? `${o.scanned_checkpoints ?? 0}/${o.total_checkpoints}`
                          : '—'}
                      </TableCell>
                      <TableCell>
                        {o.status === 'pending' && (
                          <Button size="small" variant="outlined"
                            onClick={() => { setSelectedOcc(o); setResolveNotes('') }}>
                            Resolve
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Paper>
          )
      }

      {/* Resolve Dialog */}
      <Dialog open={!!selectedOcc} onClose={() => setSelectedOcc(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Resolve Occurrence</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Typography variant="body2" color="text.secondary">
              Scheduled: {selectedOcc && new Date(selectedOcc.scheduled_at).toLocaleString('en-SG')}
            </Typography>
            <TextField label="Notes (optional)" multiline rows={2} value={resolveNotes}
              onChange={e => setResolveNotes(e.target.value)} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelectedOcc(null)}>Cancel</Button>
          <Button color="error"
            disabled={resolveMutation.isPending}
            onClick={() => selectedOcc && resolveMutation.mutate({ id: selectedOcc.id, status: 'missed' })}>
            Mark Missed
          </Button>
          <Button color="warning"
            disabled={resolveMutation.isPending}
            onClick={() => selectedOcc && resolveMutation.mutate({ id: selectedOcc.id, status: 'incomplete' })}>
            Incomplete
          </Button>
          <Button variant="contained" color="success"
            disabled={resolveMutation.isPending}
            onClick={() => selectedOcc && resolveMutation.mutate({ id: selectedOcc.id, status: 'completed' })}>
            Completed
          </Button>
        </DialogActions>
      </Dialog>
      </Stack>

      <FilterRail groups={filterGroups} storageKey="compliance-occurrences" />
    </Box>
  )
}

// ── Tab: Report ────────────────────────────────────────────────────────────────

function ReportTab() {
  const [dateFrom, setDateFrom] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 30)
    return d.toISOString().slice(0, 10)
  })
  const [dateTo, setDateTo] = useState(() => new Date().toISOString().slice(0, 10))
  const [triggerFetch, setTriggerFetch] = useState(false)

  const { data: report, isLoading, error } = useQuery({
    queryKey: ['compliance-report', dateFrom, dateTo],
    queryFn: () => getComplianceReport({ date_from: dateFrom, date_to: dateTo }),
    enabled: triggerFetch,
  })

  return (
    <Stack spacing={3}>
      <Stack direction="row" spacing={2} alignItems="flex-end" flexWrap="wrap">
        <TextField label="From" type="date" size="small" value={dateFrom}
          onChange={e => setDateFrom(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
        <TextField label="To" type="date" size="small" value={dateTo}
          onChange={e => setDateTo(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
        <Button variant="contained" onClick={() => setTriggerFetch(true)}>
          Generate Report
        </Button>
      </Stack>

      {isLoading && <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}><CircularProgress /></Box>}
      {error && <Alert severity="error">Failed to load report.</Alert>}

      {report && (
        <Stack spacing={3}>
          {/* Summary KPIs */}
          <Grid container spacing={2}>
            {[
              { label: 'Compliance Rate', value: report.summary.compliance_rate != null ? `${report.summary.compliance_rate}%` : '—', color: report.summary.compliance_rate != null ? (report.summary.compliance_rate >= 90 ? '#00E396' : report.summary.compliance_rate >= 70 ? '#FF9800' : '#FF4560') : undefined },
              { label: 'Completed', value: report.summary.completed, color: '#00E396' },
              { label: 'Missed', value: report.summary.missed, color: report.summary.missed ? '#FF4560' : undefined },
              { label: 'Avg Score', value: report.summary.avg_score != null ? `${report.summary.avg_score}%` : '—' },
            ].map(kpi => (
              <Grid key={kpi.label} item xs={6} md={3}>
                <Paper sx={{ p: 2, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2, textAlign: 'center' }}>
                  <Typography variant="body2" color="text.secondary">{kpi.label}</Typography>
                  <Typography variant="h4" fontWeight={700} sx={{ color: kpi.color ?? 'text.primary' }}>
                    {kpi.value}
                  </Typography>
                </Paper>
              </Grid>
            ))}
          </Grid>

          {/* By Guard */}
          {report.by_guard.length > 0 && (
            <Paper sx={{ p: 2, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2 }}>
              <Typography variant="subtitle2" gutterBottom>Compliance by Guard</Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Guard</TableCell>
                    <TableCell>Completed</TableCell>
                    <TableCell>Missed</TableCell>
                    <TableCell>Total</TableCell>
                    <TableCell>Rate</TableCell>
                    <TableCell>Avg Score</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {report.by_guard.map((g, i) => (
                    <TableRow key={i} hover>
                      <TableCell sx={{ fontWeight: 500 }}>{g.guard_name ?? 'Unassigned'}</TableCell>
                      <TableCell>{g.completed}</TableCell>
                      <TableCell>{g.missed}</TableCell>
                      <TableCell>{g.total}</TableCell>
                      <TableCell sx={{ minWidth: 110 }}><ScoreBar score={g.compliance_rate} /></TableCell>
                      <TableCell>{g.avg_score != null ? `${g.avg_score}%` : '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Paper>
          )}

          {/* By Route */}
          {report.by_route.length > 0 && (
            <Paper sx={{ p: 2, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2 }}>
              <Typography variant="subtitle2" gutterBottom>Compliance by Route</Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Route</TableCell>
                    <TableCell>Site</TableCell>
                    <TableCell>Completed</TableCell>
                    <TableCell>Missed</TableCell>
                    <TableCell>Rate</TableCell>
                    <TableCell>Avg Score</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {report.by_route.map((r) => (
                    <TableRow key={r.route_id} hover>
                      <TableCell sx={{ fontWeight: 500 }}>{r.route_name}</TableCell>
                      <TableCell>{r.site_name ?? '—'}</TableCell>
                      <TableCell>{r.completed}</TableCell>
                      <TableCell>{r.missed}</TableCell>
                      <TableCell sx={{ minWidth: 110 }}><ScoreBar score={r.compliance_rate} /></TableCell>
                      <TableCell>{r.avg_score != null ? `${r.avg_score}%` : '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Paper>
          )}
        </Stack>
      )}
    </Stack>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function CompliancePage() {
  const [tab, setTab] = useState(0)

  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <Stack direction="row" alignItems="center" spacing={1.5} mb={3}>
        <RouteIcon sx={{ color: 'primary.main', fontSize: 28 }} />
        <Typography variant="h5" fontWeight={700}>Guard Tour Compliance</Typography>
      </Stack>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 3, borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
        <Tab label="Dashboard" />
        <Tab label="Schedules" />
        <Tab label="Occurrences" />
        <Tab label="Report" />
      </Tabs>

      {tab === 0 && <DashboardTab />}
      {tab === 1 && <SchedulesTab />}
      {tab === 2 && <OccurrencesTab />}
      {tab === 3 && <ReportTab />}
    </Box>
  )
}

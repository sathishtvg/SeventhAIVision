import { useMemo, useState } from 'react'
import {
  Box,
  Tab,
  Tabs,
  Typography,
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Paper,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Alert,
  Divider,
  Tooltip,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import { BrandLoader } from '@/components/common/BrandLoader'
import AssignmentTurnedInIcon from '@mui/icons-material/AssignmentTurnedIn'
import DirectionsCarIcon from '@mui/icons-material/DirectionsCar'
import { openInNewWindow } from '@/lib/popoutWindow'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getShifts, createShift, startShift, endShift, generateHandover,
  getRoutes, getDobEntries, createDobEntry,
  getVisitors, createVisitor, checkinVisitor, getVisitorLogs,
} from '@/api/guards'
import { getSites } from '@/api/sites'
import { getUsers } from '@/api/users'
import { usePermission } from '@/hooks/usePermission'
import { PageHeader } from '@/components/common/PageHeader'

const SEVERITY_COLOR: Record<string, 'error' | 'warning' | 'success' | 'default'> = {
  critical: 'error', high: 'error', medium: 'warning', low: 'success', info: 'default',
}

export default function GuardOps() {
  const [tab, setTab] = useState(0)

  // The VMS shortcut is gated on the guard's own posting: getSites() is
  // already narrowed server-side to the sites this user is assigned to
  // (dependencies/sites.py), so filtering that to vms_enabled gives exactly
  // "a site I'm assigned to that runs visitor management". A guard at a site
  // without VMS never sees the button rather than seeing one that opens an
  // empty grid.
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const hasVms = useMemo(() => (sites ?? []).some((s) => s.vms_enabled), [sites])

  return (
    <Box>
      <PageHeader pageKey="guard-ops" />
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
        {hasVms && (
          <Tooltip title="Open the gatehouse vehicle board in its own full-screen window — every visitor vehicle on site with its entry time and parking expiry">
            <Button
              variant="contained"
              startIcon={<DirectionsCarIcon />}
              onClick={() => openInNewWindow('/vms-onsite', { fullscreen: true })}
            >
              VMS
            </Button>
          </Tooltip>
        )}
      </Stack>
      <Paper sx={{ mb: 2 }}>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} textColor="inherit" indicatorColor="primary">
          <Tab label="Shifts" />
          <Tab label="Patrol Routes" />
          <Tab label="Occurrence Book (DOB)" />
          <Tab label="Visitor Management" />
        </Tabs>
      </Paper>
      {tab === 0 && <ShiftsTab />}
      {tab === 1 && <PatrolRoutesTab />}
      {tab === 2 && <DOBTab />}
      {tab === 3 && <VisitorsTab />}
    </Box>
  )
}

// ── Shifts ─────────────────────────────────────────────────────────────────

function ShiftsTab() {
  const qc = useQueryClient()
  const canManage = usePermission('shift:manage')
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ guard_user_id: '', site_id: '', scheduled_start: '', scheduled_end: '', notes: '' })

  const { data: shifts = [], isLoading } = useQuery({ queryKey: ['shifts'], queryFn: () => getShifts() })
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => getUsers() })

  const createMut = useMutation({
    mutationFn: () => createShift(form),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['shifts'] }); setOpen(false) },
  })
  const [handoverShiftId, setHandoverShiftId] = useState<string | null>(null)
  const [handoverNotes, setHandoverNotes] = useState('')
  const [handoverResult, setHandoverResult] = useState<any | null>(null)

  const startMut = useMutation({
    mutationFn: (id: string) => startShift(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['shifts'] }),
  })
  const endMut = useMutation({
    mutationFn: (id: string) => endShift(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['shifts'] }),
  })
  const handoverMut = useMutation({
    mutationFn: ({ shiftId, notes }: { shiftId: string; notes: string }) =>
      generateHandover(shiftId, undefined, notes || undefined),
    onSuccess: (data) => {
      setHandoverResult(data)
      qc.invalidateQueries({ queryKey: ['shifts'] })
    },
  })

  if (isLoading) return <BrandLoader variant="full" />

  return (
    <Box>
      {canManage && (
        <Button variant="contained" sx={{ mb: 2 }} onClick={() => setOpen(true)}>
          Schedule Shift
        </Button>
      )}
      <Paper>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Guard</TableCell>
              <TableCell>Site</TableCell>
              <TableCell>Scheduled Start</TableCell>
              <TableCell>Scheduled End</TableCell>
              <TableCell>Status</TableCell>
              {canManage && <TableCell>Actions</TableCell>}
            </TableRow>
          </TableHead>
          <TableBody>
            {(shifts as any[]).map((s) => (
              <TableRow key={s.id}>
                <TableCell>{s.guard_name || s.guard_email || s.guard_user_id?.slice(0, 8)}</TableCell>
                <TableCell>{s.site_name || '—'}</TableCell>
                <TableCell>{s.scheduled_start ? new Date(s.scheduled_start).toLocaleString() : '—'}</TableCell>
                <TableCell>{s.scheduled_end ? new Date(s.scheduled_end).toLocaleString() : '—'}</TableCell>
                <TableCell>
                  <Chip
                    label={s.status}
                    color={s.status === 'active' ? 'success' : s.status === 'completed' ? 'default' : 'primary'}
                    size="small"
                  />
                </TableCell>
                {canManage && (
                  <TableCell>
                    <Stack direction="row" spacing={0.5}>
                      {s.status === 'scheduled' && (
                        <Button size="small" onClick={() => startMut.mutate(s.id)}>Start</Button>
                      )}
                      {s.status === 'active' && (
                        <>
                          <Button size="small" color="warning" onClick={() => endMut.mutate(s.id)}>End</Button>
                          <Button
                            size="small" variant="outlined" color="success"
                            startIcon={<AssignmentTurnedInIcon fontSize="small" />}
                            onClick={() => { setHandoverShiftId(s.id); setHandoverNotes(''); setHandoverResult(null) }}
                          >
                            Handover
                          </Button>
                        </>
                      )}
                    </Stack>
                  </TableCell>
                )}
              </TableRow>
            ))}
            {shifts.length === 0 && (
              <TableRow><TableCell colSpan={6} align="center">No shifts scheduled</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Schedule Shift</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField
            select label="Guard" value={form.guard_user_id}
            onChange={(e) => setForm({ ...form, guard_user_id: e.target.value })} fullWidth
          >
            {(users as any[]).map((u) => (
              <MenuItem key={u.id} value={u.id}>{u.full_name || u.email}</MenuItem>
            ))}
          </TextField>
          <TextField
            select label="Site" value={form.site_id}
            onChange={(e) => setForm({ ...form, site_id: e.target.value })} fullWidth
          >
            {(sites as any[]).map((s) => (
              <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
            ))}
          </TextField>
          <TextField
            label="Scheduled Start" type="datetime-local" value={form.scheduled_start}
            onChange={(e) => setForm({ ...form, scheduled_start: e.target.value })} fullWidth
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            label="Scheduled End" type="datetime-local" value={form.scheduled_end}
            onChange={(e) => setForm({ ...form, scheduled_end: e.target.value })} fullWidth
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            label="Notes" value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })} multiline rows={2} fullWidth
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={() => createMut.mutate()} disabled={createMut.isPending}>
            Schedule
          </Button>
        </DialogActions>
      </Dialog>

      {/* Handover Dialog */}
      <Dialog open={!!handoverShiftId} onClose={() => { setHandoverShiftId(null); setHandoverResult(null) }} maxWidth="sm" fullWidth>
        <DialogTitle>Generate Shift Handover Report</DialogTitle>
        <DialogContent sx={{ pt: 2 }}>
          {handoverResult ? (
            <Box>
              <Alert severity="success" sx={{ mb: 2 }}>Handover report generated successfully.</Alert>
              <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1.5 }}>
                {[
                  { label: 'Open Incidents', value: handoverResult.open_incidents },
                  { label: 'Open Alerts', value: handoverResult.open_alerts },
                  { label: 'Patrol Routes Completed', value: `${handoverResult.patrol_routes_completed} / ${handoverResult.patrol_routes_total}` },
                  { label: 'Checkpoints Scanned', value: `${handoverResult.checkpoints_scanned} / ${handoverResult.checkpoints_total}` },
                ].map(({ label, value }) => (
                  <Box key={label} sx={{ p: 1.5, bgcolor: 'rgba(255,255,255,0.05)', borderRadius: 1 }}>
                    <Typography variant="caption" color="text.secondary">{label}</Typography>
                    <Typography variant="h6">{value}</Typography>
                  </Box>
                ))}
              </Box>
              {handoverResult.outgoing_notes && (
                <>
                  <Divider sx={{ my: 2 }} />
                  <Typography variant="body2" color="text.secondary">{handoverResult.outgoing_notes}</Typography>
                </>
              )}
            </Box>
          ) : (
            <TextField
              label="Handover Notes" value={handoverNotes}
              onChange={(e) => setHandoverNotes(e.target.value)}
              multiline rows={4} fullWidth placeholder="Note any open items, security concerns, or instructions for the incoming guard…"
            />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => { setHandoverShiftId(null); setHandoverResult(null) }}>
            {handoverResult ? 'Close' : 'Cancel'}
          </Button>
          {!handoverResult && (
            <Button
              variant="contained" color="success"
              disabled={handoverMut.isPending}
              onClick={() => handoverShiftId && handoverMut.mutate({ shiftId: handoverShiftId, notes: handoverNotes })}
            >
              Generate Report
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </Box>
  )
}

// ── Patrol Routes ──────────────────────────────────────────────────────────

function PatrolRoutesTab() {
  const { data: routes = [], isLoading } = useQuery({ queryKey: ['patrol_routes'], queryFn: () => getRoutes() })

  if (isLoading) return <BrandLoader variant="full" />

  return (
    <Box>
      <Paper>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Route Name</TableCell>
              <TableCell>Site</TableCell>
              <TableCell>Checkpoints</TableCell>
              <TableCell>Status</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(routes as any[]).map((r) => (
              <TableRow key={r.id}>
                <TableCell>{r.name}</TableCell>
                <TableCell>{r.site_name || '—'}</TableCell>
                <TableCell>{r.checkpoint_count || 0}</TableCell>
                <TableCell>
                  <Chip label={r.is_active ? 'Active' : 'Inactive'} color={r.is_active ? 'success' : 'default'} size="small" />
                </TableCell>
              </TableRow>
            ))}
            {routes.length === 0 && (
              <TableRow><TableCell colSpan={4} align="center">No patrol routes configured</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>
    </Box>
  )
}

// ── Daily Occurrence Book ──────────────────────────────────────────────────

function DOBTab() {
  const qc = useQueryClient()
  const canWrite = usePermission('dob:write')
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ entry_type: 'general', body: '', severity: '' })

  const today = new Date().toISOString().split('T')[0]
  const { data: entries = [], isLoading } = useQuery({
    queryKey: ['dob', today],
    queryFn: () => getDobEntries({ date_from: `${today}T00:00:00Z`, date_until: `${today}T23:59:59Z` }),
  })

  const createMut = useMutation({
    mutationFn: () => createDobEntry(form),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['dob'] }); setOpen(false); setForm({ entry_type: 'general', body: '', severity: '' }) },
  })

  if (isLoading) return <BrandLoader variant="full" />

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 2 }}>
        <Typography variant="h6">
          Daily Occurrence Book — {today}
        </Typography>
        {canWrite && (
          <Button variant="contained" onClick={() => setOpen(true)}>+ New Entry</Button>
        )}
      </Box>
      <Paper>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Time</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>Author</TableCell>
              <TableCell>Entry</TableCell>
              <TableCell>Severity</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(entries as any[]).map((e) => (
              <TableRow key={e.id}>
                <TableCell sx={{ whiteSpace: 'nowrap' }}>
                  {new Date(e.occurred_at).toLocaleTimeString()}
                </TableCell>
                <TableCell>
                  <Chip label={e.entry_type.replace(/_/g, ' ')} size="small" variant="outlined" />
                </TableCell>
                <TableCell>{e.author_name || '—'}</TableCell>
                <TableCell sx={{ maxWidth: 400 }}>{e.body}</TableCell>
                <TableCell>
                  {e.severity && (
                    <Chip label={e.severity} color={SEVERITY_COLOR[e.severity] || 'default'} size="small" />
                  )}
                </TableCell>
              </TableRow>
            ))}
            {entries.length === 0 && (
              <TableRow><TableCell colSpan={5} align="center">No entries today</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>New DOB Entry</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField
            select label="Entry Type" value={form.entry_type}
            onChange={(e) => setForm({ ...form, entry_type: e.target.value })} fullWidth
          >
            {['general', 'incident', 'patrol_start', 'patrol_end', 'visitor_arrival',
              'visitor_departure', 'guard_relief', 'equipment_check', 'handover'].map((t) => (
              <MenuItem key={t} value={t}>{t.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</MenuItem>
            ))}
          </TextField>
          <TextField
            label="Entry" value={form.body}
            onChange={(e) => setForm({ ...form, body: e.target.value })}
            multiline rows={4} fullWidth required
          />
          <TextField
            select label="Severity (optional)" value={form.severity}
            onChange={(e) => setForm({ ...form, severity: e.target.value })} fullWidth
          >
            <MenuItem value="">None</MenuItem>
            {['info', 'low', 'medium', 'high', 'critical'].map((s) => (
              <MenuItem key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</MenuItem>
            ))}
          </TextField>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained" onClick={() => createMut.mutate()}
            disabled={!form.body.trim() || createMut.isPending}
          >
            Add Entry
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

// ── Visitor Management ─────────────────────────────────────────────────────

function VisitorsTab() {
  const qc = useQueryClient()
  const canManage = usePermission('visitor:manage')
  const canCheckin = usePermission('visitor:checkin')
  const [viewLogs, setViewLogs] = useState(false)
  const [openCreate, setOpenCreate] = useState(false)
  const [openCheckin, setOpenCheckin] = useState(false)
  const [createForm, setCreateForm] = useState({ full_name: '', company: '', host_name: '', purpose: '', vehicle_plate: '' })
  const [checkinForm, setCheckinForm] = useState({ full_name: '', event_type: 'arrival', badge_number: '' })

  const { data: visitors = [], isLoading } = useQuery({ queryKey: ['visitors'], queryFn: () => getVisitors() })
  const { data: logs = [] } = useQuery({ queryKey: ['visitor_logs'], queryFn: () => getVisitorLogs(), enabled: viewLogs })

  const createMut = useMutation({
    mutationFn: () => createVisitor(createForm),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['visitors'] }); setOpenCreate(false) },
  })
  const checkinMut = useMutation({
    mutationFn: () => checkinVisitor(checkinForm),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['visitor_logs'] }); setOpenCheckin(false) },
  })

  if (isLoading) return <BrandLoader variant="full" />

  return (
    <Box>
      <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
        <Button size="small" variant={!viewLogs ? 'contained' : 'outlined'} onClick={() => setViewLogs(false)}>Pre-registered</Button>
        <Button size="small" variant={viewLogs ? 'contained' : 'outlined'} onClick={() => setViewLogs(true)}>Arrival/Departure Log</Button>
        {canManage && <Button size="small" variant="contained" onClick={() => setOpenCreate(true)}>+ Pre-register</Button>}
        {canCheckin && <Button size="small" variant="contained" color="success" onClick={() => setOpenCheckin(true)}>Check In/Out</Button>}
      </Box>

      {!viewLogs ? (
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Company</TableCell>
                <TableCell>Host</TableCell>
                <TableCell>Expected From</TableCell>
                <TableCell>Expected Until</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(visitors as any[]).map((v) => {
                const isOverstay = v.expected_until && new Date(v.expected_until) < new Date() && !v.departed_at
                return (
                  <TableRow key={v.id} sx={isOverstay ? { bgcolor: 'rgba(255,82,82,0.08)' } : {}}>
                    <TableCell>
                      <Stack direction="row" spacing={0.5} alignItems="center">
                        <span>{v.full_name}</span>
                        {isOverstay && (
                          <Chip label="Overstay" size="small" color="error" />
                        )}
                      </Stack>
                    </TableCell>
                    <TableCell>{v.company || '—'}</TableCell>
                    <TableCell>{v.host_user_name || v.host_name || '—'}</TableCell>
                    <TableCell>{v.expected_from ? new Date(v.expected_from).toLocaleString() : '—'}</TableCell>
                    <TableCell>
                      <Stack direction="row" spacing={0.5} alignItems="center">
                        <span>{v.expected_until ? new Date(v.expected_until).toLocaleString() : '—'}</span>
                        {isOverstay && (
                          <Typography variant="caption" color="error">
                            ({Math.round((Date.now() - new Date(v.expected_until).getTime()) / 60000)}m overdue)
                          </Typography>
                        )}
                      </Stack>
                    </TableCell>
                  </TableRow>
                )
              })}
              {visitors.length === 0 && (
                <TableRow><TableCell colSpan={5} align="center">No pre-registered visitors</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      ) : (
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Time</TableCell>
                <TableCell>Visitor</TableCell>
                <TableCell>Event</TableCell>
                <TableCell>Guard</TableCell>
                <TableCell>Badge</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(logs as any[]).map((l) => (
                <TableRow key={l.id}>
                  <TableCell>{new Date(l.occurred_at).toLocaleString()}</TableCell>
                  <TableCell>{l.visitor_name || 'Walk-in'}</TableCell>
                  <TableCell>
                    <Chip
                      label={l.event_type}
                      color={l.event_type === 'arrival' ? 'success' : l.event_type === 'denied' ? 'error' : 'default'}
                      size="small"
                    />
                  </TableCell>
                  <TableCell>{l.guard_name || '—'}</TableCell>
                  <TableCell>{l.badge_number || '—'}</TableCell>
                </TableRow>
              ))}
              {logs.length === 0 && (
                <TableRow><TableCell colSpan={5} align="center">No log entries</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {/* Pre-register dialog */}
      <Dialog open={openCreate} onClose={() => setOpenCreate(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Pre-register Visitor</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="Full Name" value={createForm.full_name} onChange={(e) => setCreateForm({ ...createForm, full_name: e.target.value })} fullWidth required />
          <TextField label="Company" value={createForm.company} onChange={(e) => setCreateForm({ ...createForm, company: e.target.value })} fullWidth />
          <TextField label="Host Name" value={createForm.host_name} onChange={(e) => setCreateForm({ ...createForm, host_name: e.target.value })} fullWidth />
          <TextField label="Purpose" value={createForm.purpose} onChange={(e) => setCreateForm({ ...createForm, purpose: e.target.value })} fullWidth />
          <TextField label="Vehicle Plate" value={createForm.vehicle_plate} onChange={(e) => setCreateForm({ ...createForm, vehicle_plate: e.target.value })} fullWidth />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenCreate(false)}>Cancel</Button>
          <Button variant="contained" onClick={() => createMut.mutate()} disabled={!createForm.full_name || createMut.isPending}>Pre-register</Button>
        </DialogActions>
      </Dialog>

      {/* Check-in dialog */}
      <Dialog open={openCheckin} onClose={() => setOpenCheckin(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Visitor Check In/Out</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="Visitor Name (walk-in)" value={checkinForm.full_name} onChange={(e) => setCheckinForm({ ...checkinForm, full_name: e.target.value })} fullWidth />
          <TextField select label="Event Type" value={checkinForm.event_type} onChange={(e) => setCheckinForm({ ...checkinForm, event_type: e.target.value })} fullWidth>
            <MenuItem value="arrival">Arrival</MenuItem>
            <MenuItem value="departure">Departure</MenuItem>
            <MenuItem value="denied">Denied Entry</MenuItem>
          </TextField>
          <TextField label="Badge Number" value={checkinForm.badge_number} onChange={(e) => setCheckinForm({ ...checkinForm, badge_number: e.target.value })} fullWidth />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenCheckin(false)}>Cancel</Button>
          <Button variant="contained" onClick={() => checkinMut.mutate()} disabled={checkinMut.isPending}>Log Event</Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

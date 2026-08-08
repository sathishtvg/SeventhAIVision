import { useState } from 'react'
import {
  Box, Typography, Tab, Tabs, Grid, Chip, Button, Dialog,
  DialogTitle, DialogContent, DialogActions, TextField,
  MenuItem, Table, TableHead, TableRow, TableCell, TableBody,
  CircularProgress, Skeleton, IconButton, Tooltip, Alert,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import LockIcon from '@mui/icons-material/Lock'
import LockOpenIcon from '@mui/icons-material/LockOpen'
import BlockIcon from '@mui/icons-material/Block'
import WarningIcon from '@mui/icons-material/Warning'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import KeyIcon from '@mui/icons-material/Key'
import DoorFrontIcon from '@mui/icons-material/DoorFront'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getAccessDashboard, listDoors, createDoor, deactivateDoor,
  listCredentials, createCredential, deactivateCredential,
  listRules, createRule, deleteRule,
  listAccessEvents,
} from '@/api/access'
import GlassCard from '@/components/common/GlassCard'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import { usePermission } from '@/hooks/usePermission'

const EVENT_COLOR: Record<string, string> = {
  granted:     '#00E396',
  denied:      '#FF4560',
  forced:      '#FF4560',
  tamper:      '#FF9800',
  held_open:   '#FF9800',
  door_opened: '#6C63FF',
  door_closed: '#6C63FF',
}

const EVENT_ICON: Record<string, React.ReactNode> = {
  granted:     <CheckCircleIcon sx={{ fontSize: 16 }} />,
  denied:      <BlockIcon sx={{ fontSize: 16 }} />,
  forced:      <WarningIcon sx={{ fontSize: 16 }} />,
  tamper:      <WarningIcon sx={{ fontSize: 16 }} />,
  held_open:   <LockOpenIcon sx={{ fontSize: 16 }} />,
  door_opened: <LockOpenIcon sx={{ fontSize: 16 }} />,
  door_closed: <LockIcon sx={{ fontSize: 16 }} />,
}

// ── Dashboard tab ─────────────────────────────────────────────────────────────

function DashboardTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['access-dashboard'],
    queryFn: getAccessDashboard,
    refetchInterval: 30_000,
  })

  const kpis = [
    { label: 'Total Doors',         value: data?.door_summary.total_doors ?? 0,          color: '#6C63FF' },
    { label: 'Active Doors',        value: data?.door_summary.active_doors ?? 0,          color: '#00E396' },
    { label: 'Total Credentials',   value: data?.credential_summary.total_credentials ?? 0, color: '#6C63FF' },
    { label: 'Active Credentials',  value: data?.credential_summary.active_credentials ?? 0, color: '#00E396' },
    { label: 'Events Today',        value: data?.event_summary.events_today ?? 0,         color: '#2196F3' },
    { label: 'Granted Today',       value: data?.event_summary.granted_today ?? 0,        color: '#00E396' },
    { label: 'Denied Today',        value: data?.event_summary.denied_today ?? 0,         color: '#FF4560' },
    { label: 'Forced / Tamper',     value: (data?.event_summary.forced_today ?? 0) + (data?.event_summary.tamper_today ?? 0), color: '#FF4560' },
  ]

  return (
    <Box>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {kpis.map(kpi => (
          <Grid size={{ xs: 6, sm: 3 }} key={kpi.label}>
            <GlassCard sx={{ p: 2, borderTop: `3px solid ${kpi.color}` }}>
              {isLoading ? <Skeleton height={40} /> : (
                <>
                  <Typography variant="h4" fontWeight={700} sx={{ color: kpi.value > 0 ? kpi.color : 'rgba(255,255,255,0.25)' }}>
                    {kpi.value}
                  </Typography>
                  <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.5)', fontSize: '0.7rem' }}>
                    {kpi.label}
                  </Typography>
                </>
              )}
            </GlassCard>
          </Grid>
        ))}
      </Grid>

      <Typography variant="subtitle2" sx={{ mb: 1, color: 'rgba(255,255,255,0.6)' }}>
        Recent Access Events
      </Typography>
      <GlassCard sx={{ p: 0 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Time</TableCell>
              <TableCell>Door</TableCell>
              <TableCell>Event</TableCell>
              <TableCell>Holder</TableCell>
              <TableCell>Reason</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading && [...Array(5)].map((_, i) => (
              <TableRow key={i}>
                {[...Array(5)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}
              </TableRow>
            ))}
            {data?.recent_events.map(ev => (
              <TableRow key={ev.id} hover>
                <TableCell sx={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                  {new Date(ev.occurred_at).toLocaleTimeString()}
                </TableCell>
                <TableCell>{ev.door_name}</TableCell>
                <TableCell>
                  <Chip
                    icon={EVENT_ICON[ev.event_type] as any}
                    label={ev.event_type.replace('_', ' ')}
                    size="small"
                    sx={{ bgcolor: `${EVENT_COLOR[ev.event_type]}22`, color: EVENT_COLOR[ev.event_type], fontSize: '0.7rem' }}
                  />
                </TableCell>
                <TableCell sx={{ fontSize: '0.8rem' }}>{ev.holder_name ?? '—'}</TableCell>
                <TableCell sx={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.5)' }}>
                  {ev.denial_reason ?? '—'}
                </TableCell>
              </TableRow>
            ))}
            {!isLoading && !data?.recent_events.length && (
              <TableRow>
                <TableCell colSpan={5} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 3 }}>
                  No events yet
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </GlassCard>
    </Box>
  )
}

// ── Doors tab ─────────────────────────────────────────────────────────────────

function DoorsTab() {
  const canWrite = usePermission('access:write')
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ name: '', location: '', door_type: 'card_reader' })

  const { data = [], isLoading } = useQuery({
    queryKey: ['access-doors'],
    queryFn: () => listDoors(),
  })

  const createMut = useMutation({
    mutationFn: createDoor,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['access-doors'] }); setOpen(false) },
  })

  const deactivateMut = useMutation({
    mutationFn: deactivateDoor,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['access-doors'] }),
  })

  return (
    <Box>
      {canWrite && (
        <Button startIcon={<AddIcon />} variant="contained" size="small" sx={{ mb: 2 }}
          onClick={() => setOpen(true)}>
          Add Door
        </Button>
      )}
      <GlassCard sx={{ p: 0 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>Location</TableCell>
              <TableCell>Site</TableCell>
              <TableCell>Status</TableCell>
              {canWrite && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading && [...Array(4)].map((_, i) => (
              <TableRow key={i}>{[...Array(5)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
            ))}
            {data.map(d => (
              <TableRow key={d.id} hover>
                <TableCell fontWeight={600}>{d.name}</TableCell>
                <TableCell>
                  <Chip label={d.door_type.replace('_', ' ')} size="small"
                    sx={{ bgcolor: 'rgba(108,99,255,0.15)', color: '#6C63FF', fontSize: '0.7rem' }} />
                </TableCell>
                <TableCell sx={{ color: 'rgba(255,255,255,0.6)', fontSize: '0.8rem' }}>
                  {d.location ?? '—'}
                </TableCell>
                <TableCell sx={{ color: 'rgba(255,255,255,0.6)', fontSize: '0.8rem' }}>
                  {d.site_name ?? '—'}
                </TableCell>
                <TableCell>
                  <Chip label={d.is_active ? 'Active' : 'Inactive'} size="small"
                    sx={{ bgcolor: d.is_active ? 'rgba(0,227,150,0.15)' : 'rgba(255,69,96,0.15)',
                          color: d.is_active ? '#00E396' : '#FF4560', fontSize: '0.7rem' }} />
                </TableCell>
                {canWrite && (
                  <TableCell align="right">
                    <Tooltip title="Deactivate">
                      <IconButton size="small" color="error"
                        onClick={() => deactivateMut.mutate(d.id)}
                        disabled={!d.is_active}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                )}
              </TableRow>
            ))}
            {!isLoading && !data.length && (
              <TableRow>
                <TableCell colSpan={6} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 3 }}>
                  No doors configured
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </GlassCard>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add Door</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="Name" value={form.name} required size="small"
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          <TextField label="Location" value={form.location} size="small"
            onChange={e => setForm(f => ({ ...f, location: e.target.value }))} />
          <TextField label="Type" value={form.door_type} size="small" select
            onChange={e => setForm(f => ({ ...f, door_type: e.target.value }))}>
            {['card_reader', 'biometric', 'pin', 'combined', 'manual'].map(t => (
              <MenuItem key={t} value={t}>{t.replace('_', ' ')}</MenuItem>
            ))}
          </TextField>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={() => createMut.mutate(form)}
            disabled={!form.name || createMut.isPending}>
            {createMut.isPending ? <CircularProgress size={18} /> : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

// ── Credentials tab ───────────────────────────────────────────────────────────

function CredentialsTab() {
  const canWrite = usePermission('access:write')
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ holder_name: '', credential_type: 'card', credential_ref: '' })

  const { data = [], isLoading } = useQuery({
    queryKey: ['access-credentials'],
    queryFn: () => listCredentials(),
  })

  const createMut = useMutation({
    mutationFn: createCredential,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['access-credentials'] }); setOpen(false) },
  })

  const deactivateMut = useMutation({
    mutationFn: deactivateCredential,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['access-credentials'] }),
  })

  return (
    <Box>
      {canWrite && (
        <Button startIcon={<AddIcon />} variant="contained" size="small" sx={{ mb: 2 }}
          onClick={() => setOpen(true)}>
          Add Credential
        </Button>
      )}
      <GlassCard sx={{ p: 0 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Holder</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>Reference</TableCell>
              <TableCell>Expires</TableCell>
              <TableCell>Status</TableCell>
              {canWrite && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading && [...Array(4)].map((_, i) => (
              <TableRow key={i}>{[...Array(5)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
            ))}
            {data.map(c => (
              <TableRow key={c.id} hover>
                <TableCell fontWeight={600}>
                  {c.user_full_name ?? c.holder_name ?? '—'}
                  {c.user_email && (
                    <Typography variant="caption" display="block" sx={{ color: 'rgba(255,255,255,0.4)' }}>
                      {c.user_email}
                    </Typography>
                  )}
                </TableCell>
                <TableCell>
                  <Chip label={c.credential_type} size="small" icon={<KeyIcon sx={{ fontSize: '14px !important' }} />}
                    sx={{ bgcolor: 'rgba(0,217,192,0.12)', color: '#00D9C0', fontSize: '0.7rem' }} />
                </TableCell>
                <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.78rem', color: 'rgba(255,255,255,0.6)' }}>
                  {c.credential_ref}
                </TableCell>
                <TableCell sx={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.5)' }}>
                  {c.expires_at ? new Date(c.expires_at).toLocaleDateString() : 'Never'}
                </TableCell>
                <TableCell>
                  <Chip label={c.is_active ? 'Active' : 'Revoked'} size="small"
                    sx={{ bgcolor: c.is_active ? 'rgba(0,227,150,0.15)' : 'rgba(255,69,96,0.15)',
                          color: c.is_active ? '#00E396' : '#FF4560', fontSize: '0.7rem' }} />
                </TableCell>
                {canWrite && (
                  <TableCell align="right">
                    <Tooltip title="Revoke">
                      <IconButton size="small" color="error"
                        onClick={() => deactivateMut.mutate(c.id)} disabled={!c.is_active}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                )}
              </TableRow>
            ))}
            {!isLoading && !data.length && (
              <TableRow>
                <TableCell colSpan={6} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 3 }}>
                  No credentials registered
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </GlassCard>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add Credential</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="Holder Name" value={form.holder_name} required size="small"
            onChange={e => setForm(f => ({ ...f, holder_name: e.target.value }))} />
          <TextField label="Credential Type" value={form.credential_type} size="small" select
            onChange={e => setForm(f => ({ ...f, credential_type: e.target.value }))}>
            {['card', 'pin', 'fingerprint', 'face', 'qr'].map(t => (
              <MenuItem key={t} value={t}>{t}</MenuItem>
            ))}
          </TextField>
          <TextField label="Reference / Card UID / PIN" value={form.credential_ref} required size="small"
            onChange={e => setForm(f => ({ ...f, credential_ref: e.target.value }))} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={() => createMut.mutate(form)}
            disabled={!form.holder_name || !form.credential_ref || createMut.isPending}>
            {createMut.isPending ? <CircularProgress size={18} /> : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

// ── Rules tab ─────────────────────────────────────────────────────────────────

function RulesTab() {
  const canWrite = usePermission('access:write')
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ credential_id: '', door_id: '', schedule_days: '1234567', time_from: '', time_to: '' })

  const { data = [], isLoading } = useQuery({ queryKey: ['access-rules'], queryFn: () => listRules() })
  const { data: doors = [] } = useQuery({ queryKey: ['access-doors'], queryFn: () => listDoors({ is_active: true }) })
  const { data: creds = [] } = useQuery({ queryKey: ['access-credentials'], queryFn: () => listCredentials({ is_active: true }) })

  const createMut = useMutation({
    mutationFn: createRule,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['access-rules'] }); setOpen(false) },
  })

  const deleteMut = useMutation({
    mutationFn: deleteRule,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['access-rules'] }),
  })

  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

  return (
    <Box>
      {canWrite && (
        <Button startIcon={<AddIcon />} variant="contained" size="small" sx={{ mb: 2 }}
          onClick={() => setOpen(true)}>
          Add Rule
        </Button>
      )}
      <GlassCard sx={{ p: 0 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Credential Holder</TableCell>
              <TableCell>Door</TableCell>
              <TableCell>Schedule</TableCell>
              <TableCell>Time Window</TableCell>
              <TableCell>Status</TableCell>
              {canWrite && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading && [...Array(3)].map((_, i) => (
              <TableRow key={i}>{[...Array(5)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
            ))}
            {data.map(r => (
              <TableRow key={r.id} hover>
                <TableCell>{r.credential_holder ?? '—'}</TableCell>
                <TableCell fontWeight={600}>{r.door_name}</TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', gap: 0.3 }}>
                    {DAYS.map((d, i) => {
                      const num = String(i + 1)
                      const active = r.schedule_days.includes(num)
                      return (
                        <Box key={d} sx={{
                          width: 22, height: 22, borderRadius: '4px',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          fontSize: '0.6rem', fontWeight: 700,
                          bgcolor: active ? 'rgba(108,99,255,0.3)' : 'rgba(255,255,255,0.05)',
                          color: active ? '#6C63FF' : 'rgba(255,255,255,0.2)',
                        }}>
                          {d[0]}
                        </Box>
                      )
                    })}
                  </Box>
                </TableCell>
                <TableCell sx={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.6)' }}>
                  {r.time_from && r.time_to ? `${r.time_from} – ${r.time_to}` : 'Any time'}
                </TableCell>
                <TableCell>
                  <Chip label={r.is_active ? 'Active' : 'Inactive'} size="small"
                    sx={{ bgcolor: r.is_active ? 'rgba(0,227,150,0.15)' : 'rgba(255,69,96,0.15)',
                          color: r.is_active ? '#00E396' : '#FF4560', fontSize: '0.7rem' }} />
                </TableCell>
                {canWrite && (
                  <TableCell align="right">
                    <Tooltip title="Delete rule">
                      <IconButton size="small" color="error" onClick={() => deleteMut.mutate(r.id)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                )}
              </TableRow>
            ))}
            {!isLoading && !data.length && (
              <TableRow>
                <TableCell colSpan={6} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 3 }}>
                  No access rules configured
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </GlassCard>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add Access Rule</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="Credential" value={form.credential_id} required size="small" select
            onChange={e => setForm(f => ({ ...f, credential_id: e.target.value }))}>
            {creds.map(c => (
              <MenuItem key={c.id} value={c.id}>
                {c.user_full_name ?? c.holder_name} ({c.credential_type})
              </MenuItem>
            ))}
          </TextField>
          <TextField label="Door" value={form.door_id} required size="small" select
            onChange={e => setForm(f => ({ ...f, door_id: e.target.value }))}>
            {doors.map(d => <MenuItem key={d.id} value={d.id}>{d.name}</MenuItem>)}
          </TextField>
          <TextField label="Schedule Days (1=Mon…7=Sun)" value={form.schedule_days} size="small"
            helperText="e.g. 12345 for Mon–Fri"
            onChange={e => setForm(f => ({ ...f, schedule_days: e.target.value }))} />
          <Box sx={{ display: 'flex', gap: 1 }}>
            <TextField label="From" value={form.time_from} size="small" type="time"
              slotProps={{ inputLabel: { shrink: true } }}
              onChange={e => setForm(f => ({ ...f, time_from: e.target.value }))} />
            <TextField label="To" value={form.time_to} size="small" type="time"
              slotProps={{ inputLabel: { shrink: true } }}
              onChange={e => setForm(f => ({ ...f, time_to: e.target.value }))} />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained"
            onClick={() => createMut.mutate({
              credential_id: form.credential_id, door_id: form.door_id,
              schedule_days: form.schedule_days,
              time_from: form.time_from || undefined, time_to: form.time_to || undefined,
            })}
            disabled={!form.credential_id || !form.door_id || createMut.isPending}>
            {createMut.isPending ? <CircularProgress size={18} /> : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

// ── Events tab ────────────────────────────────────────────────────────────────

function EventsTab() {
  const [eventType, setEventType] = useState<string>('')

  const { data = [], isLoading } = useQuery({
    queryKey: ['access-events', eventType],
    queryFn: () => listAccessEvents({ hours: 48, limit: 200, event_type: eventType || undefined }),
    refetchInterval: 30_000,
  })

  const EVENT_TYPES = ['', 'granted', 'denied', 'forced', 'held_open', 'tamper', 'door_opened', 'door_closed']

  const filterGroups: FilterGroup[] = [{
    key: 'eventType',
    label: 'Event Type',
    value: eventType,
    onChange: setEventType,
    options: EVENT_TYPES.map((t) => ({
      value: t,
      label: t ? t.charAt(0).toUpperCase() + t.slice(1).replace(/_/g, ' ') : 'All',
    })),
  }]

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      <GlassCard sx={{ p: 0 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Time</TableCell>
              <TableCell>Door</TableCell>
              <TableCell>Event</TableCell>
              <TableCell>Holder</TableCell>
              <TableCell>Credential</TableCell>
              <TableCell>Reason</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading && [...Array(6)].map((_, i) => (
              <TableRow key={i}>{[...Array(6)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
            ))}
            {data.map(ev => (
              <TableRow key={ev.id} hover>
                <TableCell sx={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                  {new Date(ev.occurred_at).toLocaleString()}
                </TableCell>
                <TableCell fontWeight={500}>{ev.door_name}</TableCell>
                <TableCell>
                  <Chip
                    icon={EVENT_ICON[ev.event_type] as any}
                    label={ev.event_type.replace('_', ' ')}
                    size="small"
                    sx={{ bgcolor: `${EVENT_COLOR[ev.event_type] ?? '#888'}22`,
                          color: EVENT_COLOR[ev.event_type] ?? '#888', fontSize: '0.7rem' }} />
                </TableCell>
                <TableCell sx={{ fontSize: '0.8rem' }}>{ev.holder_name ?? '—'}</TableCell>
                <TableCell sx={{ fontSize: '0.75rem', fontFamily: 'monospace', color: 'rgba(255,255,255,0.5)' }}>
                  {ev.credential_ref ?? '—'}
                </TableCell>
                <TableCell sx={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.4)' }}>
                  {ev.denial_reason ?? '—'}
                </TableCell>
              </TableRow>
            ))}
            {!isLoading && !data.length && (
              <TableRow>
                <TableCell colSpan={6} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 3 }}>
                  No events in the last 48 hours
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </GlassCard>
      </Box>

      <FilterRail groups={filterGroups} storageKey="access-events" />
    </Box>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AccessControlPage() {
  const [tab, setTab] = useState(0)

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 3 }}>
        <DoorFrontIcon sx={{ color: '#6C63FF', fontSize: 28 }} />
        <Typography variant="h5" fontWeight={700}>
          Access Control
        </Typography>
      </Box>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 3,
        '& .MuiTab-root': { fontSize: '0.85rem', minWidth: 100 } }}>
        <Tab label="Overview" />
        <Tab label="Doors" />
        <Tab label="Credentials" />
        <Tab label="Rules" />
        <Tab label="Events" />
      </Tabs>

      {tab === 0 && <DashboardTab />}
      {tab === 1 && <DoorsTab />}
      {tab === 2 && <CredentialsTab />}
      {tab === 3 && <RulesTab />}
      {tab === 4 && <EventsTab />}
    </Box>
  )
}

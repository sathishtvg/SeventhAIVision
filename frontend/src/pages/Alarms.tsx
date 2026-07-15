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
  MenuItem,
  Paper,
  Select,
  Stack,
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
import AddIcon from '@mui/icons-material/Add'
import LockIcon from '@mui/icons-material/Lock'
import LockOpenIcon from '@mui/icons-material/LockOpen'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import SecurityIcon from '@mui/icons-material/Security'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import VpnKeyIcon from '@mui/icons-material/VpnKey'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getAlarmDashboard,
  listPanels,
  listEvents,
  createPanel,
  armPanel,
  disarmPanel,
  createZone,
  bypassZone,
  getPanel,
  rotatePanelKey,
  type AlarmPanel,
  type AlarmEvent,
  type AlarmZone,
} from '@/api/alarms'

// ── Helpers ───────────────────────────────────────────────────────────────────

const ARM_STATE_COLOR: Record<string, string> = {
  disarmed: '#00E396',
  armed_away: '#6C63FF',
  armed_stay: '#6C63FF',
  armed_night: '#6C63FF',
  alarm: '#FF4560',
}

const ARM_STATE_LABEL: Record<string, string> = {
  disarmed: 'Disarmed',
  armed_away: 'Armed Away',
  armed_stay: 'Armed Stay',
  armed_night: 'Armed Night',
  alarm: 'ALARM',
}

const ZONE_STATE_COLOR: Record<string, string> = {
  normal: '#00E396',
  alarm: '#FF4560',
  tamper: '#FF4560',
  fault: '#FF9800',
  bypass: '#9E9E9E',
  open: '#FF9800',
  restored: '#00E396',
}

const SEV_COLOR: Record<string, 'success' | 'error' | 'warning' | 'info' | 'default'> = {
  info: 'default',
  low: 'info',
  medium: 'warning',
  high: 'error',
  critical: 'error',
}

function ZoneDot({ state }: { state: string }) {
  return (
    <Tooltip title={state}>
      <Box sx={{
        width: 10, height: 10, borderRadius: '50%',
        bgcolor: ZONE_STATE_COLOR[state] ?? '#9E9E9E',
        display: 'inline-block',
        boxShadow: state === 'alarm' ? `0 0 6px ${ZONE_STATE_COLOR.alarm}` : undefined,
      }} />
    </Tooltip>
  )
}

function KpiCard({ label, value, icon, color }: {
  label: string; value: number | string; icon?: React.ReactNode; color?: string
}) {
  return (
    <Paper sx={{ p: 2.5, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2 }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Box>
          <Typography variant="body2" color="text.secondary">{label}</Typography>
          <Typography variant="h4" fontWeight={700} sx={{ color: color ?? 'text.primary' }}>{value}</Typography>
        </Box>
        {icon && <Box sx={{ color: color ?? 'primary.main', opacity: 0.8 }}>{icon}</Box>}
      </Stack>
    </Paper>
  )
}

// ── Panel Card ─────────────────────────────────────────────────────────────────

function PanelCard({ panel, onAction }: { panel: AlarmPanel; onAction: () => void }) {
  const qc = useQueryClient()
  const armMut = useMutation({
    mutationFn: ({ mode }: { mode: 'away' | 'stay' | 'night' }) => armPanel(panel.id, mode),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['alarm-dashboard'] }); onAction() },
  })
  const disarmMut = useMutation({
    mutationFn: () => disarmPanel(panel.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['alarm-dashboard'] }); onAction() },
  })

  const armColor = ARM_STATE_COLOR[panel.arm_state] ?? '#9E9E9E'
  const isArmed = panel.arm_state !== 'disarmed'
  const inAlarm = panel.arm_state === 'alarm'

  return (
    <Paper sx={{
      p: 2.5,
      background: inAlarm ? 'rgba(255,69,96,0.08)' : 'rgba(255,255,255,0.04)',
      border: `1px solid ${inAlarm ? 'rgba(255,69,96,0.4)' : 'rgba(255,255,255,0.08)'}`,
      borderRadius: 2,
    }}>
      <Stack direction="row" alignItems="flex-start" justifyContent="space-between" mb={1.5}>
        <Box>
          <Typography fontWeight={600}>{panel.name}</Typography>
          {panel.site_name && (
            <Typography variant="caption" color="text.secondary">{panel.site_name}</Typography>
          )}
        </Box>
        <Chip
          size="small"
          label={ARM_STATE_LABEL[panel.arm_state] ?? panel.arm_state}
          sx={{ bgcolor: `${armColor}22`, color: armColor, fontWeight: 700, border: `1px solid ${armColor}55` }}
        />
      </Stack>

      {/* Zone dots */}
      <Box sx={{ mb: 2 }}>
        <Typography variant="caption" color="text.secondary" sx={{ mr: 1 }}>
          {panel.total_zones ?? 0} zones
        </Typography>
        {(panel.alarm_zones ?? 0) > 0 && (
          <Chip size="small" label={`${panel.alarm_zones} in alarm`} color="error" />
        )}
      </Box>

      <Stack direction="row" spacing={1} alignItems="center">
        <Box sx={{
          width: 8, height: 8, borderRadius: '50%',
          bgcolor: panel.status === 'online' ? '#00E396' : panel.status === 'offline' ? '#FF4560' : '#9E9E9E',
        }} />
        <Typography variant="caption" color="text.secondary">
          {panel.status} {panel.last_contact_at && `· ${new Date(panel.last_contact_at).toLocaleTimeString('en-SG')}`}
        </Typography>
      </Stack>

      <Divider sx={{ my: 1.5, borderColor: 'rgba(255,255,255,0.06)' }} />

      <Stack direction="row" spacing={1} flexWrap="wrap">
        {isArmed ? (
          <Button size="small" startIcon={<LockOpenIcon />} color="warning" variant="outlined"
            disabled={disarmMut.isPending}
            onClick={() => disarmMut.mutate()}>
            Disarm
          </Button>
        ) : (
          <>
            <Button size="small" startIcon={<LockIcon />} variant="outlined"
              disabled={armMut.isPending}
              onClick={() => armMut.mutate({ mode: 'away' })}>
              Away
            </Button>
            <Button size="small" startIcon={<LockIcon />} variant="outlined"
              disabled={armMut.isPending}
              onClick={() => armMut.mutate({ mode: 'stay' })}>
              Stay
            </Button>
          </>
        )}
      </Stack>
    </Paper>
  )
}

// ── Create Panel Dialog ────────────────────────────────────────────────────────

function CreatePanelDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    name: '', model: '', serial_number: '', protocol: 'webhook', host: '', notes: '',
  })
  const [createdKey, setCreatedKey] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () => createPanel({ ...form, host: form.host || undefined }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['alarm-panels'] })
      qc.invalidateQueries({ queryKey: ['alarm-dashboard'] })
      setCreatedKey(data.api_key ?? null)
    },
  })

  const handleClose = () => {
    setForm({ name: '', model: '', serial_number: '', protocol: 'webhook', host: '', notes: '' })
    setCreatedKey(null)
    onClose()
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Register Alarm Panel</DialogTitle>
      <DialogContent>
        {createdKey ? (
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Alert severity="success">Panel registered successfully!</Alert>
            <Alert severity="warning">
              Copy this API key now — it will not be shown again.<br />
              Configure your panel middleware to send events with header: <code>X-Panel-Key: {createdKey}</code>
            </Alert>
            <Paper sx={{ p: 2, bgcolor: 'rgba(0,0,0,0.3)', fontFamily: 'monospace', fontSize: 12, wordBreak: 'break-all' }}>
              {createdKey}
            </Paper>
            <Button startIcon={<ContentCopyIcon />}
              onClick={() => navigator.clipboard.writeText(createdKey)}>
              Copy Key
            </Button>
          </Stack>
        ) : (
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField label="Panel Name" value={form.name} required
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
            <TextField label="Model (e.g. DSC PowerSeries)" value={form.model}
              onChange={e => setForm(f => ({ ...f, model: e.target.value }))} />
            <TextField label="Serial Number" value={form.serial_number}
              onChange={e => setForm(f => ({ ...f, serial_number: e.target.value }))} />
            <FormControl fullWidth>
              <InputLabel>Protocol</InputLabel>
              <Select value={form.protocol} label="Protocol"
                onChange={e => setForm(f => ({ ...f, protocol: e.target.value }))}>
                <MenuItem value="webhook">Webhook (HTTP POST)</MenuItem>
                <MenuItem value="contact_id_tcp">Contact ID / TCP</MenuItem>
                <MenuItem value="mqtt">MQTT</MenuItem>
                <MenuItem value="sdk">Manufacturer SDK</MenuItem>
              </Select>
            </FormControl>
            {form.protocol !== 'webhook' && (
              <TextField label="Host / IP" value={form.host}
                onChange={e => setForm(f => ({ ...f, host: e.target.value }))}
                helperText="IP address or hostname of the panel" />
            )}
            <TextField label="Notes" multiline rows={2} value={form.notes}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>{createdKey ? 'Done' : 'Cancel'}</Button>
        {!createdKey && (
          <Button variant="contained" disabled={!form.name || mutation.isPending}
            onClick={() => mutation.mutate()}>
            {mutation.isPending ? 'Registering…' : 'Register Panel'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}

// ── Panel Detail / Zone Management ─────────────────────────────────────────────

function PanelDetail({ panelId, onBack }: { panelId: string; onBack: () => void }) {
  const qc = useQueryClient()
  const [zoneDialog, setZoneDialog] = useState(false)
  const [zoneForm, setZoneForm] = useState({ zone_number: 1, name: '', zone_type: 'motion' })
  const [rotatedKey, setRotatedKey] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['alarm-panel-detail', panelId],
    queryFn: () => getPanel(panelId),
    refetchInterval: 15_000,
  })

  const addZoneMut = useMutation({
    mutationFn: () => createZone(panelId, { ...zoneForm, zone_number: Number(zoneForm.zone_number) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alarm-panel-detail', panelId] })
      setZoneDialog(false)
    },
  })

  const bypassMut = useMutation({
    mutationFn: (zoneId: string) => bypassZone(zoneId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alarm-panel-detail', panelId] }),
  })

  const rotateKeyMut = useMutation({
    mutationFn: () => rotatePanelKey(panelId),
    onSuccess: (res) => setRotatedKey(res.api_key),
  })

  if (isLoading) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}><CircularProgress /></Box>
  if (!data) return <Alert severity="error">Failed to load panel.</Alert>

  return (
    <Stack spacing={3}>
      <Stack direction="row" alignItems="center" spacing={2}>
        <Button size="small" onClick={onBack}>← Back</Button>
        <Typography variant="h6" fontWeight={600}>{data.name}</Typography>
        {data.site_name && <Typography color="text.secondary">— {data.site_name}</Typography>}
      </Stack>

      <Stack direction="row" spacing={2} alignItems="center">
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setZoneDialog(true)}>
          Add Zone
        </Button>
        <Tooltip title="Generate a new API key — old key is invalidated immediately">
          <Button variant="outlined" color="warning" startIcon={<VpnKeyIcon />}
            disabled={rotateKeyMut.isPending}
            onClick={() => {
              if (window.confirm('This will immediately invalidate the current API key. Continue?')) {
                rotateKeyMut.mutate()
              }
            }}>
            Rotate Key
          </Button>
        </Tooltip>
      </Stack>

      {/* Rotated key reveal dialog */}
      <Dialog open={!!rotatedKey} onClose={() => setRotatedKey(null)} maxWidth="sm" fullWidth>
        <DialogTitle>New API Key Generated</DialogTitle>
        <DialogContent>
          <Alert severity="warning" sx={{ mb: 2 }}>
            This key is shown <strong>once</strong>. Update your panel immediately before closing this dialog.
          </Alert>
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              fullWidth
              value={rotatedKey ?? ''}
              inputProps={{ readOnly: true, style: { fontFamily: 'monospace', fontSize: 13 } }}
              size="small"
            />
            <Button size="small" startIcon={<ContentCopyIcon />}
              onClick={() => navigator.clipboard.writeText(rotatedKey ?? '')}>
              Copy
            </Button>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRotatedKey(null)} variant="contained">Done</Button>
        </DialogActions>
      </Dialog>

      {/* Zones table */}
      <Paper sx={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
        <Typography variant="subtitle2" sx={{ p: 2 }}>Zones ({data.zones?.length ?? 0})</Typography>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>#</TableCell>
              <TableCell>Zone Name</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>State</TableCell>
              <TableCell>Camera</TableCell>
              <TableCell>Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(data.zones ?? []).map((z: AlarmZone) => (
              <TableRow key={z.id} hover>
                <TableCell>{z.zone_number}</TableCell>
                <TableCell sx={{ fontWeight: 500 }}>{z.name}</TableCell>
                <TableCell>{z.zone_type}</TableCell>
                <TableCell>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <ZoneDot state={z.current_state} />
                    <Typography variant="body2"
                      sx={{ color: ZONE_STATE_COLOR[z.current_state] ?? 'text.primary' }}>
                      {z.current_state}
                    </Typography>
                  </Stack>
                </TableCell>
                <TableCell>{z.camera_name ?? '—'}</TableCell>
                <TableCell>
                  <Button size="small" variant="outlined"
                    color={z.current_state === 'bypass' ? 'warning' : 'default'}
                    disabled={bypassMut.isPending}
                    onClick={() => bypassMut.mutate(z.id)}>
                    {z.current_state === 'bypass' ? 'Unbypass' : 'Bypass'}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {(data.zones ?? []).length === 0 && (
              <TableRow>
                <TableCell colSpan={6} align="center">
                  <Typography color="text.secondary" variant="body2">No zones configured</Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>

      {/* Recent events */}
      {(data.recent_events ?? []).length > 0 && (
        <Paper sx={{ p: 2, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Recent Events</Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Event</TableCell>
                <TableCell>Zone</TableCell>
                <TableCell>Severity</TableCell>
                <TableCell>Time</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.recent_events.map((e: AlarmEvent) => (
                <TableRow key={e.id} hover>
                  <TableCell sx={{ fontFamily: 'monospace', fontSize: 12 }}>{e.event_type}</TableCell>
                  <TableCell>{e.zone_name ?? (e.zone_number != null ? `Zone ${e.zone_number}` : '—')}</TableCell>
                  <TableCell><Chip size="small" label={e.severity} color={SEV_COLOR[e.severity]} /></TableCell>
                  <TableCell sx={{ fontSize: 12 }}>{new Date(e.occurred_at).toLocaleString('en-SG')}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}

      {/* Add Zone Dialog */}
      <Dialog open={zoneDialog} onClose={() => setZoneDialog(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add Zone</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField label="Zone Number" type="number" value={zoneForm.zone_number}
              onChange={e => setZoneForm(f => ({ ...f, zone_number: Number(e.target.value) }))}
              inputProps={{ min: 1, max: 255 }} />
            <TextField label="Zone Name" value={zoneForm.name} required
              onChange={e => setZoneForm(f => ({ ...f, name: e.target.value }))} />
            <FormControl fullWidth>
              <InputLabel>Zone Type</InputLabel>
              <Select value={zoneForm.zone_type} label="Zone Type"
                onChange={e => setZoneForm(f => ({ ...f, zone_type: e.target.value }))}>
                {['motion', 'door', 'window', 'glass_break', 'smoke', 'heat', 'panic', 'tamper', '24hr', 'vibration'].map(t => (
                  <MenuItem key={t} value={t}>{t.replace('_', ' ')}</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setZoneDialog(false)}>Cancel</Button>
          <Button variant="contained" disabled={!zoneForm.name || addZoneMut.isPending}
            onClick={() => addZoneMut.mutate()}>Add</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}

// ── Tab: Dashboard ─────────────────────────────────────────────────────────────

function DashboardTab({ onSelectPanel }: { onSelectPanel: (id: string) => void }) {
  const [createOpen, setCreateOpen] = useState(false)
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['alarm-dashboard'],
    queryFn: getAlarmDashboard,
    refetchInterval: 30_000,
  })

  if (isLoading) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}><CircularProgress /></Box>
  if (error || !data) return <Alert severity="error">Failed to load alarm dashboard.</Alert>

  const ps = data.panel_summary
  const zs = data.zone_summary
  const es = data.event_summary

  return (
    <Stack spacing={3}>
      <Stack direction="row" justifyContent="flex-end">
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>
          Register Panel
        </Button>
      </Stack>

      {/* KPIs */}
      <Grid container spacing={2}>
        <Grid size={{ xs: 6, md: 3 }}>
          <KpiCard label="Panels Online" value={`${ps.online}/${ps.total_panels}`}
            icon={<SecurityIcon />} color={ps.online === ps.total_panels ? '#00E396' : '#FF9800'} />
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <KpiCard label="Currently in Alarm" value={ps.in_alarm}
            icon={<ErrorIcon />} color={ps.in_alarm ? '#FF4560' : undefined} />
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <KpiCard label="Zones in Alarm" value={zs.zones_in_alarm}
            icon={<WarningAmberIcon />} color={zs.zones_in_alarm ? '#FF4560' : undefined} />
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <KpiCard label="Alarm Events (24h)" value={es.alarms_24h}
            icon={<WarningAmberIcon />} color={es.alarms_24h ? '#FF9800' : undefined} />
        </Grid>
      </Grid>

      {/* Panel cards */}
      {data.panels.length === 0 ? (
        <Alert severity="info">No alarm panels registered. Click "Register Panel" to add one.</Alert>
      ) : (
        <Grid container spacing={2}>
          {data.panels.map(p => (
            <Grid key={p.id} item xs={12} sm={6} md={4}>
              <Box onClick={() => onSelectPanel(p.id)} sx={{ cursor: 'pointer' }}>
                <PanelCard panel={p} onAction={() => refetch()} />
              </Box>
            </Grid>
          ))}
        </Grid>
      )}

      {/* Recent alarms */}
      {data.recent_alarms.length > 0 && (
        <Paper sx={{ p: 2.5, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Recent Alarms</Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Event</TableCell>
                <TableCell>Panel</TableCell>
                <TableCell>Zone</TableCell>
                <TableCell>Severity</TableCell>
                <TableCell>Time</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.recent_alarms.map(a => (
                <TableRow key={a.id} hover>
                  <TableCell sx={{ fontFamily: 'monospace', fontSize: 12 }}>{a.event_type}</TableCell>
                  <TableCell>{a.panel_name}</TableCell>
                  <TableCell>{a.zone_name ?? (a.zone_number != null ? `Zone ${a.zone_number}` : '—')}</TableCell>
                  <TableCell><Chip size="small" label={a.severity} color={SEV_COLOR[a.severity as keyof typeof SEV_COLOR] ?? 'default'} /></TableCell>
                  <TableCell sx={{ fontSize: 12 }}>{new Date(a.occurred_at).toLocaleString('en-SG')}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}

      <CreatePanelDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </Stack>
  )
}

// ── Tab: Events ────────────────────────────────────────────────────────────────

function EventsTab() {
  const [severityFilter, setSeverityFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  const { data: events = [], isLoading } = useQuery({
    queryKey: ['alarm-events', severityFilter, typeFilter],
    queryFn: () => listEvents({
      severity: severityFilter || undefined,
      event_type: typeFilter || undefined,
      limit: 100,
    }),
    refetchInterval: 30_000,
  })

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2}>
        <FormControl size="small" sx={{ minWidth: 130 }}>
          <InputLabel>Severity</InputLabel>
          <Select value={severityFilter} label="Severity"
            onChange={e => setSeverityFilter(e.target.value)}>
            <MenuItem value="">All</MenuItem>
            <MenuItem value="critical">Critical</MenuItem>
            <MenuItem value="high">High</MenuItem>
            <MenuItem value="medium">Medium</MenuItem>
            <MenuItem value="low">Low</MenuItem>
            <MenuItem value="info">Info</MenuItem>
          </Select>
        </FormControl>
        <TextField size="small" label="Event Type" value={typeFilter}
          onChange={e => setTypeFilter(e.target.value)}
          placeholder="e.g. zone_alarm" />
      </Stack>

      {isLoading
        ? <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}><CircularProgress /></Box>
        : (
          <Paper sx={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Time</TableCell>
                  <TableCell>Panel / Site</TableCell>
                  <TableCell>Event</TableCell>
                  <TableCell>Zone</TableCell>
                  <TableCell>Description</TableCell>
                  <TableCell>Severity</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {events.map((e: AlarmEvent) => (
                  <TableRow key={e.id} hover sx={{
                    bgcolor: e.severity === 'critical' ? 'rgba(255,69,96,0.06)'
                      : e.severity === 'high' ? 'rgba(255,69,96,0.03)' : undefined,
                  }}>
                    <TableCell sx={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                      {new Date(e.occurred_at).toLocaleString('en-SG')}
                    </TableCell>
                    <TableCell>
                      <Box>{e.panel_name}</Box>
                      {e.site_name && <Typography variant="caption" color="text.secondary">{e.site_name}</Typography>}
                    </TableCell>
                    <TableCell sx={{ fontFamily: 'monospace', fontSize: 12 }}>{e.event_type}</TableCell>
                    <TableCell>
                      {e.zone_name ?? (e.zone_number != null ? `Zone ${e.zone_number}` : '—')}
                    </TableCell>
                    <TableCell sx={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {e.description ?? '—'}
                    </TableCell>
                    <TableCell>
                      <Chip size="small" label={e.severity} color={SEV_COLOR[e.severity] ?? 'default'} />
                    </TableCell>
                  </TableRow>
                ))}
                {events.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} align="center">
                      <Typography color="text.secondary" variant="body2">No events found</Typography>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </Paper>
        )
      }
    </Stack>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function AlarmsPage() {
  const [tab, setTab] = useState(0)
  const [selectedPanelId, setSelectedPanelId] = useState<string | null>(null)

  if (selectedPanelId) {
    return (
      <Box sx={{ p: { xs: 2, md: 3 } }}>
        <PanelDetail panelId={selectedPanelId} onBack={() => setSelectedPanelId(null)} />
      </Box>
    )
  }

  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <Stack direction="row" alignItems="center" spacing={1.5} mb={3}>
        <SecurityIcon sx={{ color: 'primary.main', fontSize: 28 }} />
        <Typography variant="h5" fontWeight={700}>Alarm Panel Integration</Typography>
      </Stack>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 3, borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
        <Tab label="Dashboard" />
        <Tab label="Event Log" />
      </Tabs>

      {tab === 0 && <DashboardTab onSelectPanel={setSelectedPanelId} />}
      {tab === 1 && <EventsTab />}
    </Box>
  )
}

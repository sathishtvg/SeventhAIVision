import { useState } from 'react'
import {
  Box, Typography, Grid, Paper, Chip, Button, Tabs, Tab,
  Table, TableHead, TableRow, TableCell, TableBody, IconButton,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Tooltip, CircularProgress, LinearProgress, Alert,
} from '@mui/material'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import VideocamIcon from '@mui/icons-material/Videocam'
import BatteryAlertIcon from '@mui/icons-material/BatteryAlert'
import AssignmentIndIcon from '@mui/icons-material/AssignmentInd'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import StopIcon from '@mui/icons-material/Stop'
import AddIcon from '@mui/icons-material/Add'
import PersonAddIcon from '@mui/icons-material/PersonAdd'
import PersonOffIcon from '@mui/icons-material/PersonOff'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import StorageIcon from '@mui/icons-material/Storage'
import LinkIcon from '@mui/icons-material/Link'
import {
  getBWCDashboard, listCameras, registerCamera, assignCamera, unassignCamera,
  startRecording, stopRecording, listRecordings, listBWCEvents, linkRecordingToIncident,
} from '@/api/bwc'
import type { BodyCamera, BWCRecording } from '@/api/bwc'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'

// ── KPI Card ──────────────────────────────────────────────────────────────────

function KpiCard({ label, value, icon, color, warn }: {
  label: string; value: number; icon: React.ReactNode; color: string; warn?: boolean
}) {
  return (
    <Paper sx={{
      p: 2, display: 'flex', alignItems: 'center', gap: 2, borderRadius: 2,
      ...(warn && value > 0 ? { border: '1px solid #FF4560' } : {}),
    }}>
      <Box sx={{
        width: 44, height: 44, borderRadius: '12px', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        background: `${color}22`, color,
      }}>
        {icon}
      </Box>
      <Box>
        <Typography variant="h5" fontWeight={700} color={warn && value > 0 ? 'error.main' : 'text.primary'}>
          {value}
        </Typography>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
      </Box>
    </Paper>
  )
}

// ── Status chip ───────────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, 'default' | 'success' | 'info' | 'warning' | 'error'> = {
  available: 'success', assigned: 'info', recording: 'error',
  docked: 'default', low_battery: 'warning', fault: 'error', retired: 'default',
}

function CameraStatusChip({ status }: { status: string }) {
  return <Chip label={status.replace('_', ' ')} color={STATUS_COLOR[status] ?? 'default'} size="small" />
}

// ── Battery bar ───────────────────────────────────────────────────────────────

function BatteryBar({ pct }: { pct?: number }) {
  if (pct == null) return <Typography variant="caption" color="text.disabled">—</Typography>
  const color = pct <= 15 ? '#FF4560' : pct <= 30 ? '#FF9800' : '#00E396'
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 80 }}>
      <LinearProgress
        variant="determinate" value={pct}
        sx={{
          flex: 1, height: 6, borderRadius: 3,
          bgcolor: 'rgba(255,255,255,0.08)',
          '& .MuiLinearProgress-bar': { bgcolor: color, borderRadius: 3 },
        }}
      />
      <Typography variant="caption" sx={{ minWidth: 30 }}>{pct}%</Typography>
    </Box>
  )
}

// ── Storage bar ───────────────────────────────────────────────────────────────

function StorageBar({ used, total }: { used?: number; total: number }) {
  if (used == null) return <Typography variant="caption" color="text.disabled">—</Typography>
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0
  const color = pct >= 90 ? '#FF4560' : pct >= 75 ? '#FF9800' : '#6C63FF'
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 100 }}>
      <LinearProgress
        variant="determinate" value={pct}
        sx={{
          flex: 1, height: 6, borderRadius: 3,
          bgcolor: 'rgba(255,255,255,0.08)',
          '& .MuiLinearProgress-bar': { bgcolor: color, borderRadius: 3 },
        }}
      />
      <Typography variant="caption" sx={{ minWidth: 40 }}>{used.toFixed(0)}/{total}GB</Typography>
    </Box>
  )
}

// ── Register Camera Dialog ────────────────────────────────────────────────────

function RegisterDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ serial_number: '', model: '', firmware_version: '', storage_total_gb: '64', notes: '' })
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm(f => ({ ...f, [k]: e.target.value }))
  const mut = useMutation({
    mutationFn: () => registerCamera({ ...form, storage_total_gb: parseFloat(form.storage_total_gb) || 64 }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['bwc-cameras'] }); onClose() },
  })
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Register Body Camera</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <TextField label="Serial Number *" value={form.serial_number} onChange={set('serial_number')} size="small" fullWidth />
        <TextField label="Model" value={form.model} onChange={set('model')} size="small" fullWidth />
        <TextField label="Firmware Version" value={form.firmware_version} onChange={set('firmware_version')} size="small" fullWidth />
        <TextField label="Storage (GB)" type="number" value={form.storage_total_gb} onChange={set('storage_total_gb')} size="small" fullWidth />
        <TextField label="Notes" value={form.notes} onChange={set('notes')} size="small" fullWidth multiline rows={2} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.serial_number || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Register'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Assign Dialog ─────────────────────────────────────────────────────────────

function AssignDialog({ camera, onClose }: { camera: BodyCamera; onClose: () => void }) {
  const qc = useQueryClient()
  const [userId, setUserId] = useState('')
  const [notes, setNotes] = useState('')
  const mut = useMutation({
    mutationFn: () => assignCamera(camera.id, userId, notes || undefined),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['bwc-cameras'] }); onClose() },
  })
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Assign Camera {camera.serial_number}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <Alert severity="info" sx={{ fontSize: '0.8rem' }}>
          Enter the User ID (UUID) of the officer to assign this camera to.
        </Alert>
        <TextField label="User ID (UUID) *" value={userId} onChange={e => setUserId(e.target.value)} size="small" fullWidth />
        <TextField label="Notes" value={notes} onChange={e => setNotes(e.target.value)} size="small" fullWidth />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!userId || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Assign'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Start Recording Dialog ────────────────────────────────────────────────────

function StartRecordingDialog({ camera, onClose }: { camera: BodyCamera; onClose: () => void }) {
  const qc = useQueryClient()
  const [title, setTitle] = useState('')
  const [trigger, setTrigger] = useState('manual')
  const mut = useMutation({
    mutationFn: () => startRecording(camera.id, { title: title || undefined, trigger_type: trigger }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bwc-cameras'] })
      qc.invalidateQueries({ queryKey: ['bwc-recordings'] })
      onClose()
    },
  })
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <FiberManualRecordIcon color="error" fontSize="small" />
        Start Recording — {camera.serial_number}
      </DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <TextField label="Recording Title (optional)" value={title} onChange={e => setTitle(e.target.value)} size="small" fullWidth />
        <TextField
          select label="Trigger Type" value={trigger}
          onChange={e => setTrigger(e.target.value)} size="small" fullWidth
          SelectProps={{ native: true }}
        >
          {['manual', 'pre_event', 'auto_incident', 'panic', 'scheduled'].map(t => (
            <option key={t} value={t}>{t}</option>
          ))}
        </TextField>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="error" disabled={mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Start Recording'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Cameras Tab ───────────────────────────────────────────────────────────────

function CamerasTab() {
  const [registerOpen, setRegisterOpen] = useState(false)
  const [assignTarget, setAssignTarget] = useState<BodyCamera | null>(null)
  const [recordTarget, setRecordTarget] = useState<BodyCamera | null>(null)
  const qc = useQueryClient()

  const { data: cameras = [], isLoading } = useQuery({
    queryKey: ['bwc-cameras'],
    queryFn: () => listCameras(),
    refetchInterval: 15000,
  })

  const unassignMut = useMutation({
    mutationFn: (id: string) => unassignCamera(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['bwc-cameras'] }),
  })

  const stopMut = useMutation({
    mutationFn: ({ cameraId, recordingId }: { cameraId: string; recordingId: string }) =>
      stopRecording(cameraId, recordingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bwc-cameras'] })
      qc.invalidateQueries({ queryKey: ['bwc-recordings'] })
    },
  })

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setRegisterOpen(true)}>
          Register Camera
        </Button>
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
      ) : (
        <Grid container spacing={2}>
          {cameras.map(cam => (
            <Grid size={{ xs: 12, sm: 6, md: 4 }} key={cam.id}>
              <Paper sx={{
                p: 2, borderRadius: 2,
                border: cam.is_recording ? '1px solid #FF4560' : cam.battery_pct != null && cam.battery_pct <= 20 ? '1px solid #FF9800' : undefined,
              }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
                  <Box>
                    <Typography variant="subtitle1" fontWeight={700} fontFamily="monospace">
                      {cam.serial_number}
                    </Typography>
                    {cam.model && <Typography variant="caption" color="text.secondary">{cam.model}</Typography>}
                  </Box>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    {cam.is_recording && (
                      <FiberManualRecordIcon sx={{ color: '#FF4560', fontSize: 14, animation: 'pulse 1s infinite' }} />
                    )}
                    <CameraStatusChip status={cam.status} />
                  </Box>
                </Box>

                {cam.assigned_user_name && (
                  <Typography variant="caption" color="primary.main" sx={{ display: 'block', mb: 1 }}>
                    Assigned: {cam.assigned_user_name}
                  </Typography>
                )}

                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, mb: 1.5 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <BatteryAlertIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                    <BatteryBar pct={cam.battery_pct} />
                  </Box>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <StorageIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                    <StorageBar used={cam.storage_used_gb} total={cam.storage_total_gb} />
                  </Box>
                </Box>

                <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                  {(cam.status === 'available' || cam.status === 'docked') && (
                    <Tooltip title="Assign to Officer">
                      <IconButton size="small" onClick={() => setAssignTarget(cam)}>
                        <PersonAddIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}
                  {cam.status === 'assigned' && !cam.is_recording && (
                    <>
                      <Tooltip title="Start Recording">
                        <IconButton size="small" color="error" onClick={() => setRecordTarget(cam)}>
                          <PlayArrowIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Return Camera">
                        <IconButton size="small" color="warning" onClick={() => unassignMut.mutate(cam.id)}>
                          <PersonOffIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </>
                  )}
                  {cam.is_recording && (
                    <Tooltip title="Stop Recording">
                      <IconButton size="small" color="error" onClick={() => {
                        // Find active recording for this camera from recordings query
                        stopMut.mutate({ cameraId: cam.id, recordingId: 'active' })
                      }}>
                        <StopIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}
                </Box>

                {cam.last_sync_at && (
                  <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 1 }}>
                    Last sync: {new Date(cam.last_sync_at).toLocaleString()}
                  </Typography>
                )}
              </Paper>
            </Grid>
          ))}
          {cameras.length === 0 && (
            <Grid size={12}>
              <Paper sx={{ p: 4, textAlign: 'center' }}>
                <Typography color="text.secondary">No body cameras registered yet.</Typography>
              </Paper>
            </Grid>
          )}
        </Grid>
      )}

      <RegisterDialog open={registerOpen} onClose={() => setRegisterOpen(false)} />
      {assignTarget && <AssignDialog camera={assignTarget} onClose={() => setAssignTarget(null)} />}
      {recordTarget && <StartRecordingDialog camera={recordTarget} onClose={() => setRecordTarget(null)} />}
    </Box>
  )
}

// ── Link Incident Dialog ──────────────────────────────────────────────────────

function LinkIncidentDialog({ recording, onClose }: { recording: BWCRecording; onClose: () => void }) {
  const qc = useQueryClient()
  const [incidentId, setIncidentId] = useState(recording.incident_id ?? '')

  const mut = useMutation({
    mutationFn: () => linkRecordingToIncident(recording.id, incidentId.trim() || null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bwc-recordings'] })
      onClose()
    },
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <LinkIcon fontSize="small" /> Link Recording to Incident
      </DialogTitle>
      <DialogContent sx={{ pt: 2 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Recording: <strong>{recording.title || recording.id.slice(0, 8)}</strong>
        </Typography>
        <TextField
          label="Incident ID (UUID)"
          value={incidentId}
          onChange={e => setIncidentId(e.target.value)}
          size="small"
          fullWidth
          placeholder="Paste incident UUID here"
          helperText={recording.incident_id ? `Currently linked to: ${recording.incident_id.slice(0, 8)}…` : 'Leave blank to unlink'}
        />
        {mut.isError && (
          <Alert severity="error" sx={{ mt: 1 }}>
            {(mut.error as Error)?.message || 'Link failed — check the incident ID'}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Save Link'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Recordings Tab ────────────────────────────────────────────────────────────

function RecordingsTab() {
  const [statusFilter, setStatusFilter] = useState('')
  const [linkTarget, setLinkTarget] = useState<BWCRecording | null>(null)
  const qc = useQueryClient()

  const { data: _recData, isLoading } = useQuery({
    queryKey: ['bwc-recordings', statusFilter],
    queryFn: () => listRecordings(statusFilter ? { status: statusFilter } : undefined),
    refetchInterval: 15000,
  })
  const recordings = _recData?.items ?? []

  const { data: cameras = [] } = useQuery({ queryKey: ['bwc-cameras'], queryFn: () => listCameras() })

  const stopMut = useMutation({
    mutationFn: (rec: BWCRecording) => stopRecording(rec.camera_id, rec.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bwc-recordings'] })
      qc.invalidateQueries({ queryKey: ['bwc-cameras'] })
    },
  })

  const fmtDur = (s?: number) => {
    if (s == null) return '—'
    const m = Math.floor(s / 60)
    const sec = s % 60
    return `${m}m ${sec}s`
  }

  const filterGroups: FilterGroup[] = [{
    key: 'status',
    label: 'Status',
    value: statusFilter,
    onChange: setStatusFilter,
    options: [
      { value: '', label: 'All' },
      ...['recording', 'completed', 'failed', 'deleted'].map((v) => ({
        value: v, label: v.charAt(0).toUpperCase() + v.slice(1),
      })),
    ],
  }]

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
      ) : (
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Camera</TableCell>
                <TableCell>Officer</TableCell>
                <TableCell>Title</TableCell>
                <TableCell>Trigger</TableCell>
                <TableCell>Started</TableCell>
                <TableCell>Duration</TableCell>
                <TableCell>Size</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Incident</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {recordings.map(r => (
                <TableRow key={r.id} hover>
                  <TableCell>
                    <Typography variant="caption" fontFamily="monospace">{r.serial_number || r.camera_id.slice(0, 8)}</Typography>
                  </TableCell>
                  <TableCell><Typography variant="body2">{r.user_name || '—'}</Typography></TableCell>
                  <TableCell>
                    <Typography variant="body2">{r.title || <span style={{ color: '#888' }}>Untitled</span>}</Typography>
                  </TableCell>
                  <TableCell>
                    <Chip label={r.trigger_type} size="small" variant="outlined" />
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption">{new Date(r.started_at).toLocaleString()}</Typography>
                  </TableCell>
                  <TableCell><Typography variant="caption">{fmtDur(r.duration_seconds)}</Typography></TableCell>
                  <TableCell>
                    <Typography variant="caption">{r.file_size_mb ? `${r.file_size_mb.toFixed(1)} MB` : '—'}</Typography>
                  </TableCell>
                  <TableCell>
                    {r.status === 'recording'
                      ? <Chip label="REC" color="error" size="small" icon={<FiberManualRecordIcon sx={{ fontSize: '10px!important' }} />} />
                      : <Chip label={r.status} color={r.status === 'completed' ? 'success' : 'default'} size="small" />}
                  </TableCell>
                  <TableCell>
                    {r.incident_id
                      ? <Chip label={r.incident_id.slice(0, 8)} size="small" color="info" variant="outlined" sx={{ fontFamily: 'monospace' }} />
                      : <Typography variant="caption" color="text.disabled">—</Typography>}
                  </TableCell>
                  <TableCell align="right">
                    {r.status === 'recording' && (
                      <Tooltip title="Stop Recording">
                        <IconButton size="small" color="error" onClick={() => stopMut.mutate(r)}>
                          <StopIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    <Tooltip title={r.incident_id ? 'Update Incident Link' : 'Link to Incident'}>
                      <IconButton size="small" onClick={() => setLinkTarget(r)}>
                        <LinkIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
              {recordings.length === 0 && (
                <TableRow>
                  <TableCell colSpan={10} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                    No recordings found
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}
      {linkTarget && <LinkIncidentDialog recording={linkTarget} onClose={() => setLinkTarget(null)} />}
      </Box>

      <FilterRail groups={filterGroups} storageKey="bwc-recordings" />
    </Box>
  )
}

// ── Events Tab ────────────────────────────────────────────────────────────────

function EventsTab() {
  const { data: events = [], isLoading } = useQuery({
    queryKey: ['bwc-events'],
    queryFn: () => listBWCEvents(),
    refetchInterval: 20000,
  })

  const EVENT_COLOR: Record<string, string> = {
    assigned: '#6C63FF', returned: '#9E9E9E',
    recording_started: '#FF4560', recording_stopped: '#00E396',
    low_battery: '#FF9800', fault: '#FF4560',
    docked: '#00D9C0', undocked: '#6C63FF',
  }

  return (
    <Box>
      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
      ) : (
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Time</TableCell>
                <TableCell>Camera</TableCell>
                <TableCell>Officer</TableCell>
                <TableCell>Event</TableCell>
                <TableCell>Detail</TableCell>
                <TableCell>Battery</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {events.map(e => (
                <TableRow key={e.id} hover>
                  <TableCell>
                    <Typography variant="caption">{new Date(e.occurred_at).toLocaleString()}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" fontFamily="monospace">{e.serial_number || e.camera_id.slice(0, 8)}</Typography>
                  </TableCell>
                  <TableCell><Typography variant="body2">{e.user_name || '—'}</Typography></TableCell>
                  <TableCell>
                    <Chip
                      label={e.event_type.replace(/_/g, ' ')}
                      size="small"
                      sx={{ bgcolor: `${EVENT_COLOR[e.event_type] ?? '#555'}22`, color: EVENT_COLOR[e.event_type] ?? 'text.primary' }}
                    />
                  </TableCell>
                  <TableCell><Typography variant="caption">{e.detail || '—'}</Typography></TableCell>
                  <TableCell>
                    {e.battery_pct != null
                      ? <Typography variant="caption" color={e.battery_pct <= 20 ? 'error.main' : 'text.secondary'}>{e.battery_pct}%</Typography>
                      : <Typography variant="caption" color="text.disabled">—</Typography>}
                  </TableCell>
                </TableRow>
              ))}
              {events.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                    No events yet
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}
    </Box>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function BWCPage() {
  const [tab, setTab] = useState(0)

  const { data: dash } = useQuery({
    queryKey: ['bwc-dashboard'],
    queryFn: getBWCDashboard,
    refetchInterval: 15000,
  })

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" fontWeight={700} mb={3}>
        Body Worn Camera Management
      </Typography>

      {/* KPI row */}
      <Grid container spacing={2} mb={3}>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Total Cameras" value={dash?.total_cameras ?? 0} icon={<VideocamIcon />} color="#6C63FF" />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Currently Recording" value={dash?.recording ?? 0} icon={<FiberManualRecordIcon />} color="#FF4560" />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Low Battery" value={dash?.low_battery ?? 0} icon={<BatteryAlertIcon />} color="#FF9800" warn />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Recordings Today" value={dash?.recordings_today ?? 0} icon={<AssignmentIndIcon />} color="#00D9C0" />
        </Grid>
      </Grid>

      {/* Alert banner */}
      {(dash?.storage_warning ?? 0) > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {dash!.storage_warning} camera(s) have storage usage above 90%. Please dock and offload recordings.
        </Alert>
      )}

      {/* Tabs */}
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Tab label="Cameras" icon={<VideocamIcon fontSize="small" />} iconPosition="start" />
        <Tab label="Recordings" icon={<PlayArrowIcon fontSize="small" />} iconPosition="start" />
        <Tab label="Event Log" icon={<StorageIcon fontSize="small" />} iconPosition="start" />
      </Tabs>

      {tab === 0 && <CamerasTab />}
      {tab === 1 && <RecordingsTab />}
      {tab === 2 && <EventsTab />}
    </Box>
  )
}

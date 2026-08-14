import { useState } from 'react'
import {
  Box,
  Tabs,
  Tab,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  IconButton,
  Skeleton,
  Paper,
  Tooltip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import PauseIcon from '@mui/icons-material/Pause'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import ScheduleIcon from '@mui/icons-material/Schedule'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { RestrictedZoneDialog } from '@/components/common/RestrictedZoneDialog'
import { ZonePolygonEditor } from '@/components/common/ZonePolygonEditor'
import type { ZonePoint } from '@/components/common/ZoneDrawOverlay'
import { getCameras } from '@/api/cameras'
import { getZones, deleteZone, bulkBypassZones, bulkRestoreZones, setZoneSchedule, getCrowdZones, createCrowdZone, deleteCrowdZone } from '@/api/zones'
import type { RestrictedZone } from '@/types/api'
import { PageHeader } from '@/components/common/PageHeader'

const SEVERITY_COLORS: Record<string, 'success' | 'warning' | 'error' | 'info'> = {
  low: 'info', medium: 'warning', high: 'error', critical: 'error',
}

/** Fill colour for the drawn crowd polygon — mirrors the map used by
 *  RestrictedZoneDialog so both editors read the same way. */
const CROWD_SEVERITY_HEX: Record<string, string> = {
  low: '#00E396', medium: '#FF9800', high: '#FF4560', critical: '#FF4560',
}

interface TabPanelProps { children: React.ReactNode; value: number; index: number }
function TabPanel({ children, value, index }: TabPanelProps) {
  return <Box hidden={value !== index}>{value === index && children}</Box>
}

function SkeletonRows({ cols, rows = 4 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, i) => (
        <TableRow key={i}>
          {Array.from({ length: cols }).map((__, j) => <TableCell key={j}><Skeleton /></TableCell>)}
        </TableRow>
      ))}
    </>
  )
}

// ──────────────────────────────────────────────────────────
// Zone schedule dialog
// ──────────────────────────────────────────────────────────

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function ZoneScheduleDialog({ zone, open, onClose }: { zone: RestrictedZone; open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [enabled, setEnabled] = useState(zone.schedule_enabled)
  const [timezone, setTimezone] = useState(zone.schedule_timezone || 'UTC')
  const [activeDays, setActiveDays] = useState<number[]>(zone.active_days?.length ? zone.active_days : [0, 1, 2, 3, 4, 5, 6])
  const [startTime, setStartTime] = useState(zone.active_start_time?.slice(0, 5) || '00:00')
  const [endTime, setEndTime] = useState(zone.active_end_time?.slice(0, 5) || '23:59')

  const mutation = useMutation({
    mutationFn: () => setZoneSchedule(zone.id, {
      enabled,
      timezone,
      active_days: activeDays,
      active_start_time: startTime,
      active_end_time: endTime,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['zones'] }); onClose() },
  })

  const toggleDay = (day: number) => {
    setActiveDays((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort(),
    )
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Zone Schedule — {zone.name}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        <FormControl fullWidth>
          <InputLabel>Schedule Mode</InputLabel>
          <Select value={enabled ? 'scheduled' : 'always'} label="Schedule Mode"
            onChange={(e) => setEnabled(e.target.value === 'scheduled')}>
            <MenuItem value="always">Always Active (no schedule)</MenuItem>
            <MenuItem value="scheduled">Time-based Schedule</MenuItem>
          </Select>
        </FormControl>
        {enabled && (
          <>
            <TextField
              label="Timezone"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              helperText="e.g. Asia/Singapore, UTC, America/New_York"
              fullWidth
            />
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
                Active Days
              </Typography>
              <Stack direction="row" spacing={0.5} flexWrap="wrap">
                {DAY_LABELS.map((label, idx) => (
                  <Chip
                    key={idx}
                    label={label}
                    size="small"
                    onClick={() => toggleDay(idx)}
                    color={activeDays.includes(idx) ? 'primary' : 'default'}
                    variant={activeDays.includes(idx) ? 'filled' : 'outlined'}
                    sx={{ cursor: 'pointer' }}
                  />
                ))}
              </Stack>
            </Box>
            <Stack direction="row" spacing={2}>
              <TextField
                label="Start Time"
                type="time"
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                slotProps={{ htmlInput: { step: 60 } }}
                fullWidth
              />
              <TextField
                label="End Time"
                type="time"
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
                slotProps={{ htmlInput: { step: 60 } }}
                fullWidth
              />
            </Stack>
          </>
        )}
        {mutation.error && <Typography color="error" variant="caption">{String(mutation.error)}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => mutation.mutate()} disabled={mutation.isPending || (enabled && activeDays.length === 0)}>
          Save Schedule
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ──────────────────────────────────────────────────────────
// Restricted zones tab
// ──────────────────────────────────────────────────────────

function formatBypassUntil(bypassUntil: string | null): string | null {
  if (!bypassUntil) return null
  const dt = new Date(bypassUntil)
  if (dt <= new Date()) return null
  const mins = Math.round((dt.getTime() - Date.now()) / 60000)
  return mins < 60 ? `${mins}m` : `${Math.round(mins / 60)}h ${mins % 60}m`
}

function RestrictedZonesTable() {
  const queryClient = useQueryClient()
  const { data: zones, isLoading } = useQuery({ queryKey: ['zones'], queryFn: getZones })
  const [scheduleZone, setScheduleZone] = useState<RestrictedZone | null>(null)
  const [addOpen, setAddOpen] = useState(false)

  const { mutate: remove } = useMutation({
    mutationFn: deleteZone,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['zones'] }),
  })

  const { mutate: bypass, isPending: bypassPending } = useMutation({
    mutationFn: ({ id, minutes }: { id: string; minutes: number }) => bulkBypassZones([id], minutes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['zones'] }),
  })

  const { mutate: restore, isPending: restorePending } = useMutation({
    mutationFn: (id: string) => bulkRestoreZones([id]),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['zones'] }),
  })

  const isBusy = bypassPending || restorePending

  return (
    <>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
        <PermissionGuard permission="zone:manage">
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setAddOpen(true)}>
            Add Zone
          </Button>
        </PermissionGuard>
      </Box>
      <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Camera ID</TableCell>
              <TableCell>Severity</TableCell>
              <TableCell>Applies To</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Bypass</TableCell>
              <TableCell>Schedule</TableCell>
              <TableCell>Polygon</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading
              ? <SkeletonRows cols={9} />
              : zones?.length === 0
              ? (
                  <TableRow>
                    <TableCell colSpan={9} align="center" sx={{ py: 4 }}>
                      <Typography color="text.secondary">No restricted zones defined</Typography>
                    </TableCell>
                  </TableRow>
                )
              : zones?.map((zone) => {
                  const bypassRemaining = formatBypassUntil(zone.bypass_until)
                  const isBypassed = !!bypassRemaining
                  return (
                    <TableRow key={zone.id} hover>
                      <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{zone.name}</Typography></TableCell>
                      <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{zone.camera_id.slice(0, 8)}…</Typography></TableCell>
                      <TableCell><Chip label={zone.severity} size="small" color={SEVERITY_COLORS[zone.severity] ?? 'default'} sx={{ textTransform: 'capitalize' }} /></TableCell>
                      <TableCell>
                        <Stack direction="row" spacing={0.5}>
                          {(zone.applies_to_modules ?? ['intrusion']).map((m) => (
                            <Chip key={m} label={m} size="small" variant="outlined" sx={{ textTransform: 'capitalize' }} />
                          ))}
                        </Stack>
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={!zone.is_active ? 'Inactive' : zone.is_currently_active ? 'Active' : 'Scheduled off'}
                          size="small"
                          color={!zone.is_active ? 'default' : zone.is_currently_active ? 'success' : 'warning'}
                          variant={zone.is_active ? 'filled' : 'outlined'}
                        />
                      </TableCell>
                      <TableCell>
                        {isBypassed ? (
                          <Stack direction="row" spacing={0.5} alignItems="center">
                            <Chip label={`Bypassed ${bypassRemaining}`} size="small" color="warning" />
                            <PermissionGuard permission="zone:manage">
                              <Tooltip title="Restore now">
                                <IconButton size="small" onClick={() => restore(zone.id)} disabled={isBusy}>
                                  <PlayArrowIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            </PermissionGuard>
                          </Stack>
                        ) : (
                          <PermissionGuard permission="zone:manage">
                            <Tooltip title="Bypass for 60 minutes">
                              <IconButton size="small" onClick={() => bypass({ id: zone.id, minutes: 60 })} disabled={isBusy || !zone.is_active}>
                                <PauseIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          </PermissionGuard>
                        )}
                      </TableCell>
                      <TableCell>
                        {zone.schedule_enabled ? (
                          <Tooltip title={`${DAY_LABELS.filter((_, i) => zone.active_days?.includes(i)).join(',')} ${zone.active_start_time?.slice(0,5)}–${zone.active_end_time?.slice(0,5)} ${zone.schedule_timezone}`}>
                            <Chip label="Scheduled" size="small" color="info" variant="outlined" />
                          </Tooltip>
                        ) : (
                          <Typography variant="caption" color="text.secondary">Always on</Typography>
                        )}
                      </TableCell>
                      <TableCell><Typography variant="caption" color="text.secondary">{zone.polygon.length} pts</Typography></TableCell>
                      <TableCell align="right">
                        <PermissionGuard permission="zone:manage">
                          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                            <Tooltip title="Edit schedule">
                              <IconButton size="small" onClick={() => setScheduleZone(zone)}>
                                <ScheduleIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                            <Tooltip title="Deactivate zone">
                              <IconButton size="small" color="error" onClick={() => remove(zone.id)}>
                                <DeleteIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          </Stack>
                        </PermissionGuard>
                      </TableCell>
                    </TableRow>
                  )
                })}
          </TableBody>
        </Table>
      </TableContainer>
      {scheduleZone && (
        <ZoneScheduleDialog zone={scheduleZone} open={!!scheduleZone} onClose={() => setScheduleZone(null)} />
      )}
      <RestrictedZoneDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </>
  )
}

// ──────────────────────────────────────────────────────────
// Crowd zones tab
// ──────────────────────────────────────────────────────────

function CrowdZoneDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [cameraId, setCameraId] = useState('')
  const [name, setName] = useState('')
  const [maxCapacity, setMaxCapacity] = useState('10')
  const [severity, setSeverity] = useState('medium')
  // Drawn on the camera's own feed, exactly like a restricted zone. This was
  // a JSON textarea of normalized {x,y} pairs — nobody can look at a camera
  // view and work out that the escalator landing is x 0.62-0.81, y 0.4-0.95.
  const [polygon, setPolygon] = useState<ZonePoint[]>([])

  const { data: cameras = [] } = useQuery({ queryKey: ['cameras'], queryFn: getCameras, enabled: open })

  const mutation = useMutation({
    mutationFn: () =>
      createCrowdZone({ camera_id: cameraId, name, polygon, max_capacity: parseInt(maxCapacity, 10) || 10, severity }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['crowd-zones'] }); onClose() },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Crowd Zone</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        {/* Pick the camera by name. Asking an operator to paste a UUID meant
            they had to go and look one up somewhere else first. */}
        <FormControl fullWidth>
          <InputLabel>Camera</InputLabel>
          <Select
            value={cameraId}
            label="Camera"
            onChange={(e) => { setCameraId(e.target.value); setPolygon([]) }}
          >
            {cameras.map((c: any) => (
              <MenuItem key={c.id} value={c.id}>
                {c.name}{c.location ? ` — ${c.location}` : ''}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <TextField label="Zone Name" value={name} onChange={(e) => setName(e.target.value)} fullWidth />
        <TextField
          label="Max Capacity (persons)"
          type="number"
          value={maxCapacity}
          onChange={(e) => setMaxCapacity(e.target.value)}
          slotProps={{ htmlInput: { min: 1 } }}
          fullWidth
        />
        <FormControl fullWidth>
          <InputLabel>Alert Severity</InputLabel>
          <Select value={severity} label="Alert Severity" onChange={(e) => setSeverity(e.target.value)}>
            {['low', 'medium', 'high', 'critical'].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </Select>
        </FormControl>
        {cameraId ? (
          <ZonePolygonEditor
            cameraId={cameraId}
            value={polygon}
            onChange={setPolygon}
            severityColor={CROWD_SEVERITY_HEX[severity]}
          />
        ) : (
          <Typography variant="caption" color="text.disabled">
            Select a camera to draw the zone on its live feed.
          </Typography>
        )}
        {mutation.error && <Typography color="error" variant="caption">{String(mutation.error)}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => mutation.mutate()}
          disabled={!cameraId || !name || polygon.length < 3 || mutation.isPending}
        >
          Add
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function CrowdZonesTable() {
  const qc = useQueryClient()
  const { data: zones, isLoading } = useQuery({ queryKey: ['crowd-zones'], queryFn: getCrowdZones })
  const [dialogOpen, setDialogOpen] = useState(false)
  const { mutate: remove } = useMutation({
    mutationFn: deleteCrowdZone,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['crowd-zones'] }),
  })

  return (
    <>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
        <PermissionGuard permission="zone:manage">
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setDialogOpen(true)}>
            Add Crowd Zone
          </Button>
        </PermissionGuard>
      </Box>
      <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Camera ID</TableCell>
              <TableCell>Max Capacity</TableCell>
              <TableCell>Severity</TableCell>
              <TableCell>Active</TableCell>
              <TableCell>Polygon Points</TableCell>
              <TableCell>Created</TableCell>
              <TableCell align="right">Action</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading
              ? <SkeletonRows cols={8} />
              : zones?.length === 0
              ? (
                  <TableRow>
                    <TableCell colSpan={8} align="center" sx={{ py: 4 }}>
                      <Typography color="text.secondary">No crowd zones defined</Typography>
                    </TableCell>
                  </TableRow>
                )
              : zones?.map((zone) => (
                  <TableRow key={zone.id} hover>
                    <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{zone.name}</Typography></TableCell>
                    <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{zone.camera_id.slice(0, 8)}…</Typography></TableCell>
                    <TableCell><Typography variant="body2">{zone.max_capacity}</Typography></TableCell>
                    <TableCell><Chip label={zone.severity} size="small" color={SEVERITY_COLORS[zone.severity] ?? 'default'} sx={{ textTransform: 'capitalize' }} /></TableCell>
                    <TableCell><Chip label={zone.is_active ? 'Yes' : 'No'} size="small" variant="outlined" color={zone.is_active ? 'success' : 'default'} /></TableCell>
                    <TableCell><Typography variant="caption" color="text.secondary">{zone.polygon.length} pts</Typography></TableCell>
                    <TableCell><Typography variant="caption" color="text.secondary">{new Date(zone.created_at).toLocaleDateString()}</Typography></TableCell>
                    <TableCell align="right">
                      <PermissionGuard permission="zone:manage">
                        <Tooltip title="Deactivate zone">
                          <IconButton size="small" color="error" onClick={() => remove(zone.id)}>
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </PermissionGuard>
                    </TableCell>
                  </TableRow>
                ))}
          </TableBody>
        </Table>
      </TableContainer>
      <CrowdZoneDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
    </>
  )
}

// ──────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────

export default function Zones() {
  const [tab, setTab] = useState(0)
  return (
    <Box>
      <PageHeader title="Zones" subtitle="Areas drawn on a camera view that the AI treats as restricted or monitored" />
      <GlassCard>
        <Box sx={{ borderBottom: 1, borderColor: 'rgba(255,255,255,0.1)' }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab label="Restricted Zones" />
            <Tab label="Crowd Zones" />
          </Tabs>
        </Box>
        <TabPanel value={tab} index={0}><RestrictedZonesTable /></TabPanel>
        <TabPanel value={tab} index={1}><CrowdZonesTable /></TabPanel>
      </GlassCard>
    </Box>
  )
}

import React, { useState, useEffect, useRef } from 'react'
import {
  Box, Typography, Grid, Paper, Chip, Stack, Divider, CircularProgress,
  Button, Dialog, DialogTitle, DialogContent, DialogActions,
  TextField, MenuItem, Tab, Tabs, Table, TableBody, TableCell,
  TableHead, TableRow, Tooltip, IconButton, Alert,
} from '@mui/material'
import DirectionsCarIcon from '@mui/icons-material/DirectionsCar'
import MyLocationIcon from '@mui/icons-material/MyLocation'
import AddIcon from '@mui/icons-material/Add'
import RefreshIcon from '@mui/icons-material/Refresh'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import LocalShippingIcon from '@mui/icons-material/LocalShipping'
import FlagIcon from '@mui/icons-material/Flag'
import RouteIcon from '@mui/icons-material/Route'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getGPSDashboard, listVehicles, createVehicle, listGeofences,
  listGeofenceEvents, getVehicleJourneys, VEHICLE_TYPES,
} from '@/api/gps'
import type { Vehicle, GeofenceEvent, VehicleJourney } from '@/api/gps'

// ── status helpers ────────────────────────────────────────────────────────────
const STATUS_COLOR: Record<string, string> = {
  moving: '#00E396',
  idle: '#FF9800',
  offline: '#9E9E9E',
}
const STATUS_LABEL: Record<string, string> = {
  moving: 'Moving',
  idle: 'Idle',
  offline: 'Offline',
}

function statusDot(status: string) {
  return (
    <Box component="span" sx={{
      display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
      bgcolor: STATUS_COLOR[status] ?? '#9E9E9E', mr: 1, verticalAlign: 'middle',
    }} />
  )
}

function relativeTime(ts: string | null): string {
  if (!ts) return 'Never'
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

// ── Simple map placeholder (Leaflet not installed) ─────────────────────────
function VehicleMapView({ vehicles }: { vehicles: Vehicle[] }) {
  const active = vehicles.filter(v => v.last_lat !== null && v.last_lon !== null)
  if (active.length === 0) {
    return (
      <Box sx={{ height: 380, display: 'flex', alignItems: 'center', justifyContent: 'center',
        bgcolor: 'rgba(255,255,255,0.03)', borderRadius: 2, border: '1px solid rgba(255,255,255,0.1)' }}>
        <Stack alignItems="center" spacing={1}>
          <MyLocationIcon sx={{ fontSize: 48, color: 'text.secondary', opacity: 0.4 }} />
          <Typography variant="body2" color="text.secondary">No vehicles with active GPS position</Typography>
        </Stack>
      </Box>
    )
  }

  // Find bounding box
  const lats = active.map(v => v.last_lat as number)
  const lons = active.map(v => v.last_lon as number)
  const minLat = Math.min(...lats), maxLat = Math.max(...lats)
  const minLon = Math.min(...lons), maxLon = Math.max(...lons)
  const latRange = (maxLat - minLat) || 0.01
  const lonRange = (maxLon - minLon) || 0.01

  const toX = (lon: number) => ((lon - minLon) / lonRange) * 90 + 5
  const toY = (lat: number) => (1 - (lat - minLat) / latRange) * 90 + 5

  return (
    <Box sx={{ height: 380, bgcolor: 'rgba(10,20,40,0.8)', borderRadius: 2,
      border: '1px solid rgba(255,255,255,0.1)', position: 'relative', overflow: 'hidden' }}>
      {/* Grid lines */}
      {[20, 40, 60, 80].map(p => (
        <React.Fragment key={p}>
          <Box sx={{ position: 'absolute', left: `${p}%`, top: 0, bottom: 0,
            borderLeft: '1px solid rgba(255,255,255,0.05)' }} />
          <Box sx={{ position: 'absolute', top: `${p}%`, left: 0, right: 0,
            borderTop: '1px solid rgba(255,255,255,0.05)' }} />
        </React.Fragment>
      ))}
      {active.map(v => (
        <Tooltip key={v.id} title={
          <Box>
            <Typography variant="caption" fontWeight={700}>{v.name}</Typography>
            {v.plate_number && <Typography variant="caption" display="block">{v.plate_number}</Typography>}
            <Typography variant="caption" display="block">
              {v.last_speed !== null ? `${v.last_speed} km/h` : 'Speed unknown'}
            </Typography>
            <Typography variant="caption" display="block" color="text.secondary">
              {v.last_lat?.toFixed(5)}, {v.last_lon?.toFixed(5)}
            </Typography>
            <Typography variant="caption" display="block" color="text.secondary">
              {relativeTime(v.last_position_at)}
            </Typography>
          </Box>
        } arrow>
          <Box sx={{
            position: 'absolute',
            left: `${toX(v.last_lon as number)}%`,
            top: `${toY(v.last_lat as number)}%`,
            transform: 'translate(-50%, -50%)',
            cursor: 'pointer',
            zIndex: 10,
          }}>
            <Box sx={{
              width: 14, height: 14, borderRadius: '50%',
              bgcolor: STATUS_COLOR[v.current_status],
              border: '2px solid rgba(255,255,255,0.6)',
              boxShadow: `0 0 8px ${STATUS_COLOR[v.current_status]}`,
              animation: v.current_status === 'moving' ? 'pulse 1.5s infinite' : 'none',
              '@keyframes pulse': {
                '0%': { boxShadow: `0 0 0 0 ${STATUS_COLOR['moving']}88` },
                '70%': { boxShadow: `0 0 0 8px ${STATUS_COLOR['moving']}00` },
                '100%': { boxShadow: `0 0 0 0 ${STATUS_COLOR['moving']}00` },
              },
            }} />
            <Typography variant="caption" sx={{
              position: 'absolute', top: 16, left: '50%', transform: 'translateX(-50%)',
              whiteSpace: 'nowrap', fontSize: '0.6rem', color: 'rgba(255,255,255,0.8)',
              bgcolor: 'rgba(0,0,0,0.6)', px: 0.5, borderRadius: 0.5,
            }}>
              {v.name}
            </Typography>
          </Box>
        </Tooltip>
      ))}
      <Typography variant="caption" sx={{ position: 'absolute', bottom: 8, right: 12,
        color: 'rgba(255,255,255,0.3)', fontSize: '0.6rem' }}>
        Relative position map • {active.length} vehicles
      </Typography>
    </Box>
  )
}

// ── vehicle card ──────────────────────────────────────────────────────────────
function VehicleCard({ vehicle, onClick }: { vehicle: Vehicle; onClick: () => void }) {
  const typeInfo = VEHICLE_TYPES.find(t => t.value === vehicle.vehicle_type)
  return (
    <Paper onClick={onClick} sx={{
      p: 2, cursor: 'pointer', borderRadius: 2,
      border: `1px solid ${STATUS_COLOR[vehicle.current_status]}44`,
      bgcolor: 'rgba(255,255,255,0.03)',
      transition: 'all 0.2s',
      '&:hover': { bgcolor: 'rgba(255,255,255,0.07)', borderColor: STATUS_COLOR[vehicle.current_status] },
    }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={1}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography variant="h6" sx={{ lineHeight: 1 }}>{typeInfo?.icon ?? '🚗'}</Typography>
          <Box>
            <Typography variant="subtitle2" fontWeight={700}>{vehicle.name}</Typography>
            {vehicle.plate_number && (
              <Typography variant="caption" color="text.secondary">{vehicle.plate_number}</Typography>
            )}
          </Box>
        </Stack>
        <Chip
          label={STATUS_LABEL[vehicle.current_status]}
          size="small"
          sx={{ bgcolor: STATUS_COLOR[vehicle.current_status] + '22',
            color: STATUS_COLOR[vehicle.current_status], fontWeight: 700, fontSize: '0.7rem' }}
        />
      </Stack>
      <Divider sx={{ my: 1, borderColor: 'rgba(255,255,255,0.06)' }} />
      <Stack spacing={0.5}>
        {vehicle.driver_name && (
          <Typography variant="caption" color="text.secondary">
            Driver: <span style={{ color: '#ccc' }}>{vehicle.driver_name}</span>
          </Typography>
        )}
        {vehicle.last_lat !== null && (
          <Typography variant="caption" color="text.secondary">
            Position: <span style={{ color: '#ccc' }}>{vehicle.last_lat.toFixed(4)}, {vehicle.last_lon?.toFixed(4)}</span>
          </Typography>
        )}
        {vehicle.last_speed !== null && (
          <Typography variant="caption" color="text.secondary">
            Speed: <span style={{ color: STATUS_COLOR[vehicle.current_status] }}>{vehicle.last_speed} km/h</span>
          </Typography>
        )}
        <Typography variant="caption" color="text.secondary">
          Updated: {relativeTime(vehicle.last_position_at)}
        </Typography>
      </Stack>
    </Paper>
  )
}

// ── journey log dialog ────────────────────────────────────────────────────────
function JourneyDialog({ vehicle, onClose }: { vehicle: Vehicle; onClose: () => void }) {
  const { data: journeys = [], isLoading } = useQuery({
    queryKey: ['journeys', vehicle.id],
    queryFn: () => getVehicleJourneys(vehicle.id),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth
      PaperProps={{ sx: { bgcolor: '#111827', border: '1px solid rgba(255,255,255,0.1)' } }}>
      <DialogTitle>
        <Stack direction="row" alignItems="center" spacing={1}>
          <RouteIcon />
          <span>Journey Log — {vehicle.name}</span>
        </Stack>
      </DialogTitle>
      <DialogContent>
        {isLoading ? <CircularProgress size={24} /> : (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Start</TableCell>
                <TableCell>End</TableCell>
                <TableCell>Duration</TableCell>
                <TableCell>Distance</TableCell>
                <TableCell>Max Speed</TableCell>
                <TableCell>Avg Speed</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {journeys.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} align="center" sx={{ color: 'text.secondary', py: 4 }}>
                    No journeys recorded
                  </TableCell>
                </TableRow>
              )}
              {journeys.map(j => {
                const dur = j.end_at
                  ? Math.floor((new Date(j.end_at).getTime() - new Date(j.start_at).getTime()) / 60000)
                  : null
                return (
                  <TableRow key={j.id} hover>
                    <TableCell>{new Date(j.start_at).toLocaleString()}</TableCell>
                    <TableCell>{j.end_at ? new Date(j.end_at).toLocaleString() : '—'}</TableCell>
                    <TableCell>{dur !== null ? `${dur} min` : 'Active'}</TableCell>
                    <TableCell>{Number(j.distance_km).toFixed(2)} km</TableCell>
                    <TableCell>{j.max_speed ? `${j.max_speed} km/h` : '—'}</TableCell>
                    <TableCell>{j.avg_speed ? `${j.avg_speed} km/h` : '—'}</TableCell>
                    <TableCell>
                      <Chip label={j.status} size="small"
                        color={j.status === 'active' ? 'success' : 'default'} />
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── add vehicle dialog ────────────────────────────────────────────────────────
function AddVehicleDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ name: '', vehicle_type: 'patrol_car', plate_number: '', make: '', model: '', color: '' })
  const mut = useMutation({
    mutationFn: createVehicle,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['gps-dashboard'] }); onClose() },
  })
  const set = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth
      PaperProps={{ sx: { bgcolor: '#111827', border: '1px solid rgba(255,255,255,0.1)' } }}>
      <DialogTitle>Add Vehicle</DialogTitle>
      <DialogContent>
        <Stack spacing={2} mt={1}>
          <TextField label="Vehicle Name *" size="small" value={form.name} onChange={e => set('name', e.target.value)} />
          <TextField label="Vehicle Type" select size="small" value={form.vehicle_type} onChange={e => set('vehicle_type', e.target.value)}>
            {VEHICLE_TYPES.map(t => <MenuItem key={t.value} value={t.value}>{t.icon} {t.label}</MenuItem>)}
          </TextField>
          <TextField label="Plate Number" size="small" value={form.plate_number} onChange={e => set('plate_number', e.target.value)} />
          <Stack direction="row" spacing={1}>
            <TextField label="Make" size="small" fullWidth value={form.make} onChange={e => set('make', e.target.value)} />
            <TextField label="Model" size="small" fullWidth value={form.model} onChange={e => set('model', e.target.value)} />
          </Stack>
          <TextField label="Color" size="small" value={form.color} onChange={e => set('color', e.target.value)} />
          {mut.isError && <Alert severity="error">Failed to create vehicle</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.name || mut.isPending}
          onClick={() => mut.mutate({ name: form.name, vehicle_type: form.vehicle_type,
            plate_number: form.plate_number || undefined, make: form.make || undefined,
            model: form.model || undefined, color: form.color || undefined })}>
          {mut.isPending ? 'Creating…' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────
export default function GPSPage() {
  const [tab, setTab] = useState(0)
  const [addOpen, setAddOpen] = useState(false)
  const [selectedVehicle, setSelectedVehicle] = useState<Vehicle | null>(null)

  const qc = useQueryClient()

  const { data: dashboard, isLoading } = useQuery({
    queryKey: ['gps-dashboard'],
    queryFn: () => getGPSDashboard(),
    refetchInterval: 15_000,
  })

  const { data: geofenceEvents = [] } = useQuery({
    queryKey: ['geofence-events'],
    queryFn: () => listGeofenceEvents({ hours: 24 }),
    refetchInterval: 30_000,
  })

  const summary = dashboard?.summary ?? { total: 0, moving: 0, idle: 0, offline: 0 }
  const vehicles = dashboard?.vehicles ?? []
  const recentEvents = dashboard?.recent_events ?? []

  const KPI = [
    { label: 'Total Vehicles', value: summary.total, color: '#6C63FF' },
    { label: 'Moving', value: summary.moving, color: '#00E396' },
    { label: 'Idle', value: summary.idle, color: '#FF9800' },
    { label: 'Offline', value: summary.offline, color: '#9E9E9E' },
  ]

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={3}>
        <Box>
          <Typography variant="h5" fontWeight={700}>GPS Fleet Tracking</Typography>
          <Typography variant="body2" color="text.secondary">Live vehicle positions, journeys & geofence alerts</Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Tooltip title="Refresh">
            <IconButton onClick={() => qc.invalidateQueries({ queryKey: ['gps-dashboard'] })} size="small">
              <RefreshIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Button variant="contained" startIcon={<AddIcon />} size="small" onClick={() => setAddOpen(true)}>
            Add Vehicle
          </Button>
        </Stack>
      </Stack>

      {/* KPI row */}
      <Grid container spacing={2} mb={3}>
        {KPI.map(k => (
          <Grid size={{ xs: 6, sm: 3 }} key={k.label}>
            <Paper sx={{ p: 2, borderRadius: 2, bgcolor: 'rgba(255,255,255,0.03)',
              border: `1px solid ${k.color}33`, textAlign: 'center' }}>
              <Typography variant="h4" fontWeight={800} sx={{ color: k.color }}>{k.value}</Typography>
              <Typography variant="caption" color="text.secondary">{k.label}</Typography>
            </Paper>
          </Grid>
        ))}
      </Grid>

      {isLoading ? (
        <Box display="flex" justifyContent="center" py={6}><CircularProgress /></Box>
      ) : (
        <>
          {/* Map + event feed */}
          <Grid container spacing={2} mb={3}>
            <Grid size={{ xs: 12, md: 8 }}>
              <Paper sx={{ p: 2, borderRadius: 2, bgcolor: 'rgba(255,255,255,0.03)' }}>
                <Typography variant="subtitle2" fontWeight={700} mb={1} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <MyLocationIcon fontSize="small" /> Live Positions
                </Typography>
                <VehicleMapView vehicles={vehicles} />
              </Paper>
            </Grid>
            <Grid size={{ xs: 12, md: 4 }}>
              <Paper sx={{ p: 2, borderRadius: 2, bgcolor: 'rgba(255,255,255,0.03)', height: '100%' }}>
                <Typography variant="subtitle2" fontWeight={700} mb={1} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <WarningAmberIcon fontSize="small" sx={{ color: '#FF9800' }} /> Recent Geofence Events
                </Typography>
                <Stack spacing={1} sx={{ maxHeight: 340, overflowY: 'auto' }}>
                  {recentEvents.length === 0 && (
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 2, textAlign: 'center' }}>
                      No events in the last 2 hours
                    </Typography>
                  )}
                  {recentEvents.map((ev: any) => (
                    <Box key={ev.id} sx={{ p: 1.5, borderRadius: 1, bgcolor: 'rgba(255,255,255,0.04)',
                      borderLeft: `3px solid ${ev.event_type === 'entry' ? '#00E396' : ev.event_type === 'exit' ? '#FF4560' : '#FF9800'}` }}>
                      <Typography variant="caption" fontWeight={700}>{ev.vehicle_name}</Typography>
                      <Typography variant="caption" display="block" color="text.secondary">
                        {ev.event_type === 'entry' ? 'Entered' : ev.event_type === 'exit' ? 'Exited' : 'Speed violation in'} {ev.geofence_name}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">{relativeTime(ev.occurred_at)}</Typography>
                    </Box>
                  ))}
                </Stack>
              </Paper>
            </Grid>
          </Grid>

          {/* Tabs */}
          <Paper sx={{ borderRadius: 2, bgcolor: 'rgba(255,255,255,0.03)' }}>
            <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ px: 2, borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
              <Tab label="Fleet" />
              <Tab label="Geofence Events" />
            </Tabs>

            {tab === 0 && (
              <Box sx={{ p: 2 }}>
                {vehicles.length === 0 ? (
                  <Typography color="text.secondary" textAlign="center" py={4}>
                    No vehicles registered. Click "Add Vehicle" to get started.
                  </Typography>
                ) : (
                  <Grid container spacing={2}>
                    {vehicles.map(v => (
                      <Grid size={{ xs: 12, sm: 6, md: 4, lg: 3 }} key={v.id}>
                        <VehicleCard vehicle={v} onClick={() => setSelectedVehicle(v)} />
                      </Grid>
                    ))}
                  </Grid>
                )}
              </Box>
            )}

            {tab === 1 && (
              <Box sx={{ p: 2, overflowX: 'auto' }}>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Vehicle</TableCell>
                      <TableCell>Plate</TableCell>
                      <TableCell>Event</TableCell>
                      <TableCell>Geofence</TableCell>
                      <TableCell>Speed</TableCell>
                      <TableCell>Time</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {geofenceEvents.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={6} align="center" sx={{ color: 'text.secondary', py: 4 }}>
                          No geofence events in the last 24 hours
                        </TableCell>
                      </TableRow>
                    )}
                    {geofenceEvents.map(ev => (
                      <TableRow key={ev.id} hover>
                        <TableCell>{ev.vehicle_name}</TableCell>
                        <TableCell>{ev.plate_number ?? '—'}</TableCell>
                        <TableCell>
                          <Chip label={ev.event_type} size="small" sx={{
                            bgcolor: (ev.event_type === 'entry' ? '#00E396' : ev.event_type === 'exit' ? '#FF4560' : '#FF9800') + '22',
                            color: ev.event_type === 'entry' ? '#00E396' : ev.event_type === 'exit' ? '#FF4560' : '#FF9800',
                            fontWeight: 700, fontSize: '0.7rem',
                          }} />
                        </TableCell>
                        <TableCell>{ev.geofence_name}</TableCell>
                        <TableCell>{ev.speed ? `${ev.speed} km/h` : '—'}</TableCell>
                        <TableCell sx={{ color: 'text.secondary' }}>{new Date(ev.occurred_at).toLocaleString()}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
            )}
          </Paper>
        </>
      )}

      {addOpen && <AddVehicleDialog onClose={() => setAddOpen(false)} />}
      {selectedVehicle && <JourneyDialog vehicle={selectedVehicle} onClose={() => setSelectedVehicle(null)} />}
    </Box>
  )
}

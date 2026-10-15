import { useState } from 'react'
import {
  Box,
  Typography,
  Grid,
  Paper,
  Chip,
  Divider,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Tab,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  IconButton,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import MyLocationIcon from '@mui/icons-material/MyLocation'
import AddIcon from '@mui/icons-material/Add'
import RefreshIcon from '@mui/icons-material/Refresh'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import RouteIcon from '@mui/icons-material/Route'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getGPSDashboard, listGeofenceEvents, getVehicleJourneys, VEHICLE_TYPES,
} from '@/api/gps'
import type { Vehicle } from '@/api/gps'
import { PageHeader } from '@/components/common/PageHeader'
import { FleetNav } from '@/components/fleet/FleetNav'
import { VehicleFormDialog } from '@/components/fleet/VehicleFormDialog'
import { getFleetOptions, TRACKING_LABEL, type TrackingStatus } from '@/api/fleet'
import { FleetMap } from '@/components/fleet/FleetMap'
import { useFleetLive } from '@/components/fleet/useFleetLive'
import { statusNow } from '@/components/fleet/fleetMapFormat'
import { CardGridSkeleton, ErrorState, TableBlockSkeleton } from '@/components/states'
import { useNow } from '@/motion'
import { Link as RouterLink, useNavigate } from 'react-router-dom'

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


function relativeTime(ts: string | null): string {
  if (!ts) return 'Never'
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

// ── vehicle card ──────────────────────────────────────────────────────────────
function VehicleCard({ vehicle, onClick }: { vehicle: Vehicle; onClick: () => void }) {
  const typeInfo = VEHICLE_TYPES.find(t => t.value === vehicle.vehicle_type)
  return (
    <Paper onClick={onClick} sx={{
      p: 2, cursor: 'pointer', borderRadius: 2,
      border: `1px solid ${STATUS_COLOR[vehicle.current_status]}44`,
      bgcolor: 'rgba(255,255,255,0.03)',
      transition: 'background-color 0.2s, border-color 0.2s',
      '&:hover': { bgcolor: 'rgba(255,255,255,0.07)', borderColor: STATUS_COLOR[vehicle.current_status] },
    }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography variant="h6" sx={{ lineHeight: 1 }}>{typeInfo?.icon ?? '🚗'}</Typography>
          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>{vehicle.name}</Typography>
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
  const { data: journeys = [], isLoading, isLoadingError: journeysFailed, error: journeysError, refetch: refetchJourneys } = useQuery({
    queryKey: ['journeys', vehicle.id],
    queryFn: () => getVehicleJourneys(vehicle.id),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth
      slotProps={{ paper: { sx: { bgcolor: '#111827', border: '1px solid rgba(255,255,255,0.1)' } } }}>
      <DialogTitle>
        <Stack direction="row" alignItems="center" spacing={1}>
          <RouteIcon />
          <span>Journey Log — {vehicle.name}</span>
        </Stack>
      </DialogTitle>
      <DialogContent>
        {isLoading ? <TableBlockSkeleton rows={3} /> : journeysFailed ? <ErrorState compact error={journeysError} onRetry={refetchJourneys} /> : (
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

// ── main page ─────────────────────────────────────────────────────────────────
export default function GPSPage() {
  const [tab, setTab] = useState(0)
  const [addOpen, setAddOpen] = useState(false)
  const [selectedVehicle, setSelectedVehicle] = useState<Vehicle | null>(null)

  const qc = useQueryClient()
  const navigate = useNavigate()
  const { data: fleetOptions } = useQuery({ queryKey: ['fleet-options'], queryFn: getFleetOptions })

  const { data: dashboard, isLoading, isLoadingError: dashboardFailed, error: dashboardError, refetch: refetchDashboard } = useQuery({
    queryKey: ['gps-dashboard'],
    queryFn: () => getGPSDashboard(),
    refetchInterval: 15_000,
  })

  const { data: geofenceEvents = [] } = useQuery({
    queryKey: ['geofence-events'],
    queryFn: () => listGeofenceEvents({ hours: 24 }),
    refetchInterval: 30_000,
  })

  const vehicles = dashboard?.vehicles ?? []
  const recentEvents = dashboard?.recent_events ?? []

  // The fleet as the live map has it: the counts are tracking statuses, worked out against the clock from each
  // vehicle's last fix - a vehicle is counted Online only while it is being heard from.
  const live = useFleetLive()
  const now = useNow(10_000)
  const limits = live.data?.limits ?? { online_minutes: 5, offline_minutes: 30 }
  const fleet = live.data?.items ?? []
  const count = (s: TrackingStatus) => fleet.filter((v) => statusNow(v, limits, now) === s).length
  const KPI = [
    { label: 'Total Vehicles', value: fleet.length, color: '#6C63FF' },
    { label: TRACKING_LABEL.online, value: count('online'), color: '#00E396' },
    { label: TRACKING_LABEL.stale, value: count('stale'), color: '#FF9800' },
    { label: TRACKING_LABEL.offline, value: count('offline'), color: '#9E9E9E' },
  ]

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader pageKey="gps" />
      <FleetNav />
      {/* Header */}
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 3 }}>
        <Box>
          <Typography variant="body2" color="text.secondary">Live vehicle positions, journeys & geofence alerts</Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Tooltip title="Refresh">
            <IconButton onClick={() => qc.invalidateQueries({ queryKey: ['gps-dashboard'] })} size="small">
              <RefreshIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          {/* Registered with the whole vehicle record now, by whoever may: the server checks it again. */}
          {fleetOptions?.may.edit && (
            <Button variant="contained" startIcon={<AddIcon />} size="small" onClick={() => setAddOpen(true)}>
              Add Vehicle
            </Button>
          )}
        </Stack>
      </Stack>

      {/* KPI row */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {KPI.map(k => (
          <Grid size={{ xs: 6, sm: 3 }} key={k.label}>
            <Paper sx={{ p: 2, borderRadius: 2, bgcolor: 'rgba(255,255,255,0.03)',
              border: `1px solid ${k.color}33`, textAlign: 'center' }}>
              <Typography variant="h4" sx={{ color: k.color, fontWeight: 800 }}>{k.value}</Typography>
              <Typography variant="caption" color="text.secondary">{k.label}</Typography>
            </Paper>
          </Grid>
        ))}
      </Grid>

      {isLoading ? (
        <CardGridSkeleton />
      ) : dashboardFailed ? <ErrorState compact error={dashboardError} onRetry={refetchDashboard} /> : (
        <>
          {/* Map + event feed */}
          <Grid container spacing={2} sx={{ mb: 3 }}>
            <Grid size={{ xs: 12, md: 8 }}>
              <Paper sx={{ p: 2, borderRadius: 2, bgcolor: 'rgba(255,255,255,0.03)' }}>
                <Typography variant="subtitle2" sx={{ display: 'flex', alignItems: 'center', gap: 1, fontWeight: 700, mb: 1 }}>
                  <MyLocationIcon fontSize="small" /> Live Positions
                  <Button component={RouterLink} to="/gps/map" size="small" sx={{ ml: 'auto' }}>Open the live map</Button>
                </Typography>
                <FleetMap vehicles={fleet} limits={limits} height={380} labels={false}
                          onSelect={(id) => navigate(`/gps/map?focus=${id}`)} />
              </Paper>
            </Grid>
            <Grid size={{ xs: 12, md: 4 }}>
              <Paper sx={{ p: 2, borderRadius: 2, bgcolor: 'rgba(255,255,255,0.03)', height: '100%' }}>
                <Typography variant="subtitle2" sx={{ display: 'flex', alignItems: 'center', gap: 1, fontWeight: 700, mb: 1 }}>
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
                      <Typography variant="caption" sx={{ fontWeight: 700 }}>{ev.vehicle_name}</Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
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
                  <Typography color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
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

      {addOpen && (
        <VehicleFormDialog onClose={() => setAddOpen(false)}
                           onSaved={() => { setAddOpen(false); qc.invalidateQueries({ queryKey: ['gps-dashboard'] }) }} />
      )}
      {selectedVehicle && <JourneyDialog vehicle={selectedVehicle} onClose={() => setSelectedVehicle(null)} />}
    </Box>
  )
}

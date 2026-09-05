import { useState } from 'react'
import {
  Box,
  Typography,
  Grid,
  Paper,
  Chip,
  Button,
  Tabs,
  Tab,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Select,
  FormControl,
  InputLabel,
  Tooltip,
  CircularProgress,
  LinearProgress,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import LocalParkingIcon from '@mui/icons-material/LocalParking'
import DirectionsCarIcon from '@mui/icons-material/DirectionsCar'
import AttachMoneyIcon from '@mui/icons-material/AttachMoney'
import WarningIcon from '@mui/icons-material/Warning'
import AddIcon from '@mui/icons-material/Add'
import LoginIcon from '@mui/icons-material/Login'
import LogoutIcon from '@mui/icons-material/Logout'
import PaymentIcon from '@mui/icons-material/Payment'
import DeleteIcon from '@mui/icons-material/Delete'
import LinkedCameraIcon from '@mui/icons-material/LinkedCamera'
import {
  getParkingDashboard, listCarParks, createCarPark, getCarParkOccupancy,
  createZone, listSessions, logEntry, logExit, updatePayment, createRate, listRates,
  listLprCameras, createLprCamera, deleteLprCamera, listLprTriggeredSessions,
} from '@/api/parking'
import type { CarPark, ParkingBay, ParkingSession, LprCameraConfig } from '@/api/parking'
import { getCameras } from '@/api/cameras'
import { PageHeader } from '@/components/common/PageHeader'

// ── KPI Card ──────────────────────────────────────────────────────────────────

function KpiCard({ label, value, icon, color, sub }: {
  label: string; value: number | string; icon: React.ReactNode; color: string; sub?: string
}) {
  return (
    <Paper sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 2, borderRadius: 2 }}>
      <Box sx={{
        width: 44, height: 44, borderRadius: '12px', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        background: `${color}22`, color,
      }}>
        {icon}
      </Box>
      <Box>
        <Typography variant="h5" fontWeight={700}>{value}</Typography>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        {sub && <Typography variant="caption" color="text.secondary" display="block">{sub}</Typography>}
      </Box>
    </Paper>
  )
}

// ── Bay status colors ─────────────────────────────────────────────────────────

const BAY_COLOR: Record<string, string> = {
  available: '#00E396',
  occupied: '#FF4560',
  reserved: '#FF9800',
  blocked: '#9E9E9E',
}

function BayChip({ status }: { status: string }) {
  return (
    <Box
      sx={{
        width: 14, height: 14, borderRadius: '3px',
        background: BAY_COLOR[status] ?? '#555',
        display: 'inline-block',
        title: status,
      }}
    />
  )
}

function SessionStatusChip({ status }: { status: string }) {
  const colorMap: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
    active: 'info', completed: 'success', overstay: 'warning', disputed: 'error',
  }
  return <Chip label={status} color={colorMap[status] ?? 'default'} size="small" />
}

function PaymentChip({ status }: { status: string }) {
  const colorMap: Record<string, 'default' | 'warning' | 'success' | 'error'> = {
    unpaid: 'warning', paid: 'success', waived: 'default', void: 'error',
  }
  return <Chip label={status} color={colorMap[status] ?? 'default'} size="small" variant="outlined" />
}

// ── Occupancy visual grid ─────────────────────────────────────────────────────

function OccupancyGrid({ carparkId }: { carparkId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['parking-occupancy', carparkId],
    queryFn: () => getCarParkOccupancy(carparkId),
    refetchInterval: 10000,
  })

  if (isLoading) return <Box sx={{ py: 4, display: 'flex', justifyContent: 'center' }}><CircularProgress /></Box>
  if (!data) return null

  const totalBays = data.bays.length
  const occupied = data.bays.filter(b => b.status === 'occupied').length
  const pct = totalBays > 0 ? Math.round((occupied / totalBays) * 100) : 0

  return (
    <Box>
      {/* Summary bar */}
      <Box sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
          <Typography variant="caption">{occupied} / {totalBays} occupied</Typography>
          <Typography variant="caption" fontWeight={700} color={pct > 80 ? 'error.main' : pct > 60 ? 'warning.main' : 'success.main'}>
            {pct}%
          </Typography>
        </Box>
        <LinearProgress
          variant="determinate"
          value={pct}
          sx={{
            height: 8, borderRadius: 4,
            bgcolor: 'rgba(255,255,255,0.08)',
            '& .MuiLinearProgress-bar': {
              bgcolor: pct > 80 ? '#FF4560' : pct > 60 ? '#FF9800' : '#00E396',
              borderRadius: 4,
            },
          }}
        />
      </Box>

      {/* Legend */}
      <Box sx={{ display: 'flex', gap: 2, mb: 2, flexWrap: 'wrap' }}>
        {Object.entries(BAY_COLOR).map(([status, color]) => (
          <Box key={status} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Box sx={{ width: 10, height: 10, borderRadius: '2px', bgcolor: color }} />
            <Typography variant="caption" color="text.secondary">{status}</Typography>
          </Box>
        ))}
      </Box>

      {/* Per-zone grids */}
      {data.zones.map(zone => {
        const zoneBays = data.bays.filter((b: ParkingBay) => b.zone_name === zone.name)
        return (
          <Box key={zone.id} sx={{ mb: 2 }}>
            <Typography variant="caption" fontWeight={700} color="text.secondary" sx={{ mb: 0.5, display: 'block' }}>
              {zone.name} — {zone.zone_type} (L{zone.level})
              &nbsp;·&nbsp;{zone.available ?? 0} free / {zoneBays.length} total
            </Typography>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
              {zoneBays.map((bay: ParkingBay) => (
                <Tooltip key={bay.id} title={`${bay.bay_number} · ${bay.status}${bay.vehicle_plate ? ' · ' + bay.vehicle_plate : ''}`}>
                  <Box
                    sx={{
                      width: 28, height: 28,
                      borderRadius: '5px',
                      bgcolor: BAY_COLOR[bay.status] ?? '#555',
                      opacity: bay.status === 'blocked' ? 0.4 : 1,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: 'default',
                      fontSize: '0.55rem',
                      color: '#fff',
                      fontWeight: 600,
                    }}
                  >
                    {bay.bay_number}
                  </Box>
                </Tooltip>
              ))}
              {zoneBays.length === 0 && (
                <Typography variant="caption" color="text.disabled">No bays configured</Typography>
              )}
            </Box>
          </Box>
        )
      })}
    </Box>
  )
}

// ── Add Carpark Dialog ────────────────────────────────────────────────────────

function AddCarparkDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ name: '', description: '', total_capacity: '0', levels: '1', address: '' })
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm(f => ({ ...f, [k]: e.target.value }))
  const mut = useMutation({
    mutationFn: () => createCarPark({
      ...form,
      total_capacity: parseInt(form.total_capacity) || 0,
      levels: parseInt(form.levels) || 1,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['carparks'] }); onClose() },
  })
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Carpark</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <TextField label="Name *" value={form.name} onChange={set('name')} size="small" fullWidth />
        <TextField label="Description" value={form.description} onChange={set('description')} size="small" fullWidth multiline rows={2} />
        <Grid container spacing={1}>
          <Grid size={6}><TextField label="Total Capacity" type="number" value={form.total_capacity} onChange={set('total_capacity')} size="small" fullWidth /></Grid>
          <Grid size={6}><TextField label="Levels / Floors" type="number" value={form.levels} onChange={set('levels')} size="small" fullWidth /></Grid>
        </Grid>
        <TextField label="Address" value={form.address} onChange={set('address')} size="small" fullWidth />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.name || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Add Zone Dialog ───────────────────────────────────────────────────────────

function AddZoneDialog({ open, onClose, carpark }: { open: boolean; onClose: () => void; carpark: CarPark }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ name: '', zone_type: 'regular', level: '1', capacity: '0' })
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm(f => ({ ...f, [k]: e.target.value }))
  const mut = useMutation({
    mutationFn: () => createZone(carpark.id, { ...form, level: parseInt(form.level) || 1, capacity: parseInt(form.capacity) || 0 }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['parking-occupancy', carpark.id] }); onClose() },
  })
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Add Zone — {carpark.name}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <TextField label="Zone Name *" value={form.name} onChange={set('name')} size="small" fullWidth />
        <FormControl size="small" fullWidth>
          <InputLabel>Zone Type</InputLabel>
          <Select value={form.zone_type} label="Zone Type"
            onChange={e => setForm(f => ({ ...f, zone_type: e.target.value as string }))}>
            {['regular', 'handicap', 'ev', 'vip', 'motorcycle', 'loading'].map(t => (
              <MenuItem key={t} value={t}>{t}</MenuItem>
            ))}
          </Select>
        </FormControl>
        <Grid container spacing={1}>
          <Grid size={6}><TextField label="Level" type="number" value={form.level} onChange={set('level')} size="small" fullWidth /></Grid>
          <Grid size={6}><TextField label="Capacity" type="number" value={form.capacity} onChange={set('capacity')} size="small" fullWidth /></Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.name || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Add'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Entry Dialog ──────────────────────────────────────────────────────────────

function EntryDialog({ open, onClose, carparks }: { open: boolean; onClose: () => void; carparks: CarPark[] }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ car_park_id: '', vehicle_plate: '', vehicle_type: 'car', operator_notes: '' })
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm(f => ({ ...f, [k]: e.target.value }))
  const mut = useMutation({
    mutationFn: () => logEntry(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['parking-sessions'] })
      qc.invalidateQueries({ queryKey: ['parking-dashboard'] })
      onClose()
    },
  })
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Log Vehicle Entry</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <FormControl size="small" fullWidth>
          <InputLabel>Carpark *</InputLabel>
          <Select value={form.car_park_id} label="Carpark *"
            onChange={e => setForm(f => ({ ...f, car_park_id: e.target.value as string }))}>
            {carparks.map(c => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
          </Select>
        </FormControl>
        <TextField label="Vehicle Plate" value={form.vehicle_plate} onChange={set('vehicle_plate')} size="small" fullWidth />
        <FormControl size="small" fullWidth>
          <InputLabel>Vehicle Type</InputLabel>
          <Select value={form.vehicle_type} label="Vehicle Type"
            onChange={e => setForm(f => ({ ...f, vehicle_type: e.target.value as string }))}>
            {['car', 'motorcycle', 'truck', 'van', 'bicycle'].map(t => <MenuItem key={t} value={t}>{t}</MenuItem>)}
          </Select>
        </FormControl>
        <TextField label="Notes" value={form.operator_notes} onChange={set('operator_notes')} size="small" fullWidth />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.car_park_id || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Log Entry'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Exit + Payment Dialog ─────────────────────────────────────────────────────

function ExitDialog({ session, onClose }: { session: ParkingSession; onClose: () => void }) {
  const qc = useQueryClient()
  const [payMethod, setPayMethod] = useState('cash')
  const [result, setResult] = useState<{ duration_minutes: number; fee_amount: number } | null>(null)

  const exitMut = useMutation({
    mutationFn: () => logExit(session.id, { payment_status: 'paid', payment_method: payMethod }),
    onSuccess: (data) => {
      setResult(data)
      qc.invalidateQueries({ queryKey: ['parking-sessions'] })
      qc.invalidateQueries({ queryKey: ['parking-dashboard'] })
    },
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Log Exit — {session.vehicle_plate || 'No plate'}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        {result ? (
          <Box sx={{ textAlign: 'center', py: 2 }}>
            <Typography variant="h4" fontWeight={700} color="success.main">
              SGD {result.fee_amount.toFixed(2)}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Duration: {result.duration_minutes} min
            </Typography>
          </Box>
        ) : (
          <FormControl size="small" fullWidth>
            <InputLabel>Payment Method</InputLabel>
            <Select value={payMethod} label="Payment Method"
              onChange={e => setPayMethod(e.target.value as string)}>
              {['cash', 'card', 'cashless', 'season_pass', 'waived'].map(m => (
                <MenuItem key={m} value={m}>{m}</MenuItem>
              ))}
            </Select>
          </FormControl>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        {!result && (
          <Button variant="contained" color="error" disabled={exitMut.isPending} onClick={() => exitMut.mutate()}>
            {exitMut.isPending ? <CircularProgress size={18} /> : 'Log Exit & Charge'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}

// ── Add Rate Dialog ───────────────────────────────────────────────────────────

function AddRateDialog({ open, onClose, carpark }: { open: boolean; onClose: () => void; carpark: CarPark }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    rate_name: '', zone_type: 'regular',
    first_hour_rate: '2.00', subsequent_rate: '1.00', daily_max_rate: '20.00',
  })
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm(f => ({ ...f, [k]: e.target.value }))
  const mut = useMutation({
    mutationFn: () => createRate(carpark.id, {
      ...form,
      first_hour_rate: parseFloat(form.first_hour_rate) || 0,
      subsequent_rate: parseFloat(form.subsequent_rate) || 0,
      daily_max_rate: parseFloat(form.daily_max_rate) || undefined,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['parking-rates', carpark.id] }); onClose() },
  })
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Add Rate — {carpark.name}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <TextField label="Rate Name *" value={form.rate_name} onChange={set('rate_name')} size="small" fullWidth />
        <FormControl size="small" fullWidth>
          <InputLabel>Zone Type</InputLabel>
          <Select value={form.zone_type} label="Zone Type"
            onChange={e => setForm(f => ({ ...f, zone_type: e.target.value as string }))}>
            {['regular', 'handicap', 'ev', 'vip', 'motorcycle', 'loading'].map(t => (
              <MenuItem key={t} value={t}>{t}</MenuItem>
            ))}
          </Select>
        </FormControl>
        <Grid container spacing={1}>
          <Grid size={4}><TextField label="1st Hr (SGD)" type="number" value={form.first_hour_rate} onChange={set('first_hour_rate')} size="small" fullWidth /></Grid>
          <Grid size={4}><TextField label="Sub. Hr (SGD)" type="number" value={form.subsequent_rate} onChange={set('subsequent_rate')} size="small" fullWidth /></Grid>
          <Grid size={4}><TextField label="Daily Max" type="number" value={form.daily_max_rate} onChange={set('daily_max_rate')} size="small" fullWidth /></Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.rate_name || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Overview Tab ──────────────────────────────────────────────────────────────

function OverviewTab() {
  const [addOpen, setAddOpen] = useState(false)
  const [addZoneFor, setAddZoneFor] = useState<CarPark | null>(null)
  const [addRateFor, setAddRateFor] = useState<CarPark | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  const { data: carparks = [], isLoading } = useQuery({
    queryKey: ['carparks'],
    queryFn: listCarParks,
    refetchInterval: 15000,
  })

  const { data: rates = [] } = useQuery({
    queryKey: ['parking-rates', expanded],
    queryFn: () => expanded ? listRates(expanded) : Promise.resolve([]),
    enabled: !!expanded,
  })

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setAddOpen(true)}>
          Add Carpark
        </Button>
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
      ) : (
        <Grid container spacing={2}>
          {carparks.map(cp => {
            const total = cp.bay_count ?? 0
            const occ = cp.occupied_bays ?? 0
            const pct = total > 0 ? Math.round((occ / total) * 100) : 0
            const isExpanded = expanded === cp.id
            return (
              <Grid size={{ xs: 12, md: 6 }} key={cp.id}>
                <Paper sx={{ p: 2, borderRadius: 2 }}>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
                    <Box>
                      <Typography variant="subtitle1" fontWeight={700}>{cp.name}</Typography>
                      {cp.site_name && (
                        <Typography variant="caption" color="text.secondary">{cp.site_name}</Typography>
                      )}
                    </Box>
                    <Box sx={{ display: 'flex', gap: 1 }}>
                      <Button size="small" onClick={() => setAddZoneFor(cp)}>+ Zone</Button>
                      <Button size="small" onClick={() => setAddRateFor(cp)}>+ Rate</Button>
                      <Button size="small" variant={isExpanded ? 'contained' : 'outlined'}
                        onClick={() => setExpanded(isExpanded ? null : cp.id)}>
                        {isExpanded ? 'Collapse' : 'View Bays'}
                      </Button>
                    </Box>
                  </Box>

                  <Box sx={{ display: 'flex', gap: 3, mb: 1 }}>
                    <Box><Typography variant="h6" fontWeight={700}>{cp.available_bays ?? 0}</Typography><Typography variant="caption" color="success.main">Available</Typography></Box>
                    <Box><Typography variant="h6" fontWeight={700}>{occ}</Typography><Typography variant="caption" color="error.main">Occupied</Typography></Box>
                    <Box><Typography variant="h6" fontWeight={700}>{total}</Typography><Typography variant="caption" color="text.secondary">Total</Typography></Box>
                    <Box><Typography variant="h6" fontWeight={700}>{cp.levels}</Typography><Typography variant="caption" color="text.secondary">Levels</Typography></Box>
                  </Box>

                  <LinearProgress
                    variant="determinate"
                    value={pct}
                    sx={{
                      height: 6, borderRadius: 3, mb: 1,
                      bgcolor: 'rgba(255,255,255,0.08)',
                      '& .MuiLinearProgress-bar': {
                        bgcolor: pct > 80 ? '#FF4560' : pct > 60 ? '#FF9800' : '#00E396',
                        borderRadius: 3,
                      },
                    }}
                  />
                  <Typography variant="caption" color="text.secondary">{pct}% occupied</Typography>

                  {isExpanded && (
                    <Box sx={{ mt: 2, pt: 2, borderTop: 1, borderColor: 'divider' }}>
                      <OccupancyGrid carparkId={cp.id} />
                      {rates.length > 0 && (
                        <Box sx={{ mt: 2 }}>
                          <Typography variant="caption" fontWeight={700} color="text.secondary">Rates</Typography>
                          {rates.map(r => (
                            <Box key={r.id} sx={{ display: 'flex', gap: 1, mt: 0.5, flexWrap: 'wrap' }}>
                              <Chip label={r.rate_name} size="small" />
                              <Typography variant="caption">
                                1st hr SGD {r.first_hour_rate.toFixed(2)} · sub SGD {r.subsequent_rate.toFixed(2)}
                                {r.daily_max_rate ? ` · max SGD ${r.daily_max_rate.toFixed(2)}` : ''}
                              </Typography>
                            </Box>
                          ))}
                        </Box>
                      )}
                    </Box>
                  )}
                </Paper>
              </Grid>
            )
          })}
          {carparks.length === 0 && (
            <Grid size={12}>
              <Paper sx={{ p: 4, textAlign: 'center' }}>
                <Typography color="text.secondary">No carparks configured yet. Add one to get started.</Typography>
              </Paper>
            </Grid>
          )}
        </Grid>
      )}

      <AddCarparkDialog open={addOpen} onClose={() => setAddOpen(false)} />
      {addZoneFor && <AddZoneDialog open carpark={addZoneFor} onClose={() => setAddZoneFor(null)} />}
      {addRateFor && <AddRateDialog open carpark={addRateFor} onClose={() => setAddRateFor(null)} />}
    </Box>
  )
}

// ── Sessions Tab ──────────────────────────────────────────────────────────────

function SessionsTab() {
  const [entryOpen, setEntryOpen] = useState(false)
  const [exitTarget, setExitTarget] = useState<ParkingSession | null>(null)
  const [statusFilter, setStatusFilter] = useState('active')
  const [plateSearch, setPlateSearch] = useState('')
  const qc = useQueryClient()

  const { data: _sessData, isLoading } = useQuery({
    queryKey: ['parking-sessions', statusFilter, plateSearch],
    queryFn: () => listSessions({
      status: statusFilter || undefined,
      plate: plateSearch || undefined,
    }),
    refetchInterval: 15000,
  })
  const sessions = _sessData?.items ?? []

  const { data: carparks = [] } = useQuery({ queryKey: ['carparks'], queryFn: listCarParks })

  const payMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => updatePayment(id, status, 'cash'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['parking-sessions'] }),
  })

  const fmtDate = (d?: string) => d ? new Date(d).toLocaleString() : '—'

  // Status moves to the rail; the plate search stays on the page. It is a
  // free-text lookup, and hiding the box you type a plate into behind a
  // collapsed panel would make the common case slower, not faster.
  const filterGroups: FilterGroup[] = [{
    key: 'status',
    label: 'Status',
    value: statusFilter,
    onChange: setStatusFilter,
    options: [
      { value: '', label: 'All' },
      ...['active', 'completed', 'overstay', 'disputed'].map((v) => ({
        value: v, label: v.charAt(0).toUpperCase() + v.slice(1),
      })),
    ],
  }]

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
        <TextField
          size="small" placeholder="Search plate..." value={plateSearch}
          onChange={e => setPlateSearch(e.target.value)}
          sx={{ minWidth: 180 }}
        />
        <Box sx={{ flex: 1 }} />
        <Button variant="contained" startIcon={<LoginIcon />} onClick={() => setEntryOpen(true)}>
          Log Entry
        </Button>
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
      ) : (
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Vehicle</TableCell>
                <TableCell>Carpark / Zone</TableCell>
                <TableCell>Bay</TableCell>
                <TableCell>Entry</TableCell>
                <TableCell>Exit</TableCell>
                <TableCell>Duration</TableCell>
                <TableCell>Fee (SGD)</TableCell>
                <TableCell>Payment</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {sessions.map(s => (
                <TableRow key={s.id} hover>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600} fontFamily="monospace">
                      {s.vehicle_plate || '—'}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">{s.vehicle_type}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{s.carpark_name || '—'}</Typography>
                    {s.zone_name && <Typography variant="caption" color="text.secondary">{s.zone_name}</Typography>}
                  </TableCell>
                  <TableCell><Typography variant="caption">{s.bay_number || '—'}</Typography></TableCell>
                  <TableCell><Typography variant="caption">{fmtDate(s.entry_at)}</Typography></TableCell>
                  <TableCell><Typography variant="caption">{fmtDate(s.exit_at)}</Typography></TableCell>
                  <TableCell>
                    <Typography variant="caption">
                      {s.duration_minutes != null ? `${s.duration_minutes} min` : '—'}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>
                      {s.fee_amount != null ? s.fee_amount.toFixed(2) : '—'}
                    </Typography>
                  </TableCell>
                  <TableCell><PaymentChip status={s.payment_status} /></TableCell>
                  <TableCell><SessionStatusChip status={s.status} /></TableCell>
                  <TableCell align="right">
                    {s.status === 'active' && (
                      <Tooltip title="Log Exit">
                        <IconButton size="small" color="warning" onClick={() => setExitTarget(s)}>
                          <LogoutIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    {s.status === 'completed' && s.payment_status === 'unpaid' && (
                      <Tooltip title="Mark Paid">
                        <IconButton size="small" color="success" onClick={() => payMut.mutate({ id: s.id, status: 'paid' })}>
                          <PaymentIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {sessions.length === 0 && (
                <TableRow>
                  <TableCell colSpan={10} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                    No sessions found
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <EntryDialog open={entryOpen} onClose={() => setEntryOpen(false)} carparks={carparks} />
      {exitTarget && <ExitDialog session={exitTarget} onClose={() => setExitTarget(null)} />}
      </Box>

      <FilterRail groups={filterGroups} storageKey="parking-sessions" />
    </Box>
  )
}

// ── LPR Cameras Tab ───────────────────────────────────────────────────────────

function AddLprCameraDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [cameraId, setCameraId] = useState('')
  const [carParkId, setCarParkId] = useState('')
  const [triggerType, setTriggerType] = useState<'entry' | 'exit' | 'both'>('both')
  const [defaultZoneId, setDefaultZoneId] = useState('')
  const [notes, setNotes] = useState('')

  const { data: cameras = [] } = useQuery({ queryKey: ['cameras'], queryFn: getCameras, enabled: open })
  const { data: carparks = [] } = useQuery({ queryKey: ['carparks'], queryFn: listCarParks, enabled: open })
  const { data: occupancy } = useQuery({
    queryKey: ['parking-occupancy', carParkId],
    queryFn: () => getCarParkOccupancy(carParkId),
    enabled: open && !!carParkId,
  })
  const zones = occupancy?.zones ?? []

  const mut = useMutation({
    mutationFn: () => createLprCamera({
      camera_id: cameraId,
      car_park_id: carParkId,
      trigger_type: triggerType,
      default_zone_id: defaultZoneId || undefined,
      notes: notes || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['lpr-cameras'] })
      setCameraId(''); setCarParkId(''); setTriggerType('both'); setDefaultZoneId(''); setNotes('')
      onClose()
    },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add LPR Camera Trigger</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        <FormControl size="small" fullWidth>
          <InputLabel>Camera *</InputLabel>
          <Select value={cameraId} label="Camera *" onChange={e => setCameraId(e.target.value)}>
            {(cameras as any[]).map((c: any) => (
              <MenuItem key={c.id} value={c.id}>{c.name}{c.location ? ` — ${c.location}` : ''}</MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl size="small" fullWidth>
          <InputLabel>Car Park *</InputLabel>
          <Select value={carParkId} label="Car Park *" onChange={e => { setCarParkId(e.target.value); setDefaultZoneId('') }}>
            {(carparks as CarPark[]).map(cp => (
              <MenuItem key={cp.id} value={cp.id}>{cp.name}</MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl size="small" fullWidth>
          <InputLabel>Trigger Type</InputLabel>
          <Select value={triggerType} label="Trigger Type" onChange={e => setTriggerType(e.target.value as 'entry' | 'exit' | 'both')}>
            <MenuItem value="entry">Entry only</MenuItem>
            <MenuItem value="exit">Exit only</MenuItem>
            <MenuItem value="both">Both entry & exit</MenuItem>
          </Select>
        </FormControl>
        {zones.length > 0 && (
          <FormControl size="small" fullWidth>
            <InputLabel>Default Zone (optional)</InputLabel>
            <Select value={defaultZoneId} label="Default Zone (optional)" onChange={e => setDefaultZoneId(e.target.value)}>
              <MenuItem value="">None</MenuItem>
              {zones.map((z: any) => <MenuItem key={z.id} value={z.id}>{z.name} ({z.zone_type})</MenuItem>)}
            </Select>
          </FormControl>
        )}
        <TextField label="Notes" value={notes} onChange={e => setNotes(e.target.value)} size="small" fullWidth multiline rows={2} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!cameraId || !carParkId || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? <CircularProgress size={18} /> : 'Add'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function LprCamerasTab() {
  const qc = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)

  const { data: configs = [], isLoading } = useQuery({
    queryKey: ['lpr-cameras'],
    queryFn: listLprCameras,
  })

  const { data: lprSessions = [] } = useQuery({
    queryKey: ['lpr-triggered-sessions'],
    queryFn: () => listLprTriggeredSessions({ limit: 20 }),
    refetchInterval: 15000,
  })

  const deleteConfig = useMutation({
    mutationFn: (id: string) => deleteLprCamera(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['lpr-cameras'] }),
  })

  const TRIGGER_COLORS: Record<string, 'info' | 'success' | 'warning'> = {
    entry: 'info', exit: 'warning', both: 'success',
  }

  return (
    <Box>
      {/* Config table */}
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h6" fontWeight={600}>LPR Camera → Carpark Mappings</Typography>
        <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setAddOpen(true)}>
          Add Camera
        </Button>
      </Stack>

      {isLoading ? (
        <CircularProgress size={24} />
      ) : configs.length === 0 ? (
        <Typography color="text.secondary" variant="body2" sx={{ mb: 4 }}>
          No LPR cameras configured. Add a camera to automatically create parking sessions on plate detection.
        </Typography>
      ) : (
        <Paper sx={{ mb: 4, overflow: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Camera</TableCell>
                <TableCell>Car Park</TableCell>
                <TableCell>Trigger</TableCell>
                <TableCell>Default Zone</TableCell>
                <TableCell>Notes</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(configs as LprCameraConfig[]).map(cfg => (
                <TableRow key={cfg.id} hover>
                  <TableCell>{cfg.camera_name}</TableCell>
                  <TableCell>{cfg.car_park_name}</TableCell>
                  <TableCell>
                    <Chip label={cfg.trigger_type} size="small" color={TRIGGER_COLORS[cfg.trigger_type] ?? 'default'} />
                  </TableCell>
                  <TableCell>{cfg.default_zone_name ?? <Typography color="text.disabled" variant="caption">—</Typography>}</TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">{cfg.notes ?? '—'}</Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Tooltip title="Remove configuration">
                      <IconButton size="small" color="error" onClick={() => deleteConfig.mutate(cfg.id)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}

      {/* LPR-triggered sessions */}
      <Typography variant="h6" fontWeight={600} mb={1}>LPR-Triggered Sessions</Typography>
      <Typography variant="caption" color="text.secondary" display="block" mb={2}>
        Parking sessions automatically created or closed by plate recognition
      </Typography>
      <Paper sx={{ overflow: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Plate</TableCell>
              <TableCell>Car Park</TableCell>
              <TableCell>Zone</TableCell>
              <TableCell>Entry</TableCell>
              <TableCell>Exit</TableCell>
              <TableCell>Duration</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Payment</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {lprSessions.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} align="center">
                  <Typography variant="caption" color="text.secondary">No LPR-triggered sessions yet</Typography>
                </TableCell>
              </TableRow>
            )}
            {lprSessions.map((s) => (
              <TableRow key={s.id} hover>
                <TableCell><Typography variant="body2" fontWeight={600}>{s.vehicle_plate ?? '—'}</Typography></TableCell>
                <TableCell>{s.carpark_name ?? '—'}</TableCell>
                <TableCell>{s.zone_name ?? '—'}</TableCell>
                <TableCell><Typography variant="caption">{new Date(s.entry_at).toLocaleString()}</Typography></TableCell>
                <TableCell><Typography variant="caption">{s.exit_at ? new Date(s.exit_at).toLocaleString() : '—'}</Typography></TableCell>
                <TableCell>{s.duration_minutes != null ? `${s.duration_minutes}m` : '—'}</TableCell>
                <TableCell><SessionStatusChip status={s.status} /></TableCell>
                <TableCell><PaymentChip status={s.payment_status} /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>

      <AddLprCameraDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </Box>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ParkingPage() {
  const [tab, setTab] = useState(0)

  const { data: dash } = useQuery({
    queryKey: ['parking-dashboard'],
    queryFn: getParkingDashboard,
    refetchInterval: 15000,
  })

  const occupied = dash?.occupied_bays ?? 0
  const total = (dash?.available_bays ?? 0) + occupied + (dash?.reserved_bays ?? 0)
  const occPct = total > 0 ? Math.round((occupied / total) * 100) : 0

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader pageKey="parking" />

      {/* KPI row */}
      <Grid container spacing={2} mb={3}>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Available Bays" value={dash?.available_bays ?? 0} icon={<LocalParkingIcon />} color="#00E396"
            sub={`${occPct}% occupied`} />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Active Sessions" value={dash?.active_sessions ?? 0} icon={<DirectionsCarIcon />} color="#6C63FF" />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Revenue Today" value={`SGD ${(dash?.revenue_today ?? 0).toFixed(2)}`} icon={<AttachMoneyIcon />} color="#00D9C0" />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <KpiCard label="Overstay / Alerts" value={dash?.overstay_count ?? 0} icon={<WarningIcon />} color="#FF4560" />
        </Grid>
      </Grid>

      {/* Tabs */}
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Tab label="Carparks & Occupancy" icon={<LocalParkingIcon fontSize="small" />} iconPosition="start" />
        <Tab label="Sessions" icon={<DirectionsCarIcon fontSize="small" />} iconPosition="start" />
        <Tab label="LPR Cameras" icon={<LinkedCameraIcon fontSize="small" />} iconPosition="start" />
      </Tabs>

      {tab === 0 && <OverviewTab />}
      {tab === 1 && <SessionsTab />}
      {tab === 2 && <LprCamerasTab />}
    </Box>
  )
}

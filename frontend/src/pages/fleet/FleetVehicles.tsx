/**
 * The fleet's vehicles: found, sorted and paged by the server, registered,
 * changed and archived here.
 *
 * Delete is Archive: the vehicle leaves the lists and the map with a reason,
 * keeps its history and can be restored ("Archived" shows them). What the
 * person may do is what the server says (`may`); it checks again. Somebody
 * limited to sites sees their sites' vehicles only.
 */
import { useEffect, useState } from 'react'
import {
  Box, Button, Chip, IconButton, InputAdornment, MenuItem, Table, TableBody, TableCell, TableContainer, TableHead, TablePagination,
  TableRow, TableSortLabel, TextField, Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import ArchiveIcon from '@mui/icons-material/ArchiveOutlined'
import EditIcon from '@mui/icons-material/EditOutlined'
import RestoreIcon from '@mui/icons-material/RestoreOutlined'
import SearchIcon from '@mui/icons-material/Search'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'
import { PageHeader } from '@/components/common/PageHeader'
import { GlassCard } from '@/components/common/GlassCard'
import { SkeletonRows } from '@/components/common/SkeletonRows'
import Stack from '@/components/common/Stack'
import { FleetNav } from '@/components/fleet/FleetNav'
import { VehicleFormDialog } from '@/components/fleet/VehicleFormDialog'
import { ArchiveVehicleDialog } from '@/components/fleet/ArchiveVehicleDialog'
import { SaveStatus, TableEmptyRow, TableErrorRow, TableRefreshingRow } from '@/components/states'
import {
  getFleetOptions, listFleetVehicles, restoreFleetVehicle, type FleetVehicle, type VehicleQuery,
} from '@/api/fleet'

const COLS = 7
const SORTABLE: { key: string; label: string }[] = [
  { key: 'plate_number', label: 'Registration' },
  { key: 'name', label: 'Name' },
  { key: 'vehicle_type', label: 'Type' },
  { key: 'site_name', label: 'Site' },
  { key: 'service_status', label: 'Service' },
]
const SERVICE_COLOUR: Record<string, 'success' | 'warning' | 'default'> = {
  in_service: 'success', maintenance: 'warning', out_of_service: 'default',
}
const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }

export default function FleetVehicles() {
  const qc = useQueryClient()
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [siteId, setSiteId] = useState('')
  const [vehicleType, setVehicleType] = useState('')
  const [service, setService] = useState('')
  const [archived, setArchived] = useState<'active' | 'archived' | 'all'>('active')
  const [sort, setSort] = useState('plate_number')
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const [page, setPage] = useState(0)
  const [perPage, setPerPage] = useState(25)
  const [editing, setEditing] = useState<'new' | null>(null)
  const [archiving, setArchiving] = useState<FleetVehicle | null>(null)

  // The search is asked for when the person stops typing, not at every key.
  useEffect(() => {
    const t = setTimeout(() => { setQ(typed.trim()); setPage(0) }, 300)
    return () => clearTimeout(t)
  }, [typed])

  const params: VehicleQuery = {
    q: q || undefined, site_id: siteId || undefined, vehicle_type: vehicleType || undefined,
    service_status: service || undefined, archived, sort, order, limit: perPage, offset: page * perPage,
  }
  const {
    data, isLoading, isLoadingError: vehiclesFailed, error: vehiclesError, refetch: refetchVehicles,
    isPlaceholderData: vehiclesRefreshing,
  } = useQuery({
    queryKey: ['fleet-vehicles', q, siteId, vehicleType, service, archived, sort, order, page, perPage],
    queryFn: () => listFleetVehicles(params),
    // A changed filter, sort or page keeps the last rows on screen, under a line, until the new ones arrive.
    placeholderData: keepPreviousData,
  })
  const { data: options } = useQuery({ queryKey: ['fleet-options'], queryFn: getFleetOptions })
  const restore = useMutation({
    mutationFn: (v: FleetVehicle) => restoreFleetVehicle(v.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fleet-vehicles'] }),
  })
  const again = () => {
    qc.invalidateQueries({ queryKey: ['fleet-vehicles'] })
    qc.invalidateQueries({ queryKey: ['gps-dashboard'] })
  }
  const may = data?.may
  const sortBy = (key: string) => {
    if (sort === key) setOrder(order === 'asc' ? 'desc' : 'asc')
    else { setSort(key); setOrder('asc') }
    setPage(0)
  }
  const filter = (setter: (v: string) => void) => (e: { target: { value: string } }) => { setter(e.target.value); setPage(0) }

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Fleet vehicles" subtitle="Register, change and archive the organisation's vehicles" />
      <FleetNav />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField size="small" label="Search" placeholder="Registration, name, make, driver…" value={typed}
                     onChange={(e) => setTyped(e.target.value)} sx={{ minWidth: 260, flex: 1 }}
                     slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> } }} />
          <TextField select size="small" label="Site" value={siteId} onChange={filter(setSiteId)} sx={{ minWidth: 170 }} slotProps={shrunk}>
            <MenuItem value="">Every site</MenuItem>
            {options?.sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Type" value={vehicleType} onChange={filter(setVehicleType)} sx={{ minWidth: 150 }} slotProps={shrunk}>
            <MenuItem value="">Every type</MenuItem>
            {options?.vehicle_types.map((t) => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Service" value={service} onChange={filter(setService)} sx={{ minWidth: 160 }} slotProps={shrunk}>
            <MenuItem value="">Any service status</MenuItem>
            {options?.service_statuses.map((s) => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Showing" value={archived} sx={{ minWidth: 170 }}
                     onChange={(e) => { setArchived(e.target.value as typeof archived); setPage(0) }}>
            <MenuItem value="active">Vehicles in use</MenuItem>
            <MenuItem value="archived">Archived vehicles</MenuItem>
            <MenuItem value="all">All vehicles</MenuItem>
          </TextField>
          {may?.edit && (
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => setEditing('new')}>Register a vehicle</Button>
          )}
        </Stack>
      </GlassCard>
      <SaveStatus state={restore.isPending ? 'saving' : restore.isError ? 'failed' : 'idle'} error={restore.error}
                  savingText="Restoring…" />
      <GlassCard sx={{ p: 0 }}>
        <TableContainer>
          <Table size="small" aria-label="Fleet vehicles">
            <TableHead>
              <TableRow>
                {SORTABLE.map((c) => (
                  <TableCell key={c.key} sortDirection={sort === c.key ? order : false}>
                    <TableSortLabel active={sort === c.key} direction={sort === c.key ? order : 'asc'} onClick={() => sortBy(c.key)}>
                      {c.label}
                    </TableSortLabel>
                  </TableCell>
                ))}
                <TableCell>Driver</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {vehiclesRefreshing && <TableRefreshingRow />}
              {isLoading ? (
                <SkeletonRows cols={COLS} rows={6} />
              ) : vehiclesFailed ? (
                <TableErrorRow cols={COLS} title="The vehicles could not be loaded" error={vehiclesError} onRetry={refetchVehicles} />
              ) : !data?.items.length ? (
                <TableEmptyRow cols={COLS} title={q || siteId || vehicleType || service ? 'No vehicle matches' : archived === 'archived' ? 'No archived vehicles' : 'No vehicles yet'}
                               hint={archived === 'active' && !q && may?.edit ? 'Register the first one with the button above.' : undefined} />
              ) : data.items.map((v) => (
                <TableRow key={v.id} hover sx={{ opacity: v.archived ? 0.7 : 1 }}>
                  <TableCell>
                    <Typography component={RouterLink} to={`/gps/vehicles/${v.id}`} variant="body2"
                                sx={{ fontWeight: 700, color: 'inherit', textDecoration: 'none', '&:hover': { textDecoration: 'underline' } }}>
                      {v.plate_number ?? 'No registration'}
                    </Typography>
                    {v.archived && <Chip size="small" label="Archived" sx={{ ml: 1 }} />}
                  </TableCell>
                  <TableCell>{v.name}<Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {[v.make, v.model, v.color].filter(Boolean).join(' · ')}</Typography></TableCell>
                  <TableCell>{v.type_label}</TableCell>
                  <TableCell>{v.site_name ?? <Typography variant="caption" color="text.secondary">No site</Typography>}</TableCell>
                  <TableCell><Chip size="small" variant="outlined" color={SERVICE_COLOUR[v.service_status]} label={v.service_label} /></TableCell>
                  <TableCell>{v.driver_name ?? '—'}</TableCell>
                  <TableCell align="right">
                    {!v.archived && may?.edit && (
                      <Tooltip title="Change"><IconButton size="small" aria-label={`Change ${v.plate_number ?? v.name}`}
                                                          component={RouterLink} to={`/gps/vehicles/${v.id}?edit=1`}>
                        <EditIcon fontSize="small" /></IconButton></Tooltip>
                    )}
                    {!v.archived && may?.archive && (
                      <Tooltip title="Archive"><IconButton size="small" aria-label={`Archive ${v.plate_number ?? v.name}`}
                                                           onClick={() => setArchiving(v)}><ArchiveIcon fontSize="small" /></IconButton></Tooltip>
                    )}
                    {v.archived && may?.archive && (
                      <Tooltip title={`Archived: ${v.archive_reason ?? ''}`}>
                        <IconButton size="small" aria-label={`Restore ${v.plate_number ?? v.name}`} disabled={restore.isPending}
                                    onClick={() => restore.mutate(v)}><RestoreIcon fontSize="small" /></IconButton>
                      </Tooltip>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        <TablePagination component="div" count={data?.total ?? 0} page={page} rowsPerPage={perPage}
                         rowsPerPageOptions={[10, 25, 50, 100]} onPageChange={(_, p) => setPage(p)}
                         onRowsPerPageChange={(e) => { setPerPage(Number(e.target.value)); setPage(0) }} />
      </GlassCard>
      {editing && (
        <VehicleFormDialog onClose={() => setEditing(null)} onSaved={() => { setEditing(null); again() }} />
      )}
      {archiving && (
        <ArchiveVehicleDialog vehicle={archiving} onClose={() => setArchiving(null)}
                              onArchived={() => { setArchiving(null); again() }} />
      )}
    </Box>
  )
}

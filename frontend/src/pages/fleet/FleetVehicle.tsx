/**
 * One fleet vehicle: its record, the organisation's own fields, its groups,
 * and who changed it and when.
 *
 * An archived vehicle says so - when, by whom and why - and keeps everything;
 * whoever may archive can restore it. The GPS device, journeys and events of
 * a vehicle come with the next phases of the fleet work, and are not shown
 * here until they exist.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Skeleton, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, Typography,
} from '@mui/material'
import ArchiveIcon from '@mui/icons-material/ArchiveOutlined'
import EditIcon from '@mui/icons-material/EditOutlined'
import RestoreIcon from '@mui/icons-material/RestoreOutlined'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link as RouterLink, useParams, useSearchParams } from 'react-router-dom'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import Stack from '@/components/common/Stack'
import { FleetNav } from '@/components/fleet/FleetNav'
import { ArchiveVehicleDialog } from '@/components/fleet/ArchiveVehicleDialog'
import { VehicleFormDialog } from '@/components/fleet/VehicleFormDialog'
import { DeviceTab } from '@/components/fleet/DeviceTab'
import { EmptyState, ErrorState, SaveStatus, TableEmptyRow, TableErrorRow } from '@/components/states'
import { SkeletonRows } from '@/components/common/SkeletonRows'
import {
  HISTORY_WORDS, getFleetVehicle, getVehicleHistory, restoreFleetVehicle, type FleetVehicleDetail, type VehicleField,
} from '@/api/fleet'

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—')

export default function FleetVehicle() {
  const { id = '' } = useParams()
  const [search, setSearch] = useSearchParams()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'details' | 'device' | 'groups' | 'history'>('details')
  const [archiving, setArchiving] = useState(false)
  const editing = search.get('edit') === '1'
  const setEditing = (on: boolean) => setSearch(on ? { edit: '1' } : {}, { replace: true })

  const { data: vehicle, isLoading, isLoadingError: vehicleFailed, error: vehicleError, refetch: refetchVehicle } = useQuery({
    queryKey: ['fleet-vehicle', id], queryFn: () => getFleetVehicle(id),
  })
  const {
    data: history, isLoading: historyLoading, isLoadingError: historyFailed, error: historyError, refetch: refetchHistory,
  } = useQuery({ queryKey: ['fleet-vehicle-history', id], queryFn: () => getVehicleHistory(id), enabled: tab === 'history' })
  const restore = useMutation({ mutationFn: () => restoreFleetVehicle(id), onSuccess: () => again() })
  const again = () => {
    qc.invalidateQueries({ queryKey: ['fleet-vehicle', id] })
    qc.invalidateQueries({ queryKey: ['fleet-vehicle-history', id] })
    qc.invalidateQueries({ queryKey: ['fleet-vehicles'] })
  }

  if (vehicleFailed) return <ErrorState title="This vehicle could not be loaded" error={vehicleError} onRetry={refetchVehicle} sx={{ m: 3 }} />
  if (isLoading || !vehicle) return <Box sx={{ p: 3 }}><Skeleton variant="text" width={280} height={48} /><Skeleton variant="rounded" height={260} sx={{ mt: 2 }} /></Box>

  const may = vehicle.may
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title={vehicle.plate_number ?? vehicle.name}
                  subtitle={[vehicle.name, vehicle.type_label, vehicle.site_name ?? 'No site'].join(' · ')} />
      <FleetNav />
      <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', mb: 2 }}>
        {!vehicle.archived && may.edit && (
          <Button variant="outlined" startIcon={<EditIcon />} onClick={() => setEditing(true)}>Change</Button>)}
        {!vehicle.archived && may.archive && (
          <Button color="warning" variant="outlined" startIcon={<ArchiveIcon />} onClick={() => setArchiving(true)}>Archive</Button>)}
        {vehicle.archived && may.archive && (
          <Button variant="contained" startIcon={<RestoreIcon />} disabled={restore.isPending}
                  onClick={() => restore.mutate()}>Restore</Button>)}
      </Stack>
      <SaveStatus state={restore.isPending ? 'saving' : restore.isError ? 'failed' : 'idle'} error={restore.error} savingText="Restoring…" />
      {vehicle.archived && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Archived {when(vehicle.archived_at)}{vehicle.archived_by_name ? ` by ${vehicle.archived_by_name}` : ''}:
          {' '}{vehicle.archive_reason}. {vehicle.archived_note}
        </Alert>
      )}
      <Tabs value={tab} onChange={(_, t) => setTab(t)} sx={{ mb: 2 }} aria-label="About this vehicle" variant="scrollable" allowScrollButtonsMobile>
        <Tab value="details" label="Details" />
        <Tab value="device" label="GPS device" />
        <Tab value="groups" label={`Groups (${vehicle.groups.length})`} />
        <Tab value="history" label="History of changes" />
      </Tabs>
      {tab === 'details' && <Details vehicle={vehicle} />}
      {tab === 'device' && <DeviceTab vehicleId={vehicle.id} archived={vehicle.archived} />}
      {tab === 'groups' && (
        <GlassCard sx={{ p: 2 }}>
          {vehicle.groups.length === 0 ? (
            <EmptyState compact title="In no group" hint="Groups are made and filled on the Groups page." />
          ) : (
            <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap' }}>
              {vehicle.groups.map((g) => <Chip key={g.id} label={g.name} component={RouterLink} to="/gps/groups" clickable />)}
            </Stack>
          )}
        </GlassCard>
      )}
      {tab === 'history' && (
        <GlassCard sx={{ p: 0 }}>
          <Table size="small" aria-label="History of changes">
            <TableHead><TableRow><TableCell>When</TableCell><TableCell>What</TableCell><TableCell>Who</TableCell><TableCell>Detail</TableCell></TableRow></TableHead>
            <TableBody>
              {historyLoading ? (
                <SkeletonRows cols={4} rows={3} />
              ) : historyFailed ? (
                <TableErrorRow cols={4} title="The history could not be loaded" error={historyError} onRetry={refetchHistory} />
              ) : !history?.items.length ? (
                <TableEmptyRow cols={4} title="Nothing recorded yet" />
              ) : history.items.map((h, at) => (
                <TableRow key={`${h.created_at}-${at}`}>
                  <TableCell>{when(h.created_at)}</TableCell>
                  <TableCell>{HISTORY_WORDS[h.action] ?? h.action}</TableCell>
                  <TableCell>{h.by_name ?? '—'}</TableCell>
                  <TableCell><Typography variant="caption">{describe(h.detail)}</Typography></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </GlassCard>
      )}
      {editing && !vehicle.archived && may.edit && (
        <VehicleFormDialog vehicle={vehicle} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); again() }} />
      )}
      {archiving && (
        <ArchiveVehicleDialog vehicle={vehicle} onClose={() => setArchiving(false)} onArchived={() => { setArchiving(false); again() }} />
      )}
    </Box>
  )
}

/** What changed, in words: the fields' names, never their values - the record itself shows those. */
function describe(detail: Record<string, unknown> | null): string {
  if (!detail) return ''
  if (Array.isArray(detail.changed)) return `Changed: ${(detail.changed as string[]).map((f) => f.replace(/_/g, ' ')).join(', ')}`
  if (typeof detail.reason === 'string') return `Reason: ${detail.reason}`
  if (typeof detail.was_archived_for === 'string') return `Had been archived: ${detail.was_archived_for}`
  return ''
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '200px 1fr' }, gap: { xs: 0, sm: 2 }, py: 0.75 }}>
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      <Typography variant="body2" component="div">{children || '—'}</Typography>
    </Box>
  )
}

function customText(field: VehicleField, value: unknown): string {
  if (value === undefined || value === null || value === '') return '—'
  if (field.field_type === 'boolean') return value ? 'Yes' : 'No'
  if (field.field_type === 'date') return new Date(`${String(value)}T00:00:00`).toLocaleDateString()
  return String(value)
}

function Details({ vehicle: v }: { vehicle: FleetVehicleDetail }) {
  const shown = v.fields.filter((f) => f.is_active || v.custom_fields?.[f.field_key] !== undefined)
  return (
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2 }}>
      <GlassCard sx={{ p: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>The vehicle</Typography>
        <Row label="Registration number">{v.plate_number}</Row>
        <Row label="Name">{v.name}</Row>
        <Row label="Type">{v.type_label}</Row>
        <Row label="Make and model">{[v.make, v.model].filter(Boolean).join(' ')}</Row>
        <Row label="Colour">{v.color}</Row>
        <Row label="Service status">{v.service_label}</Row>
        <Row label="Notes">{v.notes}</Row>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>Where and who</Typography>
        <Row label="Site">{v.site_name ?? 'No site'}</Row>
        <Row label="Department">{v.department}</Row>
        <Row label="Driver">{v.driver_display}</Row>
        <Row label="Driver's phone">{v.driver_phone}</Row>
        <Row label="Registered">{when(v.created_at)}</Row>
        <Row label="Last changed">{when(v.updated_at)}</Row>
      </GlassCard>
      {shown.length > 0 && (
        <GlassCard sx={{ p: 2, gridColumn: { md: '1 / -1' } }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>The organisation's own fields</Typography>
          {shown.map((f) => (
            <Row key={f.field_key} label={f.is_active ? f.label : `${f.label} (retired)`}>
              {customText(f, v.custom_fields?.[f.field_key])}
            </Row>
          ))}
        </GlassCard>
      )}
    </Box>
  )
}

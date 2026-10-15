/**
 * A vehicle's GPS device: which device reports for it, through which Traccar,
 * whether tracking is on, and what has arrived from it.
 *
 * The status is the server's, worked out from the time of the last fix: a
 * vehicle is never called Online without a recent one, and one that has never
 * reported says so. Positions the server refused are listed with their reason,
 * so a device that sends nonsense can be seen doing it. Whoever may manage
 * devices links one, tests it, and can register it in Traccar from here.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, FormControlLabel, MenuItem, Skeleton, Switch, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import Stack from '@/components/common/Stack'
import { EmptyState, ErrorState, SaveStatus, ageText } from '@/components/states'
import { useNow } from '@/motion'
import {
  TRACKING_COLOUR, TRACKING_LABEL, getVehicleDevice, listProviders, removeVehicleDevice, setVehicleDevice, testVehicleDevice,
  type DeviceState,
} from '@/api/fleet'

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—')

export function DeviceTab({ vehicleId, archived }: { vehicleId: string; archived: boolean }) {
  const { data: device, isLoading, isLoadingError: deviceFailed, error: deviceError, refetch } = useQuery({
    queryKey: ['fleet-device', vehicleId], queryFn: () => getVehicleDevice(vehicleId), refetchInterval: 30_000,
  })
  if (isLoading) return <Skeleton variant="rounded" height={240} />
  if (deviceFailed || !device) return <ErrorState title="The device could not be loaded" error={deviceError} onRetry={refetch} />
  return (
    <Box sx={{ display: 'grid', gap: 2 }}>
      <Reading device={device} />
      {/* Keyed by the device, so the form starts again from what the server holds when that changes. */}
      {device.may_manage && !archived && (
        <DeviceForm key={`${device.device_identifier}|${device.provider?.id}`} vehicleId={vehicleId} device={device} />
      )}
      {archived && <Alert severity="info">An archived vehicle has no device. Restore it to give it one.</Alert>}
      <Refused device={device} />
    </Box>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '200px 1fr' }, gap: { xs: 0, sm: 2 }, py: 0.5 }}>
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      <Typography variant="body2" component="div">{children ?? '—'}</Typography>
    </Box>
  )
}

function Reading({ device: d }: { device: DeviceState }) {
  const now = useNow()
  const heard = d.last_fix_at ? ageText(now - new Date(d.last_fix_at).getTime()) : null
  return (
    <GlassCard sx={{ p: 2 }}>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', mb: 1, flexWrap: 'wrap' }}>
        <Typography variant="subtitle2">Tracking</Typography>
        <Chip size="small" color={TRACKING_COLOUR[d.status]} label={TRACKING_LABEL[d.status]} />
        {d.status === 'online' && d.motion && <Chip size="small" variant="outlined" label={d.motion === 'moving' ? 'Moving' : 'Stopped'} />}
        <Typography variant="caption" color="text.secondary">
          Online under {d.limits.online_minutes} min since the last fix, stale under {d.limits.offline_minutes} min, then offline.
        </Typography>
      </Stack>
      {!d.device_identifier ? (
        <EmptyState compact title="No device is linked" hint="Link one below to start tracking this vehicle." />
      ) : (
        <>
          <Row label="Device ID">{d.device_identifier}</Row>
          <Row label="Reports through">{d.provider?.name}</Row>
          <Row label="Tracking">{d.tracking_enabled ? 'On' : 'Off'}</Row>
          <Row label="Last fix">
            {d.last_fix_at ? `${when(d.last_fix_at)} (${heard})` : 'No position has arrived from this device yet'}
          </Row>
          <Row label="Last heard from">{when(d.last_received_at)}</Row>
          {d.last_fix_at && (
            <>
              <Row label="Last known place">{d.last_lat?.toFixed(5)}, {d.last_lon?.toFixed(5)}</Row>
              <Row label="Speed and heading">
                {d.last_speed != null ? `${Number(d.last_speed).toFixed(0)} km/h` : 'Speed not reported'}
                {d.last_heading != null ? `, heading ${d.last_heading}°` : ''}
              </Row>
              <Row label="Accuracy">{d.last_accuracy != null ? `${Number(d.last_accuracy).toFixed(0)} m` : 'Not reported'}</Row>
              <Row label="Satellites and ignition">
                {[d.last_satellites != null ? `${d.last_satellites} satellites` : null,
                  d.last_ignition != null ? `ignition ${d.last_ignition ? 'on' : 'off'}` : null].filter(Boolean).join(', ') || 'Not reported'}
              </Row>
            </>
          )}
        </>
      )}
    </GlassCard>
  )
}

function DeviceForm({ vehicleId, device }: { vehicleId: string; device: DeviceState }) {
  const qc = useQueryClient()
  const [identifier, setIdentifier] = useState(device.device_identifier ?? '')
  const [provider, setProvider] = useState(device.provider?.id ?? '')
  const [enabled, setEnabled] = useState(device.tracking_enabled || !device.device_identifier)
  const [register, setRegister] = useState(true)
  const { data: providers } = useQuery({ queryKey: ['fleet-providers'], queryFn: listProviders })
  const again = () => {
    qc.invalidateQueries({ queryKey: ['fleet-device', vehicleId] })
    qc.invalidateQueries({ queryKey: ['fleet-vehicles'] })
  }
  const save = useMutation({
    mutationFn: () => setVehicleDevice(vehicleId, {
      device_identifier: identifier.trim() || null, provider_connection_id: provider || null,
      tracking_enabled: enabled && !!identifier.trim(), register_with_provider: register && !!identifier.trim(),
    }),
    onSuccess: again,
  })
  const remove = useMutation({ mutationFn: () => removeVehicleDevice(vehicleId), onSuccess: again })
  const test = useMutation({ mutationFn: () => testVehicleDevice(vehicleId) })
  const active = providers?.items.filter((p) => p.is_active) ?? []
  const builtin = providers?.builtin.available ?? device.builtin_available
  return (
    <GlassCard sx={{ p: 2 }}>
      <Typography variant="subtitle2" sx={{ mb: 1.5 }}>The device</Typography>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
        <TextField size="small" label="Device ID" value={identifier} onChange={(e) => setIdentifier(e.target.value)}
                   helperText="The tracker's IMEI, or the ID the Traccar Client phone app shows" />
        <TextField select size="small" label="Reports through" value={provider} onChange={(e) => setProvider(e.target.value)}
                   slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
                   helperText={!builtin && !provider ? "The platform's Traccar is not set up on this server" : undefined}>
          <MenuItem value="">The platform's Traccar</MenuItem>
          {active.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
        </TextField>
      </Box>
      <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap', alignItems: 'center', mt: 1 }}>
        <FormControlLabel label="Tracking on" control={<Switch checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />} />
        <FormControlLabel label="Register it in Traccar" control={<Checkbox checked={register} onChange={(e) => setRegister(e.target.checked)} />} />
      </Stack>
      <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', alignItems: 'center', mt: 1 }}>
        <Button variant="contained" disabled={save.isPending || (enabled && !identifier.trim())} onClick={() => save.mutate()}>Save</Button>
        {device.device_identifier && (
          <>
            <Button variant="outlined" disabled={test.isPending} onClick={() => test.mutate()}>Test the device</Button>
            <Button color="warning" disabled={remove.isPending} onClick={() => remove.mutate()}>Unlink the device</Button>
          </>
        )}
        <SaveStatus state={save.isPending || remove.isPending ? 'saving' : save.isError || remove.isError ? 'failed' : save.isSuccess ? 'saved' : 'idle'}
                    error={save.error ?? remove.error} />
      </Stack>
      {save.data?.registered && (
        <Alert severity={save.data.registered.ok ? 'success' : 'warning'} sx={{ mt: 1.5 }}>{save.data.registered.message}</Alert>
      )}
      {test.data && (
        <Alert severity={test.data.provider.ok ? 'success' : 'warning'} sx={{ mt: 1.5 }}>
          {test.data.provider.message}
          {test.data.provider.last_update ? `. Traccar last heard from it ${new Date(test.data.provider.last_update).toLocaleString()}.` : '.'}
          {' '}This server last heard from it: {when(test.data.last_received_at)}.
        </Alert>
      )}
      {test.isError && <Alert severity="error" sx={{ mt: 1.5 }}>The test could not be run.</Alert>}
    </GlassCard>
  )
}

function Refused({ device: d }: { device: DeviceState }) {
  if (!d.rejects.length && !d.status_changes.length) return null
  return (
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2 }}>
      <GlassCard sx={{ p: 0 }}>
        <Typography variant="subtitle2" sx={{ p: 2, pb: 0 }}>Positions not kept</Typography>
        <Table size="small" aria-label="Positions not kept">
          <TableHead><TableRow><TableCell>Why</TableCell><TableCell align="right">Times</TableCell><TableCell>Last</TableCell></TableRow></TableHead>
          <TableBody>
            {d.rejects.length === 0 ? (
              <TableRow><TableCell colSpan={3}>None</TableCell></TableRow>
            ) : d.rejects.map((r) => (
              <TableRow key={r.reason}><TableCell>{r.label}</TableCell><TableCell align="right">{r.times}</TableCell><TableCell>{when(r.last_at)}</TableCell></TableRow>
            ))}
          </TableBody>
        </Table>
      </GlassCard>
      <GlassCard sx={{ p: 0 }}>
        <Typography variant="subtitle2" sx={{ p: 2, pb: 0 }}>Status changes</Typography>
        <Table size="small" aria-label="Status changes">
          <TableHead><TableRow><TableCell>When</TableCell><TableCell>From</TableCell><TableCell>To</TableCell></TableRow></TableHead>
          <TableBody>
            {d.status_changes.map((c) => (
              <TableRow key={c.changed_at}><TableCell>{when(c.changed_at)}</TableCell>
                <TableCell>{TRACKING_LABEL[c.from_status]}</TableCell><TableCell>{TRACKING_LABEL[c.to_status]}</TableCell></TableRow>
            ))}
          </TableBody>
        </Table>
      </GlassCard>
    </Box>
  )
}

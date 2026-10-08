/**
 * The dialogs of device health and the asset register: one device's health,
 * one asset, adding or changing an asset, and putting known devices into the
 * register.
 *
 * Wherever a reading is shown, what the platform does not measure is shown
 * with it — the server sends the list and this never leaves it out.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel,
  MenuItem, Skeleton, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { getSites } from '@/api/sites'
import {
  addAsset, apiError, changeAsset, getAsset, getDevice, getUnregistered, registerDevices, restoreAsset, retireAsset,
} from '@/api/securityAssets'
import type { Asset, AssetKind, AssetStatus, DeviceKind, Register, Unregistered } from '@/api/securityAssets'
import {
  HEALTH_COLOUR, HEALTH_LABEL, ORDER_LABEL, STATUS_LABEL, downLine, factLines, fmt, madeBy, sinceLine, warrantyLine,
} from './assetFormat'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }
const boxed = { gap: 1.5, p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1.5 }

/** What the platform does not measure. Shown wherever a reading is. */
export function NotMeasured({ items }: { items: string[] }) {
  if (!items.length) return null
  return (
    <Typography variant="caption" color="text.secondary" data-testid="not-measured">
      Not measured by the platform: {items.join('; ')}.
    </Typography>
  )
}

interface DeviceProps { device: { kind: DeviceKind; device_id: string } | null; onClose: () => void }

export function DeviceDialog(props: DeviceProps) {
  return props.device ? <DeviceView {...props} device={props.device} /> : null
}

function DeviceView({ device, onClose }: DeviceProps & { device: { kind: DeviceKind; device_id: string } }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['device-health', device.kind, device.device_id], queryFn: () => getDevice(device.kind, device.device_id) })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        {data ? (
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <span>{data.name}</span>
            <Chip size="small" color={HEALTH_COLOUR[data.state]} label={HEALTH_LABEL[data.state]} />
          </Stack>) : 'Device'}
      </DialogTitle>
      <DialogContent>
        {isLoading && <Skeleton height={160} />}
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {data && (
          <Stack sx={{ gap: 1.5, mt: 0.5 }}>
            <Typography variant="body2" color="text.secondary">
              {data.kind_label}{data.site_name ? ` · ${data.site_name}` : ''}
              {data.asset_code ? ` · ${data.asset_code}` : ' · not in the asset register'}
              {sinceLine(data) ? ` · ${HEALTH_LABEL[data.state].toLowerCase()} ${sinceLine(data)}` : ''}
            </Typography>
            <Box sx={boxed} data-testid="reading">
              {data.reasons.map((line) => <Typography key={line} variant="body2">{line}</Typography>)}
              {factLines(data.facts).map((line) => (
                <Typography key={line} variant="caption" color="text.secondary" sx={{ display: 'block' }}>{line}</Typography>))}
            </Box>
            <Typography variant="caption" color="text.secondary">{data.note}</Typography>
            <NotMeasured items={data.not_measured} />
            <Divider />
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>How long it was down</Typography>
            {data.down.map((d) => <Typography key={d.days} variant="body2">{downLine(d)}</Typography>)}
            <Typography variant="caption" color="text.secondary">{data.down[0]?.note}</Typography>
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>What was kept of it</Typography>
            {!data.history.length ? (
              <Typography variant="body2" color="text.secondary">Nothing yet: its state is kept when it changes.</Typography>
            ) : data.history.map((h) => (
              <Stack key={h.observed_at} direction="row" data-testid="kept" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                <Chip size="small" variant="outlined" color={HEALTH_COLOUR[h.state]} label={HEALTH_LABEL[h.state]} />
                <Typography variant="caption" color="text.secondary">{fmt(h.observed_at)} · {h.reasons.join(' ')}</Typography>
              </Stack>))}
          </Stack>)}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

interface FormProps {
  open: boolean; kinds: Register['kinds']; asset?: Asset | null; onClose: () => void; onDone: (made: Asset) => void
}

export function AssetFormDialog(props: FormProps) {
  return props.open ? <AssetForm {...props} /> : null
}

function AssetForm({ kinds, asset, onClose, onDone }: FormProps) {
  const [kind, setKind] = useState<AssetKind | ''>(asset?.kind ?? '')
  const [name, setName] = useState(asset?.name ?? '')
  const [siteId, setSiteId] = useState(asset?.site_id ?? '')
  const [make, setMake] = useState(asset?.make ?? '')
  const [model, setModel] = useState(asset?.model ?? '')
  const [serial, setSerial] = useState(asset?.serial_number ?? '')
  const [location, setLocation] = useState(asset?.location ?? '')
  const [vendor, setVendor] = useState(asset?.vendor ?? '')
  const [installed, setInstalled] = useState(asset?.installed_on ?? '')
  const [warranty, setWarranty] = useState(asset?.warranty_until ?? '')
  const [status, setStatus] = useState<Exclude<AssetStatus, 'RETIRED'>>(
    asset && asset.status !== 'RETIRED' ? asset.status : 'IN_SERVICE')
  const [notes, setNotes] = useState(asset?.notes ?? '')
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const act = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), site_id: siteId || null, make: make.trim() || null, model: model.trim() || null,
                     serial_number: serial.trim() || null, location: location.trim() || null,
                     vendor: vendor.trim() || null, installed_on: installed || null, warranty_until: warranty || null,
                     status, notes: notes.trim() || null }
      return asset ? changeAsset(asset.id, body) : addAsset({ kind: kind as AssetKind, ...body })
    },
    onSuccess: (made) => { onDone(made); onClose() },
  })
  const chosen = kinds.find((k) => k.key === kind)
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>{asset ? `Change ${asset.asset_code}` : 'Add an asset'}</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField select label="Kind" value={kind} sx={{ minWidth: 220 }} slotProps={shrunk} disabled={!!asset}
                       helperText={asset ? 'What kind of thing it is does not change' : undefined}
                       onChange={(e) => setKind(e.target.value as AssetKind)}>
              <MenuItem value="" disabled>Choose a kind</MenuItem>
              {kinds.map((k) => <MenuItem key={k.key} value={k.key}>{k.label}</MenuItem>)}
            </TextField>
            <TextField label="Name" value={name} sx={{ flex: 1, minWidth: 240 }} onChange={(e) => setName(e.target.value)}
                       slotProps={{ htmlInput: { maxLength: 200 } }} />
          </Stack>
          {chosen && !asset && (
            <Alert severity="info">
              {chosen.monitored
                ? 'A device the platform already knows is put into the register with “Register known devices”, which gives it that device’s health. Added here, it is a record with no reading until it is said which device it is.'
                : 'The platform does not know this kind of thing as a device, so it will have no health reading. It is still an asset: its make, serial number, vendor, warranty and the work done on it are kept.'}
            </Alert>)}
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField select label="Site" value={siteId} sx={{ minWidth: 220 }} slotProps={shrunk}
                       onChange={(e) => setSiteId(e.target.value)}>
              <MenuItem value="">No particular site</MenuItem>
              {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
            <TextField label="Where it is" value={location} sx={{ flex: 1, minWidth: 240 }}
                       onChange={(e) => setLocation(e.target.value)} slotProps={{ htmlInput: { maxLength: 1000 } }} />
          </Stack>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField label="Make" value={make} onChange={(e) => setMake(e.target.value)} slotProps={{ htmlInput: { maxLength: 120 } }} />
            <TextField label="Model" value={model} onChange={(e) => setModel(e.target.value)} slotProps={{ htmlInput: { maxLength: 120 } }} />
            <TextField label="Serial number" value={serial} onChange={(e) => setSerial(e.target.value)}
                       slotProps={{ htmlInput: { maxLength: 120 } }} />
          </Stack>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField label="Vendor" value={vendor} sx={{ minWidth: 220 }} onChange={(e) => setVendor(e.target.value)}
                       slotProps={{ htmlInput: { maxLength: 200 } }} />
            <TextField label="Installed on" type="date" value={installed} onChange={(e) => setInstalled(e.target.value)}
                       slotProps={{ inputLabel: { shrink: true } }} />
            <TextField label="Warranty until" type="date" value={warranty} onChange={(e) => setWarranty(e.target.value)}
                       slotProps={{ inputLabel: { shrink: true } }} />
            <TextField select label="Status" value={status} sx={{ minWidth: 160 }}
                       onChange={(e) => setStatus(e.target.value as Exclude<AssetStatus, 'RETIRED'>)}>
              {(['IN_SERVICE', 'UNDER_REPAIR', 'SPARE'] as const).map((s) => <MenuItem key={s} value={s}>{STATUS_LABEL[s]}</MenuItem>)}
            </TextField>
          </Stack>
          <TextField label="Notes" value={notes} multiline minRows={2} onChange={(e) => setNotes(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 5000 } }} />
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" disabled={!kind || !name.trim() || act.isPending} onClick={() => act.mutate()}>
          {asset ? 'Save the changes' : 'Add it'}</Button>
      </DialogActions>
    </Dialog>
  )
}

interface AssetProps { id: string | null; kinds: Register['kinds']; onClose: () => void; onChanged: () => Promise<unknown> }

export function AssetDialog(props: AssetProps) {
  return props.id ? <AssetView {...props} id={props.id} /> : null
}

function AssetView({ id, kinds, onClose, onChanged }: AssetProps & { id: string }) {
  const [changing, setChanging] = useState(false)
  const [retiring, setRetiring] = useState(false)
  const [reason, setReason] = useState('')
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ['security-asset', id], queryFn: () => getAsset(id) })
  const again = () => Promise.all([refetch(), onChanged()])
  const run = useMutation({
    mutationFn: (what: () => Promise<unknown>) => what().then(again),
    onSuccess: () => { setRetiring(false); setReason('') },
  })
  const a = data
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>
        {a ? (
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <span>{a.asset_code} · {a.name}</span>
            <Chip size="small" variant="outlined" label={STATUS_LABEL[a.status]} />
            {a.health && <Chip size="small" color={HEALTH_COLOUR[a.health.state]} label={HEALTH_LABEL[a.health.state]} />}
          </Stack>) : 'Asset'}
      </DialogTitle>
      <DialogContent>
        {isLoading && <Skeleton height={200} />}
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {a && (
          <Stack sx={{ gap: 1.5, mt: 0.5 }}>
            <Typography variant="body2" color="text.secondary">
              {a.kind_label}{a.site_name ? ` · ${a.site_name}` : ' · no particular site'}
              {a.place_name ? ` · ${a.place_name}` : ''}{a.location ? ` · ${a.location}` : ''}
            </Typography>
            <Box sx={boxed}>
              <Typography variant="body2">{madeBy(a) || 'Make, model and serial number are not recorded.'}</Typography>
              <Typography variant="body2">
                {a.vendor ? `Vendor: ${a.vendor}` : 'Vendor not recorded'}
                {a.installed_on ? ` · installed ${a.installed_on}` : ''}
              </Typography>
              <Typography variant="body2">{warrantyLine(a)}</Typography>
              {a.notes && <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{a.notes}</Typography>}
              {a.status === 'RETIRED' && (
                <Typography variant="body2">
                  Retired by {a.retired_by_name ?? 'somebody no longer on the system'}: {a.retire_reason}</Typography>)}
            </Box>
            <Box sx={boxed} data-testid="asset-health">
              {a.health ? (
                <>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {HEALTH_LABEL[a.health.state]}{sinceLine(a.health) ? ` ${sinceLine(a.health)}` : ''}
                  </Typography>
                  {a.health.reasons.map((line) => <Typography key={line} variant="body2">{line}</Typography>)}
                  <Typography variant="caption" color="text.secondary">{a.note}</Typography>
                  <NotMeasured items={a.not_measured} />
                </>
              ) : <Typography variant="body2">{a.not_monitored ?? 'There is no reading of it.'}</Typography>}
            </Box>
            <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap' }}>
              {a.may.change && a.status !== 'RETIRED' && <Button size="small" onClick={() => setChanging(true)}>Change</Button>}
              {a.may.retire && <Button size="small" color="error" onClick={() => setRetiring(true)}>Retire it</Button>}
              {a.may.restore && (
                <Button size="small" disabled={run.isPending} onClick={() => run.mutate(() => restoreAsset(a.id))}>
                  Put it back in service</Button>)}
            </Stack>
            {retiring && (
              <Stack sx={boxed}>
                <TextField label="Why it is retired" value={reason} autoFocus multiline minRows={2}
                           helperText="It is kept, with its history. The device it is, if any, is not touched."
                           onChange={(e) => setReason(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button size="small" onClick={() => setRetiring(false)}>Not yet</Button>
                  <Button size="small" variant="contained" color="error" disabled={!reason.trim() || run.isPending}
                          onClick={() => run.mutate(() => retireAsset(a.id, reason.trim()))}>Retire it</Button>
                </Stack>
              </Stack>)}
            {run.isError && <Alert severity="error">{apiError(run.error)}</Alert>}
            {a.work_orders && (
              <>
                <Divider />
                <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Work on it</Typography>
                {!a.work_orders.length ? <Typography variant="body2" color="text.secondary">None recorded.</Typography>
                  : a.work_orders.map((o) => (
                    <Typography key={o.id} variant="body2" data-testid="asset-order">
                      {o.number} · {o.title} — {ORDER_LABEL[o.state as keyof typeof ORDER_LABEL]}
                      {o.completion_note ? `: ${o.completion_note}` : ''}
                    </Typography>))}
              </>)}
          </Stack>)}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
      <AssetFormDialog open={changing} kinds={kinds} asset={a} onClose={() => setChanging(false)} onDone={() => { void again() }} />
    </Dialog>
  )
}

interface KnownProps { open: boolean; onClose: () => void; onDone: () => Promise<unknown> }

export function RegisterDevicesDialog(props: KnownProps) {
  return props.open ? <KnownDevices {...props} /> : null
}

const key = (d: Pick<Unregistered, 'kind' | 'device_id'>) => `${d.kind}:${d.device_id}`

function KnownDevices({ onClose, onDone }: KnownProps) {
  const [chosen, setChosen] = useState<string[] | null>(null)
  const { data, isLoading, error } = useQuery({ queryKey: ['assets-unregistered'], queryFn: getUnregistered })
  const items = data ?? []
  // Everything is ticked to begin with: the common case is "all of them".
  const ticked = chosen ?? items.map(key)
  const act = useMutation({
    mutationFn: () => registerDevices(items.filter((d) => ticked.includes(key(d)))
      .map((d) => ({ kind: d.kind, device_id: d.device_id }))).then((done) => onDone().then(() => done)),
    onSuccess: onClose,
  })
  const flip = (k: string) => setChosen(ticked.includes(k) ? ticked.filter((x) => x !== k) : [...ticked, k])
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Register known devices</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 1.5, mt: 0.5 }}>
          <Alert severity="info">
            Each is entered as the platform knows it: its name, its site, and its make, model and serial number where
            the device has them. The vendor, the warranty and the rest are for you to fill in afterwards.
          </Alert>
          {isLoading && <Skeleton height={120} />}
          {!!error && <Alert severity="error">{apiError(error)}</Alert>}
          {data && !items.length && <Alert severity="success">Every device the platform knows is in the register.</Alert>}
          {items.map((d) => (
            <FormControlLabel key={key(d)} data-testid="known-device"
                              control={<Checkbox size="small" checked={ticked.includes(key(d))} onChange={() => flip(key(d))} />}
                              label={`${d.kind_label}: ${d.name}${d.site_name ? ` — ${d.site_name}` : ''}`} />))}
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" disabled={!items.length || !ticked.length || act.isPending} onClick={() => act.mutate()}>
          Register {ticked.length} {ticked.length === 1 ? 'device' : 'devices'}</Button>
      </DialogActions>
    </Dialog>
  )
}

/**
 * Assets and maintenance: the health of the devices the platform knows, the
 * register of security assets, and the work done to keep them working.
 *
 * A health reading is made of what a device reports, and the screen says what
 * is not measured beside it. A work order the platform put forward is a
 * suggestion until a person accepts it.
 *
 * Cameras, recorders, sensors, drones, alarm panels, guard kit and facility
 * defects are on the screens that have always had them, unchanged.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, FormControlLabel, MenuItem, Skeleton, Switch, Tab, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Tabs, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { getSites } from '@/api/sites'
import { apiError, getHealth, getRegister } from '@/api/securityAssets'
import type { AssetKind, AssetStatus, DeviceKind, HealthState, Warranty } from '@/api/securityAssets'
import {
  changeSchedule, getMaintenanceSettings, listOrders, listSchedules, setMaintenanceSettings,
} from '@/api/maintenance'
import type { OrderState } from '@/api/maintenance'
import { AssetDialog, AssetFormDialog, DeviceDialog, NotMeasured, RegisterDevicesDialog } from '@/components/assets/AssetDialogs'
import { OrderDialog, RaiseDialog, ScheduleDialog } from '@/components/assets/OrderDialogs'
import {
  HEALTH_COLOUR, HEALTH_LABEL, KIND_LABEL, ORDER_COLOUR, ORDER_LABEL, ORIGIN_LABEL, STATUS_LABEL, WARRANTY_LABEL,
  dueIn, every, factLines, fmt, fmtDate, heldBy, madeBy, sinceLine, warrantyLine,
} from '@/components/assets/assetFormat'
import { ErrorState } from '@/components/states'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }
const STATES: HealthState[] = ['DOWN', 'DEGRADED', 'NOT_KNOWN', 'OK', 'OFF']

function HealthTab() {
  const [siteId, setSiteId] = useState('')
  const [kind, setKind] = useState<DeviceKind | ''>('')
  const [state, setState] = useState<HealthState | ''>('')
  const [open, setOpen] = useState<{ kind: DeviceKind; device_id: string } | null>(null)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data, isLoading, error, refetch: refetchData } = useQuery({
    queryKey: ['device-health', siteId, kind, state],
    queryFn: () => getHealth({ site_id: siteId || undefined, kind: kind || undefined, state: state || undefined }),
    placeholderData: keepPreviousData, refetchInterval: 60_000,
  })
  return (
    <>
      {data && (
        <GlassCard sx={{ p: 2, mb: 2 }} data-testid="health-summary">
          <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 700, mr: 1 }}>{data.summary.devices} devices</Typography>
            {STATES.filter((s) => data.summary.by_state[s]).map((s) => (
              <Chip key={s} size="small" color={HEALTH_COLOUR[s]} label={`${HEALTH_LABEL[s]}: ${data.summary.by_state[s]}`} />))}
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>{data.note}</Typography>
          <NotMeasured items={data.not_measured} />
        </GlassCard>)}
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 180 }} slotProps={shrunk}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">Every site</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Kind of device" value={kind} sx={{ minWidth: 180 }} slotProps={shrunk}
                     onChange={(e) => setKind(e.target.value as DeviceKind | '')}>
            <MenuItem value="">Every kind</MenuItem>
            {(data?.kinds ?? []).map((k) => <MenuItem key={k.key} value={k.key}>{k.label}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="State" value={state} sx={{ minWidth: 160 }} slotProps={shrunk}
                     onChange={(e) => setState(e.target.value as HealthState | '')}>
            <MenuItem value="">Any</MenuItem>
            {STATES.map((s) => <MenuItem key={s} value={s}>{HEALTH_LABEL[s]}</MenuItem>)}
          </TextField>
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {!!error && <ErrorState compact error={error} onRetry={refetchData} />}
        {isLoading ? <Skeleton height={200} /> : !data?.items.length && !error ? (
          <Alert severity="info">No device matches.</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow><TableCell>Device</TableCell><TableCell>State</TableCell><TableCell>Why</TableCell><TableCell /></TableRow>
              </TableHead>
              <TableBody>
                {(data?.items ?? []).map((r) => (
                  <TableRow key={`${r.kind}:${r.device_id}`} data-testid="device-row" hover>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{r.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {r.kind_label}{r.site_name ? ` · ${r.site_name}` : ''} · {r.asset_code ?? 'not in the register'}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip size="small" color={HEALTH_COLOUR[r.state]} label={HEALTH_LABEL[r.state]} />
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{sinceLine(r)}</Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{r.reasons.join(' ')}</Typography>
                      <Typography variant="caption" color="text.secondary">{factLines(r.facts).join(' · ')}</Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Button size="small" onClick={() => setOpen({ kind: r.kind, device_id: r.device_id })}>Open</Button>
                    </TableCell>
                  </TableRow>))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>
      <DeviceDialog device={open} onClose={() => setOpen(null)} />
    </>
  )
}

function RegisterTab() {
  const qc = useQueryClient()
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [siteId, setSiteId] = useState('')
  const [kind, setKind] = useState<AssetKind | ''>('')
  const [status, setStatus] = useState<AssetStatus | ''>('')
  const [warranty, setWarranty] = useState<Warranty | ''>('')
  const [adding, setAdding] = useState(false)
  const [known, setKnown] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data, isLoading, error, refetch: refetchData2 } = useQuery({
    queryKey: ['security-assets', q, siteId, kind, status, warranty],
    queryFn: () => getRegister({ q: q || undefined, site_id: siteId || undefined, kind: kind || undefined,
                                 status: status || undefined, warranty: warranty || undefined }),
    placeholderData: keepPreviousData,
  })
  const again = () => qc.invalidateQueries({ queryKey: ['security-assets'] })
  const items = data?.items ?? []
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }} component="form"
               onSubmit={(e: React.FormEvent) => { e.preventDefault(); setQ(typed.trim()) }}>
          <TextField size="small" label="Name, code, serial or vendor" value={typed} sx={{ minWidth: 230 }}
                     onChange={(e) => setTyped(e.target.value)} slotProps={{ htmlInput: { maxLength: 200 } }} />
          <Button type="submit" variant="outlined">Search</Button>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 170 }} slotProps={shrunk}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">Every site</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Kind of asset" value={kind} sx={{ minWidth: 170 }} slotProps={shrunk}
                     onChange={(e) => setKind(e.target.value as AssetKind | '')}>
            <MenuItem value="">Every kind</MenuItem>
            {(data?.kinds ?? []).map((k) => <MenuItem key={k.key} value={k.key}>{k.label}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Status" value={status} sx={{ minWidth: 150 }} slotProps={shrunk}
                     onChange={(e) => setStatus(e.target.value as AssetStatus | '')}>
            <MenuItem value="">Not retired</MenuItem>
            {(data?.statuses ?? []).map((s) => <MenuItem key={s} value={s}>{STATUS_LABEL[s]}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Warranty" value={warranty} sx={{ minWidth: 160 }} slotProps={shrunk}
                     onChange={(e) => setWarranty(e.target.value as Warranty | '')}>
            <MenuItem value="">Any</MenuItem>
            {(Object.keys(WARRANTY_LABEL) as Warranty[]).map((w) => <MenuItem key={w} value={w}>{WARRANTY_LABEL[w]}</MenuItem>)}
          </TextField>
          {data?.can_manage && (
            <>
              <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAdding(true)}>Add an asset</Button>
              <Button variant="outlined" onClick={() => setKnown(true)}>Register known devices</Button>
            </>)}
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {!!error && <ErrorState compact error={error} onRetry={refetchData2} />}
        {isLoading ? <Skeleton height={200} /> : !items.length && !error ? (
          <Alert severity="info">
            {data?.can_manage ? 'No asset matches. Add one, or register the devices the platform already knows.'
              : 'No asset matches.'}
          </Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Asset</TableCell><TableCell>Where</TableCell><TableCell>Health</TableCell>
                  <TableCell>Warranty</TableCell><TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((a) => (
                  <TableRow key={a.id} data-testid="asset-row" hover sx={{ opacity: a.status === 'RETIRED' ? 0.55 : 1 }}>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{a.asset_code} · {a.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {[a.kind_label, madeBy(a), a.status === 'IN_SERVICE' ? '' : STATUS_LABEL[a.status]].filter(Boolean).join(' · ')}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{a.site_name ?? 'No particular site'}</Typography>
                      <Typography variant="caption" color="text.secondary">{a.place_name ?? a.location ?? ''}</Typography>
                    </TableCell>
                    <TableCell>
                      {a.health ? <Chip size="small" color={HEALTH_COLOUR[a.health.state]} label={HEALTH_LABEL[a.health.state]} />
                        // No reading is not a good reading: it is said to be none.
                        : <Typography variant="caption" color="text.secondary">No reading</Typography>}
                      {!!a.open_orders && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          {a.open_orders} open {a.open_orders === 1 ? 'order' : 'orders'}</Typography>)}
                    </TableCell>
                    <TableCell><Typography variant="caption">{warrantyLine(a)}</Typography></TableCell>
                    <TableCell align="right"><Button size="small" onClick={() => setOpenId(a.id)}>Open</Button></TableCell>
                  </TableRow>))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>
      <AssetFormDialog open={adding} kinds={data?.kinds ?? []} onClose={() => setAdding(false)}
                       onDone={(made) => { void again().then(() => setOpenId(made.id)) }} />
      <RegisterDevicesDialog open={known} onClose={() => setKnown(false)} onDone={again} />
      <AssetDialog id={openId} kinds={data?.kinds ?? []} onClose={() => setOpenId(null)} onChanged={again} />
    </>
  )
}

function OrdersTab() {
  const qc = useQueryClient()
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [state, setState] = useState<OrderState | ''>('')
  const [mine, setMine] = useState(false)
  const [raising, setRaising] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const [hours, setHours] = useState('')
  const { data, isLoading, error, refetch: refetchData3 } = useQuery({
    queryKey: ['work-orders', q, state, mine],
    queryFn: () => listOrders({ q: q || undefined, state: state || undefined, mine: mine || undefined }),
    placeholderData: keepPreviousData,
  })
  const { data: asked } = useQuery({ queryKey: ['maintenance-settings'], queryFn: getMaintenanceSettings })
  const again = () => qc.invalidateQueries({ queryKey: ['work-orders'] })
  const ask = useMutation({
    mutationFn: (on: boolean) => setMaintenanceSettings({
      suggest_from_health: on, suggest_after_hours: Number(hours) || asked?.suggest_after_hours || 4 })
      .then(() => qc.invalidateQueries({ queryKey: ['maintenance-settings'] })),
  })
  const items = data?.items ?? []
  return (
    <>
      {asked && (
        <GlassCard sx={{ p: 2, mb: 2 }} data-testid="suggestions">
          <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
            <Typography variant="body2" sx={{ flex: 1, minWidth: 260 }}>
              {asked.suggest_from_health
                ? `A device read as down for ${asked.suggest_after_hours} hours or more is put forward as a work order.`
                : 'A device read as down does not put a work order forward: nobody has asked for that.'}
            </Typography>
            {asked.can_manage && (
              <>
                <TextField size="small" type="number" label="After (hours)" sx={{ width: 140 }}
                           value={hours || String(asked.suggest_after_hours)} onChange={(e) => setHours(e.target.value)}
                           slotProps={{ htmlInput: { min: 1, max: 168 } }} />
                <Button size="small" variant="outlined" disabled={ask.isPending}
                        onClick={() => ask.mutate(!asked.suggest_from_health)}>
                  {asked.suggest_from_health ? 'Stop putting them forward' : 'Put them forward'}</Button>
                {asked.suggest_from_health && !!hours && Number(hours) !== asked.suggest_after_hours && (
                  <Button size="small" disabled={ask.isPending} onClick={() => ask.mutate(true)}>Save the hours</Button>)}
              </>)}
          </Stack>
          <Typography variant="caption" color="text.secondary">{asked.note}</Typography>
          {ask.isError && <Alert severity="error" sx={{ mt: 1 }}>{apiError(ask.error)}</Alert>}
        </GlassCard>)}
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }} component="form"
               onSubmit={(e: React.FormEvent) => { e.preventDefault(); setQ(typed.trim()) }}>
          <TextField size="small" label="Title, number or asset" value={typed} sx={{ minWidth: 220 }}
                     onChange={(e) => setTyped(e.target.value)} slotProps={{ htmlInput: { maxLength: 200 } }} />
          <Button type="submit" variant="outlined">Search</Button>
          <TextField select size="small" label="State" value={state} sx={{ minWidth: 170 }} slotProps={shrunk}
                     onChange={(e) => setState(e.target.value as OrderState | '')}>
            <MenuItem value="">Not over</MenuItem>
            {(data?.states ?? []).map((s) => <MenuItem key={s} value={s}>{ORDER_LABEL[s]}</MenuItem>)}
          </TextField>
          <FormControlLabel label="Given to me" sx={{ ml: 0 }}
                            control={<Switch size="small" checked={mine} onChange={(e) => setMine(e.target.checked)} />} />
          {data && (
            <Stack direction="row" sx={{ gap: 0.5, flexWrap: 'wrap' }} data-testid="order-counts">
              <Chip size="small" color="info" variant="outlined" label={`Put forward: ${data.counts.suggested}`} />
              <Chip size="small" variant="outlined" label={`Open: ${data.counts.open}`} />
              <Chip size="small" variant="outlined" label={`In progress: ${data.counts.in_progress}`} />
              {!!data.counts.overdue && <Chip size="small" color="error" label={`Overdue: ${data.counts.overdue}`} />}
            </Stack>)}
          {data?.can_manage && (
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => setRaising(true)}>Raise a work order</Button>)}
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {!!error && <ErrorState compact error={error} onRetry={refetchData3} />}
        {isLoading ? <Skeleton height={200} /> : !items.length && !error ? (
          <Alert severity="info">No work order matches.</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Order</TableCell><TableCell>Where it came from</TableCell><TableCell>State</TableCell>
                  <TableCell>Who has it</TableCell><TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((o) => (
                  <TableRow key={o.id} data-testid="order-row" hover>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{o.number} · {o.title}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {[KIND_LABEL[o.kind], o.asset_code ? `${o.asset_code} ${o.asset_name}` : '', o.site_name ?? '']
                          .filter(Boolean).join(' · ')}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{ORIGIN_LABEL[o.origin]}</Typography>
                      <Typography variant="caption" color="text.secondary">{o.suggestion_reason ?? ''}</Typography>
                    </TableCell>
                    <TableCell>
                      <Chip size="small" color={ORDER_COLOUR[o.state]} label={ORDER_LABEL[o.state]} />
                      {o.overdue && <Chip size="small" color="error" label="Overdue" sx={{ ml: 0.5 }} />}
                      {o.due_at && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>Due {fmt(o.due_at)}</Typography>)}
                    </TableCell>
                    <TableCell><Typography variant="body2">{heldBy(o)}</Typography></TableCell>
                    <TableCell align="right"><Button size="small" onClick={() => setOpenId(o.id)}>Open</Button></TableCell>
                  </TableRow>))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>
      <RaiseDialog open={raising} onClose={() => setRaising(false)}
                   onDone={(made) => { void again().then(() => setOpenId(made.id)) }} />
      <OrderDialog id={openId} onClose={() => setOpenId(null)} onChanged={again} />
    </>
  )
}

function SchedulesTab() {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const { data, isLoading, error, refetch: refetchData4 } = useQuery({ queryKey: ['maintenance-schedules'], queryFn: listSchedules })
  const again = () => qc.invalidateQueries({ queryKey: ['maintenance-schedules'] })
  const flip = useMutation({
    mutationFn: (s: { id: string; is_active: boolean }) => changeSchedule(s.id, { is_active: !s.is_active }).then(again) })
  const items = data?.items ?? []
  return (
    <>
      <GlassCard sx={{ p: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, mb: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
          <Typography variant="body2" color="text.secondary" sx={{ flex: 1, minWidth: 260 }}>
            What is done every so many days. Before each date the work is put forward for somebody to accept.
          </Typography>
          {data?.can_manage && (
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAdding(true)}>Add a schedule</Button>)}
        </Stack>
        {!!error && <ErrorState compact error={error} onRetry={refetchData4} />}
        {flip.isError && <Alert severity="error">{apiError(flip.error)}</Alert>}
        {isLoading ? <Skeleton height={160} /> : !items.length && !error ? (
          <Alert severity="info">No schedule is kept.</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow><TableCell>Work</TableCell><TableCell>On</TableCell><TableCell>When</TableCell><TableCell /></TableRow>
              </TableHead>
              <TableBody>
                {items.map((s) => (
                  <TableRow key={s.id} data-testid="schedule-row" hover sx={{ opacity: s.is_active ? 1 : 0.55 }}>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{s.title}</Typography>
                      <Typography variant="caption" color="text.secondary">{s.instructions ?? ''}</Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{s.asset_code ? `${s.asset_code} ${s.asset_name}` : 'Nothing in particular'}</Typography>
                      <Typography variant="caption" color="text.secondary">{s.site_name ?? ''}</Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{every(s)} · {dueIn(s)}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        Next {fmtDate(s.next_due_on)}{s.last_done_on ? ` · last done ${fmtDate(s.last_done_on)}` : ' · not done yet'}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      {data?.can_manage && (
                        <Button size="small" disabled={flip.isPending} onClick={() => flip.mutate(s)}>
                          {s.is_active ? 'Switch off' : 'Switch on'}</Button>)}
                    </TableCell>
                  </TableRow>))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>
      <ScheduleDialog open={adding} onClose={() => setAdding(false)} onDone={() => { void again() }} />
    </>
  )
}

type Part = 'health' | 'register' | 'orders' | 'schedules'

export default function AssetsMaintenance() {
  const assets = usePermission('asset:read')
  const maintenance = usePermission('maintenance:read')
  const [part, setPart] = useState<Part>(assets ? 'health' : 'orders')
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Assets & Maintenance"
                  subtitle="The health of devices as they report it, the register of security assets, and the work that keeps them working" />
      <Tabs value={part} onChange={(_, v: Part) => setPart(v)} sx={{ mb: 2 }}>
        {assets && <Tab value="health" label="Device health" />}
        {assets && <Tab value="register" label="Asset register" />}
        {maintenance && <Tab value="orders" label="Work orders" />}
        {maintenance && <Tab value="schedules" label="Schedules" />}
      </Tabs>
      {part === 'health' && assets && <HealthTab />}
      {part === 'register' && assets && <RegisterTab />}
      {part === 'orders' && maintenance && <OrdersTab />}
      {part === 'schedules' && maintenance && <SchedulesTab />}
    </Box>
  )
}

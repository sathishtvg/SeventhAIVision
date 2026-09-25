/**
 * Drone Patrol — dashboard and fleet.
 *
 * The figures come from GET /drones/dashboard; the fleet from GET /drones.
 * Registering a drone, a provider or a site edge gateway happens here too:
 * a gateway's credential is shown once, when it is created or rotated, and
 * never again — the server keeps only its hash.
 */
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Grid, IconButton,
  MenuItem, Skeleton, Tab, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tabs,
  TextField, Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import FlightTakeoffIcon from '@mui/icons-material/FlightTakeoff'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { getSites, getSiteCameras } from '@/api/sites'
import {
  apiError, createDrone, createGateway, createProvider, getFleetDashboard, getProviderCatalogue,
  listDrones, listGateways, listProviders, rotateGatewayCredential, setDroneEnabled, updateDrone,
} from '@/api/drones'
import type { Drone, Gateway } from '@/api/drones'
import { BatteryBar, ComponentDot, DroneStatusChip, LicenceBanner } from '@/components/drones/droneUi'
import { RISK_COLOR, ago, pretty, useDroneRealtime } from '@/components/drones/droneFormat'
import { DroneNav } from './DroneNav'

function Kpi({ label, value, tone, hint }: { label: string; value: number | string; tone?: string; hint?: string }) {
  return (
    <GlassCard sx={{ p: 2, height: '100%' }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h4" sx={{ fontWeight: 700, color: tone }}>{value}</Typography>
      {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
    </GlassCard>
  )
}

export default function DroneDashboard() {
  const [tab, setTab] = useState(0)
  useDroneRealtime([['drone-dashboard'], ['drones']])
  const { data: dash } = useQuery({ queryKey: ['drone-dashboard'], queryFn: getFleetDashboard, refetchInterval: 15_000 })
  const openAlerts = dash ? dash.open_events_by_risk.MEDIUM + dash.open_events_by_risk.HIGH + dash.open_events_by_risk.CRITICAL : 0

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Drone Patrol" subtitle="Fleet, missions and what the drones have seen" />
      <DroneNav />
      <LicenceBanner />
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {[
          ['Total drones', dash?.fleet.total],
          ['Online', dash?.fleet.online, '#00c48c'],
          ['Offline', dash?.fleet.offline, dash?.fleet.offline ? '#ff7a45' : undefined],
          ['Active missions', dash?.missions.active_missions, '#4f8cff'],
          ['Failed missions', dash?.missions.failed_last_24h, dash?.missions.failed_last_24h ? '#ff3b5c' : undefined, 'last 24 hours'],
          ['Open alerts', openAlerts, openAlerts ? RISK_COLOR.HIGH : undefined, 'medium risk and above'],
          ['Battery warnings', dash?.fleet.battery_warnings, dash?.fleet.battery_warnings ? '#ffb020' : undefined],
          ['Maintenance due', dash?.fleet.maintenance_due, dash?.fleet.maintenance_due ? '#ffb020' : undefined, 'within 7 days'],
        ].map(([label, value, tone, hint]) => (
          <Grid key={label as string} size={{ xs: 6, sm: 4, md: 3, lg: 1.5 }}>
            <Kpi label={label as string} value={value ?? '—'} tone={tone as string | undefined}
                 hint={hint as string | undefined} />
          </Grid>
        ))}
      </Grid>
      <GlassCard>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ px: 2 }}>
          <Tab label="Fleet" />
          <Tab label="Providers" />
          <Tab label="Edge gateways" />
        </Tabs>
        {tab === 0 && <Fleet />}
        {tab === 1 && <Providers />}
        {tab === 2 && <Gateways />}
      </GlassCard>
    </Box>
  )
}

// ── Fleet ────────────────────────────────────────────────────────────────────

function Fleet() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const canCreate = usePermission('drone:create')
  const canUpdate = usePermission('drone:update')
  const [editing, setEditing] = useState<Drone | 'new' | null>(null)
  const { data, isLoading } = useQuery({ queryKey: ['drones'], queryFn: () => listDrones(), refetchInterval: 15_000 })
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => setDroneEnabled(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['drones'] }),
  })

  if (isLoading) return <Box sx={{ p: 2 }}><Skeleton height={200} /></Box>
  const drones = data?.items ?? []
  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" sx={{ justifyContent: 'flex-end', mb: 1 }}>
        {canCreate && <Button startIcon={<AddIcon />} variant="contained" onClick={() => setEditing('new')}>Register drone</Button>}
      </Stack>
      {!drones.length ? (
        <Alert severity="info">No drones yet. Register one, then give it a provider and a camera.</Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Drone</TableCell><TableCell>Site</TableCell><TableCell>Status</TableCell>
                <TableCell>Battery</TableCell><TableCell>Health</TableCell><TableCell>Last heartbeat</TableCell>
                <TableCell>Mission</TableCell><TableCell align="right" />
              </TableRow>
            </TableHead>
            <TableBody>
              {drones.map((d) => (
                <TableRow key={d.id} hover>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{d.name}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {d.code}{d.provider_name ? ` · ${d.provider_name}` : ' · no provider'}
                      {d.edge_gateway_name ? ` · via ${d.edge_gateway_name}` : ''}
                    </Typography>
                  </TableCell>
                  <TableCell>{d.site_name ?? '—'}</TableCell>
                  <TableCell><DroneStatusChip status={d.status} /></TableCell>
                  <TableCell><BatteryBar pct={d.battery_level} /></TableCell>
                  <TableCell>
                    <ComponentDot label="GPS" state={d.gps_status} />
                    <ComponentDot label="Camera" state={d.camera_status} />
                    <ComponentDot label="Link" state={d.communication_status} />
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" color={d.seconds_since_heartbeat != null
                      && d.seconds_since_heartbeat > d.heartbeat_timeout_seconds ? 'error' : undefined}>
                      {ago(d.seconds_since_heartbeat)}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    {d.active_session_id ? (
                      <Button size="small" startIcon={<FlightTakeoffIcon />}
                              onClick={() => navigate(`/drone-patrols/${d.active_session_id}`)}>Live</Button>
                    ) : <Typography variant="caption" color="text.secondary">idle</Typography>}
                  </TableCell>
                  <TableCell align="right">
                    {canUpdate && (
                      <Stack direction="row" sx={{ gap: 1, justifyContent: 'flex-end' }}>
                        <Button size="small" onClick={() => setEditing(d)}>Edit</Button>
                        <Button size="small" color={d.status === 'DISABLED' ? 'success' : 'warning'}
                                disabled={toggle.isPending || !!d.active_session_id}
                                onClick={() => toggle.mutate({ id: d.id, enabled: d.status === 'DISABLED' })}>
                          {d.status === 'DISABLED' ? 'Enable' : 'Disable'}
                        </Button>
                      </Stack>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {toggle.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(toggle.error)}</Alert>}
      {editing && <DroneDialog drone={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />}
    </Box>
  )
}

function DroneDialog({ drone, onClose }: { drone: Drone | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    name: drone?.name ?? '', code: drone?.code ?? '', site_id: drone?.site_id ?? '',
    provider_config_id: drone?.provider_config_id ?? '', edge_gateway_id: drone?.edge_gateway_id ?? '',
    camera_id: drone?.camera_id ?? '', manufacturer: drone?.manufacturer ?? '', model: drone?.model ?? '',
    serial_number: drone?.serial_number ?? '', heartbeat_timeout_seconds: drone?.heartbeat_timeout_seconds ?? 30,
  })
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data: providers } = useQuery({ queryKey: ['drone-providers'], queryFn: listProviders })
  const { data: gateways } = useQuery({ queryKey: ['drone-gateways'], queryFn: listGateways })
  const { data: cameras } = useQuery({
    queryKey: ['site-cameras', form.site_id], queryFn: () => getSiteCameras(form.site_id), enabled: !!form.site_id,
  })
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: form.name, code: form.code, site_id: form.site_id || null,
        provider_config_id: form.provider_config_id || null, edge_gateway_id: form.edge_gateway_id || null,
        camera_id: form.camera_id || null, manufacturer: form.manufacturer || null, model: form.model || null,
        serial_number: form.serial_number || null, heartbeat_timeout_seconds: Number(form.heartbeat_timeout_seconds),
      }
      return drone ? updateDrone(drone.id, body) : createDrone(body)
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['drones'] }); onClose() },
  })
  const siteGateways = (gateways ?? []).filter((g) => g.site_id === form.site_id)
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{drone ? `Edit ${drone.name}` : 'Register a drone'}</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 1 }}>
          <Stack direction="row" sx={{ gap: 2 }}>
            <TextField label="Name" value={form.name} onChange={set('name')} fullWidth required />
            <TextField label="Code" value={form.code} onChange={set('code')} sx={{ width: 160 }} required
                       helperText="Unique, e.g. D-01" />
          </Stack>
          <TextField select label="Site" value={form.site_id} onChange={set('site_id')} required>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select label="Provider" value={form.provider_config_id} onChange={set('provider_config_id')}
                     helperText="How the platform talks to this aircraft">
            <MenuItem value="">None yet</MenuItem>
            {(providers ?? []).map((p) => <MenuItem key={p.id} value={p.id}>{p.name} ({p.provider_key})</MenuItem>)}
          </TextField>
          <TextField select label="Camera" value={form.camera_id} onChange={set('camera_id')}
                     disabled={!form.site_id}
                     helperText="The camera that carries this drone's video. The AI runs on it; without one, no AI runs on its flights.">
            <MenuItem value="">None</MenuItem>
            {((cameras ?? []) as { id: string; name: string }[]).map((c) => (
              <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>
            ))}
          </TextField>
          <TextField select label="Site edge gateway" value={form.edge_gateway_id} onChange={set('edge_gateway_id')}
                     disabled={!form.site_id} helperText="Only if this drone is flown by a gateway at its site">
            <MenuItem value="">None — flown from the centre</MenuItem>
            {siteGateways.map((g) => <MenuItem key={g.id} value={g.id}>{g.name}</MenuItem>)}
          </TextField>
          <Stack direction="row" sx={{ gap: 2 }}>
            <TextField label="Manufacturer" value={form.manufacturer} onChange={set('manufacturer')} fullWidth />
            <TextField label="Model" value={form.model} onChange={set('model')} fullWidth />
          </Stack>
          <Stack direction="row" sx={{ gap: 2 }}>
            <TextField label="Serial number" value={form.serial_number} onChange={set('serial_number')} fullWidth />
            <TextField label="Heartbeat timeout (s)" type="number" value={form.heartbeat_timeout_seconds}
                       onChange={set('heartbeat_timeout_seconds')} sx={{ width: 200 }} />
          </Stack>
          {save.error && <Alert severity="error">{apiError(save.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.name || !form.code || !form.site_id || save.isPending}
                onClick={() => save.mutate()}>Save</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Providers ────────────────────────────────────────────────────────────────

function Providers() {
  const qc = useQueryClient()
  const canCreate = usePermission('drone:create')
  const [open, setOpen] = useState(false)
  const { data: providers } = useQuery({ queryKey: ['drone-providers'], queryFn: listProviders })
  const { data: catalogue } = useQuery({ queryKey: ['drone-catalogue'], queryFn: getProviderCatalogue })
  const [form, setForm] = useState<{ name: string; key: string; settings: Record<string, string> }>(
    { name: '', key: '', settings: {} })
  const entry = useMemo(() => catalogue?.find((c) => c.key === form.key), [catalogue, form.key])
  const save = useMutation({
    mutationFn: () => createProvider({ name: form.name, provider_key: form.key, settings: form.settings }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['drone-providers'] }); setOpen(false) },
  })
  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', mb: 1 }}>
        <Typography variant="body2" color="text.secondary">
          A provider is the software that talks to a kind of aircraft. Only the simulator is installed until
          real hardware is chosen.
        </Typography>
        {canCreate && <Button startIcon={<AddIcon />} variant="contained" onClick={() => setOpen(true)}>Add provider</Button>}
      </Stack>
      <Table size="small">
        <TableHead><TableRow><TableCell>Name</TableCell><TableCell>Provider</TableCell>
          <TableCell>Settings</TableCell><TableCell>Drones</TableCell><TableCell>Status</TableCell></TableRow></TableHead>
        <TableBody>
          {(providers ?? []).map((p) => (
            <TableRow key={p.id}>
              <TableCell>{p.name}</TableCell>
              <TableCell>{p.provider_key}{catalogue?.find((c) => c.key === p.provider_key)?.simulated && (
                <Chip size="small" label="Simulated" sx={{ ml: 1 }} />)}</TableCell>
              <TableCell>
                <Typography variant="caption">
                  {Object.entries(p.config ?? {}).map(([k, v]) => `${k}: ${String(v)}`).join(' · ') || '—'}
                  {p.has_secret ? ' · credentials stored' : ''}
                </Typography>
              </TableCell>
              <TableCell>{p.drone_count}</TableCell>
              <TableCell>{p.is_active ? 'Active' : 'Disabled'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add provider</DialogTitle>
        <DialogContent>
          <Stack sx={{ gap: 2, mt: 1 }}>
            <TextField label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <TextField select label="Provider" value={form.key}
                       onChange={(e) => setForm({ ...form, key: e.target.value, settings: {} })}>
              {(catalogue ?? []).map((c) => <MenuItem key={c.key} value={c.key}>{c.name}{c.simulated ? ' (simulated)' : ''}</MenuItem>)}
            </TextField>
            {entry?.fields.map((f) => (
              <TextField key={f.key} label={f.label} type={f.secret ? 'password' : f.kind === 'string' ? 'text' : 'number'}
                         helperText={f.help ?? undefined} required={f.required}
                         value={form.settings[f.key] ?? ''}
                         onChange={(e) => setForm({ ...form, settings: { ...form.settings, [f.key]: e.target.value } })} />
            ))}
            {save.error && <Alert severity="error">{apiError(save.error)}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={!form.name || !form.key || save.isPending} onClick={() => save.mutate()}>Save</Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

// ── Edge gateways ────────────────────────────────────────────────────────────

const GW_COLOR: Record<Gateway['status'], 'success' | 'warning' | 'error' | 'default'> = {
  ONLINE: 'success', DEGRADED: 'warning', OFFLINE: 'error', UNKNOWN: 'default',
}

function Gateways() {
  const qc = useQueryClient()
  const canCreate = usePermission('drone:create')
  const canUpdate = usePermission('drone:update')
  const [open, setOpen] = useState(false)
  const [issued, setIssued] = useState<Gateway | null>(null)
  const [form, setForm] = useState({ site_id: '', name: '', code: '' })
  const { data: gateways } = useQuery({ queryKey: ['drone-gateways'], queryFn: listGateways, refetchInterval: 15_000 })
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const create = useMutation({
    mutationFn: () => createGateway(form),
    onSuccess: (g) => { qc.invalidateQueries({ queryKey: ['drone-gateways'] }); setOpen(false); setIssued(g) },
  })
  const rotate = useMutation({
    mutationFn: (id: string) => rotateGatewayCredential(id),
    onSuccess: (g) => { qc.invalidateQueries({ queryKey: ['drone-gateways'] }); setIssued(g) },
  })
  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', mb: 1 }}>
        <Typography variant="body2" color="text.secondary">
          A site edge gateway flies its site's drones and keeps flying through a lost link.
        </Typography>
        {canCreate && <Button startIcon={<AddIcon />} variant="contained" onClick={() => setOpen(true)}>Register gateway</Button>}
      </Stack>
      <Table size="small">
        <TableHead><TableRow><TableCell>Gateway</TableCell><TableCell>Site</TableCell><TableCell>Status</TableCell>
          <TableCell>Last seen</TableCell><TableCell>Backlog</TableCell><TableCell>Storage free</TableCell>
          <TableCell>Drones</TableCell><TableCell /></TableRow></TableHead>
        <TableBody>
          {(gateways ?? []).map((g) => (
            <TableRow key={g.id}>
              <TableCell>{g.name}<Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                {g.code}{g.software_version ? ` · ${g.software_version}` : ''}</Typography></TableCell>
              <TableCell>{g.site_name}</TableCell>
              <TableCell>
                <Tooltip title={(g.health?.problems ?? []).join('; ')}>
                  <Chip size="small" color={GW_COLOR[g.status]} label={pretty(g.status)} />
                </Tooltip>
              </TableCell>
              <TableCell>{g.last_seen_at ? new Date(g.last_seen_at).toLocaleString() : 'never'}</TableCell>
              <TableCell>{g.buffer_depth ?? '—'}</TableCell>
              <TableCell>{g.storage_free_pct != null ? `${Math.round(g.storage_free_pct)}%` : '—'}</TableCell>
              <TableCell>{g.drone_count}</TableCell>
              <TableCell align="right">
                {canUpdate && <Button size="small" onClick={() => rotate.mutate(g.id)}>New credential</Button>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Register a site edge gateway</DialogTitle>
        <DialogContent>
          <Stack sx={{ gap: 2, mt: 1 }}>
            <TextField select label="Site" value={form.site_id} onChange={(e) => setForm({ ...form, site_id: e.target.value })}>
              {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
            <TextField label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <TextField label="Code" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
            {create.error && <Alert severity="error">{apiError(create.error)}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={!form.site_id || !form.name || !form.code || create.isPending}
                  onClick={() => create.mutate()}>Register</Button>
        </DialogActions>
      </Dialog>
      {issued?.credential && (
        <Dialog open onClose={() => setIssued(null)} maxWidth="sm" fullWidth>
          <DialogTitle>Credential for {issued.name}</DialogTitle>
          <DialogContent>
            <Alert severity="warning" sx={{ mb: 2 }}>{issued.credential_note}</Alert>
            <Stack direction="row" sx={{ alignItems: 'center', gap: 1 }}>
              <TextField value={issued.credential} fullWidth slotProps={{ input: { readOnly: true } }} />
              <IconButton onClick={() => navigator.clipboard?.writeText(issued.credential ?? '')}><ContentCopyIcon /></IconButton>
            </Stack>
          </DialogContent>
          <DialogActions><Button variant="contained" onClick={() => setIssued(null)}>I have copied it</Button></DialogActions>
        </Dialog>
      )}
    </Box>
  )
}

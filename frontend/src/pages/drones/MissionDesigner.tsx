/**
 * Drone Patrol — mission designer: map, route, waypoints, security zones, AI
 * profile, schedule and recording policy, and pre-flight before it flies.
 *
 * The route is drawn on the map: click to add a waypoint, drag to move one,
 * drag the H marker to move the launch point. The whole path is saved in one
 * request (PUT /drone-routes/{id}/waypoints), as the backend wants it — never
 * waypoint by waypoint.
 */
import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Box, Button, Checkbox, Chip, Divider, FormControlLabel, Grid, IconButton, MenuItem, Skeleton,
  Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, ToggleButton, ToggleButtonGroup,
  Tooltip, Typography,
} from '@mui/material'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import DeleteIcon from '@mui/icons-material/Delete'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { useTenantTimeZone } from '@/lib/tenantTime'
import { getSites } from '@/api/sites'
import {
  apiError, createMission, createRoute, createSchedule, deleteSchedule, getMission, getPreflight, getRoute,
  listDrones, listProfiles, listZones, replaceWaypoints, runMission, updateMission, updateRoute,
} from '@/api/drones'
import type {
  LatLng, Mission, MissionInput, Route, ScheduleInput, ScheduleType, SyncMode, Waypoint,
} from '@/api/drones'
import { DroneMap, FitTo, LicenceBanner } from '@/components/drones/droneUi'
import { pretty } from '@/components/drones/droneFormat'
import { RouteEditor, ZoneLayer } from '@/components/drones/MapEditors'
import { formatDistance, routeLengthM } from '@/components/drones/geo'
import { DroneNav } from './DroneNav'

const SYNC_MODES: { v: SyncMode | ''; label: string }[] = [
  { v: '', label: "Site's recording policy" },
  { v: 'incident_only', label: 'Events to the centre, full video at the site' },
  { v: 'central', label: 'Everything to the centre' },
  { v: 'scheduled', label: 'Events, in the upload window' },
  { v: 'local_only', label: 'Keep everything at the site' },
  { v: 'manual', label: 'Upload on request only' },
]
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export default function MissionDesigner() {
  const { id } = useParams()
  const isNew = !id || id === 'new'
  const { data: mission, isLoading, error } = useQuery({ queryKey: ['drone-mission', id],
                                                          queryFn: () => getMission(id!), enabled: !isNew })
  const { data: route, isLoading: routeLoading } = useQuery({
    queryKey: ['drone-route', mission?.route_id], queryFn: () => getRoute(mission!.route_id!),
    enabled: !!mission?.route_id })
  if (!isNew && (isLoading || routeLoading)) return <Box sx={{ p: 3 }}><Skeleton height={400} /></Box>
  if (!isNew && !mission) {
    return <Box sx={{ p: 3 }}><Alert severity="error">{error ? apiError(error) : 'Mission not found.'}</Alert></Box>
  }
  // Keyed, so opening another mission (or the one just created) starts the
  // form afresh from what the server holds.
  return <Designer key={mission?.id ?? 'new'} mission={mission ?? null} route={route ?? null} />
}

function Designer({ mission, route }: { mission: Mission | null; route: Route | null }) {
  const isNew = !mission
  const navigate = useNavigate()
  const qc = useQueryClient()
  const canEdit = usePermission(isNew ? 'drone:mission:create' : 'drone:mission:update')
  const canRun = usePermission('drone:mission:execute')
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })

  const [siteId, setSiteId] = useState(mission?.site_id ?? '')
  const [form, setForm] = useState<MissionInput>(mission
    ? { name: mission.name, description: mission.description, drone_id: mission.drone_id,
        security_profile_id: mission.security_profile_id, recording_sync_mode: mission.recording_sync_mode,
        priority: mission.priority, min_battery_pct: mission.min_battery_pct,
        max_duration_minutes: mission.max_duration_minutes, enabled: mission.enabled }
    : { name: '', priority: 3, min_battery_pct: 30, enabled: true })
  const [base, setBase] = useState<LatLng | null>(route?.base_latitude != null && route?.base_longitude != null
    ? { lat: route.base_latitude, lng: route.base_longitude } : null)
  const [wps, setWps] = useState<Waypoint[]>(route?.waypoints ?? [])
  const [routeSettings, setRouteSettings] = useState(route
    ? { default_altitude_m: route.default_altitude_m, default_speed_mps: route.default_speed_mps,
        return_to_base: route.return_to_base }
    : { default_altitude_m: 40, default_speed_mps: 5, return_to_base: true })
  const [selected, setSelected] = useState<number | null>(null)
  const [placingBase, setPlacingBase] = useState(false)
  const [dirtyRoute, setDirtyRoute] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const site = sites?.find((s) => s.id === siteId)
  const chooseSite = (sid: string) => {  // a new route starts at the site
    setSiteId(sid)
    const s = sites?.find((x) => x.id === sid)
    setBase(s?.latitude != null && s?.longitude != null ? { lat: s.latitude, lng: s.longitude } : null)
    setDirtyRoute(true)
  }

  const { data: drones } = useQuery({ queryKey: ['drones', siteId], queryFn: () => listDrones({ site_id: siteId }),
                                      enabled: !!siteId })
  const { data: zones } = useQuery({ queryKey: ['drone-zones', siteId], queryFn: () => listZones(siteId), enabled: !!siteId })
  const { data: profiles } = useQuery({ queryKey: ['drone-profiles'], queryFn: listProfiles })

  const editWps = (next: Waypoint[]) => { setWps(next); setDirtyRoute(true) }
  const length = routeLengthM(base, wps, routeSettings.return_to_base)
  const points = useMemo(() => {
    const p: [number, number][] = wps.map((w) => [w.latitude, w.longitude])
    if (base) p.push([base.lat, base.lng])
    return p
  }, [wps, base])

  const save = useMutation({
    mutationFn: async () => {
      if (!siteId) throw new Error('Choose a site.')
      let routeId = mission?.route_id ?? null
      const payloadWps = wps.map((w) => ({ ...w, altitude_m: w.altitude_m || null }))
      if (!routeId) {
        if (wps.length) {
          const r = await createRoute({ site_id: siteId, name: `${form.name} route`, base_latitude: base?.lat,
                                        base_longitude: base?.lng, ...routeSettings, waypoints: payloadWps })
          routeId = r.id
        }
      } else if (dirtyRoute) {
        await updateRoute(routeId, { base_latitude: base?.lat ?? null, base_longitude: base?.lng ?? null, ...routeSettings })
        await replaceWaypoints(routeId, payloadWps)
      }
      const body: MissionInput = { ...form, route_id: routeId, recording_sync_mode: form.recording_sync_mode || null,
                                   drone_id: form.drone_id || null, security_profile_id: form.security_profile_id || null }
      return mission ? updateMission(mission.id, body) : createMission({ ...body, site_id: siteId })
    },
    onSuccess: (m) => {
      qc.invalidateQueries({ queryKey: ['drone-missions'] })
      qc.invalidateQueries({ queryKey: ['drone-mission', m.id] })
      qc.invalidateQueries({ queryKey: ['drone-route'] })
      qc.invalidateQueries({ queryKey: ['drone-preflight', m.id] })
      setDirtyRoute(false)
      setMessage({ ok: true, text: 'Saved.' })
      if (isNew) navigate(`/drone-missions/${m.id}`, { replace: true })
    },
    onError: (e) => setMessage({ ok: false, text: apiError(e) }),
  })

  const sel = selected != null ? wps[selected] : null
  const setSel = (patch: Partial<Waypoint>) =>
    editWps(wps.map((w, i) => (i === selected ? { ...w, ...patch } : w)))
  const moveWp = (i: number, d: number) => {
    const j = i + d
    if (j < 0 || j >= wps.length) return
    const next = [...wps];
    [next[i], next[j]] = [next[j], next[i]]
    editWps(next)
    setSelected(j)
  }

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title={isNew ? 'New drone mission' : mission?.name ?? 'Mission'}
                  subtitle={isNew ? 'Choose a site, draw the route, choose how it judges what it sees'
                    : `${mission?.site_name ?? ''}${mission?.in_flight ? ' · in flight now' : ''}`}
                  action={canEdit ? (
                    <Button variant="contained" onClick={() => save.mutate()}
                            disabled={save.isPending || !form.name || !siteId}>
                      {save.isPending ? 'Saving…' : 'Save'}</Button>) : undefined} />
      <DroneNav />
      <LicenceBanner />
      {message && <Alert severity={message.ok ? 'success' : 'error'} sx={{ mb: 2 }} onClose={() => setMessage(null)}>
        {message.text}</Alert>}
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, lg: 8 }}>
          <GlassCard sx={{ p: 2 }}>
            <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 1, flexWrap: 'wrap', gap: 1 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Route</Typography>
              <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                <Chip size="small" label={`${wps.length} waypoint(s)`} />
                <Chip size="small" label={formatDistance(length)} />
                {route?.summary?.outside_site_geofence?.length ? (
                  <Tooltip title="Waypoints outside the site's geofence — a warning, not a refusal">
                    <Chip size="small" color="warning" label={`${route.summary.outside_site_geofence.length} outside site`} />
                  </Tooltip>) : null}
                {canEdit && (
                  <ToggleButtonGroup size="small" exclusive value={placingBase ? 'base' : 'wp'}
                                     onChange={(_, v) => v && setPlacingBase(v === 'base')}>
                    <ToggleButton value="wp">Add waypoints</ToggleButton>
                    <ToggleButton value="base">Place launch point</ToggleButton>
                  </ToggleButtonGroup>
                )}
              </Stack>
            </Stack>
            {!siteId ? (
              <Alert severity="info">Choose a site to draw the route on its map.</Alert>
            ) : (
              <DroneMap height={520} center={site?.latitude != null && site?.longitude != null
                ? [site.latitude, site.longitude] : undefined}>
                <ZoneLayer zones={zones ?? []} />
                <RouteEditor base={base} waypoints={wps} returnToBase={routeSettings.return_to_base} editable={canEdit}
                             selected={selected} onSelect={setSelected} onChange={editWps}
                             onBaseChange={(p) => { setBase(p); setDirtyRoute(true); setPlacingBase(false) }}
                             placingBase={placingBase} />
                <FitTo points={points} />
              </DroneMap>
            )}
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              Click the map to add a waypoint; drag a numbered marker to move it; the H marker is where it takes
              off and lands. Coloured outlines are the site's security zones.
            </Typography>
            {!!wps.length && (
              <Table size="small" sx={{ mt: 2 }}>
                <TableHead><TableRow><TableCell>#</TableCell><TableCell>Name</TableCell><TableCell>Hover (s)</TableCell>
                  <TableCell>Observe (s)</TableCell><TableCell>Snapshot</TableCell><TableCell>Zone</TableCell><TableCell /></TableRow></TableHead>
                <TableBody>
                  {wps.map((w, i) => (
                    <TableRow key={i} selected={i === selected} hover onClick={() => setSelected(i)}>
                      <TableCell>{i + 1}</TableCell>
                      <TableCell>{w.name || '—'}</TableCell>
                      <TableCell>{w.hover_seconds}</TableCell>
                      <TableCell>{w.observe_seconds}</TableCell>
                      <TableCell>{w.snapshot_required ? 'yes' : '—'}</TableCell>
                      <TableCell>{w.security_zone_name ?? '—'}</TableCell>
                      <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                        {canEdit && (<>
                          <IconButton size="small" onClick={() => moveWp(i, -1)}><ArrowUpwardIcon fontSize="small" /></IconButton>
                          <IconButton size="small" onClick={() => moveWp(i, 1)}><ArrowDownwardIcon fontSize="small" /></IconButton>
                          <IconButton size="small" onClick={() => { editWps(wps.filter((_, j) => j !== i)); setSelected(null) }}>
                            <DeleteIcon fontSize="small" /></IconButton>
                        </>)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {sel && canEdit && (
              <Stack direction="row" sx={{ gap: 2, mt: 2, flexWrap: 'wrap', alignItems: 'center' }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>Waypoint {selected! + 1}</Typography>
                <TextField size="small" label="Name" value={sel.name ?? ''} onChange={(e) => setSel({ name: e.target.value || null })} />
                <TextField size="small" type="number" label="Altitude (m)" value={sel.altitude_m ?? ''} sx={{ width: 120 }}
                           placeholder={String(routeSettings.default_altitude_m)}
                           onChange={(e) => setSel({ altitude_m: e.target.value ? Number(e.target.value) : null })} />
                <TextField size="small" type="number" label="Hover (s)" value={sel.hover_seconds} sx={{ width: 110 }}
                           onChange={(e) => setSel({ hover_seconds: Number(e.target.value) || 0 })} />
                <TextField size="small" type="number" label="Observe (s)" value={sel.observe_seconds} sx={{ width: 110 }}
                           onChange={(e) => setSel({ observe_seconds: Number(e.target.value) || 0 })} />
                <FormControlLabel control={<Checkbox checked={sel.snapshot_required}
                                                     onChange={(_, v) => setSel({ snapshot_required: v })} />} label="Snapshot" />
              </Stack>
            )}
          </GlassCard>
        </Grid>

        <Grid size={{ xs: 12, lg: 4 }}>
          <GlassCard sx={{ p: 2, mb: 2 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Mission</Typography>
            <Stack sx={{ gap: 2 }}>
              <TextField select label="Site" value={siteId} disabled={!isNew} onChange={(e) => chooseSite(e.target.value)}>
                {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
              </TextField>
              <TextField label="Name" value={form.name ?? ''} disabled={!canEdit}
                         onChange={(e) => setForm({ ...form, name: e.target.value })} />
              <TextField select label="Drone" value={form.drone_id ?? ''} disabled={!canEdit || !siteId}
                         onChange={(e) => setForm({ ...form, drone_id: e.target.value || null })}>
                <MenuItem value="">None</MenuItem>
                {(drones?.items ?? []).map((d) => <MenuItem key={d.id} value={d.id}>{d.name} ({d.code}) · {pretty(d.status)}</MenuItem>)}
              </TextField>
              <TextField select label="AI security profile" value={form.security_profile_id ?? ''} disabled={!canEdit}
                         helperText="Which detections matter and how seriously. Without one, every module at defaults."
                         onChange={(e) => setForm({ ...form, security_profile_id: e.target.value || null })}>
                <MenuItem value="">None — defaults</MenuItem>
                {(profiles ?? []).filter((p) => p.is_active).map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
              </TextField>
              <TextField select label="Recording policy" value={form.recording_sync_mode ?? ''} disabled={!canEdit}
                         onChange={(e) => setForm({ ...form, recording_sync_mode: (e.target.value || null) as SyncMode | null })}>
                {SYNC_MODES.map((m) => <MenuItem key={m.v} value={m.v}>{m.label}</MenuItem>)}
              </TextField>
              <Stack direction="row" sx={{ gap: 2 }}>
                <TextField type="number" label="Min. battery %" value={form.min_battery_pct ?? 30} disabled={!canEdit}
                           onChange={(e) => setForm({ ...form, min_battery_pct: Number(e.target.value) })} />
                <TextField type="number" label="Max minutes" value={form.max_duration_minutes ?? ''} disabled={!canEdit}
                           onChange={(e) => setForm({ ...form, max_duration_minutes: e.target.value ? Number(e.target.value) : null })} />
              </Stack>
              <Stack direction="row" sx={{ gap: 2 }}>
                <TextField type="number" label="Altitude (m)" value={routeSettings.default_altitude_m} disabled={!canEdit}
                           onChange={(e) => { setRouteSettings({ ...routeSettings, default_altitude_m: Number(e.target.value) }); setDirtyRoute(true) }} />
                <TextField type="number" label="Speed (m/s)" value={routeSettings.default_speed_mps} disabled={!canEdit}
                           onChange={(e) => { setRouteSettings({ ...routeSettings, default_speed_mps: Number(e.target.value) }); setDirtyRoute(true) }} />
              </Stack>
              <FormControlLabel control={<Switch checked={routeSettings.return_to_base} disabled={!canEdit}
                onChange={(_, v) => { setRouteSettings({ ...routeSettings, return_to_base: v }); setDirtyRoute(true) }} />}
                                label="Return to the launch point" />
              <FormControlLabel control={<Switch checked={!!form.enabled} disabled={!canEdit}
                onChange={(_, v) => setForm({ ...form, enabled: v })} />} label="Enabled" />
            </Stack>
          </GlassCard>
          {!isNew && mission && <Preflight missionId={mission.id} canRun={canRun && !!mission.enabled && !mission.in_flight} />}
          {!isNew && mission && <Schedules missionId={mission.id} canEdit={canEdit} />}
        </Grid>
      </Grid>
    </Box>
  )
}

// ── Pre-flight ───────────────────────────────────────────────────────────────

function Preflight({ missionId, canRun }: { missionId: string; canRun: boolean }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data, isFetching, refetch } = useQuery({ queryKey: ['drone-preflight', missionId],
                                                   queryFn: () => getPreflight(missionId), refetchInterval: 30_000 })
  const run = useMutation({
    mutationFn: () => runMission(missionId),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['drone-mission', missionId] })
      if (r.session.status !== 'BLOCKED') navigate(`/drone-patrols/${r.session.id}`)
      else refetch()
    },
  })
  const failing = data?.checks.filter((c) => !c.passed) ?? []
  return (
    <GlassCard sx={{ p: 2, mb: 2 }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Pre-flight</Typography>
        <Stack direction="row" sx={{ gap: 1 }}>
          <Button size="small" onClick={() => refetch()} disabled={isFetching}>Check again</Button>
          {canRun && <Button size="small" variant="contained" startIcon={<PlayArrowIcon />}
                             disabled={!data?.passed || run.isPending} onClick={() => run.mutate()}>Run now</Button>}
        </Stack>
      </Stack>
      {!data ? <Skeleton height={80} /> : (
        <>
          <Alert severity={data.passed ? (data.warnings.length ? 'warning' : 'success') : 'error'} sx={{ mb: 1 }}>
            {data.passed ? (data.warnings.length ? 'Ready to fly, with warnings.' : 'Ready to fly.')
              : `${data.blocking.length} check(s) stop it launching.`}
          </Alert>
          {data.estimate && (
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              About {Math.round(data.estimate.duration_s / 60)} min, {formatDistance(data.estimate.distance_m)},
              needs {Math.round(data.estimate.battery_needed_pct)}% battery plus reserve.
            </Typography>
          )}
          {failing.map((c) => (
            <Stack key={c.code} direction="row" sx={{ gap: 1, alignItems: 'flex-start', mb: 0.5 }}>
              {c.severity === 'BLOCK' ? <ErrorIcon color="error" fontSize="small" /> : <WarningAmberIcon color="warning" fontSize="small" />}
              <Box><Typography variant="body2" sx={{ fontWeight: 600 }}>{c.label}</Typography>
                <Typography variant="caption" color="text.secondary">{c.detail}</Typography></Box>
            </Stack>
          ))}
          {!failing.length && (
            <Stack direction="row" sx={{ gap: 1, alignItems: 'center' }}>
              <CheckCircleIcon color="success" fontSize="small" />
              <Typography variant="body2">All {data.checks.length} checks pass.</Typography>
            </Stack>
          )}
          {run.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(run.error)}</Alert>}
        </>
      )}
    </GlassCard>
  )
}

// ── Schedules ────────────────────────────────────────────────────────────────

function Schedules({ missionId, canEdit }: { missionId: string; canEdit: boolean }) {
  const qc = useQueryClient()
  const tz = useTenantTimeZone() ?? 'Asia/Singapore'
  const today = new Date().toISOString().slice(0, 10)
  const { data: mission } = useQuery({ queryKey: ['drone-mission', missionId], queryFn: () => getMission(missionId) })
  const [form, setForm] = useState<ScheduleInput>({ schedule_type: 'DAILY', timezone: tz, start_date: today,
                                                    launch_time: '22:00', weekdays: [], specific_dates: [], grace_minutes: 15 })
  const [dates, setDates] = useState('')
  const add = useMutation({
    mutationFn: () => createSchedule(missionId, {
      ...form, specific_dates: dates.split(/[\s,]+/).filter(Boolean) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['drone-mission', missionId] }),
  })
  const remove = useMutation({
    mutationFn: (sid: string) => deleteSchedule(sid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['drone-mission', missionId] }),
  })
  return (
    <GlassCard sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Schedule</Typography>
      {!(mission?.schedules ?? []).length && <Typography variant="body2" color="text.secondary">Runs only when started by hand.</Typography>}
      {(mission?.schedules ?? []).map((s) => (
        <Stack key={s.id} direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', py: 0.5 }}>
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {pretty(s.schedule_type)} at {s.launch_time.slice(0, 5)} {s.enabled ? '' : '(off)'}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {s.schedule_type === 'SELECTED_DAYS' ? s.weekdays.map((d) => WEEKDAYS[d]).join(', ') + ' · ' : ''}
              next: {s.next_runs?.[0] ? new Date(s.next_runs[0].utc).toLocaleString() : 'none'} · {s.timezone}
            </Typography>
          </Box>
          {canEdit && <IconButton size="small" onClick={() => remove.mutate(s.id)}><DeleteIcon fontSize="small" /></IconButton>}
        </Stack>
      ))}
      {canEdit && (
        <>
          <Divider sx={{ my: 1.5 }} />
          <Stack sx={{ gap: 1.5 }}>
            <Stack direction="row" sx={{ gap: 1 }}>
              <TextField select size="small" label="Repeats" value={form.schedule_type} sx={{ flex: 1 }}
                         onChange={(e) => setForm({ ...form, schedule_type: e.target.value as ScheduleType })}>
                {(['ONCE', 'DAILY', 'WEEKLY', 'SELECTED_DAYS', 'SPECIFIC_DATE'] as ScheduleType[]).map((t) =>
                  <MenuItem key={t} value={t}>{pretty(t)}</MenuItem>)}
              </TextField>
              <TextField size="small" type="time" label="Launch" value={form.launch_time} sx={{ width: 130 }}
                         onChange={(e) => setForm({ ...form, launch_time: e.target.value })} slotProps={{ inputLabel: { shrink: true } }} />
            </Stack>
            <Stack direction="row" sx={{ gap: 1 }}>
              <TextField size="small" type="date" label="From" value={form.start_date} sx={{ flex: 1 }}
                         onChange={(e) => setForm({ ...form, start_date: e.target.value })} slotProps={{ inputLabel: { shrink: true } }} />
              <TextField size="small" type="number" label="Grace (min)" value={form.grace_minutes} sx={{ width: 120 }}
                         onChange={(e) => setForm({ ...form, grace_minutes: Number(e.target.value) })} />
            </Stack>
            {form.schedule_type === 'SELECTED_DAYS' && (
              <ToggleButtonGroup size="small" value={form.weekdays}
                                 onChange={(_, v: number[]) => setForm({ ...form, weekdays: v })}>
                {WEEKDAYS.map((d, i) => <ToggleButton key={d} value={i}>{d}</ToggleButton>)}
              </ToggleButtonGroup>
            )}
            {form.schedule_type === 'SPECIFIC_DATE' && (
              <TextField size="small" label="Dates" placeholder="2026-10-01, 2026-10-15" value={dates}
                         onChange={(e) => setDates(e.target.value)} />
            )}
            <Typography variant="caption" color="text.secondary">Times are in {form.timezone}.</Typography>
            <Button variant="outlined" onClick={() => add.mutate()} disabled={add.isPending}>Add schedule</Button>
            {add.error && <Alert severity="error">{apiError(add.error)}</Alert>}
          </Stack>
        </>
      )}
    </GlassCard>
  )
}

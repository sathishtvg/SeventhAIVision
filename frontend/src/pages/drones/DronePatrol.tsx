/**
 * Drone Patrol — one flight. While it flies: live video (the drone's camera,
 * through the same HLS path as the Live Wall), position, telemetry, waypoint
 * progress, events as they happen, and the operator controls. After it lands:
 * the same screen as a replay, with a timeline to scrub the flown track.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Grid, IconButton, List,
  ListItemButton, ListItemText, Skeleton, Slider, Table, TableBody, TableCell, TableHead, TableRow, TextField,
  ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material'
import PauseIcon from '@mui/icons-material/Pause'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import HomeIcon from '@mui/icons-material/Home'
import StopIcon from '@mui/icons-material/Stop'
import CancelIcon from '@mui/icons-material/Cancel'
import VideocamOffIcon from '@mui/icons-material/VideocamOff'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleMarker, Marker, Polyline, Tooltip as MapTooltip } from 'react-leaflet'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { HlsPlayer } from '@/components/common/HlsPlayer'
import { usePermission } from '@/hooks/usePermission'
import { useAuthStore } from '@/store/auth'
import { useWsStore } from '@/store/websocket'
import { getStreams } from '@/api/cameras'
import {
  AIRBORNE, IN_FLIGHT, apiError, getDrone, getSession, getTrack, listCommands, listEvents, mediaUrl, sendCommand,
} from '@/api/drones'
import type { CommandKind, LatLng, Session, TrackPoint } from '@/api/drones'
import { BatteryBar, DroneMap, FitTo, LicenceBanner, RiskChip, SessionStatusChip } from '@/components/drones/droneUi'
import { RISK_COLOR, droneIcon, fmt, pretty, useDroneRealtime } from '@/components/drones/droneFormat'
import { RouteEditor, ZoneLayer } from '@/components/drones/MapEditors'
import { formatDistance } from '@/components/drones/geo'
import { DroneNav } from './DroneNav'

const WP_COLOR: Record<string, 'default' | 'success' | 'primary' | 'warning' | 'error'> = {
  PENDING: 'default', REACHED: 'primary', OBSERVED: 'success', SKIPPED: 'warning', FAILED: 'error',
}

export default function DronePatrol() {
  const { id } = useParams()
  const qc = useQueryClient()
  const { data: session, isLoading, error } = useQuery({
    queryKey: ['drone-patrol', id], queryFn: () => getSession(id!),
    refetchInterval: (q) => (q.state.data && IN_FLIGHT.includes(q.state.data.status) ? 5_000 : false),
  })
  const live = !!session && IN_FLIGHT.includes(session.status)
  const { data: track } = useQuery({ queryKey: ['drone-track', id], queryFn: () => getTrack(id!),
                                     refetchInterval: live ? 10_000 : false, enabled: !!session })
  const { data: events } = useQuery({ queryKey: ['drone-events', 'session', id],
                                      queryFn: () => listEvents({ session_id: id, limit: 200 }), enabled: !!session })
  useDroneRealtime([['drone-patrol', id!], ['drone-events', 'session', id!], ['drone-commands', id!]],
                   (type, p) => type !== 'drone_telemetry' && (p.session_id === id || type.startsWith('drone_event')))

  // Live position straight from the telemetry announcements, between track reloads.
  const [latest, setLatest] = useState<TrackPoint | null>(null)
  useEffect(() => {
    if (!live) return
    return useWsStore.subscribe((st) => {
      if (!st.lastMessage) return
      try {
        const m = JSON.parse(st.lastMessage) as { event_type?: string; payload?: TrackPoint & { session_id?: string } }
        if (m.event_type === 'drone_telemetry' && m.payload && m.payload.session_id === id) setLatest(m.payload)
      } catch { /* not ours */ }
    })
  }, [live, id])
  useEffect(() => { if (!live) qc.invalidateQueries({ queryKey: ['drone-track', id] }) }, [live, id, qc])

  if (isLoading) return <Box sx={{ p: 3 }}><Skeleton height={500} /></Box>
  if (error || !session) {
    return <Box sx={{ p: 3 }}><Alert severity="error">{error ? apiError(error) : 'Patrol session not found.'}</Alert></Box>
  }
  const points = track?.points ?? []
  const now = latest && (!points.length || latest.recorded_at > points[points.length - 1].recorded_at)
    ? latest : points[points.length - 1] ?? null
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title={`${session.mission_name ?? 'Drone patrol'} · ${session.session_number}`}
                  subtitle={`${session.site_name ?? ''} · ${session.drone_name ?? 'no drone'} · ${pretty(session.triggered_by)}`}
                  action={<SessionStatusChip status={session.status} />} />
      <DroneNav />
      <LicenceBanner />
      {session.blocked_reason && <Alert severity="error" sx={{ mb: 2 }}>Blocked: {session.blocked_reason}</Alert>}
      {session.failure_reason && <Alert severity="error" sx={{ mb: 2 }}>{session.failure_reason}</Alert>}
      {session.abort_reason && <Alert severity="warning" sx={{ mb: 2 }}>Aborted: {session.abort_reason}</Alert>}
      {live
        ? <LiveView session={session} points={points} now={now} events={events?.items ?? []} />
        : <Replay session={session} points={points} events={events?.items ?? []} />}
    </Box>
  )
}

// ── Shared map layers ────────────────────────────────────────────────────────

type Ev = { id: string; drone_latitude: number | null; drone_longitude: number | null; risk_level: string
            module_type: string; label: string | null; detected_at: string }

function PlanLayers({ session }: { session: Session }) {
  const snap = session.config_snapshot ?? {}
  const base: LatLng | null = snap.route?.base_latitude != null && snap.route?.base_longitude != null
    ? { lat: snap.route.base_latitude, lng: snap.route.base_longitude } : null
  const wps = (session.waypoints ?? []).map((w) => ({ ...w }))
  return (
    <>
      <ZoneLayer zones={snap.zones ?? []} />
      <RouteEditor base={base} waypoints={wps} returnToBase={snap.route?.return_to_base ?? true} editable={false}
                   selected={null} onSelect={() => {}} onChange={() => {}} onBaseChange={() => {}} />
    </>
  )
}

function EventMarkers({ events, onOpen, until }: { events: Ev[]; onOpen: (id: string) => void; until?: string }) {
  return (
    <>
      {events.filter((e) => e.drone_latitude != null && e.drone_longitude != null && (!until || e.detected_at <= until))
        .map((e) => (
          <CircleMarker key={e.id} center={[e.drone_latitude!, e.drone_longitude!]} radius={8}
                        pathOptions={{ color: RISK_COLOR[e.risk_level as keyof typeof RISK_COLOR] ?? '#fff', weight: 3,
                                       fillOpacity: 0.5 }}
                        eventHandlers={{ click: () => onOpen(e.id) }}>
            <MapTooltip>{pretty(e.module_type)}{e.label ? ` · ${e.label}` : ''} · {e.risk_level}</MapTooltip>
          </CircleMarker>
        ))}
    </>
  )
}

function Telemetry({ p }: { p: TrackPoint | null }) {
  if (!p) return <Typography variant="body2" color="text.secondary">No telemetry yet.</Typography>
  const cell = (label: string, value: ReactNode) => (
    <Grid size={{ xs: 6 }}><Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="body2" sx={{ fontWeight: 600 }}>{value}</Typography></Grid>
  )
  return (
    <Grid container spacing={1}>
      <Grid size={{ xs: 12 }}><Typography variant="caption" color="text.secondary">Battery</Typography>
        <BatteryBar pct={p.battery_pct} /></Grid>
      {cell('Altitude', p.altitude_m != null ? `${Math.round(p.altitude_m)} m` : '—')}
      {cell('Speed', p.speed_mps != null ? `${p.speed_mps.toFixed(1)} m/s` : '—')}
      {cell('Heading', p.heading_deg != null ? `${Math.round(p.heading_deg)}°` : '—')}
      {cell('State', pretty(p.mission_state) || '—')}
      {cell('Waypoint', p.waypoint_sequence ?? '—')}
      {cell('Sample', new Date(p.recorded_at).toLocaleTimeString())}
    </Grid>
  )
}

function EventList({ events }: { events: Ev[] }) {
  const navigate = useNavigate()
  if (!events.length) return <Typography variant="body2" color="text.secondary">Nothing found on this flight.</Typography>
  return (
    <List dense disablePadding>
      {events.map((e) => (
        <ListItemButton key={e.id} onClick={() => navigate(`/drone-events/${e.id}`)}>
          <ListItemText primary={`${pretty(e.module_type)}${e.label ? ` · ${e.label}` : ''}`}
                        secondary={new Date(e.detected_at).toLocaleTimeString()} />
          <RiskChip level={e.risk_level as never} />
        </ListItemButton>
      ))}
    </List>
  )
}

function Waypoints({ session }: { session: Session }) {
  const wps = session.waypoints ?? []
  if (!wps.length) return <Typography variant="body2" color="text.secondary">No waypoints.</Typography>
  return (
    <Table size="small">
      <TableHead><TableRow><TableCell>#</TableCell><TableCell>Name</TableCell><TableCell>Status</TableCell>
        <TableCell>Reached</TableCell></TableRow></TableHead>
      <TableBody>
        {wps.map((w) => (
          <TableRow key={w.sequence}>
            <TableCell>{w.sequence}</TableCell>
            <TableCell>{w.name ?? '—'}</TableCell>
            <TableCell><Chip size="small" color={WP_COLOR[w.status] ?? 'default'} label={pretty(w.status)} /></TableCell>
            <TableCell>{w.reached_at ? new Date(w.reached_at).toLocaleTimeString() : '—'}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

// ── Live ─────────────────────────────────────────────────────────────────────

function LiveVideo({ droneId }: { droneId: string | null }) {
  const token = useAuthStore((s) => s.accessToken)
  const { data: drone } = useQuery({ queryKey: ['drone', droneId], queryFn: () => getDrone(droneId!), enabled: !!droneId })
  const cam = drone?.camera_id
  const { data: streams } = useQuery({ queryKey: ['streams', cam], queryFn: () => getStreams(cam!), enabled: !!cam })
  const stream = streams?.find((s) => s.status === 'online') ?? streams?.[0]
  if (!cam || !stream || !token) {
    return (
      <Box sx={{ aspectRatio: '16/9', display: 'flex', flexDirection: 'column', alignItems: 'center',
                 justifyContent: 'center', bgcolor: 'rgba(0,0,0,0.35)', borderRadius: 2 }}>
        <VideocamOffIcon sx={{ fontSize: 40, opacity: 0.5 }} />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1, textAlign: 'center', px: 2 }}>
          {!cam ? "This drone has no camera linked, so there is no live video. Link one on the drone's settings."
            : 'The drone camera has no stream configured.'}
        </Typography>
      </Box>
    )
  }
  return <HlsPlayer src={mediaUrl(`/api/v1/cameras/${cam}/streams/${stream.id}/hls/index.m3u8`, token)}
                    sx={{ aspectRatio: '16/9', width: '100%', borderRadius: 2, bgcolor: '#000' }} />
}

const COMMANDS: { kind: CommandKind; label: string; icon: ReactNode; perm: 'operate' | 'abort'
                  when: (s: string) => boolean; confirm?: string }[] = [
  { kind: 'pause', label: 'Pause', icon: <PauseIcon />, perm: 'operate', when: (s) => s === 'ACTIVE' },
  { kind: 'resume', label: 'Resume', icon: <PlayArrowIcon />, perm: 'operate', when: (s) => s === 'PAUSED' },
  { kind: 'return-to-home', label: 'Return home', icon: <HomeIcon />, perm: 'abort',
    when: (s) => AIRBORNE.includes(s as never) && s !== 'RETURNING', confirm: 'Bring the drone back to its launch point?' },
  { kind: 'abort', label: 'Abort', icon: <StopIcon />, perm: 'abort', when: (s) => AIRBORNE.includes(s as never),
    confirm: 'Abort the mission? The drone follows its provider\'s abort procedure.' },
  { kind: 'cancel', label: 'Cancel', icon: <CancelIcon />, perm: 'abort',
    when: (s) => ['SCHEDULED', 'PRECHECK', 'READY'].includes(s), confirm: 'Cancel this flight before it launches?' },
]

function Controls({ session }: { session: Session }) {
  const qc = useQueryClient()
  const canOperate = usePermission('drone:operate')
  const canAbort = usePermission('drone:mission:abort')
  const [ask, setAsk] = useState<(typeof COMMANDS)[number] | null>(null)
  const [reason, setReason] = useState('')
  const { data: commands } = useQuery({ queryKey: ['drone-commands', session.id], queryFn: () => listCommands(session.id),
                                        refetchInterval: 5_000 })
  const send = useMutation({
    mutationFn: ({ kind, why }: { kind: CommandKind; why?: string }) => sendCommand(session.id, kind, why),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['drone-commands', session.id] })
      qc.invalidateQueries({ queryKey: ['drone-patrol', session.id] })
      setAsk(null); setReason('')
    },
  })
  const available = COMMANDS.filter((c) => c.when(session.status) && (c.perm === 'operate' ? canOperate : canAbort))
  return (
    <>
      <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', mb: 1 }}>
        {available.map((c) => (
          <Button key={c.kind} size="small" variant={c.kind === 'abort' ? 'contained' : 'outlined'}
                  color={c.kind === 'abort' || c.kind === 'cancel' ? 'error' : 'primary'} startIcon={c.icon}
                  disabled={send.isPending}
                  onClick={() => (c.confirm ? setAsk(c) : send.mutate({ kind: c.kind }))}>{c.label}</Button>
        ))}
        {!available.length && <Typography variant="body2" color="text.secondary">No controls apply right now.</Typography>}
      </Stack>
      {send.error && <Alert severity="error" sx={{ mb: 1 }}>{apiError(send.error)}</Alert>}
      {!!commands?.length && (
        <Box>
          {commands.slice(0, 5).map((c) => (
            <Typography key={c.id} variant="caption" color="text.secondary" sx={{ display: 'block' }}>
              {new Date(c.requested_at).toLocaleTimeString()} · {pretty(c.command)} · {pretty(c.status)}
              {c.requested_by_name ? ` · ${c.requested_by_name}` : ''}{c.result ? ` — ${c.result}` : ''}
            </Typography>
          ))}
        </Box>
      )}
      <Dialog open={!!ask} onClose={() => setAsk(null)}>
        <DialogTitle>{ask?.label}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>{ask?.confirm}</Typography>
          <TextField fullWidth label="Reason (recorded)" value={reason} onChange={(e) => setReason(e.target.value)} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAsk(null)}>Back</Button>
          <Button color="error" variant="contained" disabled={send.isPending}
                  onClick={() => ask && send.mutate({ kind: ask.kind, why: reason })}>{ask?.label}</Button>
        </DialogActions>
      </Dialog>
    </>
  )
}

function LiveView({ session, points, now, events }: { session: Session; points: TrackPoint[]; now: TrackPoint | null
                                                       events: Ev[] }) {
  const navigate = useNavigate()
  const path = points.map((p) => [p.latitude, p.longitude] as [number, number])
  if (now && (!points.length || now !== points[points.length - 1])) path.push([now.latitude, now.longitude])
  const fit = useMemo(() => (session.waypoints ?? []).map((w) => [w.latitude, w.longitude] as [number, number]),
    [session.waypoints])
  return (
    <Grid container spacing={2}>
      <Grid size={{ xs: 12, lg: 7 }}>
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Live video</Typography>
            <Chip size="small" color="error" label="LIVE" />
          </Stack>
          <LiveVideo droneId={session.drone_id} />
        </GlassCard>
        <GlassCard sx={{ p: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Position</Typography>
          <DroneMap height={420}>
            <PlanLayers session={session} />
            {path.length > 1 && <Polyline positions={path} pathOptions={{ color: '#00d4ff', weight: 4 }} />}
            <EventMarkers events={events} onOpen={(eid) => navigate(`/drone-events/${eid}`)} />
            {now && <Marker position={[now.latitude, now.longitude]} icon={droneIcon(now.heading_deg)} />}
            <FitTo points={fit.length ? fit : path.slice(-1)} />
          </DroneMap>
        </GlassCard>
      </Grid>
      <Grid size={{ xs: 12, lg: 5 }}>
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Controls</Typography>
          <Controls session={session} />
        </GlassCard>
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Telemetry</Typography>
          <Telemetry p={now} />
        </GlassCard>
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Events ({events.length})</Typography>
          <EventList events={events} />
        </GlassCard>
        <GlassCard sx={{ p: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Waypoints</Typography>
          <Waypoints session={session} />
        </GlassCard>
      </Grid>
    </Grid>
  )
}

// ── Replay ───────────────────────────────────────────────────────────────────

const SPEEDS = [1, 4, 16, 64]

function Replay({ session, points, events }: { session: Session; points: TrackPoint[]; events: Ev[] }) {
  const navigate = useNavigate()
  const t0 = points.length ? new Date(points[0].recorded_at).getTime() : 0
  const t1 = points.length ? new Date(points[points.length - 1].recorded_at).getTime() : 0
  const [t, setT] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(16)
  useEffect(() => {
    if (!playing) return
    const h = window.setInterval(() => setT((v) => {
      const next = v + 250 * speed
      if (next >= t1 - t0) { setPlaying(false); return t1 - t0 }
      return next
    }), 250)
    return () => window.clearInterval(h)
  }, [playing, speed, t0, t1])

  // The sample at or before the cursor.
  const idx = useMemo(() => {
    let lo = 0, hi = points.length - 1, ans = 0
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      if (new Date(points[mid].recorded_at).getTime() - t0 <= t) { ans = mid; lo = mid + 1 } else hi = mid - 1
    }
    return ans
  }, [points, t, t0])
  const cur = points[idx] ?? null
  const flown = points.slice(0, idx + 1).map((p) => [p.latitude, p.longitude] as [number, number])
  const all = useMemo(() => points.map((p) => [p.latitude, p.longitude] as [number, number]), [points])
  const cursorIso = cur?.recorded_at
  const marks = events.map((e) => ({ value: new Date(e.detected_at).getTime() - t0 }))
    .filter((m) => m.value >= 0 && m.value <= t1 - t0)
  const snap = session.config_snapshot
  const mmss = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`

  return (
    <Grid container spacing={2}>
      <Grid size={{ xs: 12, lg: 8 }}>
        <GlassCard sx={{ p: 2 }}>
          <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 1, flexWrap: 'wrap', gap: 1 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Replay</Typography>
            <Typography variant="caption" color="text.secondary">
              {track(points.length)} · {fmt(session.launched_at ?? session.started_at)} → {fmt(session.ended_at)}
            </Typography>
          </Stack>
          {!points.length ? (
            <Alert severity="info" sx={{ mb: 1 }}>
              This flight recorded no track{session.status === 'BLOCKED' || session.status === 'MISSED'
                ? ' — it never launched' : ''}. The planned route is shown.</Alert>
          ) : null}
          <DroneMap height={480}>
            <PlanLayers session={session} />
            {all.length > 1 && <Polyline positions={all} pathOptions={{ color: '#00d4ff', weight: 2, opacity: 0.35 }} />}
            {flown.length > 1 && <Polyline positions={flown} pathOptions={{ color: '#00d4ff', weight: 4 }} />}
            <EventMarkers events={events} until={cursorIso} onOpen={(eid) => navigate(`/drone-events/${eid}`)} />
            {cur && <Marker position={[cur.latitude, cur.longitude]} icon={droneIcon(cur.heading_deg)} />}
            <FitTo points={all.length ? all : (session.waypoints ?? []).map((w) => [w.latitude, w.longitude] as [number, number])} />
          </DroneMap>
          {!!points.length && (
            <Stack direction="row" sx={{ gap: 2, alignItems: 'center', mt: 1.5 }}>
              <Tooltip title={playing ? 'Pause' : 'Play'}>
                <IconButton onClick={() => { if (t >= t1 - t0) setT(0); setPlaying(!playing) }}>
                  {playing ? <PauseIcon /> : <PlayArrowIcon />}</IconButton>
              </Tooltip>
              <Slider value={t} min={0} max={Math.max(1, t1 - t0)} step={1000} marks={marks}
                      onChange={(_, v) => { setPlaying(false); setT(v as number) }}
                      valueLabelDisplay="auto" valueLabelFormat={mmss} sx={{ flex: 1 }} />
              <Typography variant="caption" sx={{ minWidth: 90, textAlign: 'right' }}>{mmss(t)} / {mmss(t1 - t0)}</Typography>
              <ToggleButtonGroup size="small" exclusive value={speed} onChange={(_, v) => v && setSpeed(v)}>
                {SPEEDS.map((s) => <ToggleButton key={s} value={s}>{s}×</ToggleButton>)}
              </ToggleButtonGroup>
            </Stack>
          )}
          <Typography variant="caption" color="text.secondary">Coloured dots on the timeline and the map are events.</Typography>
        </GlassCard>
      </Grid>
      <Grid size={{ xs: 12, lg: 4 }}>
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>At this moment</Typography>
          <Telemetry p={cur} />
        </GlassCard>
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Flight</Typography>
          <Grid container spacing={1}>
            {([
              ['Route', session.route_name ?? '—'], ['AI profile', session.profile_name ?? 'defaults'],
              ['Distance', session.distance_m != null ? formatDistance(session.distance_m) : '—'],
              ['Planned', snap?.estimate ? `${Math.round(snap.estimate.duration_s / 60)} min` : '—'],
              ['Events', session.event_count], ['Incidents', session.incident_count],
              ['Media', session.media_count ?? 0], ['Samples', track(points.length)],
            ] as [string, ReactNode][]).map(([k, v]) => (
              <Grid key={k} size={{ xs: 6 }}><Typography variant="caption" color="text.secondary">{k}</Typography>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>{v}</Typography></Grid>
            ))}
          </Grid>
        </GlassCard>
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Events ({events.length})</Typography>
          <EventList events={events} />
        </GlassCard>
        <GlassCard sx={{ p: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Waypoints</Typography>
          <Waypoints session={session} />
        </GlassCard>
      </Grid>
    </Grid>
  )
}

const track = (n: number) => `${n} sample${n === 1 ? '' : 's'}`

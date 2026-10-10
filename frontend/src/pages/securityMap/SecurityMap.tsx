/**
 * The security map — what the platform knows the position of, in layers.
 *
 * Sites, cameras, guards, incidents, alerts, situations, drones, checkpoints
 * and the places somebody drew: each on its own layer, each shown to someone
 * who may already read it elsewhere. A thing with no position cannot be drawn
 * and is counted under the map, so that an empty patch is not read as a quiet
 * one.
 *
 * A GUARD IS DRAWN WHERE THEY LAST RECORDED BEING, AND IT SAYS HOW LONG AGO.
 * There is no live position of anybody.
 *
 * Selecting an incident, an alert or a situation lists what is near it,
 * nearest guard first. It is a list to read: nobody is sent anywhere from here.
 */
import { useEffect, useMemo, useState } from 'react'
import { CircleMarker, MapContainer, Polygon, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useNavigate } from 'react-router-dom'
import { Alert, Box, Button, Chip, IconButton, MenuItem, Skeleton, TextField, Typography } from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import { useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { getSites } from '@/api/sites'
import { getAround, getFeatures, getLayers } from '@/api/siteMap'
import type { Feature, LayerKey, NearGuard } from '@/api/siteMap'
import {
  DEFAULT_CENTRE, ON_AT_FIRST, TILE_ATTRIBUTION, TILE_URL, about, ago, colourOf, distance, fmt, gathered, pretty,
  sizeOf,
} from '@/components/securityMap/mapFormat'
import { ErrorState } from '@/components/states'

type Selectable = 'INCIDENT' | 'ALERT' | 'SITUATION'
const SELECTABLE: LayerKey[] = ['INCIDENT', 'ALERT', 'SITUATION']
/** Drawn first to last: what an officer acts on is on top. */
const ORDER: LayerKey[] = ['DRONE_ZONE', 'PLACE', 'SITE', 'CHECKPOINT', 'CAMERA', 'DRONE', 'GUARD', 'ALERT', 'SITUATION',
                           'INCIDENT']
/** The layers whose marks carry their name on the map itself. A field of
 *  anonymous dots tells an officer nothing until every one has been hovered. */
const NAMED: LayerKey[] = ['SITE', 'GUARD', 'INCIDENT', 'SITUATION', 'DRONE']
const LEGEND: [string, string][] = [
  ['#ff4560', 'needs attention: high, offline, an emergency'], ['#f59e0b', 'medium, busy, degraded'],
  ['#22c55e', 'in order: online, free, low'], ['#a78bfa', 'a place'],
]

/** What is written beside a named mark: who or what, and for a guard how old the position is. */
function nameOf(group: Feature[]): string {
  const f = group[0]
  if (f.layer === 'GUARD') return `${f.label} · ${ago(f.detail.position_age_s as number | null)}`
  return group.length > 1 ? `${group.length} · ${f.label}` : f.label ?? ''
}

const HOME: Partial<Record<LayerKey, (f: Feature) => string>> = {
  SITUATION: (f) => `/situations/${f.id}`, INCIDENT: () => '/incidents', ALERT: () => '/alerts',
}

/** Bring everything drawn into view, once per change of what is drawn. */
function Fit({ points, token }: { points: [number, number][]; token: string }) {
  const map = useMap()
  useEffect(() => {
    if (points.length === 1) map.setView(points[0], 17)
    else if (points.length > 1) map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 18 })
    // Only when what is drawn changes — not on every refresh of the same things.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])
  return null
}

function GuardLine({ guard }: { guard: NearGuard }) {
  return (
    <Box data-testid="near-guard" sx={{ py: 0.75, borderTop: '1px solid', borderColor: 'divider',
                                       opacity: guard.stale ? 0.65 : 1 }}>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>{guard.full_name ?? 'A guard'}</Typography>
        <Chip size="small" variant="outlined"
              color={guard.emergency_id ? 'error' : guard.available ? 'success' : 'warning'}
              label={guard.emergency_id ? 'In an emergency' : guard.available ? 'Free' : 'Already sent somewhere'} />
        <Box sx={{ flex: 1 }} />
        <Typography variant="body2">{distance(guard.distance_m)}</Typography>
      </Stack>
      <Typography variant="caption" color="text.secondary">
        {guard.position_source ? `Last recorded at a ${guard.position_source}, ${ago(guard.position_age_s)}`
          : 'No position recorded this shift'}
        {guard.stale && ' — too long ago to say where they are now'}</Typography>
    </Box>
  )
}

export default function SecurityMap() {
  const navigate = useNavigate()
  const [siteId, setSiteId] = useState('')
  const [hours, setHours] = useState(24)
  const [on, setOn] = useState<LayerKey[]>(ON_AT_FIRST)
  const [picked, setPicked] = useState<{ kind: Selectable; id: string } | null>(null)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data: info } = useQuery({ queryKey: ['site-map-layers'], queryFn: getLayers, staleTime: 300_000 })
  const { data, isLoading, error, isLoadingError: dataFailed, refetch: refetchData } = useQuery({
    queryKey: ['site-map-features', siteId, hours],
    queryFn: () => getFeatures({ site_id: siteId || undefined, hours }),
    refetchInterval: 30_000,
  })
  const { data: near, error: nearError, refetch: refetchNear } = useQuery({
    queryKey: ['site-map-around', picked], queryFn: () => getAround(picked!.kind, picked!.id), enabled: !!picked,
  })

  const visible = useMemo(() => ORDER.filter((key) => on.includes(key) && data?.layers[key]), [on, data])
  const drawn = useMemo(() => visible.flatMap((key) => data?.layers[key] ?? []), [visible, data])
  const points = useMemo(
    () => drawn.filter((f) => f.latitude !== null).map((f) => [f.latitude!, f.longitude!] as [number, number]),
    [drawn])
  const routes = useMemo(() => {
    const byRoute = new Map<string, Feature[]>()
    for (const cp of (on.includes('CHECKPOINT') ? data?.layers.CHECKPOINT ?? [] : [])) {
      const key = String(cp.detail.route_id)
      byRoute.set(key, [...(byRoute.get(key) ?? []), cp])
    }
    return [...byRoute.values()].map((cps) =>
      cps.sort((a, b) => Number(a.detail.sequence) - Number(b.detail.sequence))
        .map((cp) => [cp.latitude!, cp.longitude!] as [number, number]))
  }, [on, data])
  const toggle = (key: LayerKey) => setOn((now) => now.includes(key) ? now.filter((k) => k !== key) : [...now, key])
  const uncounted = Object.entries(data?.without_position ?? {}).filter(([key]) => on.includes(key as LayerKey))

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Security Map"
                  subtitle="Where the cameras, guards, incidents, situations, drones and places are — each as you may already see it elsewhere" />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 180 }}
                     onChange={(e) => { setSiteId(e.target.value); setPicked(null) }}>
            <MenuItem value="">All sites</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Incidents, alerts and situations from" value={hours} sx={{ minWidth: 250 }}
                     onChange={(e) => setHours(Number(e.target.value))}>
            <MenuItem value={1}>The last hour</MenuItem>
            <MenuItem value={24}>The last 24 hours</MenuItem>
            <MenuItem value={168}>The last 7 days</MenuItem>
          </TextField>
          {info?.can_manage && <Button onClick={() => navigate('/site-places')}>Draw places</Button>}
        </Stack>
        <Stack direction="row" sx={{ gap: 0.75, flexWrap: 'wrap', mt: 1.5 }} data-testid="layer-toggles">
          {(info?.layers ?? []).filter((l) => l.may_see).map((l) => {
            const count = data?.layers[l.key]?.length
            const shown = on.includes(l.key)
            return (
              <Chip key={l.key} size="small" clickable onClick={() => toggle(l.key)}
                    color={shown ? 'primary' : 'default'} variant={shown ? 'filled' : 'outlined'}
                    label={count === undefined ? l.label : `${l.label} ${count}`} />
            )
          })}
        </Stack>
        <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap', mt: 1.25 }} data-testid="legend">
          {LEGEND.map(([colour, means]) => (
            <Stack key={colour} direction="row" sx={{ gap: 0.75, alignItems: 'center' }}>
              <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: colour }} />
              <Typography variant="caption" color="text.secondary">{means}</Typography>
            </Stack>))}
          <Stack direction="row" sx={{ gap: 0.75, alignItems: 'center' }}>
            <Box sx={{ width: 10, height: 10, borderRadius: '50%', border: '1.5px dashed', borderColor: 'text.secondary' }} />
            <Typography variant="caption" color="text.secondary">a guard&rsquo;s position over an hour old</Typography>
          </Stack>
        </Stack>
      </GlassCard>

      {error && <ErrorState compact error={error} onRetry={refetchData} sx={{ mb: 2 }} />}
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: picked ? '1fr 380px' : '1fr' } }}>
        <GlassCard sx={{ p: 1, overflow: 'hidden' }}>
          {isLoading ? <Skeleton height={520} /> : dataFailed ? <ErrorState compact error={error} onRetry={refetchData} /> : (
            <Box sx={{ height: 560, borderRadius: 1, overflow: 'hidden' }}>
              <MapContainer center={DEFAULT_CENTRE} zoom={12} style={{ height: '100%', width: '100%' }}>
                <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
                <Fit points={points} token={`${siteId}:${visible.join(',')}:${points.length}`} />
                {drawn.filter((f) => f.outline).map((f) => (
                  <Polygon key={`${f.layer}:${f.id}:area`} positions={f.outline!}
                           pathOptions={{ color: colourOf(f), weight: 1.5, fillOpacity: 0.12 }}>
                    <Tooltip sticky>{f.label} — {about(f)}</Tooltip>
                  </Polygon>))}
                {routes.filter((r) => r.length > 1).map((r, i) => (
                  <Polyline key={`route:${i}`} positions={r} pathOptions={{ color: '#94a3b8', weight: 2, dashArray: '4 6' }} />))}
                {/* An area is drawn as its outline. It gets a mark as well only when it
                    has a point of its own — and a site always does. */}
                {gathered(drawn.filter((f) => !f.outline || f.layer === 'SITE' || f.detail.has_point)).map((group) => {
                  const f = group[0]
                  const stale = f.layer === 'GUARD' && !!f.detail.stale
                  const selectable = SELECTABLE.includes(f.layer)
                  const chosen = picked?.id === f.id
                  return (
                    <CircleMarker key={`${f.layer}:${f.id}`} center={[f.latitude!, f.longitude!]}
                                  radius={sizeOf(f.layer) + (chosen ? 4 : 0)}
                                  pathOptions={{ color: colourOf(f), weight: chosen ? 4 : 2, fillColor: colourOf(f),
                                                 fillOpacity: stale ? 0.15 : 0.55, dashArray: stale ? '3 3' : undefined }}
                                  eventHandlers={selectable ? {
                                    click: () => setPicked({ kind: f.layer as Selectable, id: f.id }) } : undefined}>
                      {NAMED.includes(f.layer) ? (
                        <Tooltip permanent direction="right" offset={[8, 0]}>{nameOf(group)}</Tooltip>
                      ) : (
                        <Tooltip direction="top">
                          <b>{group.length > 1 ? `${group.length} here — ` : ''}{f.label}</b><br />{about(f)}
                          {selectable && <><br /><i>Select to see what is near</i></>}
                        </Tooltip>
                      )}
                    </CircleMarker>
                  )
                })}
              </MapContainer>
            </Box>
          )}
        </GlassCard>

        {picked && (
          <GlassCard sx={{ p: 2, maxHeight: 580, overflowY: 'auto' }} data-testid="around">
            <Stack direction="row" sx={{ alignItems: 'flex-start', gap: 1 }}>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  {pretty(picked.kind)}</Typography>
                <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>{near?.subject.label ?? '…'}</Typography>
                {near && <Typography variant="caption" color="text.secondary">{about(near.subject)} · {fmt(near.subject.at)}</Typography>}
              </Box>
              <IconButton size="small" aria-label="Close" onClick={() => setPicked(null)}><CloseIcon fontSize="small" /></IconButton>
            </Stack>
            {nearError && <ErrorState compact error={nearError} onRetry={refetchNear} sx={{ mt: 1 }} />}
            {near && (
              <>
                {HOME[near.subject.layer] && (
                  <Button size="small" sx={{ mt: 0.5 }} onClick={() => navigate(HOME[near.subject.layer]!(near.subject))}>
                    Open it</Button>)}
                {!near.located && (
                  <Alert severity="warning" sx={{ mt: 1 }}>
                    This has no position recorded, so nothing can be said to be near it. The guards on shift at its
                    site are listed below without a distance.</Alert>)}
                <Typography variant="subtitle2" sx={{ mt: 1.5 }}>Guards on shift at the site</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                  Nearest first by last recorded position; those free before those already sent somewhere.</Typography>
                {!near.nearest_guards.length
                  ? <Typography variant="body2" color="text.secondary" sx={{ py: 0.75 }}>
                      {near.not_shown.some((n) => n.layer === 'GUARD') ? 'Guards are not shown to you.' : 'Nobody is clocked in at this site.'}</Typography>
                  : near.nearest_guards.map((guard) => <GuardLine key={guard.user_id} guard={guard} />)}
                {(['CAMERA', 'DRONE', 'PLACE', 'CHECKPOINT'] as const).map((key) => {
                  const list = near.nearby[key] ?? []
                  if (!list.length) return null
                  return (
                    <Box key={key} sx={{ mt: 1.5 }}>
                      <Typography variant="subtitle2">
                        {{ CAMERA: 'Cameras', DRONE: 'Drones', PLACE: 'Places', CHECKPOINT: 'Patrol checkpoints' }[key]} within {near.radius_m} m
                      </Typography>
                      {list.map((f) => (
                        <Stack key={f.id} direction="row" data-testid="near-thing"
                               sx={{ gap: 1, py: 0.5, borderTop: '1px solid', borderColor: 'divider' }}>
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            <Typography variant="body2">{f.label}</Typography>
                            <Typography variant="caption" color="text.secondary">{about(f)}</Typography>
                          </Box>
                          <Typography variant="body2">{distance(f.distance_m)}</Typography>
                        </Stack>))}
                    </Box>
                  )
                })}
                {near.not_shown.map((n) => (
                  <Alert key={n.layer} severity="info" sx={{ mt: 1 }}>{n.label} are not shown: {n.reason}</Alert>))}
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
                  {near.note} This list sends nobody anywhere.</Typography>
              </>
            )}
          </GlassCard>
        )}
      </Box>

      {data && (
        <Box sx={{ mt: 2 }} data-testid="map-notes">
          {uncounted.length > 0 && (
            <Alert severity="warning" sx={{ mb: 1 }}>
              <b>Not on the map for want of a position:</b>{' '}
              {uncounted.map(([key, n]) => `${n} ${(info?.layers.find((l) => l.key === key)?.label ?? key).toLowerCase()}`).join(', ')}.
              An empty patch of map is not necessarily a quiet one.</Alert>)}
          {Object.keys(data.more).length > 0 && (
            <Alert severity="warning" sx={{ mb: 1 }}>
              There are more than the map shows of: {Object.keys(data.more).map((k) => pretty(k).toLowerCase()).join(', ')}.
              Choose a site or a shorter period.</Alert>)}
          {data.not_shown.map((n) => (
            <Alert key={n.layer} severity="info" sx={{ mb: 1 }}>{n.label} are not shown: {n.reason}</Alert>))}
          <Typography variant="caption" color="text.secondary">{data.note} As of {fmt(data.as_of)}.</Typography>
        </Box>
      )}
    </Box>
  )
}

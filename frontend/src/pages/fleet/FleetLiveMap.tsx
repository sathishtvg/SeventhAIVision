/**
 * The live fleet map: every vehicle the person may see, where it last
 * reported, kept current while the page is open.
 *
 * Searched by registration number, name, driver or site, filtered by site,
 * type and tracking status; a vehicle chosen on the list or the map shows its
 * detail and can be centred. A vehicle that has never reported is listed and
 * not drawn: the map never puts a vehicle where it has not been. Until one
 * has reported, the map says so rather than looking like an empty fleet.
 */
import { useMemo, useState } from 'react'
import {
  Box, Button, Chip, List, ListItemButton, ListItemText, MenuItem, Skeleton, TextField, Typography,
} from '@mui/material'
import CenterIcon from '@mui/icons-material/CenterFocusStrong'
import { Link as RouterLink, useSearchParams } from 'react-router-dom'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import Stack from '@/components/common/Stack'
import { FleetMap } from '@/components/fleet/FleetMap'
import { FleetNav } from '@/components/fleet/FleetNav'
import { useFleetLive } from '@/components/fleet/useFleetLive'
import { drawn, matches, statusNow } from '@/components/fleet/fleetMapFormat'
import { EmptyState, ErrorState, ageText } from '@/components/states'
import { useNow } from '@/motion'
import { TRACKING_COLOUR, TRACKING_LABEL, type MapVehicle, type TrackingStatus } from '@/api/fleet'

const STATUSES: TrackingStatus[] = ['online', 'stale', 'offline', 'not_configured']
const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }

export default function FleetLiveMap() {
  const [search, setSearch] = useSearchParams()
  const { data, isLoading, isLoadingError: mapFailed, error: mapError, refetch } = useFleetLive()
  const now = useNow(10_000)
  const [q, setQ] = useState('')
  const [site, setSite] = useState('')
  const [type, setType] = useState('')
  const [statuses, setStatuses] = useState<TrackingStatus[]>([])
  const [focusTick, setFocusTick] = useState(0)
  const selectedId = search.get('focus')
  const select = (id: string) => setSearch({ focus: id }, { replace: true })

  const limits = data?.limits ?? { online_minutes: 5, offline_minutes: 30 }
  const all = useMemo(() => data?.items ?? [], [data])
  const statusOf = (v: MapVehicle) => statusNow(v, limits, now)
  const shown = all.filter((v) => matches(v, { q, site, type, statuses }, statusOf(v)))
  const counts = STATUSES.map((s) => [s, all.filter((v) => statusOf(v) === s).length] as const)
  const sites = [...new Map(all.filter((v) => v.site_id).map((v) => [v.site_id!, v.site_name ?? 'Site'])).entries()]
  const types = [...new Map(all.map((v) => [v.vehicle_type, v.type_label])).entries()]
  const selected = all.find((v) => v.id === selectedId) ?? null
  const reported = all.some(drawn)
  const toggle = (s: TrackingStatus) => setStatuses((was) => (was.includes(s) ? was.filter((x) => x !== s) : [...was, s]))

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Live fleet map" subtitle="Where each vehicle last reported, kept current while the page is open" />
      <FleetNav />
      {mapFailed ? (
        <ErrorState title="The fleet could not be loaded" error={mapError} onRetry={refetch} />
      ) : (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '340px 1fr' }, gap: 2 }}>
          <GlassCard sx={{ p: 2, order: { xs: 2, md: 1 }, minWidth: 0 }}>
            <TextField size="small" fullWidth label="Search" placeholder="Registration, name, driver, site" value={q}
                       onChange={(e) => setQ(e.target.value)} sx={{ mb: 1.5 }} />
            <Stack direction="row" sx={{ gap: 1, mb: 1.5 }}>
              <TextField select size="small" label="Site" value={site} onChange={(e) => setSite(e.target.value)} sx={{ flex: 1 }} slotProps={shrunk}>
                <MenuItem value="">Every site</MenuItem>
                {sites.map(([id, name]) => <MenuItem key={id} value={id}>{name}</MenuItem>)}
              </TextField>
              <TextField select size="small" label="Type" value={type} onChange={(e) => setType(e.target.value)} sx={{ flex: 1 }} slotProps={shrunk}>
                <MenuItem value="">Every type</MenuItem>
                {types.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
              </TextField>
            </Stack>
            <Stack direction="row" sx={{ gap: 0.75, flexWrap: 'wrap', mb: 1 }} role="group" aria-label="Tracking status">
              {counts.map(([s, n]) => (
                <Chip key={s} size="small" label={`${TRACKING_LABEL[s]} ${n}`} color={statuses.includes(s) ? TRACKING_COLOUR[s] : 'default'}
                      variant={statuses.includes(s) ? 'filled' : 'outlined'} onClick={() => toggle(s)} aria-pressed={statuses.includes(s)} />
              ))}
            </Stack>
            {isLoading ? (
              [0, 1, 2, 3].map((i) => <Skeleton key={i} height={52} />)
            ) : shown.length === 0 ? (
              <EmptyState compact title={all.length ? 'No vehicle matches' : 'No vehicles yet'} />
            ) : (
              <List dense sx={{ maxHeight: { md: 520 }, overflowY: 'auto', mx: -1 }}>
                {shown.map((v) => {
                  const s = statusOf(v)
                  return (
                    <ListItemButton key={v.id} selected={v.id === selectedId} onClick={() => select(v.id)}>
                      <ListItemText
                        primary={<Stack direction="row" sx={{ gap: 1, alignItems: 'center' }}>
                          <Typography variant="body2" sx={{ fontWeight: 700 }}>{v.plate_number ?? v.name}</Typography>
                          <Chip size="small" color={TRACKING_COLOUR[s]} label={TRACKING_LABEL[s]} />
                        </Stack>}
                        secondary={s === 'not_configured' ? `${v.name} · no device`
                          : v.last_fix_at ? `${v.name} · last fix ${ageText(now - new Date(v.last_fix_at).getTime())}` : `${v.name} · nothing has arrived yet`} />
                    </ListItemButton>
                  )
                })}
              </List>
            )}
          </GlassCard>
          <Box sx={{ order: { xs: 1, md: 2 }, minWidth: 0, display: 'grid', gap: 2, alignContent: 'start' }}>
            {!isLoading && !reported && (
              <EmptyState compact title="No vehicle has reported a position yet"
                          hint={<>Link a device on a vehicle's GPS device tab, with the provider set up (<RouterLink to="/gps/providers">Providers</RouterLink>).</>} />
            )}
            {isLoading ? <Skeleton variant="rounded" height={480} /> : (
              <FleetMap vehicles={shown} limits={limits} height={480} selectedId={selectedId} focusTick={focusTick} onSelect={select} />
            )}
            {selected && <Detail v={selected} status={statusOf(selected)} now={now} onFocus={() => setFocusTick((n) => n + 1)} />}
          </Box>
        </Box>
      )}
    </Box>
  )
}

function Detail({ v, status, now, onFocus }: { v: MapVehicle; status: TrackingStatus; now: number; onFocus: () => void }) {
  return (
    <GlassCard sx={{ p: 2 }}>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>{v.plate_number ?? v.name}</Typography>
        <Chip size="small" color={TRACKING_COLOUR[status]} label={TRACKING_LABEL[status]} />
        {status === 'online' && v.motion && <Chip size="small" variant="outlined" label={v.motion === 'moving' ? 'Moving' : 'Stopped'} />}
        <Box sx={{ flex: 1 }} />
        {drawn(v) && <Button size="small" startIcon={<CenterIcon />} onClick={onFocus}>Focus</Button>}
        <Button size="small" component={RouterLink} to={`/gps/vehicles/${v.id}`}>Open the vehicle</Button>
      </Stack>
      <Typography variant="body2">{v.name} · {v.type_label} · {v.site_name ?? 'No site'}{v.driver_name ? ` · ${v.driver_name}` : ''}</Typography>
      {status === 'not_configured' ? (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Not tracked: no device is linked, or tracking is off.</Typography>
      ) : v.last_fix_at ? (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          {status === 'online' ? 'At' : 'Last known at'} {Number(v.last_lat).toFixed(5)}, {Number(v.last_lon).toFixed(5)},{' '}
          {new Date(v.last_fix_at).toLocaleString()} ({ageText(now - new Date(v.last_fix_at).getTime())})
          {status === 'online' && v.last_speed != null ? ` · ${Number(v.last_speed).toFixed(0)} km/h` : ''}
          {status === 'online' && v.last_heading != null ? `, heading ${v.last_heading}°` : ''}
        </Typography>
      ) : (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Nothing has arrived from its device yet.</Typography>
      )}
    </GlassCard>
  )
}

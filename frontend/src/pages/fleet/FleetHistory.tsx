/**
 * Journey history: where one vehicle went over a range of up to a week, its
 * trips and stops, and the route played back.
 *
 * Journeys are worked out by the server from the positions it kept, and only
 * from two or more; with fewer the page says so. What the server left out - a
 * fix the device called inaccurate, a jump no vehicle could make - is counted
 * and said, and so is any time no position came. The vehicle and the range are
 * in the address, so a history can be linked to. Exporting is for whoever may
 * export, and is in the audit log.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Box, Button, IconButton, MenuItem, Skeleton, Slider, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material'
import PlayIcon from '@mui/icons-material/PlayArrow'
import PauseIcon from '@mui/icons-material/Pause'
import DownloadIcon from '@mui/icons-material/Download'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import Stack from '@/components/common/Stack'
import { FleetNav } from '@/components/fleet/FleetNav'
import { RouteMap } from '@/components/fleet/RouteMap'
import { PLAY_SPEEDS, PLAY_STEP_MS, lastAt, localInput, minutesText, step } from '@/components/fleet/journeyFormat'
import { EmptyState, ErrorState, SaveStatus } from '@/components/states'
import {
  downloadJourneys, getFleetMap, getFleetOptions, getJourneyHistory, vehicleLabel, type JourneyExport, type JourneyHistory,
} from '@/api/fleet'

const HOUR = 3_600_000
const iso = (local: string) => (local ? new Date(local).toISOString() : '')

export default function FleetHistory() {
  const [search, setSearch] = useSearchParams()
  const vehicleId = search.get('vehicle') ?? ''
  const now = useMemo(() => new Date(), [])
  const start = search.get('start') ?? localInput(new Date(now.getTime() - 24 * HOUR))
  const end = search.get('end') ?? localInput(now)
  const put = (changes: Record<string, string>) => setSearch({ vehicle: vehicleId, start, end, ...changes }, { replace: true })
  const tooLong = new Date(end).getTime() - new Date(start).getTime() > 7 * 24 * HOUR
  const backwards = new Date(end).getTime() <= new Date(start).getTime()

  const { data: options } = useQuery({ queryKey: ['fleet-options'], queryFn: getFleetOptions })
  const { data: fleet } = useQuery({ queryKey: ['fleet-map'], queryFn: () => getFleetMap() })
  const {
    data: history, isLoading, isLoadingError: historyFailed, error: historyError, refetch,
  } = useQuery({
    queryKey: ['fleet-journeys', vehicleId, start, end],
    queryFn: () => getJourneyHistory(vehicleId, iso(start), iso(end)),
    enabled: !!vehicleId && !tooLong && !backwards,
  })

  const lastDays = (days: number) => {
    const to = new Date()
    put({ start: localInput(new Date(to.getTime() - days * 24 * HOUR)), end: localInput(to) })
  }
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Journey history" subtitle="Where a vehicle went, its trips and stops, and the route played back" />
      <FleetNav />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Vehicle" value={vehicleId} sx={{ minWidth: 240 }}
                     onChange={(e) => put({ vehicle: e.target.value })}
                     slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}>
            <MenuItem value="" disabled>Choose a vehicle</MenuItem>
            {fleet?.items.map((v) => <MenuItem key={v.id} value={v.id}>{vehicleLabel(v)}</MenuItem>)}
          </TextField>
          <TextField size="small" type="datetime-local" label="From" value={start} onChange={(e) => put({ start: e.target.value })}
                     slotProps={{ inputLabel: { shrink: true } }} />
          <TextField size="small" type="datetime-local" label="To" value={end} onChange={(e) => put({ end: e.target.value })}
                     slotProps={{ inputLabel: { shrink: true } }} />
          <Button size="small" onClick={() => lastDays(1)}>Last 24 h</Button>
          <Button size="small" onClick={() => lastDays(7)}>Last 7 days</Button>
        </Stack>
        {(tooLong || backwards) && (
          <Alert severity="warning" sx={{ mt: 1.5 }}>{backwards ? 'The range ends before it starts.' : 'A range is a week at most.'}</Alert>
        )}
      </GlassCard>
      {!vehicleId ? (
        <EmptyState title="Choose a vehicle" hint="Its journeys over the range appear here." />
      ) : tooLong || backwards ? null : isLoading ? (
        <Skeleton variant="rounded" height={420} />
      ) : historyFailed ? (
        <ErrorState title="The journeys could not be loaded" error={historyError} onRetry={refetch} />
      ) : history ? (
        <Journeys key={`${vehicleId}|${start}|${end}`} history={history} mayExport={!!options?.may.export_history}
                  start={iso(start)} end={iso(end)} />
      ) : null}
    </Box>
  )
}

function Journeys({ history: h, mayExport, start, end }: { history: JourneyHistory; mayExport: boolean; start: string; end: string }) {
  const first = h.route.length ? Date.parse(h.route[0].at) : 0
  const last = h.route.length ? Date.parse(h.route[h.route.length - 1].at) : 0
  const [t, setT] = useState(first)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<number>(10)
  useEffect(() => {
    if (!playing) return
    const timer = setInterval(() => setT((was) => {
      const next = step(was, speed, last)
      if (next >= last) setPlaying(false)
      return next
    }), PLAY_STEP_MS)
    return () => clearInterval(timer)
  }, [playing, speed, last])
  const at = lastAt(h.route, t)
  const save = useMutation({
    mutationFn: (kind: JourneyExport) => downloadJourneys(h.vehicle.id, start, end, kind, (h.vehicle.plate_number ?? h.vehicle.name).replace(/\s/g, '')),
  })

  if (!h.enough) {
    return (
      <EmptyState title="Not enough positions for a journey"
                  hint={`${h.positions === 0 ? 'No position' : 'One position'} was kept for this vehicle over the range. A journey is worked out from two or more.`} />
    )
  }
  const left = h.left_out.inaccurate + h.left_out.impossible_jump
  return (
    <Box sx={{ display: 'grid', gap: 2 }}>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, gap: 2 }}>
        {[['Distance', `${h.summary.distance_km.toFixed(1)} km`], ['Driving', minutesText(h.summary.driving_minutes)],
          ['Stopped', minutesText(h.summary.stopped_minutes)], ['Trips', String(h.trips.length)]].map(([label, value]) => (
          <GlassCard key={label} sx={{ p: 2, textAlign: 'center' }}>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>{value}</Typography>
            <Typography variant="caption" color="text.secondary">{label}</Typography>
          </GlassCard>
        ))}
      </Box>
      {(left > 0 || h.gaps.length > 0) && (
        <Alert severity="info">
          {left > 0 && `${left} position${left === 1 ? ' was' : 's were'} left out (${h.left_out.inaccurate} reported as inaccurate, ${h.left_out.impossible_jump} a jump no vehicle could make). `}
          {h.gaps.length > 0 && `No position came for ${h.gaps.length === 1 ? 'one stretch' : `${h.gaps.length} stretches`} of more than 15 minutes; that time is counted neither as driving nor as stopped.`}
        </Alert>
      )}
      <RouteMap route={h.route} stops={h.stops} at={playing || t > first ? at : null} />
      <GlassCard sx={{ p: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
          <Tooltip title={playing ? 'Pause' : 'Play the journey back'}>
            <IconButton aria-label={playing ? 'Pause' : 'Play'} onClick={() => { if (t >= last) setT(first); setPlaying((p) => !p) }}>
              {playing ? <PauseIcon /> : <PlayIcon />}
            </IconButton>
          </Tooltip>
          <ToggleButtonGroup size="small" exclusive value={speed} onChange={(_, v) => v && setSpeed(v)} aria-label="Playback speed">
            {PLAY_SPEEDS.map((s) => <ToggleButton key={s} value={s}>{s}×</ToggleButton>)}
          </ToggleButtonGroup>
          <Slider sx={{ flex: 1, minWidth: 200, mx: 1 }} min={first} max={last} value={t} step={1000}
                  onChange={(_, v) => setT(v as number)} aria-label="Moment of the journey"
                  valueLabelDisplay="auto" valueLabelFormat={(v) => new Date(v).toLocaleString()} />
          <Typography variant="body2" sx={{ minWidth: 170 }}>
            {at ? `${new Date(at.at).toLocaleString()}${at.speed != null ? ` · ${Math.round(at.speed)} km/h` : ''}` : '—'}
          </Typography>
        </Stack>
        {h.route_thinned && (
          <Typography variant="caption" color="text.secondary">
            The route is drawn with fewer points so it stays quick; the distance is worked out from every position kept.
          </Typography>
        )}
      </GlassCard>
      {mayExport && (
        <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
          {(['trips', 'stops', 'positions'] as JourneyExport[]).map((k) => (
            <Button key={k} variant="outlined" size="small" startIcon={<DownloadIcon />} disabled={save.isPending} onClick={() => save.mutate(k)}>
              Export {k} (CSV)
            </Button>
          ))}
          <SaveStatus state={save.isPending ? 'saving' : save.isError ? 'failed' : save.isSuccess ? 'saved' : 'idle'} error={save.error}
                      savingText="Exporting…" savedText="Exported" />
        </Stack>
      )}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' }, gap: 2 }}>
        <GlassCard sx={{ p: 0 }}>
          <Typography variant="subtitle2" sx={{ p: 2, pb: 0 }}>Trips</Typography>
          <TableContainer>
            <Table size="small" aria-label="Trips">
              <TableHead><TableRow><TableCell>From</TableCell><TableCell>To</TableCell><TableCell align="right">km</TableCell><TableCell align="right">Max km/h</TableCell></TableRow></TableHead>
              <TableBody>
                {h.trips.length === 0 ? <TableRow><TableCell colSpan={4}>No trip in the range</TableCell></TableRow> : h.trips.map((x) => (
                  <TableRow key={x.start} hover sx={{ cursor: 'pointer' }} onClick={() => { setPlaying(false); setT(Date.parse(x.start)) }}>
                    <TableCell>{new Date(x.start).toLocaleString()}</TableCell><TableCell>{new Date(x.end).toLocaleTimeString()} ({minutesText(x.minutes)})</TableCell>
                    <TableCell align="right">{x.distance_km.toFixed(1)}</TableCell><TableCell align="right">{x.max_speed != null ? Math.round(x.max_speed) : '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </GlassCard>
        <GlassCard sx={{ p: 0 }}>
          <Typography variant="subtitle2" sx={{ p: 2, pb: 0 }}>Stops</Typography>
          <TableContainer>
            <Table size="small" aria-label="Stops">
              <TableHead><TableRow><TableCell>From</TableCell><TableCell>For</TableCell><TableCell>Ignition</TableCell></TableRow></TableHead>
              <TableBody>
                {h.stops.length === 0 ? <TableRow><TableCell colSpan={3}>No stop of 3 minutes or more</TableCell></TableRow> : h.stops.map((s) => (
                  <TableRow key={s.start} hover sx={{ cursor: 'pointer' }} onClick={() => { setPlaying(false); setT(Date.parse(s.start)) }}>
                    <TableCell>{new Date(s.start).toLocaleString()}</TableCell><TableCell>{minutesText(s.minutes)}</TableCell>
                    <TableCell>{s.ignition_off ? 'Off' : '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </GlassCard>
      </Box>
    </Box>
  )
}

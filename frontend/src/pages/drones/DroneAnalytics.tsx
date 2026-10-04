/**
 * Drone Patrol — analytics: what the flights and their events add up to.
 *
 * Three questions, top to bottom: how are the patrols going (statistics and
 * trends), where are the drones finding things (the risk map), and what might
 * be worth doing about it (recommendations).
 *
 * The risk map's score and the recommendations are both analytical — computed
 * from recorded events by fixed rules. Each says so where it is shown: neither
 * is a prediction or a conclusion.
 */
import { useMemo, useState } from 'react'
import {
  Alert, Box, Chip, Grid, MenuItem, Skeleton, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import { useQuery } from '@tanstack/react-query'
import { Circle, CircleMarker, Polygon, Tooltip as MapTooltip } from 'react-leaflet'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { getSites } from '@/api/sites'
import { apiError, getAnalyticsOverview, getRecommendations, getRiskMap, listZones } from '@/api/drones'
import type { AnalyticsGroup, AreaLevel, Rate, RiskArea, RiskLevel, RiskMap } from '@/api/drones'
import { DroneMap, FitTo, LicenceBanner, RiskChip } from '@/components/drones/droneUi'
import { RISK_COLOR, fmt } from '@/components/drones/droneFormat'
import { formatDistance } from '@/components/drones/geo'
import { BarList, ColumnChart, StatTile } from '@/components/drones/charts'
import type { Datum } from '@/components/drones/charts'
import { DroneNav } from './DroneNav'

const PERIODS = [7, 30, 90]
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const percent = (r: Rate) => (r == null ? '—' : `${Math.round(r * 100)}%`)
/** "2026-10-03" as a short local date, without shifting it through a timezone. */
const dayLabel = (iso: string, withWeekday = true) => {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { day: 'numeric', month: 'short',
                                                              ...(withWeekday ? { weekday: 'short' } : {}) })
}
/** The area levels wear the risk colours; NONE has no colour of its own. */
const LEVEL_COLOR: Record<AreaLevel, string> = {
  HIGH: RISK_COLOR.HIGH, MEDIUM: RISK_COLOR.MEDIUM, LOW: RISK_COLOR.LOW, NONE: RISK_COLOR.INFO,
}

export default function DroneAnalytics() {
  const [days, setDays] = useState(30)
  const [siteId, setSiteId] = useState('')
  const query = useMemo(() => {
    const today = new Date()
    return { from: isoDay(new Date(today.getTime() - (days - 1) * 86_400_000)), to: isoDay(today),
             site_id: siteId || undefined }
  }, [days, siteId])
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const overview = useQuery({ queryKey: ['drone-analytics', 'overview', query], queryFn: () => getAnalyticsOverview(query),
                              retry: false })
  const risk = useQuery({ queryKey: ['drone-analytics', 'risk-map', query], queryFn: () => getRiskMap(query), retry: false })
  const recs = useQuery({ queryKey: ['drone-analytics', 'recommendations', query],
                          queryFn: () => getRecommendations(query), retry: false })
  const o = overview.data

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Drone Analytics" subtitle="What the flights and their events add up to" />
      <DroneNav />
      <LicenceBanner />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <ToggleButtonGroup size="small" exclusive value={days} onChange={(_, v) => v && setDays(v)}>
            {PERIODS.map((p) => <ToggleButton key={p} value={p}>Last {p} days</ToggleButton>)}
          </ToggleButtonGroup>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 200 }}
                     slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">All sites</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          {o && <Typography variant="caption" color="text.secondary">
            {dayLabel(o.from, false)} – {dayLabel(o.to, false)}, whole days in {o.timezone}</Typography>}
        </Stack>
      </GlassCard>

      {overview.error ? <Alert severity="error" sx={{ mb: 2 }}>{apiError(overview.error)}</Alert>
        : !o ? <Skeleton height={320} sx={{ mb: 2 }} /> : (
          <>
            <GlassCard sx={{ p: 2, mb: 2 }}>
              <Grid container spacing={2}>
                {([
                  ['Flights', o.flights.total, `${(o.flights.flight_seconds / 3600).toFixed(1)} h in the air · ${formatDistance(o.flights.distance_m)}`],
                  ['Mission success', percent(o.flights.success_rate), `${o.flights.completed} of ${o.flights.due} flights completed`],
                  ['Events', o.events.total, o.events.per_flight != null ? `${o.events.per_flight} per flight` : 'No flights'],
                  ['Suspicious events', o.events.suspicious, 'Medium risk or above, not dismissed'],
                  ['False-positive rate', percent(o.events.false_positive_rate), `${o.events.false_positives} of ${o.events.total} events`],
                  ['Incident conversion', percent(o.events.incident_conversion_rate), `${o.events.with_incident} of ${o.events.total} events · ${o.events.incidents} incident(s)`],
                ] as [string, string | number, string][]).map(([label, value, sub]) => (
                  <Grid key={label} size={{ xs: 6, md: 4, lg: 2 }}><StatTile label={label} value={value} sub={sub} /></Grid>
                ))}
              </Grid>
            </GlassCard>

            <Grid container spacing={2} sx={{ mb: 2 }}>
              <Grid size={{ xs: 12, lg: 7 }}>
                <GlassCard sx={{ p: 2, height: '100%' }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Day by day</Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                    Three charts, each on its own scale.</Typography>
                  {/* Small multiples rather than one chart with two axes. */}
                  {([['Flights per day', 'flights', 'flights'], ['Events per day', 'events', 'events'],
                     ['Suspicious events per day', 'suspicious', 'suspicious events']] as const).map(([title, field, unit], n) => (
                    <Box key={field} sx={{ mb: n < 2 ? 1.5 : 0 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{title}</Typography>
                      <ColumnChart title={title} unit={unit} height={72}
                                   data={o.daily.map((d): Datum => ({ key: d.day, label: dayLabel(d.day), value: d[field] }))}
                                   axis={n === 2 ? (d, i) => (i === 0 || i === o.daily.length - 1
                                     || i === Math.floor(o.daily.length / 2) ? dayLabel(d.key, false) : null) : undefined} />
                    </Box>
                  ))}
                </GlassCard>
              </Grid>
              <Grid size={{ xs: 12, lg: 5 }}>
                <GlassCard sx={{ p: 2, mb: 2 }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Suspicious events by hour</Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                    Hour of the day in {o.timezone}. Shaded: night, 19:00 to 07:00.</Typography>
                  <ColumnChart title="Suspicious events by hour" unit="suspicious events"
                               data={o.suspicious_by_hour.map((h): Datum => ({
                                 key: String(h.hour), label: `${String(h.hour).padStart(2, '0')}:00–${String(h.hour).padStart(2, '0')}:59`,
                                 value: h.events, shaded: h.night }))}
                               axis={(d) => (Number(d.key) % 6 === 0 ? d.key.padStart(2, '0') : null)} />
                </GlassCard>
                <GlassCard sx={{ p: 2 }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Suspicious events by day of the week</Typography>
                  <ColumnChart title="Suspicious events by day of the week" unit="suspicious events" height={72}
                               data={o.suspicious_by_weekday.map((d): Datum => ({ key: String(d.weekday), label: d.name, value: d.events }))}
                               axis={(d) => d.label} />
                </GlassCard>
              </Grid>
            </Grid>

            <Grid container spacing={2} sx={{ mb: 2 }}>
              <Grid size={{ xs: 12, lg: 5 }}>
                <GlassCard sx={{ p: 2, height: '100%' }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Top detection types</Typography>
                  <BarList unit="events" note="No events in this period."
                           data={o.detection_types.map((t) => ({
                             key: t.module_type, name: t.name, value: t.events,
                             note: t.false_positives ? `${percent(t.false_positive_rate)} false positive` : undefined }))} />
                  <Typography variant="subtitle2" sx={{ mt: 2, mb: 0.5 }}>Events by risk level</Typography>
                  <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap' }}>
                    {(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'] as RiskLevel[]).map((l) => (
                      <RiskChip key={l} level={l} score={o.events.by_risk[l] ?? 0} />))}
                  </Stack>
                  {!!o.flights.did_not_complete.length && (
                    <>
                      <Typography variant="subtitle2" sx={{ mt: 2, mb: 0.5 }}>Why flights did not complete</Typography>
                      {o.flights.did_not_complete.map((r) => (
                        <Typography key={`${r.status}-${r.reason}`} variant="body2" color="text.secondary">
                          {r.flights}× {r.status.toLowerCase()} — {r.reason}</Typography>))}
                    </>
                  )}
                </GlassCard>
              </Grid>
              <Grid size={{ xs: 12, lg: 7 }}>
                <GlassCard sx={{ p: 2, height: '100%' }}>
                  <GroupTable title="By mission" rows={o.missions} empty="No missions flew in this period." />
                  <Box sx={{ height: 16 }} />
                  <GroupTable title="By drone" rows={o.drones} empty="No drones flew in this period." />
                </GlassCard>
              </Grid>
            </Grid>
          </>
        )}

      <RiskMapSection data={risk.data} error={risk.error} siteId={siteId} />

      <GlassCard sx={{ p: 2 }}>
        <Stack direction="row" sx={{ gap: 1, alignItems: 'center', mb: 0.5 }}>
          <AutoAwesomeIcon fontSize="small" color="primary" />
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Recommendations</Typography>
        </Stack>
        {recs.error ? <Alert severity="error">{apiError(recs.error)}</Alert> : !recs.data ? <Skeleton height={80} /> : (
          <>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
              {recs.data.disclaimer}</Typography>
            {!recs.data.recommendations.length ? (
              <Typography variant="body2" color="text.secondary">
                Nothing in this period crosses the thresholds for a suggestion.</Typography>
            ) : recs.data.recommendations.map((r, i) => (
              <Box key={`${r.code}-${i}`} sx={{ py: 1.25, borderTop: i ? '1px solid' : 'none', borderColor: 'divider' }}>
                <Stack direction="row" sx={{ gap: 1, alignItems: 'center', mb: 0.5, flexWrap: 'wrap' }}>
                  <Typography variant="body2" sx={{ fontWeight: 700 }}>{r.subject}</Typography>
                  <Chip size="small" variant="outlined" label="System-generated" />
                </Stack>
                <Typography variant="body2">{r.observation}</Typography>
                <Typography variant="body2" color="text.secondary">Suggested: {r.suggestion}</Typography>
              </Box>
            ))}
          </>
        )}
      </GlassCard>
    </Box>
  )
}

function GroupTable({ title, rows, empty }: { title: string; rows: AnalyticsGroup[]; empty: string }) {
  return (
    <>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>{title}</Typography>
      {!rows.length ? <Typography variant="body2" color="text.secondary">{empty}</Typography> : (
        <TableContainer>
          <Table size="small">
            <TableHead><TableRow><TableCell>Name</TableCell><TableCell align="right">Flights</TableCell>
              <TableCell align="right">Completed</TableCell><TableCell align="right">Success</TableCell>
              <TableCell align="right">Events</TableCell><TableCell align="right">Suspicious</TableCell></TableRow></TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.name}>
                  <TableCell>{r.name}</TableCell>
                  <TableCell align="right">{r.flights}</TableCell>
                  <TableCell align="right">{r.completed}</TableCell>
                  <TableCell align="right">{percent(r.success_rate)}</TableCell>
                  <TableCell align="right">{r.events}</TableCell>
                  <TableCell align="right">{r.suspicious}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </>
  )
}

// ── The risk map ─────────────────────────────────────────────────────────────

function AreaLevelChip({ area }: { area: RiskArea }) {
  const color = LEVEL_COLOR[area.level]
  return <Chip size="small" label={`${area.level} · ${area.score}`}
               sx={{ bgcolor: `${color}22`, color: 'text.primary', border: `1px solid ${color}`, fontWeight: 700 }} />
}

function RiskMapSection({ data, error, siteId }: { data: RiskMap | undefined; error: unknown; siteId: string }) {
  const { data: zones } = useQuery({ queryKey: ['drone-zones', siteId], queryFn: () => listZones(siteId), enabled: !!siteId })
  const byZone = useMemo(() => new Map((data?.areas ?? []).filter((a) => a.zone_id).map((a) => [a.zone_id!, a])), [data])
  const fit = useMemo(() => {
    const pts: [number, number][] = (data?.hot_spots ?? []).map((h) => [h.latitude, h.longitude])
    ;(zones ?? []).forEach((z) => {
      if (z.center_latitude != null && z.center_longitude != null) pts.push([z.center_latitude, z.center_longitude])
      ;(z.polygon ?? []).forEach((p) => pts.push([p.lat, p.lng]))
    })
    return pts
  }, [data, zones])
  const most = Math.max(1, ...(data?.hot_spots ?? []).map((h) => h.events))
  return (
    <GlassCard sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Risk map</Typography>
      {error ? <Alert severity="error" sx={{ mt: 1 }}>{apiError(error)}</Alert> : !data ? <Skeleton height={200} /> : (
        <>
          <Alert severity="info" icon={false} sx={{ my: 1 }}>{data.disclaimer}</Alert>
          <Grid container spacing={2}>
            <Grid size={{ xs: 12, lg: 7 }}>
              {!siteId ? (
                <Alert severity="info">Choose a site above to see its areas and hot spots on the map. The table
                  ranks every area across {data.scope}.</Alert>
              ) : (
                <>
                  <DroneMap height={380} scrollZoom={false}>
                    {(zones ?? []).filter((z) => z.is_active).map((z) => {
                      const area = byZone.get(z.id)
                      const color = area ? LEVEL_COLOR[area.level] : LEVEL_COLOR.NONE
                      // On hover, not pinned: a pinned label on every zone covers the hot spots.
                      const label = <MapTooltip sticky>
                        {z.name}{area ? ` · ${area.level} ${area.score}` : ' · no events'}</MapTooltip>
                      const style = { color, weight: 2, fillOpacity: area ? 0.25 : 0.05 }
                      if (z.shape === 'CIRCLE' && z.center_latitude != null && z.center_longitude != null && z.radius_m) {
                        return <Circle key={z.id} center={[z.center_latitude, z.center_longitude]} radius={z.radius_m}
                                       pathOptions={style}>{label}</Circle>
                      }
                      const pts = (z.polygon ?? []).map((p) => [p.lat, p.lng] as [number, number])
                      return pts.length >= 3 ? <Polygon key={z.id} positions={pts} pathOptions={style}>{label}</Polygon> : null
                    })}
                    {data.hot_spots.map((h) => (
                      <CircleMarker key={`${h.latitude},${h.longitude}`} center={[h.latitude, h.longitude]}
                                    radius={6 + 12 * Math.sqrt(h.events / most)}
                                    pathOptions={{ color: '#ffffff', weight: 2, fillColor: '#6C63FF', fillOpacity: 0.65 }}>
                        <MapTooltip>{h.events} event(s) here, {h.suspicious} suspicious</MapTooltip>
                      </CircleMarker>
                    ))}
                    <FitTo points={fit} />
                  </DroneMap>
                  <Stack direction="row" sx={{ gap: 2, mt: 1, flexWrap: 'wrap', alignItems: 'center' }}>
                    {(['HIGH', 'MEDIUM', 'LOW', 'NONE'] as AreaLevel[]).map((l) => (
                      <Stack key={l} direction="row" sx={{ gap: 0.75, alignItems: 'center' }}>
                        <Box sx={{ width: 14, height: 14, borderRadius: '3px', border: `2px solid ${LEVEL_COLOR[l]}`,
                                   bgcolor: `${LEVEL_COLOR[l]}40` }} />
                        <Typography variant="caption">{l === 'NONE' ? 'Zone with no events' : `${l} zone`}</Typography>
                      </Stack>
                    ))}
                    <Stack direction="row" sx={{ gap: 0.75, alignItems: 'center' }}>
                      <Box sx={{ width: 14, height: 14, borderRadius: '50%', border: '2px solid #fff', bgcolor: '#6C63FF' }} />
                      <Typography variant="caption">Hot spot</Typography>
                    </Stack>
                  </Stack>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                    Point at a zone for its name and score; the table lists them all. A hot spot is events within about{' '}
                    {data.method.cell_metres} m of each other, larger where there were more; false positives are left out.
                  </Typography>
                </>
              )}
              {!!data.repeated_intrusion_locations.length && (
                <Box sx={{ mt: 1.5 }}>
                  <Typography variant="subtitle2">Repeated intrusion locations</Typography>
                  {data.repeated_intrusion_locations.map((r) => (
                    <Typography key={`${r.latitude},${r.longitude}`} variant="body2" color="text.secondary">
                      {r.events} times on {r.days} day(s){r.zone_name ? ` near ${r.zone_name}` : ''}
                      {r.site_name ? `, ${r.site_name}` : ''} — {r.at_night} at night, last {fmt(r.last_seen_at)}
                    </Typography>
                  ))}
                </Box>
              )}
            </Grid>
            <Grid size={{ xs: 12, lg: 5 }}>
              {!data.areas.length ? <Typography variant="body2" color="text.secondary">No events in this period.</Typography> : (
                <TableContainer>
                  <Table size="small">
                    <TableHead><TableRow><TableCell>Area</TableCell><TableCell>Analytical level</TableCell>
                      <TableCell align="right">Events</TableCell><TableCell align="right">Suspicious</TableCell>
                      <TableCell align="right">At night</TableCell></TableRow></TableHead>
                    <TableBody>
                      {data.areas.map((a) => (
                        <TableRow key={`${a.site_id}-${a.area}`}>
                          <TableCell>{a.area}
                            {a.site_name && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                              {a.site_name}</Typography>}</TableCell>
                          <TableCell><AreaLevelChip area={a} /></TableCell>
                          <TableCell align="right">{a.events}</TableCell>
                          <TableCell align="right">{a.suspicious}</TableCell>
                          <TableCell align="right">{a.night_suspicious}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              )}
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                How the score is made: {data.method.description}</Typography>
            </Grid>
          </Grid>
        </>
      )}
    </GlassCard>
  )
}

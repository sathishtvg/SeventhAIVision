import { useState, useEffect } from 'react'
import {
  Box, Grid, Typography, Skeleton, LinearProgress, Chip,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  ToggleButton, ToggleButtonGroup,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import {
  getSummary, getAlertsBySeverity, getAlertsByModule,
  getDetectionsTrend, getAlertsTrend, getTopCameras, getIncidentResolutionTime,
} from '@/api/analytics'
import type { AnalyticsCount, AnalyticsTrendPoint } from '@/types/api'
import { fadeUpSx, useCountUp } from '@/lib/motion'
import { PageHeader } from '@/components/common/PageHeader'

// ──────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────

function StatCard({ label, value, color = 'primary.main', sub }: {
  label: string
  value: number | undefined | null
  color?: string
  /** Small line under the number — trend, or why the number is what it is. */
  sub?: React.ReactNode
}) {
  const animatedValue = useCountUp(value ?? undefined)
  return (
    <GlassCard sx={{ p: 3, height: '100%' }}>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 1 }}>
        {label}
      </Typography>
      {value === undefined ? (
        <Skeleton width={60} height={48} />
      ) : (
        <Typography variant="h3" sx={{ fontWeight: 700, color, mt: 0.5 }}>
          {value === null ? '—' : animatedValue}
        </Typography>
      )}
      {sub != null && <Box sx={{ mt: 0.5 }}>{sub}</Box>}
    </GlassCard>
  )
}

/** "8 days ago" / "3h ago". Returns null when there is no timestamp at all. */
function relativeAge(iso: string | null | undefined): { text: string; days: number } | null {
  if (!iso) return null
  const ms = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(ms / 60000)
  const days = ms / 86_400_000
  if (mins < 1) return { text: 'just now', days }
  if (mins < 60) return { text: `${mins}m ago`, days }
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return { text: `${hrs}h ago`, days }
  const d = Math.floor(hrs / 24)
  return { text: `${d} day${d === 1 ? '' : 's'} ago`, days }
}

/** Period-on-period change, rendered as a coloured delta. Rising alerts are
 *  not "good", so this deliberately does not colour by direction — it colours
 *  by magnitude of change and lets the operator judge. */
function TrendDelta({ current, previous, days }: { current?: number; previous?: number; days: number }) {
  if (current == null || previous == null) return null
  if (previous === 0 && current === 0) {
    return <Typography variant="caption" color="text.disabled">no activity either period</Typography>
  }
  if (previous === 0) {
    return <Typography variant="caption" color="text.secondary">new vs. previous {days}d</Typography>
  }
  const pct = Math.round(((current - previous) / previous) * 100)
  const arrow = pct > 0 ? '▲' : pct < 0 ? '▼' : '—'
  return (
    <Typography variant="caption" color="text.secondary">
      {arrow} {Math.abs(pct)}% vs. previous {days}d
    </Typography>
  )
}

function HorizontalBar({ items, total, colorMap }: {
  items: AnalyticsCount[]
  total: number
  colorMap?: Record<string, string>
}) {
  // Bars grow in from 0 on mount instead of snapping straight to their final
  // width — starts at 0% for one frame, then the real value kicks in the
  // MuiLinearProgress-bar transform transition below. setTimeout, not rAF:
  // this only needs to flip a boolean once after the initial paint, and
  // browsers suspend rAF much more aggressively than timers in background tabs.
  const [grown, setGrown] = useState(false)
  useEffect(() => {
    const id = setTimeout(() => setGrown(true), 20)
    return () => clearTimeout(id)
  }, [])

  if (!items.length) return <Typography color="text.secondary" variant="body2">No data</Typography>
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      {items.map((item, i) => (
        <Box key={item.label}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.25 }}>
            <Typography variant="caption" sx={{ textTransform: 'capitalize' }}>{item.label.replace('_', ' ')}</Typography>
            <Typography variant="caption" color="text.secondary">{item.count}</Typography>
          </Box>
          <LinearProgress
            variant="determinate"
            value={grown && total > 0 ? (item.count / total) * 100 : 0}
            sx={{
              height: 8,
              borderRadius: 4,
              '& .MuiLinearProgress-bar': {
                backgroundColor: colorMap?.[item.label] ?? 'primary.main',
                borderRadius: 4,
                transitionProperty: 'transform',
                transitionDuration: '0.7s',
                transitionTimingFunction: 'cubic-bezier(0.16, 1, 0.3, 1)',
                transitionDelay: `${Math.min(i, 8) * 50}ms`,
              },
              backgroundColor: 'rgba(255,255,255,0.08)',
            }}
          />
        </Box>
      ))}
    </Box>
  )
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#FF4560',
  high: '#FF9800',
  medium: '#FFC107',
  low: '#00E396',
  info: '#6C63FF',
}

// Inline sparkline-style trend using CSS boxes
function TrendBars({ data, color = '#6C63FF' }: { data: AnalyticsTrendPoint[]; color?: string }) {
  if (!data.length) return <Typography color="text.secondary" variant="body2">No data</Typography>
  const maxCount = Math.max(...data.map((d) => d.count), 1)
  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-end', gap: 0.5, height: 80, mt: 1 }}>
      {data.map((d) => (
        <Box key={d.day} sx={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0.5 }}>
          <Box
            sx={{
              width: '100%',
              height: `${(d.count / maxCount) * 64}px`,
              minHeight: d.count > 0 ? 4 : 0,
              backgroundColor: color,
              borderRadius: '2px 2px 0 0',
              opacity: 0.85,
            }}
          />
          <Typography variant="caption" sx={{ fontSize: '0.6rem', color: 'text.secondary', writingMode: 'initial' }}>
            {new Date(d.day).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
          </Typography>
        </Box>
      ))}
    </Box>
  )
}

// ──────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────

export default function Analytics() {
  const [window, setWindow] = useState<'7' | '30' | '90'>('30')
  const days = parseInt(window, 10)

  const { data: summary } = useQuery({
    queryKey: ['analytics-summary', days],
    queryFn: () => getSummary(undefined, days),
  })
  const { data: bySeverity } = useQuery({ queryKey: ['analytics-by-severity', days], queryFn: () => getAlertsBySeverity(days) })
  const { data: byModule } = useQuery({ queryKey: ['analytics-by-module', days], queryFn: () => getAlertsByModule(days) })
  // Pinned to 7 before, so selecting 30/90 left these two charts showing a
  // week — and reading "No data" whenever the last week happened to be quiet.
  const { data: detTrend } = useQuery({ queryKey: ['analytics-det-trend', days], queryFn: () => getDetectionsTrend(days) })
  const { data: alertTrend } = useQuery({ queryKey: ['analytics-alert-trend', days], queryFn: () => getAlertsTrend(days) })
  const { data: topCams } = useQuery({ queryKey: ['analytics-top-cameras', days], queryFn: () => getTopCameras(days, 8) })
  const { data: resolutionTime } = useQuery({ queryKey: ['analytics-resolution', days], queryFn: () => getIncidentResolutionTime(days) })

  const detAge = relativeAge(summary?.last_detection_at)
  const alertAge = relativeAge(summary?.last_alert_at)
  // One day of silence on a 24/7 surveillance platform is not a quiet day,
  // it is something to look at. This is the threshold the banner uses.
  const pipelineStale = detAge != null && detAge.days > 1

  const totalBySeverity = bySeverity?.reduce((s, i) => s + i.count, 0) ?? 0
  const totalByModule = byModule?.reduce((s, i) => s + i.count, 0) ?? 0
  const maxCamAlerts = topCams?.length ? topCams[0].alert_count : 1

  return (
    <Box>
      <PageHeader pageKey="analytics" />
      {/* Data freshness. Without this a screen full of zeros is ambiguous —
          a genuinely quiet period and a dead ingestion pipeline look the
          same, and the second one is an outage nobody is being told about. */}
      {pipelineStale && (
        <GlassCard sx={{ p: 2, mb: 3, borderLeft: '3px solid', borderColor: 'warning.main' }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            No detections for {detAge!.text.replace(' ago', '')} — today's counters read 0 because
            nothing has arrived, not because the site was quiet.
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Last detection {detAge!.text}
            {alertAge ? ` · last alert ${alertAge.text}` : ''}
            {summary?.active_cameras != null && summary?.active_cameras_total != null
              ? ` · ${summary.active_cameras} of ${summary.active_cameras_total} cameras active`
              : ''}
            . Check the ingestion service and AI workers if this is unexpected.
          </Typography>
        </GlassCard>
      )}

      {/* Time window selector */}
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 3 }}>
        <ToggleButtonGroup
          value={window}
          exclusive
          onChange={(_, v) => { if (v) setWindow(v) }}
          size="small"
        >
          <ToggleButton value="7">7 days</ToggleButton>
          <ToggleButton value="30">30 days</ToggleButton>
          <ToggleButton value="90">90 days</ToggleButton>
        </ToggleButtonGroup>
      </Box>

      {/* KPI summary row */}
      <Grid container spacing={3} sx={{ mb: 3 }}>
        <Grid size={{ xs: 12, sm: 6, md: 3 }} sx={fadeUpSx(0)}>
          <StatCard
            label="Detections Today" value={summary?.detections_today} color="#6C63FF"
            sub={detAge && (
              <Typography variant="caption" color={detAge.days > 1 ? 'warning.main' : 'text.secondary'}>
                last detection {detAge.text}
              </Typography>
            )}
          />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }} sx={fadeUpSx(1)}>
          <StatCard
            label="Alerts Today" value={summary?.alerts_today} color="#FF4560"
            sub={alertAge && (
              <Typography variant="caption" color={alertAge.days > 1 ? 'warning.main' : 'text.secondary'}>
                last alert {alertAge.text}
              </Typography>
            )}
          />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }} sx={fadeUpSx(2)}>
          <StatCard
            label={`Detections (${window}d)`} value={summary?.detections_window} color="#00D9C0"
            sub={<TrendDelta current={summary?.detections_window} previous={summary?.detections_window_prev} days={days} />}
          />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }} sx={fadeUpSx(3)}>
          <StatCard
            label={`Alerts (${window}d)`} value={summary?.alerts_window} color="#FF9800"
            sub={<TrendDelta current={summary?.alerts_window} previous={summary?.alerts_window_prev} days={days} />}
          />
        </Grid>
      </Grid>

      {/* Trend charts */}
      <Grid container spacing={3} sx={{ mb: 3 }}>
        <Grid size={{ xs: 12, md: 6 }}>
          <GlassCard sx={{ p: 3 }}>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>Detections (last {days} days)</Typography>
            {!detTrend ? <Skeleton height={80} /> : <TrendBars data={detTrend} color="#6C63FF" />}
          </GlassCard>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <GlassCard sx={{ p: 3 }}>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>Alerts (last {days} days)</Typography>
            {!alertTrend ? <Skeleton height={80} /> : <TrendBars data={alertTrend} color="#FF4560" />}
          </GlassCard>
        </Grid>
      </Grid>

      {/* Breakdowns + top cameras */}
      <Grid container spacing={3} sx={{ mb: 3 }}>
        <Grid size={{ xs: 12, md: 4 }}>
          <GlassCard sx={{ p: 3, height: '100%' }}>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>Alerts by Severity ({window}d)</Typography>
            {!bySeverity
              ? <Skeleton height={120} />
              : <HorizontalBar items={bySeverity} total={totalBySeverity} colorMap={SEVERITY_COLORS} />}
          </GlassCard>
        </Grid>

        <Grid size={{ xs: 12, md: 4 }}>
          <GlassCard sx={{ p: 3, height: '100%' }}>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>Alerts by Module ({window}d)</Typography>
            {!byModule
              ? <Skeleton height={120} />
              : <HorizontalBar items={byModule} total={totalByModule} />}
          </GlassCard>
        </Grid>

        <Grid size={{ xs: 12, md: 4 }}>
          <GlassCard sx={{ p: 3, height: '100%' }}>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>Incident Resolution ({window}d)</Typography>
            {!resolutionTime ? (
              <Skeleton height={120} />
            ) : (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
                <Box>
                  <Typography variant="caption" color="text.secondary">Resolved Incidents</Typography>
                  <Typography variant="h4" sx={{ fontWeight: 700, color: '#00E396' }}>{resolutionTime.resolved_count}</Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">Avg Resolution Time</Typography>
                  <Typography variant="h5" sx={{ fontWeight: 700 }}>
                    {resolutionTime.avg_hours != null ? `${resolutionTime.avg_hours}h` : '—'}
                  </Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">P95 Resolution Time</Typography>
                  <Typography variant="h5" sx={{ fontWeight: 700 }}>
                    {resolutionTime.p95_hours != null ? `${resolutionTime.p95_hours}h` : '—'}
                  </Typography>
                </Box>
              </Box>
            )}
          </GlassCard>
        </Grid>
      </Grid>

      {/* Top cameras */}
      <GlassCard sx={{ p: 3 }}>
        <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>Top Cameras by Alerts ({window}d)</Typography>
        {!topCams ? (
          <Skeleton height={160} />
        ) : topCams.length === 0 ? (
          <Typography color="text.secondary" variant="body2">No alert data</Typography>
        ) : (
          <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Camera</TableCell>
                  <TableCell>Alerts</TableCell>
                  <TableCell sx={{ minWidth: 180 }}>Share</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {topCams.map((cam, i) => (
                  <TableRow key={cam.camera_id} hover>
                    <TableCell>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Chip label={`#${i + 1}`} size="small" sx={{ minWidth: 32 }} />
                        <Box>
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>
                            {cam.camera_name ?? 'Unknown'}
                          </Typography>
                          <Typography variant="caption" sx={{ fontFamily: 'monospace', color: 'text.secondary' }}>
                            {cam.camera_id.slice(0, 8)}…
                          </Typography>
                        </Box>
                      </Box>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 700, color: '#FF4560' }}>
                        {cam.alert_count}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <LinearProgress
                          variant="determinate"
                          value={(cam.alert_count / maxCamAlerts) * 100}
                          sx={{
                            flex: 1, height: 6, borderRadius: 3,
                            backgroundColor: 'rgba(255,255,255,0.08)',
                            '& .MuiLinearProgress-bar': { backgroundColor: '#FF4560', borderRadius: 3 },
                          }}
                        />
                        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 32 }}>
                          {maxCamAlerts > 0 ? `${Math.round((cam.alert_count / maxCamAlerts) * 100)}%` : '—'}
                        </Typography>
                      </Box>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>
    </Box>
  )
}

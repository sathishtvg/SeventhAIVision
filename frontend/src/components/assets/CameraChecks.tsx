/**
 * A camera's checks and recording gaps on the device health page (Phase 6 of the known limits, 4 November 2026).
 *
 *   CheckLine         a camera's latest check in one line, a figure over its limit marked and an old check greyed
 *   CameraChecks      a camera opened: its frame rate over 7 days against its limit, the checks that broke a limit or
 *                     could not run, and its recording gaps
 *   CheckLimitsButton the organisation's limits, changed by whoever may change its settings
 *
 * Every figure is the server's: this draws and words them and works nothing out but the chart's scale.
 */
import { useMemo, useState } from 'react'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Skeleton, TextField, Typography,
} from '@mui/material'
import { useTheme } from '@mui/material/styles'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { usePermission } from '@/hooks/usePermission'
import { apiError } from '@/api/securityAssets'
import { getCameraWeek, getCheckLimits, LIMIT_KEYS, setCheckLimits } from '@/api/cameraChecks'
import type { Check, CheckedCamera, CameraWeek, Gap, LimitKey } from '@/api/cameraChecks'
import { fmt } from './assetFormat'
import { checkParts, length } from './cameraCheckFormat'
import { ErrorState } from '@/components/states'

/** A camera's latest check, a line per stream, and its recording gaps in 24 hours. */
export function CheckLine({ camera, now }: { camera: CheckedCamera | undefined; now?: Date }) {
  if (!camera) return null
  const at = now ?? new Date()
  const g = camera.gaps_24h
  const several = camera.streams.length > 1
  return (
    <Box data-testid="check-line" sx={{ mt: 0.25 }}>
      {camera.streams.map((s, n) => {
        const broke = new Set((s.check?.breaks ?? []).map((b) => b.limit))
        const old = !!s.check?.stale
        return (
          <Typography key={s.stream_id} variant="caption" component="div" data-testid="check-stream"
                      sx={{ color: old ? 'text.disabled' : 'text.secondary' }}>
            {several ? `Stream ${n + 1}: ` : ''}
            {s.not_checked ?? (!s.check ? 'Not checked yet' : checkParts(s.check, at).map((p, i) => (
              <span key={p.key + i}>
                {i ? ' · ' : ''}
                <Box component="span" data-broke={broke.has(p.key as LimitKey) || undefined}
                     sx={broke.has(p.key as LimitKey) && !old ? { color: 'warning.main', fontWeight: 700 } : undefined}>
                  {p.text}
                </Box>
              </span>)))}
            {old ? ' (old: not gone by)' : ''}
          </Typography>)
      })}
      {g.gaps > 0 && (
        <Typography variant="caption" component="div" data-testid="gap-line" sx={{ color: 'warning.main', fontWeight: 600 }}>
          {g.open ? 'Not recording now, for longer than its limit'
            : `${g.gaps} recording ${g.gaps === 1 ? 'gap' : 'gaps'} over its limit in 24 hours${g.longest_s ? `, the longest ${length(g.longest_s)}` : ''}`}
        </Typography>)}
    </Box>
  )
}

// ─── A camera opened ────────────────────────────────────────────────────────

const W = 560
const H = 150
const PAD = { left: 34, right: 10, top: 10, bottom: 22 }

function RateChart({ checks, from, to, leastPct, label }: {
  checks: Check[]; from: number; to: number; leastPct: number; label: string
}) {
  const theme = useTheme()
  const ran = checks.filter((c) => c.result === 'OK' && c.received_rate !== null)
  const top = Math.max(1, ...ran.map((c) => Math.max(c.received_rate ?? 0, c.stated_rate ?? 0))) * 1.15
  const x = (iso: string) => PAD.left + ((new Date(iso).getTime() - from) / Math.max(1, to - from)) * (W - PAD.left - PAD.right)
  const y = (v: number) => H - PAD.bottom - (v / top) * (H - PAD.top - PAD.bottom)
  const line = ran.map((c) => `${x(c.checked_at).toFixed(1)},${y(c.received_rate ?? 0).toFixed(1)}`).join(' ')
  const limit = ran.filter((c) => c.stated_rate !== null)
    .map((c) => `${x(c.checked_at).toFixed(1)},${y((c.stated_rate ?? 0) * leastPct / 100).toFixed(1)}`).join(' ')
  const failed = checks.filter((c) => c.result !== 'OK')
  const ticks = [0, Math.round(top / 2), Math.round(top / 1.15)]
  const days = Math.max(1, Math.round((to - from) / 86_400_000))
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Box component="svg" viewBox={`0 0 ${W} ${H}`} role="img" data-testid="rate-chart"
           aria-label={`${label}: frame rate of each check over ${days} days, against its limit`}
           sx={{ width: '100%', height: 'auto', display: 'block' }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke={theme.palette.divider} strokeWidth={1} />
            <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" fontSize={10} fill={theme.palette.text.secondary}>{t}</text>
          </g>))}
        <text x={PAD.left} y={H - 6} fontSize={10} fill={theme.palette.text.secondary}>{days} days ago</text>
        <text x={W - PAD.right} y={H - 6} fontSize={10} textAnchor="end" fill={theme.palette.text.secondary}>now</text>
        {limit && <polyline points={limit} fill="none" stroke={theme.palette.text.secondary} strokeWidth={1.5}
                            strokeDasharray="4 3" />}
        {line && <polyline points={line} fill="none" stroke={theme.palette.primary.main} strokeWidth={2}
                           strokeLinejoin="round" />}
        {ran.map((c) => (
          <g key={c.checked_at}>
            {/* A mark only where a limit was broken: a week of checks is too many to mark each. */}
            {c.breaks.length > 0 && (
              <circle cx={x(c.checked_at)} cy={y(c.received_rate ?? 0)} r={3.5} fill={theme.palette.warning.main}
                      stroke={theme.palette.background.paper} strokeWidth={1} />)}
            <circle cx={x(c.checked_at)} cy={y(c.received_rate ?? 0)} r={8} fill="transparent">
              <title>{`${fmt(c.checked_at)} · ${checkParts(c).slice(0, -1).map((p) => p.text).join(' · ')}`}</title>
            </circle>
          </g>))}
        {failed.map((c) => (
          <g key={c.checked_at}>
            <line x1={x(c.checked_at)} x2={x(c.checked_at)} y1={H - PAD.bottom} y2={H - PAD.bottom - 8}
                  stroke={theme.palette.error.main} strokeWidth={2} />
            <rect x={x(c.checked_at) - 5} y={H - PAD.bottom - 12} width={10} height={14} fill="transparent">
              <title>{`${fmt(c.checked_at)} · ${c.result_text}`}</title>
            </rect>
          </g>))}
        {limit && (
          <text x={PAD.left + 4} y={y((ran[0].stated_rate ?? 0) * leastPct / 100) - 4} fontSize={10}
                fill={theme.palette.text.secondary}>limit</text>)}
      </Box>
    </Box>
  )
}

function gapLine(g: Gap): string {
  const how = g.ended_at ? length(g.seconds) : `${length(g.seconds)} so far, still going on`
  return `${fmt(g.started_at)} · ${how} · ${g.kind_text}${g.camera_was_offline ? ' · the camera was offline then' : ''}`
}

export function CameraChecks({ cameraId }: { cameraId: string }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['camera-checks', cameraId], queryFn: () => getCameraWeek(cameraId) })
  const shown = useMemo(() => (data ? group(data) : []), [data])
  if (isLoading) return <Skeleton height={120} />
  if (error) return <ErrorState compact error={error} onRetry={refetch} />
  if (!data) return null
  const to = new Date(data.as_of).getTime()
  const from = to - data.days * 86_400_000
  const bad = data.checks.filter((c) => c.result !== 'OK' || c.breaks.length).slice(-20).reverse()
  return (
    <Stack sx={{ gap: 1 }} data-testid="camera-checks">
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Checks in the last {data.days} days</Typography>
      {!data.checks.length ? (
        <Typography variant="body2" color="text.secondary">Not checked yet: each stream is checked every 15 minutes.</Typography>
      ) : shown.map(([streamId, checks], n) => (
        <RateChart key={streamId} checks={checks} from={from} to={to} leastPct={data.limits.least_rate_pct}
                   label={shown.length > 1 ? `Stream ${n + 1}` : 'Frame rate'} />))}
      {bad.length > 0 && (
        <Box data-testid="bad-checks">
          <Typography variant="caption" color="text.secondary">Checks that broke a limit or could not run, newest first</Typography>
          {bad.map((c) => (
            <Typography key={c.checked_at + c.stream_id} variant="body2" data-testid="bad-check">
              {fmt(c.checked_at)} · {c.result === 'OK' ? c.breaks.map((b) => b.text).join('; ') : c.result_text}
            </Typography>))}
        </Box>)}
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Recording gaps</Typography>
      {!data.gaps.length ? (
        <Typography variant="body2" color="text.secondary">
          No gap in the last {data.days} days, in what the platform records of it.
        </Typography>
      ) : data.gaps.map((g) => (
        <Typography key={g.started_at + g.stream_id} variant="body2" data-testid="gap"
                    sx={{ color: g.over_limit ? 'warning.main' : undefined }}>{gapLine(g)}</Typography>))}
      <Typography variant="caption" color="text.secondary">{data.note}</Typography>
    </Stack>
  )
}

function group(data: CameraWeek): [string, Check[]][] {
  const by = new Map<string, Check[]>()
  for (const c of data.checks) by.set(c.stream_id, [...(by.get(c.stream_id) ?? []), c])
  return [...by.entries()]
}

// ─── The limits ─────────────────────────────────────────────────────────────

export function CheckLimitsButton() {
  const may = usePermission('settings:write')
  const [open, setOpen] = useState(false)
  if (!may) return null
  return (
    <>
      <Button size="small" variant="outlined" onClick={() => setOpen(true)}>Check limits</Button>
      {open && <LimitsDialog onClose={() => setOpen(false)} />}
    </>
  )
}

function LimitsDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ['camera-check-limits'], queryFn: getCheckLimits })
  const [typed, setTyped] = useState<Partial<Record<LimitKey, string>>>({})
  const value = (k: LimitKey) => typed[k] ?? (data ? String(data[k]) : '')
  const save = useMutation({
    mutationFn: () => setCheckLimits(Object.fromEntries(LIMIT_KEYS.map((k) => [k, Number(value(k))])) as Record<LimitKey, number>),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['camera-check-limits'] })
      qc.invalidateQueries({ queryKey: ['camera-checks'] })
      onClose()
    },
  })
  const wrong = data ? LIMIT_KEYS.filter((k) => {
    const n = Number(value(k))
    return value(k) === '' || Number.isNaN(n) || n < data.ranges[k].least || n > data.ranges[k].most
  }) : []
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Camera check limits</DialogTitle>
      <DialogContent>
        {isLoading && <Skeleton height={200} />}
        {!!error && <ErrorState compact error={error} onRetry={refetch} />}
        {data && (
          <Stack sx={{ gap: 1.5, mt: 0.5 }}>
            <Typography variant="body2" color="text.secondary">
              A camera whose latest check breaks one of these, or that has a recording gap longer than its limit in
              24 hours, reads Degraded. Nobody is sent a message. A check is judged by the limits when it is made.
            </Typography>
            {LIMIT_KEYS.map((k) => (
              <TextField key={k} size="small" type="number" label={data.labels[k]} value={value(k)}
                         onChange={(e) => setTyped((t) => ({ ...t, [k]: e.target.value }))}
                         error={wrong.includes(k)} disabled={!data.may_set}
                         helperText={`${data.ranges[k].least} to ${data.ranges[k].most}; ${data.defaults[k]} to begin with`}
                         slotProps={{ htmlInput: { 'aria-label': data.labels[k], step: k === 'most_missed_pct' ? 0.1 : 1 } }} />))}
            <Typography variant="caption" color="text.secondary">
              {data.updated_at ? `Last changed by ${data.updated_by_name ?? 'somebody no longer here'}, ${fmt(data.updated_at)}`
                : 'The limits every organisation begins with.'}
            </Typography>
            {!data.may_set && <Alert severity="info">Whoever may change the organisation's settings changes these.</Alert>}
            {save.isError && <Alert severity="error">{apiError(save.error)}</Alert>}
          </Stack>)}
      </DialogContent>
      <DialogActions>
        {data?.may_set && (
          <Button onClick={() => setTyped(Object.fromEntries(LIMIT_KEYS.map((k) => [k, String(data.defaults[k])])))}>
            The defaults
          </Button>)}
        <Button onClick={onClose}>Close</Button>
        {data?.may_set && (
          <Button variant="contained" disabled={!!wrong.length || save.isPending} onClick={() => save.mutate()}>Save</Button>)}
      </DialogActions>
    </Dialog>
  )
}

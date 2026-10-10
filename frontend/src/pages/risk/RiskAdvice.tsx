/**
 * Risk and advice: where what was recorded gathers — by hour, day, place and
 * week — and what stands out, with a person's answer to it.
 *
 * Every number is a count over the period chosen. Nothing here is a forecast,
 * and the server's note saying so is shown above the advice. "Confidence" is
 * how much history a statement rests on; the chip says that in those words.
 *
 * Advice is answered for one site, by somebody who may: accepted, or not
 * accepted and why. An answer changes nothing else.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, FormControlLabel, MenuItem, Skeleton, Switch, TextField, Typography,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { getSites } from '@/api/sites'
import { answerAdvice, apiError, getAdvice, getAnswers, getPatterns } from '@/api/securityAdvice'
import type { Finding, PatternSource, Source } from '@/api/securityAdvice'
import {
  ANSWER_LABEL, LEVEL_LABEL, STEPS, WEEK_CHOICES, answerLine, cellTitle, fmt, peak, step, stepRange, totalLine,
  weeksLabel,
} from '@/components/risk/riskFormat'
import { ErrorState } from '@/components/states'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }
const SHADE = [0, 0.22, 0.42, 0.68, 1]

/** One piece of advice: what it says, what it rests on, what to consider, and what a person answered. */
function Advice({ f, siteId, weeks, onAnswered }: { f: Finding; siteId: string; weeks: number; onAnswered: () => Promise<unknown> }) {
  const [declining, setDeclining] = useState(false)
  const [reason, setReason] = useState('')
  const act = useMutation({
    mutationFn: (answer: 'ACCEPTED' | 'NOT_ACCEPTED') =>
      answerAdvice({ site_id: siteId, weeks, key: f.key, answer, reason: answer === 'NOT_ACCEPTED' ? reason.trim() : null })
        .then(onAnswered),
    onSuccess: () => { setDeclining(false); setReason('') },
  })
  return (
    <Box data-testid="finding" sx={{ py: 1.5, borderTop: 1, borderColor: 'divider' }}>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 0.5 }}>
        <Chip size="small" variant={f.confidence.level === 'HIGH' ? 'filled' : 'outlined'} label={LEVEL_LABEL[f.confidence.level]} />
        <Typography variant="caption" color="text.secondary">{f.source_label}</Typography>
      </Stack>
      <Typography variant="body1" sx={{ fontWeight: 600 }}>{f.statement}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{f.confidence.why}</Typography>
      <Typography variant="body2" sx={{ mt: 0.5 }}>To consider: {f.consider}</Typography>
      {f.answer && (
        <Box data-testid="answer" sx={{ mt: 0.75 }}>
          <Typography variant="body2">{answerLine(f.answer)}</Typography>
          {f.answer.said_then && (
            <Typography variant="caption" color="text.secondary">When that was said, it read: {f.answer.said_then}</Typography>)}
        </Box>)}
      {f.may_answer && !declining && (
        <Stack direction="row" sx={{ gap: 1, mt: 0.75, flexWrap: 'wrap' }}>
          <Button size="small" variant="outlined" disabled={act.isPending} onClick={() => act.mutate('ACCEPTED')}>
            {f.answer ? 'Accept it now' : 'Accept'}</Button>
          <Button size="small" disabled={act.isPending} onClick={() => setDeclining(true)}>Not accepted</Button>
        </Stack>)}
      {declining && (
        <Stack direction="row" sx={{ gap: 1, mt: 0.75, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <TextField size="small" label="Why it is not accepted" value={reason} autoFocus sx={{ flex: 1, minWidth: 260 }}
                     onChange={(e) => setReason(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
          <Button size="small" onClick={() => setDeclining(false)}>Not yet</Button>
          <Button size="small" variant="contained" disabled={!reason.trim() || act.isPending}
                  onClick={() => act.mutate('NOT_ACCEPTED')}>Record it</Button>
        </Stack>)}
      {act.isError && <Alert severity="error" sx={{ mt: 1 }}>{apiError(act.error)}</Alert>}
    </Box>
  )
}

/** A week of hours: one cell an hour, shaded in four steps by how many fell in it. */
function HourGrid({ s, weekdays, numbers }: { s: PatternSource; weekdays: string[]; numbers: boolean }) {
  const theme = useTheme()
  const most = peak(s.grid)
  const shade = (n: number) => (n ? alpha(theme.palette.primary.main, SHADE[n]) : 'transparent')
  return (
    <Box sx={{ overflowX: 'auto' }}>
      <Box component="table" data-testid="hour-grid" aria-label={`${s.label}: how many fell in each hour of each day`}
           sx={{ borderCollapse: 'separate', borderSpacing: '2px', minWidth: 640 }}>
        <thead>
          <tr>
            <Box component="th" sx={{ width: 84 }} />
            {Array.from({ length: 24 }, (_, hour) => (
              <Box component="th" key={hour} sx={{ fontSize: 10, fontWeight: 400, color: 'text.secondary', width: 22 }}>
                {hour % 3 === 0 ? String(hour).padStart(2, '0') : ''}</Box>))}
          </tr>
        </thead>
        <tbody>
          {s.grid.map((day, d) => (
            <tr key={weekdays[d]}>
              <Box component="th" scope="row" sx={{ fontSize: 12, fontWeight: 400, color: 'text.secondary', textAlign: 'left', pr: 1 }}>
                {weekdays[d]}</Box>
              {day.map((count, hour) => (
                <Box component="td" key={hour} title={cellTitle(weekdays[d], hour, count)} data-step={step(count, most)}
                     sx={{ height: 22, borderRadius: '3px', textAlign: 'center', fontSize: 10, color: 'text.primary',
                           bgcolor: shade(step(count, most)), border: count ? 0 : 1, borderColor: 'divider' }}>
                  {numbers && count ? count : ''}</Box>))}
            </tr>))}
        </tbody>
      </Box>
      {/* The key: what each shade stands for, in counts. */}
      <Stack direction="row" sx={{ gap: 1.5, mt: 1, alignItems: 'center', flexWrap: 'wrap' }} data-testid="grid-key">
        <Typography variant="caption" color="text.secondary">In one hour of the week:</Typography>
        {most === 0 ? <Typography variant="caption" color="text.secondary">nothing was recorded</Typography>
          : Array.from({ length: STEPS }, (_, i) => i + 1).filter((n) => stepRange(n, most) !== null).map((n) => (
            <Stack key={n} direction="row" sx={{ gap: 0.5, alignItems: 'center' }}>
              <Box sx={{ width: 14, height: 14, borderRadius: '3px', bgcolor: shade(n) }} />
              <Typography variant="caption" color="text.secondary">{stepRange(n, most)}</Typography>
            </Stack>))}
      </Stack>
    </Box>
  )
}

/** The weeks of the period as bars, oldest first, each with its count under it. */
function Weeks({ counts }: { counts: number[] }) {
  const theme = useTheme()
  const most = Math.max(1, ...counts)
  return (
    <Stack direction="row" sx={{ gap: '2px', alignItems: 'flex-end', height: 86 }} data-testid="weeks"
           aria-label="How many in each week of the period, oldest first">
      {counts.map((n, week) => (
        <Stack key={week} sx={{ alignItems: 'center', gap: 0.25, flex: 1, minWidth: 18, maxWidth: 44 }}
               title={`Week ${week + 1} of ${counts.length}: ${n}`}>
          <Box sx={{ width: '100%', height: `${Math.round((n / most) * 56)}px`, minHeight: n ? 3 : 1, borderRadius: '4px 4px 0 0',
                     bgcolor: n ? theme.palette.primary.main : 'divider' }} />
          <Typography variant="caption" color="text.secondary" sx={{ fontSize: 10 }}>{n}</Typography>
        </Stack>))}
    </Stack>
  )
}

export default function RiskAdvice() {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState('')
  const [weeks, setWeeks] = useState(4)
  const [source, setSource] = useState<Source>('INCIDENT')
  const [numbers, setNumbers] = useState(false)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const asked = { site_id: siteId || undefined, weeks }
  const advice = useQuery({ queryKey: ['risk-advice', siteId, weeks], queryFn: () => getAdvice(asked), placeholderData: keepPreviousData })
  const patterns = useQuery({ queryKey: ['risk-patterns', siteId, weeks], queryFn: () => getPatterns(asked), placeholderData: keepPreviousData })
  const answers = useQuery({ queryKey: ['risk-answers', siteId], queryFn: () => getAnswers(siteId || undefined) })
  const again = () => Promise.all([qc.invalidateQueries({ queryKey: ['risk-advice'] }), qc.invalidateQueries({ queryKey: ['risk-answers'] })])
  const shownSource = patterns.data?.sources.find((s) => s.source === source)
  const findings = advice.data?.findings ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Risk & Advice"
                  subtitle="Where what was recorded gathers, and what stands out. Counts over a period — not a forecast" />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 200 }} slotProps={shrunk}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">Every site</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Period" value={weeks} sx={{ minWidth: 190 }}
                     onChange={(e) => setWeeks(Number(e.target.value))}>
            {WEEK_CHOICES.map((w) => <MenuItem key={w} value={w}>{weeksLabel(w)}</MenuItem>)}
          </TextField>
          {advice.data && (
            <Typography variant="caption" color="text.secondary">
              {fmt(advice.data.period.from)} to {fmt(advice.data.period.to)} · hours as in {advice.data.period.timezone}
            </Typography>)}
        </Stack>
      </GlassCard>

      <GlassCard sx={{ p: 2, mb: 2 }} data-testid="advice">
        <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>What stands out</Typography>
        {!!advice.error && <ErrorState compact error={advice.error} onRetry={advice.refetch} sx={{ mt: 1 }} />}
        {advice.isLoading && <Skeleton height={140} />}
        {advice.data && (
          <>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{advice.data.note}</Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              {advice.data.confidence_note}</Typography>
            {advice.data.answer_note && advice.data.can_answer && !!findings.length && (
              <Alert severity="info" sx={{ mb: 1 }}>{advice.data.answer_note}</Alert>)}
            {!findings.length ? (
              <Alert severity="info">
                Nothing stands out in this period by the rules that are applied. That is not the same as nothing having
                happened: the counts are below.
              </Alert>
            ) : findings.map((f) => <Advice key={f.key} f={f} siteId={siteId} weeks={weeks} onAnswered={again} />)}
          </>)}
      </GlassCard>

      <GlassCard sx={{ p: 2, mb: 2 }} data-testid="patterns">
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center', mb: 1 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 700, flex: 1, minWidth: 200 }}>Where it gathers</Typography>
          <FormControlLabel label="Show the numbers" sx={{ ml: 0 }}
                            control={<Switch size="small" checked={numbers} onChange={(e) => setNumbers(e.target.checked)} />} />
        </Stack>
        {!!patterns.error && <ErrorState compact error={patterns.error} onRetry={patterns.refetch} />}
        {patterns.isLoading && <Skeleton height={220} />}
        {patterns.data && (
          <>
            <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', mb: 1.5 }}>
              {patterns.data.sources.map((s) => (
                <Chip key={s.source} data-testid="source-chip" clickable onClick={() => setSource(s.source)}
                      color={s.source === source ? 'primary' : 'default'} variant={s.source === source ? 'filled' : 'outlined'}
                      label={`${s.label}: ${s.total}`} />))}
            </Stack>
            {shownSource && (
              <>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                  {shownSource.counted_from} {totalLine(shownSource)}.
                </Typography>
                <HourGrid s={shownSource} weekdays={patterns.data.weekdays} numbers={numbers} />
                <Stack direction="row" sx={{ gap: 4, flexWrap: 'wrap', mt: 2 }}>
                  <Box sx={{ flex: 1, minWidth: 260 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>By place</Typography>
                    {!shownSource.places.length ? (
                      <Typography variant="body2" color="text.secondary">None recorded.</Typography>
                    ) : shownSource.places.map((p) => (
                      <Typography key={p.key} variant="body2" data-testid="place">
                        {p.name} — {p.count} ({p.share}%)</Typography>))}
                  </Box>
                  <Box sx={{ flex: 1, minWidth: 260 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>By week, oldest first</Typography>
                    <Weeks counts={shownSource.by_week} />
                  </Box>
                </Stack>
              </>)}
          </>)}
      </GlassCard>

      <GlassCard sx={{ p: 2 }} data-testid="answers">
        <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 0.5 }}>Answers given</Typography>
        {!!answers.error && <ErrorState compact error={answers.error} onRetry={answers.refetch} />}
        {answers.data && !answers.data.length && (
          <Typography variant="body2" color="text.secondary">No advice has been answered yet.</Typography>)}
        {(answers.data ?? []).slice(0, 10).map((a) => (
          <Box key={a.id} data-testid="given" sx={{ py: 1, borderTop: 1, borderColor: 'divider' }}>
            <Typography variant="body2">
              <strong>{ANSWER_LABEL[a.answer]}</strong> by {a.answered_by_name ?? 'somebody no longer on the system'},{' '}
              {fmt(a.answered_at)}{a.reason ? `: ${a.reason}` : ''}
            </Typography>
            <Typography variant="caption" color="text.secondary">{a.site_name} · {a.source_label} · {a.statement}</Typography>
          </Box>))}
      </GlassCard>
    </Box>
  )
}

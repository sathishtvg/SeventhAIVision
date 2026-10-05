/**
 * Security Intelligence — what a period looked like, and a score that can be read.
 *
 * Everything on this page is counted, when it is asked for, from rows the
 * platform already keeps. The site security score starts at 100 and loses
 * stated points for stated things; each line is shown, and the lines add up.
 * A finding is a fixed rule over the same counts: it says what it rests on and
 * what a person might consider, and it is advice — drawn, like everything the
 * layer says, dashed and violet, and never as something decided.
 */
import { useState } from 'react'
import { Alert, Box, Chip, Grid, MenuItem, Skeleton, Table, TableBody, TableCell, TableRow, TextField, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { apiError, getInsight, getSiteScores } from '@/api/securityIntelligence'
import type { InsightCounts, InsightFinding, SiteScore } from '@/api/securityIntelligence'
import { AiMark } from '@/components/intel/intelUi'
import { AI_COLOR, RISK_COLOR, SOURCE_LABEL, duration } from '@/components/intel/intelFormat'
import { IntelNav, IntelStatusBanner } from './IntelNav'

const BAND: Record<SiteScore['band'], { label: string; color: string }> = {
  GOOD: { label: 'Good', color: RISK_COLOR.LOW }, FAIR: { label: 'Fair', color: RISK_COLOR.MEDIUM },
  NEEDS_ATTENTION: { label: 'Needs attention', color: RISK_COLOR.HIGH }, POOR: { label: 'Poor', color: RISK_COLOR.CRITICAL },
  // A hundred from no records is not called good.
  NOTHING_RECORDED: { label: 'Nothing recorded', color: RISK_COLOR.INFO },
}
const PERIODS = [7, 14, 30, 90]
/** One hue for a magnitude: how long the bar is says how many, and the number is beside it. */
const BAR = '#7f93b0'

function ScoreCard({ site, selected, onSelect }: { site: SiteScore; selected: boolean; onSelect: () => void }) {
  const band = BAND[site.band]
  return (
    <GlassCard data-testid="site-score" onClick={onSelect}
               sx={{ p: 2, height: '100%', cursor: 'pointer', borderColor: selected ? 'primary.main' : undefined }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'baseline', gap: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>{site.site_name}</Typography>
        <Chip size="small" label={band.label}
              sx={{ bgcolor: `${band.color}22`, color: band.color, border: `1px solid ${band.color}66`, fontWeight: 700 }} />
      </Stack>
      <Typography sx={{ fontSize: '2.2rem', fontWeight: 700, lineHeight: 1.2 }}>
        {site.score}<Typography component="span" color="text.secondary" sx={{ fontSize: '1rem' }}> / {site.out_of}</Typography>
      </Typography>
      {site.deductions.length === 0
        ? <Typography variant="body2" color="text.secondary">Nothing took points off.</Typography>
        : (
          <Table size="small">
            <TableBody>
              {site.deductions.map((d) => (
                <TableRow key={d.factor} data-testid="score-line">
                  <TableCell sx={{ pl: 0, border: 0, py: 0.25 }}><Typography variant="body2">{d.detail}</Typography></TableCell>
                  <TableCell align="right" sx={{ pr: 0, border: 0, py: 0.25, whiteSpace: 'nowrap' }}>
                    <Typography variant="body2" sx={{ fontWeight: 700 }}>
                      {d.points}{d.capped ? ' (the most this can take)' : ''}</Typography></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      {site.went_well.map((w) => <Typography key={w} variant="caption" color="text.secondary" sx={{ display: 'block' }}>✓ {w}</Typography>)}
      {site.note && <Typography variant="caption" sx={{ display: 'block', mt: 0.5, fontStyle: 'italic' }}>{site.note}</Typography>}
    </GlassCard>
  )
}

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <GlassCard sx={{ p: 1.5, height: '100%' }} data-testid="insight-tile">
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {label}</Typography>
      <Typography sx={{ fontSize: '1.5rem', fontWeight: 700 }}>{value}</Typography>
      {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
    </GlassCard>
  )
}

/** A count beside others of its kind: the bar says how it compares, the number says what it is. */
function Bars({ rows, empty }: { rows: { label: string; value: number }[]; empty: string }) {
  const most = Math.max(1, ...rows.map((r) => r.value))
  if (!rows.length) return <Typography variant="body2" color="text.secondary">{empty}</Typography>
  return (
    <Box>
      {rows.map((r) => (
        <Box key={r.label} title={`${r.label}: ${r.value}`}
             sx={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 34%) 1fr 44px', gap: 1, alignItems: 'center',
                   py: 0.4 }}>
          <Typography variant="body2" noWrap title={r.label}>{r.label}</Typography>
          <Box sx={{ height: 8, borderRadius: '0 4px 4px 0', bgcolor: BAR, width: `${Math.max(2, (r.value / most) * 100)}%` }} />
          <Typography variant="body2" align="right" sx={{ fontWeight: 700 }}>{r.value}</Typography>
        </Box>
      ))}
    </Box>
  )
}

function Finding({ f }: { f: InsightFinding }) {
  return (
    <Box data-testid="finding" sx={{ p: 1.5, mb: 1, border: `1px dashed ${AI_COLOR}88`, borderRadius: 2 }}>
      <AiMark>AI-assisted finding — advice, not a decision</AiMark>
      <Typography variant="body2" sx={{ fontWeight: 600, mt: 0.5 }}>{f.finding}</Typography>
      <Typography variant="body2" color="text.secondary">To consider: {f.consider}</Typography>
    </Box>
  )
}

function Period({ c, days }: { c: InsightCounts; days: number }) {
  const hours = Array.from({ length: 24 }, (_, h) => ({ label: `${String(h).padStart(2, '0')}:00`, value: c.by_hour[h] ?? 0 }))
    .filter((h) => h.value > 0)
  const risk = (['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NOT_ASSESSED'] as const)
    .map((k) => ({ label: k === 'NOT_ASSESSED' ? 'Not assessed' : k.charAt(0) + k.slice(1).toLowerCase(), value: c.by_risk[k] ?? 0 }))
    .filter((r) => r.value > 0)
  return (
    <>
      <Grid container spacing={1.5} sx={{ mb: 2 }}>
        {[
          { label: 'Situations', value: c.situations, hint: `${c.events} event(s), ${c.repeats_folded} repeat(s) folded` },
          { label: 'High or critical', value: (c.by_risk.HIGH ?? 0) + (c.by_risk.CRITICAL ?? 0), hint: `${c.high_risk_open ?? 0} still open` },
          { label: 'Still open', value: c.still_open, hint: `${c.unattended ?? 0} waiting over 15 min` },
          { label: 'Closed as false', value: c.false_positive, hint: `of ${c.closed} closed` },
          { label: 'To first decision', value: duration(c.median_seconds_to_decide), hint: `median of ${c.decided}` },
          { label: 'Guard to arrive', value: duration(c.median_seconds_to_arrive), hint: `median of ${c.guard_arrivals}` },
          { label: 'Cameras not sending', value: c.cameras_offline ?? 0, hint: `of ${c.cameras ?? 0}` },
          { label: 'Patrols missed', value: c.patrols_missed ?? 0,
            hint: `${(c.virtual_patrols ?? 0) + (c.drone_patrols ?? 0)} scheduled or flown` },
        ].map((t) => <Grid key={t.label} size={{ xs: 6, md: 3 }}><Tile {...t} /></Grid>)}
      </Grid>
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <GlassCard sx={{ p: 2, height: '100%' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Where situations began</Typography>
            <Bars rows={c.locations.map((l) => ({ label: l.name, value: l.situations }))}
                  empty={`No situation began at a named place in the last ${days} day(s).`} />
          </GlassCard>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <GlassCard sx={{ p: 2, height: '100%' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>What reported</Typography>
            <Bars rows={Object.entries(c.by_source).map(([k, v]) => ({ label: SOURCE_LABEL[k as keyof typeof SOURCE_LABEL] ?? k, value: v }))}
                  empty="No source reported anything in the period." />
          </GlassCard>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <GlassCard sx={{ p: 2, height: '100%' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>By assessed risk</Typography>
            <Bars rows={risk} empty="No situation in the period." />
          </GlassCard>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <GlassCard sx={{ p: 2, height: '100%' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>By the hour they began</Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              Hours in {c.timezone}. Hours with none are left out.</Typography>
            <Bars rows={hours} empty="No situation in the period." />
          </GlassCard>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <GlassCard sx={{ p: 2, height: '100%' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Seen in more than one situation</Typography>
            <Bars rows={c.vehicles.map((v) => ({ label: `Plate ${v.plate}`, value: v.situations }))}
                  empty="No number plate was part of more than one situation." />
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              {c.persons.length
                ? `${c.persons.length} watchlist entr${c.persons.length === 1 ? 'y was' : 'ies were'} part of more than one situation.`
                : 'No watchlist entry was part of more than one situation.'}
              {' '}A person nobody identified is not counted as anybody, and nobody is named here.
            </Typography>
          </GlassCard>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <GlassCard sx={{ p: 2, height: '100%' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>How people answered</Typography>
            <Bars rows={[{ label: 'Followed the suggestion', value: c.followed }, { label: 'Went against it', value: c.overrides },
                         { label: 'Other decisions', value: Math.max(0, c.decisions - c.followed - c.overrides) }]
                         .filter((r) => r.value > 0)}
                  empty="No decision was recorded in the period." />
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              Going against a suggestion is a person using their judgement, and is recorded with its reason.
            </Typography>
          </GlassCard>
        </Grid>
      </Grid>
    </>
  )
}

export default function Insight() {
  const [siteId, setSiteId] = useState('')
  const [days, setDays] = useState(7)
  const scores = useQuery({ queryKey: ['intel-site-scores', days], queryFn: () => getSiteScores(days), refetchInterval: 60_000 })
  const insight = useQuery({
    queryKey: ['intel-insight', siteId, days], queryFn: () => getInsight({ site_id: siteId || undefined, days }),
    refetchInterval: 60_000 })
  const sites = scores.data?.sites ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Security Intelligence"
                  subtitle="What the period looked like, counted from the records — and a score whose every point is stated" />
      <IntelNav />
      <IntelStatusBanner />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 200 }}
                     slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">All sites</MenuItem>
            {sites.map((s) => <MenuItem key={s.site_id} value={s.site_id}>{s.site_name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Period" value={days} sx={{ minWidth: 150 }}
                     onChange={(e) => setDays(Number(e.target.value))}>
            {PERIODS.map((d) => <MenuItem key={d} value={d}>Last {d} days</MenuItem>)}
          </TextField>
          <Typography variant="caption" color="text.secondary">
            Counted when asked. Nothing here is stored, predicted or learned.</Typography>
        </Stack>
      </GlassCard>

      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>Site security score</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        Starts at 100 and loses the points shown, for the things shown. The lowest first. It counts what is open and what
        went wrong; it is not a prediction, and not a grade of anybody.
      </Typography>
      {scores.error ? <Alert severity="error" sx={{ mb: 2 }}>{apiError(scores.error)}</Alert>
        : !scores.data ? <Skeleton height={160} sx={{ mb: 2 }} />
          : !sites.length ? <Alert severity="info" sx={{ mb: 2 }}>There is no site to score.</Alert>
            : (
              <Grid container spacing={2} sx={{ mb: 3 }}>
                {sites.map((s) => (
                  <Grid key={s.site_id} size={{ xs: 12, md: 6, lg: 4 }}>
                    <ScoreCard site={s} selected={s.site_id === siteId}
                               onSelect={() => setSiteId(s.site_id === siteId ? '' : s.site_id)} />
                  </Grid>))}
              </Grid>
            )}

      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
        {insight.data?.site ? insight.data.site.name : 'All sites'} · the last {days} days</Typography>
      {insight.error ? <Alert severity="error">{apiError(insight.error)}</Alert>
        : !insight.data ? <Skeleton height={300} />
          : (
            <>
              <GlassCard sx={{ p: 2, mb: 2 }}>
                <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>What stands out</Typography>
                {insight.data.findings.length === 0
                  ? <Typography variant="body2" color="text.secondary">
                    Nothing in the period stands out by the rules this page applies.</Typography>
                  : insight.data.findings.map((f) => <Finding key={f.code + f.finding} f={f} />)}
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                  Fixed rules over the counts below. A rule about a share stays silent on fewer than five.
                </Typography>
              </GlassCard>
              <Period c={insight.data.counts} days={days} />
            </>
          )}
    </Box>
  )
}

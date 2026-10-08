/**
 * Workforce readings and recommendations: what is recorded of each guard's
 * work, the same for each site, and what a manager might consider.
 *
 * A reading is counts, each beside how much there was to do. It is not an
 * appraisal: there is no score, and the guards are in order of name — the
 * table has no column to sort them by. The server's note saying so is shown
 * above every reading.
 *
 * A recommendation is advice for a manager. Accepting one assigns no course
 * and changes no roster, and the screen says so beside the buttons.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Skeleton, Tab, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, Tabs, TextField, Typography,
} from '@mui/material'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { getSites } from '@/api/sites'
import {
  answerRecommendation, apiError, getAnswers, getMyReading, getReading, getReadings, getRecommendations,
} from '@/api/workforce'
import type { Figures, NotRead, Reading, Recommendation } from '@/api/workforce'
import {
  ANSWER_LABEL, COLUMNS, PERIODS, about, answerLine, fmt, givenAbout, lines, named, periodLabel,
} from '@/components/workforce/workforceFormat'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }
type Part = 'guards' | 'sites' | 'advice'

function NotShown({ items }: { items: NotRead[] }) {
  if (!items.length) return null
  return (
    <Alert severity="info" sx={{ mb: 2 }} data-testid="not-read">
      Not shown to you: {items.map((n) => `${n.title} (read under ${n.needs})`).join('; ')}.
    </Alert>
  )
}

/** One recommendation: what it says, what to consider, what a manager answered — and that accepting changes nothing. */
function Rec({ r, onAnswered }: { r: Recommendation; onAnswered: () => Promise<unknown> }) {
  const [declining, setDeclining] = useState(false)
  const [reason, setReason] = useState('')
  const act = useMutation({
    mutationFn: (answer: 'ACCEPTED' | 'NOT_ACCEPTED') =>
      answerRecommendation({ key: r.key, answer, reason: answer === 'NOT_ACCEPTED' ? reason.trim() : null }).then(onAnswered),
    onSuccess: () => { setDeclining(false); setReason('') },
  })
  return (
    <Box data-testid="recommendation" sx={{ py: 1.5, borderTop: 1, borderColor: 'divider' }}>
      <Typography variant="caption" color="text.secondary">{about(r)}</Typography>
      <Typography variant="body1" sx={{ fontWeight: 600 }}>{r.statement}</Typography>
      <Typography variant="body2" sx={{ mt: 0.5 }}>To consider: {r.consider}</Typography>
      {r.answer && (
        <Box data-testid="answer" sx={{ mt: 0.75 }}>
          <Typography variant="body2">{answerLine(r.answer)}</Typography>
          {r.answer.said_then && (
            <Typography variant="caption" color="text.secondary">When that was said, it read: {r.answer.said_then}</Typography>)}
        </Box>)}
      {r.may_answer && !declining && (
        <Stack direction="row" sx={{ gap: 1, mt: 0.75, flexWrap: 'wrap' }}>
          <Button size="small" variant="outlined" disabled={act.isPending} onClick={() => act.mutate('ACCEPTED')}>
            {r.answer ? 'Accept it now' : 'Accept'}</Button>
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

/** One person's reading: each section in lines, the same site by site, and what is recommended for them. */
function ReadingView({ data, onAnswered }: { data: Reading; onAnswered: () => Promise<unknown> }) {
  return (
    <Box data-testid="reading">
      <Alert severity="info" sx={{ mb: 2 }}>{data.note}</Alert>
      <NotShown items={data.not_read} />
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
        {data.sections.map((s) => (
          <GlassCard key={s.key} sx={{ p: 2 }} data-testid={`section-${s.key}`}>
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>{s.title}</Typography>
            {lines(s.key, data.figures).map((line) => <Typography key={line} variant="body2" data-testid="line">{line}</Typography>)}
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>{s.counted_from}</Typography>
          </GlassCard>))}
      </Box>
      {data.by_site.length > 1 && (
        <GlassCard sx={{ p: 0, mt: 2 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700, p: 2, pb: 1 }}>Site by site</Typography>
          <FiguresTable first="Site" rows={data.by_site.map((s) => ({ id: s.id ?? 'none', name: s.name, figures: s.figures }))} />
        </GlassCard>)}
      <GlassCard sx={{ p: 2, mt: 2 }} data-testid="recommended">
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>What is recommended</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{data.recommendations_note}</Typography>
        {!data.recommendations.length && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Nothing is recommended from the last 4 weeks' records.</Typography>)}
        {data.recommendations.map((r) => <Rec key={r.key} r={r} onAnswered={onAnswered} />)}
      </GlassCard>
    </Box>
  )
}

/** A table of counts, one row for each guard or site, in the order it was given: there is nothing to sort it by. */
function FiguresTable({ first, rows, onOpen, last }: {
  first: string; rows: { id: string; name: string; figures: Figures }[]; onOpen?: (id: string) => void
  last?: { name: string; figures: Figures }
}) {
  return (
    <TableContainer>
      <Table size="small" data-testid="figures-table" aria-label={`Counts for each ${first.toLowerCase()}, in order of name`}>
        <TableHead>
          <TableRow>
            <TableCell>{first}</TableCell>
            {COLUMNS.map((c) => <TableCell key={c.key} align="right">{c.label}</TableCell>)}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id} hover={!!onOpen} data-testid="figures-row" sx={{ cursor: onOpen ? 'pointer' : 'default' }}
                      onClick={onOpen ? () => onOpen(r.id) : undefined}>
              <TableCell>{r.name}</TableCell>
              {COLUMNS.map((c) => <TableCell key={c.key} align="right">{c.of(r.figures)}</TableCell>)}
            </TableRow>))}
          {last && (
            <TableRow data-testid="figures-row">
              <TableCell sx={{ fontWeight: 700 }}>{last.name}</TableCell>
              {COLUMNS.map((c) => <TableCell key={c.key} align="right" sx={{ fontWeight: 700 }}>{c.of(last.figures)}</TableCell>)}
            </TableRow>)}
          {!rows.length && <TableRow><TableCell colSpan={COLUMNS.length + 1}>Nobody is shown to you.</TableCell></TableRow>}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function GuardDialog({ id, days, onClose }: { id: string; days: number; onClose: () => void }) {
  const qc = useQueryClient()
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ['workforce-reading', id, days], queryFn: () => getReading(id, days) })
  const again = () => Promise.all([refetch(), qc.invalidateQueries({ queryKey: ['workforce-recommendations'] }),
                                   qc.invalidateQueries({ queryKey: ['workforce-answers'] })])
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{data ? `${named(data.guard.name)} — ${periodLabel(data.period.days).toLowerCase()}` : 'Reading'}</DialogTitle>
      <DialogContent>
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {isLoading && <Skeleton height={260} />}
        {data && <ReadingView data={data} onAnswered={again} />}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

/** The site, and the period of a reading. Recommendations are always of the last four weeks, and say so instead. */
function Filters({ siteId, setSiteId, days, setDays, period }: {
  siteId: string; setSiteId: (v: string) => void; days: number; setDays: (v: number) => void; period: boolean
}) {
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  return (
    <GlassCard sx={{ p: 2, mb: 2 }}>
      <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
        <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 190 }} slotProps={shrunk}
                   onChange={(e) => setSiteId(e.target.value)}>
          <MenuItem value="">Every site</MenuItem>
          {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
        </TextField>
        {period ? (
          <TextField select size="small" label="Period" value={days} sx={{ minWidth: 180 }}
                     onChange={(e) => setDays(Number(e.target.value))}>
            {PERIODS.map((d) => <MenuItem key={d} value={d}>{periodLabel(d)}</MenuItem>)}
          </TextField>
        ) : <Typography variant="caption" color="text.secondary">Made of the last 4 weeks' records, whatever period a reading is for.</Typography>}
      </Stack>
    </GlassCard>
  )
}

function ReadingsTab({ part, siteId, days }: { part: 'guards' | 'sites'; siteId: string; days: number }) {
  const [open, setOpen] = useState<string | null>(null)
  const { data, isLoading, error } = useQuery({
    queryKey: ['workforce-readings', siteId, days], queryFn: () => getReadings({ site_id: siteId || undefined, days }),
    placeholderData: keepPreviousData,
  })
  return (
    <>
      {!!error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(error)}</Alert>}
      {isLoading && <Skeleton height={260} />}
      {data && (
        <>
          <Alert severity="info" sx={{ mb: 2 }} data-testid="readings-note">{data.note}</Alert>
          <NotShown items={data.not_read} />
          <GlassCard sx={{ p: 0 }}>
            {part === 'guards' ? (
              <FiguresTable first="Guard" onOpen={setOpen}
                            rows={data.guards.map((g) => ({ id: g.id, name: g.is_active ? named(g.name) : `${named(g.name)} (not in use)`, figures: g.figures }))} />
            ) : (
              <FiguresTable first="Site" rows={[...data.sites.map((s) => ({ id: s.id, name: s.name, figures: s.figures })),
                                                ...(data.no_site ? [{ id: 'none', name: 'At no site', figures: data.no_site }] : [])]}
                            last={{ name: 'Together', figures: data.total }} />)}
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', p: 2, pt: 1 }}>
              {part === 'guards'
                ? 'In order of name. Each cell is a count beside how much there was to do; choose a guard for the whole reading.'
                : 'Each site\'s counts are its guards\' added together. Training and certificates are a person\'s and are in no site\'s.'}
            </Typography>
          </GlassCard>
        </>)}
      {open && <GuardDialog id={open} days={days} onClose={() => setOpen(null)} />}
    </>
  )
}

function AdviceTab({ siteId }: { siteId: string }) {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['workforce-recommendations', siteId], queryFn: () => getRecommendations(siteId || undefined),
  })
  const answers = useQuery({ queryKey: ['workforce-answers'], queryFn: getAnswers })
  const again = () => Promise.all([qc.invalidateQueries({ queryKey: ['workforce-recommendations'] }),
                                   qc.invalidateQueries({ queryKey: ['workforce-answers'] })])
  const list = (title: string, testId: string, items: Recommendation[], none: string) => (
    <GlassCard sx={{ p: 2, mb: 2 }} data-testid={testId}>
      <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>{title}</Typography>
      {!items.length && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{none}</Typography>}
      {items.map((r) => <Rec key={r.key} r={r} onAnswered={again} />)}
    </GlassCard>)
  return (
    <>
      {!!error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(error)}</Alert>}
      {isLoading && <Skeleton height={260} />}
      {data && (
        <>
          <Alert severity="info" sx={{ mb: 2 }} data-testid="advice-note">{data.note}</Alert>
          {!!data.not_read.length && (
            <Alert severity="info" sx={{ mb: 2 }}>
              Not shown to you: {data.not_read.map((n) => `${n.kind === 'TRAINING' ? 'training' : 'cover'} (read under ${n.needs})`).join('; ')}.
            </Alert>)}
          {list('Training, for a guard', 'training', data.training, 'Nothing is recommended for any guard from the last 4 weeks\' records.')}
          {list('Cover, for a site', 'coverage', data.coverage, 'Nothing is recommended for any site from the last 4 weeks\' records.')}
        </>)}
      <GlassCard sx={{ p: 2 }} data-testid="answers">
        <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 0.5 }}>Answers given</Typography>
        {answers.data && !answers.data.length && (
          <Typography variant="body2" color="text.secondary">No recommendation has been answered yet.</Typography>)}
        {(answers.data ?? []).slice(0, 10).map((a) => (
          <Box key={a.id} data-testid="given" sx={{ py: 1, borderTop: 1, borderColor: 'divider' }}>
            <Typography variant="body2">
              <strong>{ANSWER_LABEL[a.answer]}</strong> by {named(a.answered_by_name)}, {fmt(a.answered_at)}{a.reason ? `: ${a.reason}` : ''}
            </Typography>
            <Typography variant="caption" color="text.secondary">{givenAbout(a)} · {a.statement}</Typography>
          </Box>))}
      </GlassCard>
    </>
  )
}

const SUBTITLE = 'What is recorded of each guard\'s work, beside how much there was to do. Counts — not an appraisal, and nobody is ranked'

export default function WorkforceReadings() {
  const read = usePermission('workforce:read')
  const [part, setPart] = useState<Part>('guards')
  const [siteId, setSiteId] = useState('')
  const [days, setDays] = useState(28)
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Workforce Readings" subtitle={SUBTITLE} />
      {!read ? <Alert severity="info">Other people's readings are read by the people they answer to.</Alert> : (
        <>
          <Tabs value={part} onChange={(_, v: Part) => setPart(v)} sx={{ mb: 2 }}>
            <Tab value="guards" label="Guards" />
            <Tab value="sites" label="Sites" />
            <Tab value="advice" label="Recommendations" />
          </Tabs>
          <Filters siteId={siteId} setSiteId={setSiteId} days={days} setDays={setDays} period={part !== 'advice'} />
          {part === 'advice' ? <AdviceTab siteId={siteId} /> : <ReadingsTab part={part} siteId={siteId} days={days} />}
        </>)}
    </Box>
  )
}

/** A person's own reading: what is recorded of their own work, whole, and nobody else's. */
export function MyReading() {
  const own = usePermission('workforce:own')
  const [days, setDays] = useState(28)
  const { data, isLoading, error } = useQuery({
    queryKey: ['workforce-mine', days], queryFn: () => getMyReading(days), enabled: own, placeholderData: keepPreviousData,
  })
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="My Reading" subtitle="What is recorded of your own work. It is yours to read; it is not an appraisal" />
      {!own && <Alert severity="info">There is no reading of your own to show.</Alert>}
      {own && (
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" sx={{ gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
            <TextField select size="small" label="Period" value={days} sx={{ minWidth: 180 }} onChange={(e) => setDays(Number(e.target.value))}>
              {PERIODS.map((d) => <MenuItem key={d} value={d}>{periodLabel(d)}</MenuItem>)}
            </TextField>
            {data && <Chip size="small" variant="outlined" label={named(data.guard.name)} />}
          </Stack>
        </GlassCard>)}
      {!!error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(error)}</Alert>}
      {isLoading && own && <Skeleton height={260} />}
      {data && <ReadingView data={data} onAnswered={() => Promise.resolve()} />}
    </Box>
  )
}

/**
 * Data retention: how long each kind of record is kept, and where a person
 * appears in the records.
 *
 * The statement is what the jobs read, read the same way. The periods in
 * force are changed where they have always been changed: in the organisation's
 * settings and in a site's recording policy. Where a period falls back to the
 * installation's default the server says the job's own is the one in force,
 * and that is shown beside it.
 *
 * Four kinds of the newer records may be given a period here, by somebody who
 * may change the organisation's settings, after being told how many are
 * already older than it (components/governance/OptionalPeriods.tsx).
 *
 * A subject report says where a person appears and how often — not what each
 * record says. A name typed is found as text, and the screen says that what
 * matched is text and identifies nobody.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, MenuItem, Skeleton, Tab, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Tabs, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import {
  apiError, findSubject, getStaffReport, getStatement, getVisitorReport, getWrittenReport,
} from '@/api/dataGovernance'
import type { Found, Statement, SubjectReport } from '@/api/dataGovernance'
import { OptionalPeriods } from '@/components/governance/OptionalPeriods'
import {
  day, heldCount, holdLine, lineWords, matchLine, periodLine, searchesLine, siteLine, subjectLine, totalsLine,
} from '@/components/governance/governanceFormat'
import { ErrorState } from '@/components/states'

type Part = 'retention' | 'person'
type Looking = 'STAFF' | 'VISITOR' | 'TEXT'
const LOOKING: Record<Looking, { label: string; field: string; button: string }> = {
  STAFF: { label: 'A member of staff', field: 'Their name, or part of it', button: 'Find' },
  VISITOR: { label: 'A visitor', field: 'Their name, or part of it', button: 'Find' },
  TEXT: { label: 'A name or a number plate, as it was typed', field: 'The name or the plate', button: 'Look for it' },
}
const FEWEST = 3

function Heading({ children, note }: { children: string; note?: string }) {
  return (
    <Box sx={{ mb: 1 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>{children}</Typography>
      {note && <Typography variant="body2" color="text.secondary">{note}</Typography>}
    </Box>
  )
}

function Periods({ data }: { data: Statement }) {
  return (
    <GlassCard sx={{ p: 2, mb: 2 }}>
      <Heading note="Each is what the job that applies it reads now.">Periods in force</Heading>
      <TableContainer>
        <Table size="small" data-testid="periods" aria-label="Retention periods in force">
          <TableHead>
            <TableRow>
              <TableCell>Kind of record</TableCell><TableCell>Kept for</TableCell><TableCell>Set by</TableCell>
              <TableCell>Removed by</TableCell><TableCell>Holds</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.periods.map((p) => (
              <TableRow key={p.key} data-testid="period-row" sx={{ verticalAlign: 'top' }}>
                <TableCell sx={{ minWidth: 190 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>{p.label}</Typography>
                  <Typography variant="caption" color="text.secondary">Counted from {p.counted_from}</Typography>
                </TableCell>
                <TableCell sx={{ minWidth: 150 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>{periodLine(p.period)}</Typography>
                  <Typography variant="caption" color="text.secondary">{p.how}.</Typography>
                </TableCell>
                <TableCell sx={{ minWidth: 200 }}>
                  <Typography variant="body2">{p.period.source_words}</Typography>
                  {p.setting_key && (
                    <Typography variant="caption" color="text.secondary" component="div">
                      Setting: <Box component="span" sx={{ fontFamily: 'monospace' }}>{p.setting_key}</Box>
                      {p.period.set_at ? `, set ${day(p.period.set_at)}` : ''}
                    </Typography>)}
                  {!p.per_organisation && (
                    <Typography variant="caption" color="text.secondary" component="div">
                      The same for every organisation of the installation.
                    </Typography>)}
                  {p.period.note && <Typography variant="caption" color="warning.main" component="div">{p.period.note}</Typography>}
                </TableCell>
                <TableCell sx={{ minWidth: 150 }}>{p.removed_by}</TableCell>
                <TableCell sx={{ minWidth: 240 }}>
                  <Typography variant="body2">{holdLine(p)}</Typography>
                  {p.kept_past && (
                    <Typography variant="caption" color="text.secondary" component="div">Also kept past its period: {p.kept_past}</Typography>)}
                </TableCell>
              </TableRow>))}
          </TableBody>
        </Table>
      </TableContainer>
    </GlassCard>
  )
}

function Sites({ data }: { data: Statement }) {
  return (
    <GlassCard sx={{ p: 2, mb: 2 }}>
      <Heading note="A site's recording policy sets its own period. A site without one follows the organisation's.">
        Recordings, site by site
      </Heading>
      <TableContainer>
        <Table size="small" data-testid="sites" aria-label="How long each site's recordings are kept">
          <TableHead>
            <TableRow>
              <TableCell>Site</TableCell><TableCell>Kept centrally for</TableCell><TableCell>Set by</TableCell>
              <TableCell>Kept at the site for</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.sites.map((s) => (
              <TableRow key={s.site_id} data-testid="site-row">
                <TableCell>{s.site_name}{s.in_use ? '' : ' (not in use)'}</TableCell>
                <TableCell>{siteLine(s)}</TableCell>
                <TableCell>{s.source_words}{s.set_at ? `, set ${day(s.set_at)}` : ''}</TableCell>
                <TableCell>{s.at_the_site_days == null ? 'Not set' : `${s.at_the_site_days} ${s.at_the_site_days === 1 ? 'day' : 'days'}`}</TableCell>
              </TableRow>))}
            {!data.sites.length && <TableRow><TableCell colSpan={4}>No site is shown to you.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </TableContainer>
    </GlassCard>
  )
}

function Kept({ data }: { data: Statement }) {
  return (
    <GlassCard sx={{ p: 2, mb: 2 }} data-testid="kept">
      <Heading note={data.everything_else}>Kept, with no period</Heading>
      <Typography variant="body2" sx={{ mb: 1 }}>
        These are among them, and none has a period. Each is listed with whether it names a person:
      </Typography>
      {data.kept.map((k) => {
        const naming = k.tables.filter((t) => t.names_people).length
        return (
          <Box key={k.key} data-testid="kept-group" sx={{ py: 1, borderTop: 1, borderColor: 'divider' }}>
            <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
              <Typography variant="body2" sx={{ fontWeight: 600, flex: 1, minWidth: 220 }}>{k.label}</Typography>
              <Chip size="small" variant="outlined" label={naming ? 'Names people' : 'Names nobody'} />
            </Stack>
            {Object.values(k.taken_away).map((words) => (
              <Typography key={words} variant="caption" color="text.secondary" component="div">{words}.</Typography>))}
          </Box>)
      })}
    </GlassCard>
  )
}

function RetentionTab() {
  const qc = useQueryClient()
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ['retention-statement'], queryFn: getStatement })
  if (isLoading) return <Skeleton height={320} />
  if (error) return <ErrorState error={error} onRetry={refetch} />
  if (!data) return null
  const kinds = Object.entries(data.holds.by_kind)
  return (
    <>
      <Alert severity="info" sx={{ mb: 2 }} data-testid="not-law">{data.not_law}</Alert>
      <Periods data={data} />
      <Sites data={data} />
      <OptionalPeriods data={data} onChanged={() => qc.invalidateQueries({ queryKey: ['retention-statement'] })} />
      <GlassCard sx={{ p: 2, mb: 2 }} data-testid="holds">
        <Heading note={data.holds.words}>Holds in force</Heading>
        <Typography variant="body2">
          {data.holds.in_force
            ? `${data.holds.in_force} in force: ${kinds.map(([kind, n]) => `${n} on ${kind.toLowerCase().replace('_', ' ')}`).join(', ')}.`
            : 'None is in force.'}
        </Typography>
      </GlassCard>
      <Kept data={data} />
      <GlassCard sx={{ p: 2 }} data-testid="erasure">
        <Heading note="Carried out by a person answering a data-subject request. It removes or blanks:">A data-subject erasure</Heading>
        {data.erasure.map((words) => <Typography key={words} variant="body2">• {words}.</Typography>)}
      </GlassCard>
    </>
  )
}

function Report({ r }: { r: SubjectReport }) {
  return (
    <GlassCard sx={{ p: 2, mt: 2 }} data-testid="report">
      <Typography variant="h6" sx={{ fontWeight: 700 }}>{subjectLine(r)}</Typography>
      <Typography variant="body1" sx={{ mb: 1 }} data-testid="totals">{totalsLine(r)}</Typography>
      <Alert severity="info" sx={{ mb: 1 }}>{r.what_it_is}</Alert>
      {r.text_is_text && <Alert severity="warning" sx={{ mb: 1 }} data-testid="text-is-text">{r.text_is_text}</Alert>}
      {r.held.map((h) => (
        <Box key={h.table} data-testid="held" sx={{ py: 1, borderTop: 1, borderColor: 'divider' }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>{h.label} — {heldCount(h)}</Typography>
          {(h.lines ?? []).map((l) => (
            <Typography key={l.column} variant="body2" data-testid="line">
              {lineWords(l)}{' '}
              <Box component="span" sx={{ color: 'text.secondary' }}>— {l.part === 'ABOUT' ? 'concerns them' : 'a step they took'}</Box>
            </Typography>))}
          {(h.matches ?? []).map((m) => (
            <Typography key={`${m.kind}-${m.text}`} variant="body2" data-testid="match">{matchLine(m)}</Typography>))}
          {h.matches && r.matches_shown != null && (h.distinct ?? 0) > r.matches_shown && (
            <Typography variant="caption" color="text.secondary">
              The first {r.matches_shown} of {h.distinct} different pieces of text are shown. Type more of it.
            </Typography>)}
        </Box>))}
      {!!r.nothing_in.length && (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }} data-testid="nothing-in">
          Nothing in: {r.nothing_in.join('; ')}.
        </Typography>)}
      {r.searches && (
        <Box sx={{ mt: 1.5 }} data-testid="searches">
          <Typography variant="body2" sx={{ fontWeight: 600 }}>{searchesLine(r.searches)}</Typography>
          <Typography variant="caption" color="text.secondary">{r.searches.words}</Typography>
        </Box>)}
      <Box sx={{ mt: 1.5 }} data-testid="not-read">
        <Typography variant="body2" sx={{ fontWeight: 600 }}>Not read here</Typography>
        {r.not_read.map((words) => <Typography key={words} variant="body2" color="text.secondary">• {words}</Typography>)}
      </Box>
      <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1.5 }}>
        That this report was asked for, and by whom, is written in the audit log.
      </Typography>
    </GlassCard>
  )
}

function PersonTab() {
  const [looking, setLooking] = useState<Looking>('STAFF')
  const [words, setWords] = useState('')
  const [found, setFound] = useState<{ items: Found[]; more: boolean } | null>(null)
  const report = useMutation({
    mutationFn: (ask: { kind: Looking; id?: string; text?: string }) =>
      ask.kind === 'TEXT' ? getWrittenReport(ask.text ?? '') : ask.kind === 'VISITOR' ? getVisitorReport(ask.id ?? '') : getStaffReport(ask.id ?? ''),
  })
  const find = useMutation({
    mutationFn: () => findSubject(looking as 'STAFF' | 'VISITOR', words.trim()),
    onSuccess: (answer) => setFound({ items: answer.found, more: answer.more }),
  })
  const typed = words.trim()
  const ready = typed.length >= FEWEST && !find.isPending && !report.isPending
  const go = () => {
    report.reset()
    if (looking === 'TEXT') { setFound(null); report.mutate({ kind: 'TEXT', text: typed }) } else find.mutate()
  }
  const change = (next: Looking) => { setLooking(next); setFound(null); find.reset(); report.reset() }
  const failed = find.error ?? report.error
  return (
    <>
      <GlassCard sx={{ p: 2 }}>
        <Typography variant="body2" sx={{ mb: 1.5 }}>
          Where a person appears in investigations, evidence packages, responses, the occurrence book&apos;s reviews,
          procedures, authorisations of visits, maintenance, answers to advice, briefings and cases — and how often.
          It does not show what each record says.
        </Typography>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <TextField select size="small" label="Look for" value={looking} sx={{ minWidth: 300 }}
                     onChange={(e) => change(e.target.value as Looking)}>
            {(Object.keys(LOOKING) as Looking[]).map((k) => <MenuItem key={k} value={k}>{LOOKING[k].label}</MenuItem>)}
          </TextField>
          <TextField size="small" label={LOOKING[looking].field} value={words} sx={{ minWidth: 260, flex: 1 }}
                     onChange={(e) => setWords(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter' && ready) go() }}
                     helperText={`At least ${FEWEST} letters or digits`} slotProps={{ htmlInput: { maxLength: 80 } }} />
          <Button variant="contained" disabled={!ready} onClick={go}>{LOOKING[looking].button}</Button>
        </Stack>
        {looking === 'TEXT' && (
          <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1 }}>
            A member of staff or a visitor is better chosen by their record: a name typed finds only where somebody wrote it.
          </Typography>)}
      </GlassCard>
      {!!failed && <Alert severity="error" sx={{ mt: 2 }}>{apiError(failed)}</Alert>}
      {found && looking !== 'TEXT' && (
        <GlassCard sx={{ p: 2, mt: 2 }} data-testid="found">
          {!found.items.length && <Typography variant="body2">Nobody of that name.</Typography>}
          {found.items.map((p) => (
            <Stack key={p.id} direction="row" data-testid="found-row" sx={{ gap: 1, alignItems: 'center', py: 0.5 }}>
              <Typography variant="body2" sx={{ flex: 1 }}>
                {p.name ?? 'No name'}{p.detail ? ` — ${p.detail}` : ''}{p.in_use ? '' : ' (no longer in use)'}
              </Typography>
              <Button size="small" variant="outlined" disabled={report.isPending}
                      onClick={() => report.mutate({ kind: looking, id: p.id })}>
                Where they appear
              </Button>
            </Stack>))}
          {found.more && <Typography variant="caption" color="text.secondary">More match than are shown. Type more of the name.</Typography>}
        </GlassCard>)}
      {report.isPending && <Skeleton height={200} sx={{ mt: 2 }} />}
      {report.data && <Report r={report.data} />}
    </>
  )
}

export default function DataRetention() {
  const mayAsk = usePermission('subject:report')
  const [part, setPart] = useState<Part>('retention')
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Data Retention" subtitle="How long each kind of record is kept, and where a person appears in the records" />
      <Tabs value={part} onChange={(_, v: Part) => setPart(v)} sx={{ mb: 2 }}>
        <Tab value="retention" label="Retention" />
        {mayAsk && <Tab value="person" label="About a person" />}
      </Tabs>
      {part === 'person' && mayAsk ? <PersonTab /> : <RetentionTab />}
    </Box>
  )
}

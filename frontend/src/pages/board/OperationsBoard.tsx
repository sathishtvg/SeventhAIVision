/**
 * The operations board: what each part of the operation counts — for a site, a
 * customer or every site — the same figures site by site, and the daily
 * briefing made of a day's counts.
 *
 * Every figure is a count of what is recorded. A figure marked "now" is as
 * things stand at the moment of asking; a time is the middle one of those
 * measured and is a dash when there was nothing to time. Nothing here is a
 * score. A section the reader may not read is left out and named.
 *
 * A briefing is drafted by the server and published by a person.
 *
 * A report is the records themselves, taken out as a file by somebody who may.
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, Link, MenuItem, Skeleton, Tab, Table, TableBody, TableCell, TableContainer, TableHead,
  TableRow, Tabs, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { apiError, getBoard, getBoardSites, listBriefings } from '@/api/operationsBoard'
import type { Board, Briefing, Figures, NotRead, SectionKey } from '@/api/operationsBoard'
import { BriefingDialog, DraftDialog } from '@/components/board/BriefingDialogs'
import { ReportsTab } from '@/components/board/ReportsTab'
import {
  COLUMNS, DEVICE_COLOUR, DEVICE_LABEL, DEVICE_ORDER, PATROL_KINDS, PATROL_LABEL, PERIODS, STATE_COLOUR, STATE_LABEL,
  briefingFor, briefingLine, cell, fmt, fmtDay, lasting, patrolLine, periodLabel,
} from '@/components/board/boardFormat'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }
type Part = 'board' | 'sites' | 'briefings' | 'reports'

/** One figure with its label. "now" says it is as things stand, not what fell in the period. */
function Tile({ label, value, now, of }: { label: string; value: ReactNode; now?: boolean; of?: string }) {
  return (
    <Box data-testid="tile" sx={{ minWidth: 104 }}>
      <Typography variant="h5" sx={{ fontWeight: 700, lineHeight: 1.15 }}>{value}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
        {label}{now ? ' · now' : ''}</Typography>
      {of && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{of}</Typography>}
    </Box>
  )
}

const Tiles = ({ children }: { children: ReactNode }) => (
  <Stack direction="row" sx={{ gap: 3, flexWrap: 'wrap', rowGap: 1.5 }}>{children}</Stack>)

function SectionBody({ k, f, board }: { k: SectionKey; f: Figures; board: Board }) {
  if (k === 'INCIDENTS' && f.INCIDENTS) {
    const s = f.INCIDENTS
    const by = (['critical', 'high', 'medium', 'low'] as const).filter((sev) => s.by_severity[sev])
    return (
      <>
        <Tiles>
          <Tile label="Opened" value={s.opened} />
          <Tile label="Resolved" value={s.resolved} />
          <Tile label="Of those opened, still open" value={s.opened_still_open} now />
          <Tile label="Open in all" value={s.open_now} now />
        </Tiles>
        {!!by.length && (
          <Stack direction="row" sx={{ gap: 0.75, mt: 1, flexWrap: 'wrap' }}>
            {by.map((sev) => <Chip key={sev} size="small" variant="outlined" label={`${s.by_severity[sev]} ${sev}`} />)}
          </Stack>)}
      </>
    )
  }
  if (k === 'RESPONSE' && f.RESPONSE) {
    const s = f.RESPONSE
    const missed = s.missed.acknowledge + s.missed.arrival + s.missed.resolve
    return (
      <>
        <Tiles>
          <Tile label="Acted on" value={`${s.acknowledged} of ${s.opened}`} of={`half within ${lasting(s.acknowledge_seconds)}`} />
          <Tile label="Resolved" value={`${s.resolved} of ${s.opened}`} of={`half within ${lasting(s.resolve_seconds)}`} />
          <Tile label="Guards sent" value={s.sent} of={`${s.arrived} arrived, ${s.declined} declined`} />
          <Tile label="Sent to arrived" value={lasting(s.arrive_seconds)} of="the middle time" />
          {board.clocks_on_since && <Tile label="Response clocks missed" value={missed} />}
        </Tiles>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }} data-testid="clocks">
          {board.clocks_on_since
            ? `Clocks missed: ${s.missed.acknowledge} to acknowledge, ${s.missed.arrival} to arrive, ${s.missed.resolve} to resolve. The clocks have been on since ${fmt(board.clocks_on_since)}.`
            : 'The response clocks are switched off, so nothing is measured against them. That is not the same as none being missed.'}
        </Typography>
      </>
    )
  }
  if (k === 'PATROLS' && f.PATROLS) {
    return (
      <Box>
        {PATROL_KINDS.map((kind) => {
          const p = f.PATROLS![kind]
          return (
            <Typography key={kind} variant="body2" data-testid="patrol-kind">
              <strong>{PATROL_LABEL[kind]}:</strong> {p ? patrolLine(p) : 'not shown to you'}
            </Typography>)
        })}
      </Box>
    )
  }
  if (k === 'GUARDS' && f.GUARDS) {
    const s = f.GUARDS
    return (
      <Tiles>
        <Tile label="On shift" value={s.on_shift_now} now />
        <Tile label="Due, not started" value={s.due_not_started_now} now />
        <Tile label="Shifts due to begin" value={s.shifts} of={`${s.worked} worked`} />
        <Tile label="Started late" value={s.late} />
        <Tile label="Not started" value={s.not_started} />
      </Tiles>
    )
  }
  if (k === 'DEVICES' && f.DEVICES) {
    const s = f.DEVICES
    return (
      <>
        <Tiles><Tile label="Devices" value={s.devices} now /></Tiles>
        <Stack direction="row" sx={{ gap: 0.75, mt: 1, flexWrap: 'wrap' }}>
          {DEVICE_ORDER.filter((state) => s.by_state[state]).map((state) => (
            <Chip key={state} size="small" color={DEVICE_COLOUR[state]} label={`${DEVICE_LABEL[state]}: ${s.by_state[state]}`} />))}
        </Stack>
      </>
    )
  }
  if (k === 'VISITORS' && f.VISITORS) {
    const s = f.VISITORS
    return (
      <Tiles>
        <Tile label="On site" value={s.on_site_now} now />
        {s.waiting_now !== null && <Tile label="Visits waiting for a decision" value={s.waiting_now} now />}
        <Tile label="Arrivals logged" value={s.arrived} />
        <Tile label="Departures logged" value={s.departed} />
        <Tile label="Refused" value={s.refused} />
      </Tiles>
    )
  }
  if (k === 'MAINTENANCE' && f.MAINTENANCE) {
    const s = f.MAINTENANCE
    return (
      <Tiles>
        <Tile label="Suggestions waiting for a person" value={s.suggested_now} now />
        <Tile label="Orders in hand" value={s.open_now + s.in_progress_now} now of={`${s.overdue_now} overdue`} />
        <Tile label="Raised" value={s.raised} />
        <Tile label="Completed" value={s.done} />
      </Tiles>
    )
  }
  return null
}

function NotShown({ items }: { items: NotRead[] }) {
  if (!items.length) return null
  return (
    <Alert severity="info" sx={{ mb: 2 }} data-testid="not-read">
      Not shown to you: {items.map((n) => `${n.title} (read under ${n.needs})`).join('; ')}.
    </Alert>
  )
}

function BoardTab({ siteId, setSiteId, clientId, setClientId, days, setDays }: {
  siteId: string; setSiteId: (v: string) => void; clientId: string; setClientId: (v: string) => void
  days: number; setDays: (v: number) => void
}) {
  // The sites and customers the reader may choose from are the ones the table of sites gives them.
  const choice = useQuery({ queryKey: ['board-sites', '', days], queryFn: () => getBoardSites({ days }), placeholderData: keepPreviousData })
  const { data, isLoading, error } = useQuery({
    queryKey: ['board', siteId, clientId, days],
    queryFn: () => getBoard({ site_id: siteId || undefined, client_id: clientId || undefined, days }),
    placeholderData: keepPreviousData, refetchInterval: 60_000,
  })
  const sites = (choice.data?.sites ?? []).filter((s) => !clientId || s.client?.id === clientId)
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 190 }} slotProps={shrunk}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">Every site</MenuItem>
            {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          {!!choice.data?.clients.length && (
            <TextField select size="small" label="Customer" value={clientId} sx={{ minWidth: 190 }} slotProps={shrunk}
                       onChange={(e) => { setClientId(e.target.value); setSiteId('') }}>
              <MenuItem value="">Every customer</MenuItem>
              {choice.data.clients.map((c) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
            </TextField>)}
          <TextField select size="small" label="Period" value={days} sx={{ minWidth: 180 }}
                     onChange={(e) => setDays(Number(e.target.value))}>
            {PERIODS.map((d) => <MenuItem key={d} value={d}>{periodLabel(d)}</MenuItem>)}
          </TextField>
          {data && (
            <Typography variant="caption" color="text.secondary" data-testid="scope">
              {data.scope.site?.name ?? data.scope.client?.name ?? (data.scope.every_site ? 'Every site' : 'The sites you are held to')}
              {' · '}{data.scope.sites} site{data.scope.sites === 1 ? '' : 's'} · counted {fmt(data.as_at)}
            </Typography>)}
        </Stack>
      </GlassCard>
      {!!error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(error)}</Alert>}
      {isLoading && <Skeleton height={320} />}
      {data && (
        <>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>{data.note}</Typography>
          <NotShown items={data.not_read} />
          <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' } }}>
            {data.sections.map((s) => (
              <GlassCard key={s.key} sx={{ p: 2 }} data-testid={`section-${s.key}`}>
                <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>{s.title}</Typography>
                <SectionBody k={s.key} f={{ [s.key]: s.figures } as Figures} board={data} />
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>{s.counted_from}</Typography>
              </GlassCard>))}
          </Box>
          {data.advice && (
            <GlassCard sx={{ p: 2, mt: 2 }} data-testid="advice-standing">
              <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>What stands out</Typography>
              <Typography variant="body2">
                {data.advice.standing === 0 ? 'Nothing stands out' : `${data.advice.standing} piece${data.advice.standing === 1 ? '' : 's'} of advice stand${data.advice.standing === 1 ? 's' : ''}`}
                {' '}for these sites over the last {data.advice.weeks} weeks.{' '}
                <Link component={RouterLink} to="/risk-advice">Read it in Risk &amp; Advice</Link>
              </Typography>
              <Typography variant="caption" color="text.secondary">{data.advice.note}</Typography>
            </GlassCard>)}
        </>)}
    </>
  )
}

function Row({ name, sub, figures, onOpen, strong }: {
  name: string; sub?: string; figures: Figures; onOpen?: () => void; strong?: boolean
}) {
  return (
    <TableRow hover={!!onOpen} data-testid="site-row" sx={{ cursor: onOpen ? 'pointer' : 'default' }} onClick={onOpen}>
      <TableCell sx={{ fontWeight: strong ? 700 : 400 }}>{name}</TableCell>
      <TableCell>{sub ?? ''}</TableCell>
      {COLUMNS.map((col) => (
        <TableCell key={col.key} align="right" sx={{ fontWeight: strong ? 700 : 400 }}>{cell(col.of(figures))}</TableCell>))}
    </TableRow>
  )
}

function SitesTab({ days, setDays, onOpen }: { days: number; setDays: (v: number) => void; onOpen: (siteId: string) => void }) {
  const [clientId, setClientId] = useState('')
  const all = useQuery({ queryKey: ['board-sites', '', days], queryFn: () => getBoardSites({ days }), placeholderData: keepPreviousData })
  const { data, isLoading, error } = useQuery({
    queryKey: ['board-sites', clientId, days], queryFn: () => getBoardSites({ client_id: clientId || undefined, days }),
    placeholderData: keepPreviousData,
  })
  const head = (first: string, second: string) => (
    <TableHead>
      <TableRow>
        <TableCell>{first}</TableCell><TableCell>{second}</TableCell>
        {COLUMNS.map((col) => <TableCell key={col.key} align="right">{col.label}</TableCell>)}
      </TableRow>
    </TableHead>)
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          {!!all.data?.clients.length && (
            <TextField select size="small" label="Customer" value={clientId} sx={{ minWidth: 190 }} slotProps={shrunk}
                       onChange={(e) => setClientId(e.target.value)}>
              <MenuItem value="">Every customer</MenuItem>
              {all.data.clients.map((c) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
            </TextField>)}
          <TextField select size="small" label="Period" value={days} sx={{ minWidth: 180 }}
                     onChange={(e) => setDays(Number(e.target.value))}>
            {PERIODS.map((d) => <MenuItem key={d} value={d}>{periodLabel(d)}</MenuItem>)}
          </TextField>
          <Typography variant="caption" color="text.secondary">
            “Now” figures are as things stand; the others are for the period. A dash is a section you may not read.
          </Typography>
        </Stack>
      </GlassCard>
      {!!error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(error)}</Alert>}
      {isLoading && <Skeleton height={260} />}
      {data && (
        <>
          <NotShown items={data.not_read} />
          <GlassCard sx={{ p: 0, mb: 2 }}>
            <TableContainer>
              <Table size="small" data-testid="sites-table" aria-label="The board's figures for each site">
                {head('Site', 'Customer')}
                <TableBody>
                  {data.sites.map((s) => (
                    <Row key={s.id} name={s.is_active ? s.name : `${s.name} (not in use)`} sub={s.client?.name}
                         figures={s.figures} onOpen={() => onOpen(s.id)} />))}
                  {data.no_site && <Row name="At no site" sub="No camera, or the organisation's own" figures={data.no_site} />}
                  <Row name="Together" figures={data.total} strong />
                  {!data.sites.length && (
                    <TableRow><TableCell colSpan={COLUMNS.length + 2}>No site is shown to you.</TableCell></TableRow>)}
                </TableBody>
              </Table>
            </TableContainer>
          </GlassCard>
          {!!data.clients.length && (
            <GlassCard sx={{ p: 0 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 700, p: 2, pb: 1 }}>By customer</Typography>
              <TableContainer>
                <Table size="small" data-testid="clients-table" aria-label="The board's figures summed for each customer">
                  {head('Customer', 'Sites')}
                  <TableBody>
                    {data.clients.map((c) => <Row key={c.id} name={c.name} sub={String(c.sites)} figures={c.figures} />)}
                  </TableBody>
                </Table>
              </TableContainer>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', p: 2, pt: 1 }}>
                A customer's figures are its sites' counts added together. Its response times are on the board, chosen by
                customer: a middle time is not a sum.
              </Typography>
            </GlassCard>)}
        </>)}
    </>
  )
}

function BriefingsTab() {
  const qc = useQueryClient()
  const [open, setOpen] = useState<string | null>(null)
  const [drafting, setDrafting] = useState<{ siteId: string; day: string } | true | null>(null)
  const [aside, setAside] = useState(false)
  const { data, isLoading, error } = useQuery({
    queryKey: ['briefings', aside], queryFn: () => listBriefings(aside ? { state: 'DISCARDED' } : {}),
  })
  const sites = useQuery({ queryKey: ['board-sites', '', 1], queryFn: () => getBoardSites({ days: 1 }) })
  const again = () => qc.invalidateQueries({ queryKey: ['briefings'] })
  const drafted = (b: Briefing) => { setDrafting(null); again(); setOpen(b.id) }
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <Typography variant="body2" color="text.secondary" sx={{ flex: 1, minWidth: 260 }}>
            A day's counts in sentences. The platform drafts it; a person reads it, leaves out what should not go out,
            adds a note and publishes it. A published briefing is not changed.
          </Typography>
          {data?.can_manage && (
            <Button size="small" onClick={() => setAside((v) => !v)}>{aside ? 'Show what is current' : 'Show drafts set aside'}</Button>)}
          {data?.can_manage && (
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => setDrafting(true)}>Draft a briefing</Button>)}
        </Stack>
      </GlassCard>
      {!!error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(error)}</Alert>}
      {isLoading && <Skeleton height={200} />}
      {data && !data.items.length && (
        <Alert severity="info">{aside ? 'No draft has been set aside.' : 'No briefing has been published yet.'}</Alert>)}
      {(data?.items ?? []).map((b) => (
        <GlassCard key={b.id} sx={{ p: 2, mb: 1, cursor: 'pointer' }} data-testid="briefing-row" onClick={() => setOpen(b.id)}>
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>{fmtDay(b.briefing_date)}</Typography>
            <Typography variant="body2">{briefingFor(b)}</Typography>
            <Chip size="small" color={STATE_COLOUR[b.state]} label={STATE_LABEL[b.state]} />
            {b.revision > 1 && <Chip size="small" variant="outlined" label={`Revision ${b.revision}`} />}
            {b.replaced_by && <Chip size="small" variant="outlined" label="Replaced by a later revision" />}
            <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>{briefingLine(b)}</Typography>
          </Stack>
        </GlassCard>))}
      <DraftDialog open={drafting !== null} onClose={() => setDrafting(null)} onDrafted={drafted}
                   sites={sites.data?.sites ?? []} maxDaysBack={data?.max_days_back ?? 31}
                   start={drafting && drafting !== true ? drafting : undefined} />
      <BriefingDialog id={open} onClose={() => setOpen(null)} onChanged={again}
                      onCorrect={(b) => { setOpen(null); setDrafting({ siteId: b.site?.id ?? '', day: b.briefing_date }) }} />
    </>
  )
}

export default function OperationsBoard() {
  const board = usePermission('board:read')
  const briefings = usePermission('briefing:read')
  const reports = usePermission('opsreport:export')
  const [part, setPart] = useState<Part>(board ? 'board' : briefings ? 'briefings' : 'reports')
  const [siteId, setSiteId] = useState('')
  const [clientId, setClientId] = useState('')
  const [days, setDays] = useState(1)
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Operations Board"
                  subtitle="What each part of the operation counts, site by site, and the daily briefing. Counts of what is recorded — not a score" />
      <Tabs value={part} onChange={(_, v: Part) => setPart(v)} sx={{ mb: 2 }}>
        {board && <Tab value="board" label="Board" />}
        {board && <Tab value="sites" label="Sites and customers" />}
        {briefings && <Tab value="briefings" label="Daily briefing" />}
        {reports && <Tab value="reports" label="Reports" />}
      </Tabs>
      {part === 'board' && board && (
        <BoardTab siteId={siteId} setSiteId={setSiteId} clientId={clientId} setClientId={setClientId} days={days} setDays={setDays} />)}
      {part === 'sites' && board && (
        <SitesTab days={days} setDays={setDays} onOpen={(id) => { setClientId(''); setSiteId(id); setPart('board') }} />)}
      {part === 'briefings' && briefings && <BriefingsTab />}
      {part === 'reports' && reports && <ReportsTab />}
    </Box>
  )
}

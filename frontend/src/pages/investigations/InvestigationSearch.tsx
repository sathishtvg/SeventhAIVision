/**
 * Smart Investigation — one search across every kind of security record.
 *
 * Two ways in, and one truth. A phrase can be typed; what was made of it fills
 * the filters underneath, where it can be read and corrected. Or the filters
 * are set by hand. Either way the filters are the search that ran.
 *
 * The phrase is read by fixed rules, not understood: the page says which words
 * became which filter, and which words were not used. A source that was not
 * looked in is named, with the reason — an empty result never passes for
 * "nothing happened there".
 */
import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Checkbox, Chip, Collapse, Link, MenuItem, Skeleton, Table, TableBody, TableCell,
  TableContainer, TableHead, TablePagination, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'
import RouteIcon from '@mui/icons-material/Route'
import CreateNewFolderIcon from '@mui/icons-material/CreateNewFolder'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { SeverityChip } from '@/components/common/SeverityChip'
import { usePermission } from '@/hooks/usePermission'
import { getSites } from '@/api/sites'
import { getCameras } from '@/api/cameras'
import { apiError, getSources, search } from '@/api/investigations'
import type { Found, Kind, SearchAnswer, SearchBody, Severity } from '@/api/investigations'
import type { AlertSeverity } from '@/types/api'
import { FileDialog, TrailDialog } from '@/components/investigations/InvestigationDialogs'
import {
  KIND_LABEL, fmt, followable, fromLocalInput, home, keyOf, pretty, subject, toLocalInput,
} from '@/components/investigations/investigationFormat'
import { InvestigationNav } from './InvestigationNav'

const SEVERITIES: Severity[] = ['critical', 'high', 'medium', 'low', 'info']
const ROWS = 50

interface Filters {
  from: string; to: string; kinds: Kind[]; siteId: string; cameraId: string; severity: string; plate: string
  person: string; text: string
}
const EMPTY: Filters = { from: '', to: '', kinds: [], siteId: '', cameraId: '', severity: '', plate: '', person: '', text: '' }

function bodyOf(f: Filters): SearchBody {
  return {
    from: fromLocalInput(f.from), to: fromLocalInput(f.to), kinds: f.kinds.length ? f.kinds : undefined,
    site_ids: f.siteId ? [f.siteId] : undefined, camera_ids: f.cameraId ? [f.cameraId] : undefined,
    severities: f.severity ? [f.severity as Severity] : undefined, plate: f.plate.trim() || undefined,
    person: f.person.trim() || undefined, text: f.text.trim() || undefined,
  }
}

/** The filters as the server says the search ran — so a phrase can be read and corrected. */
function filtersOf(answer: SearchAnswer): Filters {
  const q = answer.query
  return {
    from: toLocalInput(q.from), to: toLocalInput(q.to), kinds: q.kinds, siteId: q.site_ids[0] ?? '',
    cameraId: q.camera_ids[0] ?? '', severity: q.severities[0] ?? '', plate: q.plate ?? '', person: q.person ?? '',
    text: q.text ?? '',
  }
}

export default function InvestigationSearch() {
  const navigate = useNavigate()
  const canFile = usePermission('investigation:manage')
  const [phrase, setPhrase] = useState('')
  const [filters, setFilters] = useState<Filters>(EMPTY)
  const [ran, setRan] = useState<SearchBody | null>(null)
  const [page, setPage] = useState(0)
  const [picked, setPicked] = useState<Record<string, Found>>({})
  const [filing, setFiling] = useState(false)
  const [filed, setFiled] = useState<{ id: string; added: number; already: number } | null>(null)
  const [following, setFollowing] = useState<{ plate?: string; watchlist_entry_id?: string } | null>(null)
  const [help, setHelp] = useState(false)

  const { data: sources } = useQuery({ queryKey: ['investigation-sources'], queryFn: getSources, staleTime: 300_000 })
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data: cameras } = useQuery({ queryKey: ['cameras'], queryFn: getCameras })

  const run = useMutation({
    mutationFn: (v: { body: SearchBody; page: number; fill: boolean }) =>
      search({ ...v.body, limit: ROWS, offset: v.page * ROWS }),
    onSuccess: (answer, v) => {
      setRan(v.body)
      setPage(v.page)
      if (v.fill) setFilters(filtersOf(answer))
    },
  })
  const answer = run.data
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => setFilters((f) => ({ ...f, [key]: value }))

  function ask(e: FormEvent) {
    e.preventDefault()
    if (!phrase.trim()) return
    setPicked({})
    run.mutate({ body: { phrase: phrase.trim() }, page: 0, fill: true })
  }
  function searchWithFilters() {
    setPicked({})
    run.mutate({ body: bodyOf(filters), page: 0, fill: false })
  }
  const pickedList = Object.values(picked)
  const searchable = (sources?.sources ?? []).filter((s) => s.may_search)
  // The cameras list carries each camera's site; the shared type does not say so.
  const camerasHere = ((cameras ?? []) as { id: string; name: string; site_id?: string | null }[])
    .filter((c) => !filters.siteId || c.site_id === filters.siteId)

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Investigation Search"
                  subtitle="One search across alerts, incidents, plates, faces, doors, visitors, the occurrence book, drones, alarms, patrols and sensors" />
      <InvestigationNav />

      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Box component="form" onSubmit={ask}>
          <Stack direction="row" sx={{ gap: 1.5, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <TextField fullWidth size="small" label="Ask in a few words" value={phrase} sx={{ flex: 1, minWidth: 280 }}
                       placeholder="vehicles at North Gate last night between 1 and 3:30"
                       onChange={(e) => setPhrase(e.target.value)} slotProps={{ htmlInput: { maxLength: 300 } }} />
            <Button type="submit" variant="contained" startIcon={<SearchIcon />} disabled={!phrase.trim() || run.isPending}>
              Ask</Button>
            <Button onClick={() => setHelp((h) => !h)}>{help ? 'Hide' : 'What can I say?'}</Button>
          </Stack>
        </Box>
        <Collapse in={help}>
          {sources && (
            <Box sx={{ mt: 1.5 }} data-testid="phrase-help">
              <Alert severity="info" sx={{ mb: 1 }}>{sources.phrase.limits}</Alert>
              <Typography variant="body2"><b>When:</b> {sources.phrase.periods.join(' · ')}</Typography>
              <Typography variant="body2"><b>What:</b> {Object.values(sources.phrase.kinds).map((w) => w[0]).join(' · ')}</Typography>
              <Typography variant="body2"><b>Kinds of event:</b> {Object.values(sources.phrase.event_types).map((w) => w[0]).join(' · ')}</Typography>
              <Typography variant="body2"><b>Plates and people:</b> {[...sources.phrase.plates, ...sources.phrase.people, ...sources.phrase.words].join(' · ')}</Typography>
              <Typography variant="body2"><b>Where:</b> {sources.phrase.places}</Typography>
            </Box>
          )}
        </Collapse>
        {answer?.phrase && (
          <Box sx={{ mt: 1.5 }} data-testid="phrase-made">
            <Stack direction="row" sx={{ gap: 0.75, flexWrap: 'wrap', alignItems: 'center' }}>
              <Typography variant="caption" color="text.secondary">Read as</Typography>
              {answer.phrase.understood.map((u) => (
                <Tooltip key={`${u.field}:${u.words}`} title={`“${u.words}”`}>
                  <Chip size="small" variant="outlined" label={`${pretty(u.field)}: ${u.as}`} /></Tooltip>))}
            </Stack>
            {answer.phrase.assumed.map((a) => (
              <Typography key={a} variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>{a}</Typography>))}
            {answer.phrase.not_understood.length > 0 && (
              <Alert severity="warning" sx={{ mt: 1 }}>
                Not understood, and not used: <b>{answer.phrase.not_understood.join(', ')}</b>. The search below ran
                without {answer.phrase.not_understood.length === 1 ? 'it' : 'them'} — use the filters to narrow it.</Alert>)}
          </Box>
        )}
      </GlassCard>

      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField size="small" type="datetime-local" label="From" value={filters.from} sx={{ width: 210 }}
                     onChange={(e) => set('from', e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
          <TextField size="small" type="datetime-local" label="To" value={filters.to} sx={{ width: 210 }}
                     onChange={(e) => set('to', e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
          <TextField select size="small" label="Kinds of record" sx={{ minWidth: 220, maxWidth: 360 }}
                     value={filters.kinds} onChange={(e) => set('kinds', e.target.value as unknown as Kind[])}
                     slotProps={{ select: { multiple: true, displayEmpty: true,
                       renderValue: (v) => (v as Kind[]).length ? (v as Kind[]).map((k) => KIND_LABEL[k]).join(', ') : 'Everything I may read' },
                       inputLabel: { shrink: true } }}>
            {searchable.map((s) => <MenuItem key={s.kind} value={s.kind}>{s.label}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Site" value={filters.siteId} sx={{ minWidth: 170 }}
                     onChange={(e) => setFilters((f) => ({ ...f, siteId: e.target.value, cameraId: '' }))}>
            <MenuItem value="">All sites</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Camera" value={filters.cameraId} sx={{ minWidth: 170 }}
                     onChange={(e) => set('cameraId', e.target.value)}>
            <MenuItem value="">Any camera</MenuItem>
            {camerasHere.map((c) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Severity" value={filters.severity} sx={{ minWidth: 130 }}
                     onChange={(e) => set('severity', e.target.value)}>
            <MenuItem value="">Any</MenuItem>
            {SEVERITIES.map((s) => <MenuItem key={s} value={s}>{pretty(s)}</MenuItem>)}
          </TextField>
          <TextField size="small" label="Number plate" value={filters.plate} sx={{ width: 150 }}
                     onChange={(e) => set('plate', e.target.value)} slotProps={{ htmlInput: { maxLength: 16 } }} />
          <TextField size="small" label="Name" value={filters.person} sx={{ width: 170 }}
                     onChange={(e) => set('person', e.target.value)} slotProps={{ htmlInput: { maxLength: 80 } }} />
          <TextField size="small" label="Words" value={filters.text} sx={{ width: 170 }}
                     onChange={(e) => set('text', e.target.value)} slotProps={{ htmlInput: { maxLength: 120 } }} />
          <Button variant="outlined" startIcon={<SearchIcon />} disabled={run.isPending} onClick={searchWithFilters}>
            Search</Button>
          <Button onClick={() => { setFilters(EMPTY); setPhrase('') }}>Clear</Button>
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          With no period, the last {sources?.default_hours ?? 24} hours. A search covers at most {sources?.max_days ?? 92} days.
          A plate is matched whole; write * for the part you do not have.</Typography>
      </GlassCard>

      {run.isError && <Alert severity="error" sx={{ mb: 2 }}>{apiError(run.error)}</Alert>}
      {filed && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setFiled(null)}
               action={<Button color="inherit" size="small" onClick={() => navigate(`/investigations/${filed.id}`)}>Open it</Button>}>
          {filed.added} record{filed.added === 1 ? '' : 's'} filed
          {filed.already > 0 && `; ${filed.already} already in the investigation`}.</Alert>)}

      {run.isPending && <GlassCard sx={{ p: 2 }}><Skeleton height={240} /></GlassCard>}
      {answer && !run.isPending && (
        <GlassCard sx={{ p: 2 }}>
          <Stack direction="row" sx={{ gap: 0.75, flexWrap: 'wrap', alignItems: 'center', mb: 1 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mr: 1 }}>
              {answer.total} record{answer.total === 1 ? '' : 's'}</Typography>
            {Object.entries(answer.found).map(([kind, n]) => (
              <Chip key={kind} size="small" variant="outlined" label={`${KIND_LABEL[kind as Kind]} ${n}`} />))}
            <Box sx={{ flex: 1 }} />
            {canFile && (
              <Button size="small" variant="contained" startIcon={<CreateNewFolderIcon />} disabled={!pickedList.length}
                      onClick={() => setFiling(true)}>
                File {pickedList.length || ''} in an investigation</Button>)}
          </Stack>
          {answer.not_searched.length > 0 && (
            <Alert severity="info" sx={{ mb: 1.5 }} data-testid="not-searched">
              <b>Not searched</b> — so nothing here says whether anything happened there:
              {answer.not_searched.map((n) => (
                <Typography key={n.kind} variant="body2" sx={{ display: 'block' }}>{n.label}: {n.reason}</Typography>))}
            </Alert>)}
          {!answer.items.length ? <Alert severity="info">Nothing matched in what was searched.</Alert> : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    {canFile && <TableCell padding="checkbox" />}
                    <TableCell>When</TableCell><TableCell>Kind</TableCell><TableCell>What</TableCell>
                    <TableCell>Where</TableCell><TableCell>Severity</TableCell><TableCell />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {answer.items.map((r) => {
                    const key = keyOf(r)
                    const about = subject(r)
                    const follow = followable(r)
                    const at = home(r)
                    return (
                      <TableRow key={key} hover selected={!!picked[key]} data-testid="found-row">
                        {canFile && (
                          <TableCell padding="checkbox">
                            <Checkbox size="small" checked={!!picked[key]}
                                      slotProps={{ input: { 'aria-label': `Select ${KIND_LABEL[r.kind]} ${r.title}` } }}
                                      onChange={(_, on) => setPicked((p) => {
                                        const next = { ...p }
                                        if (on) next[key] = r; else delete next[key]
                                        return next
                                      })} /></TableCell>)}
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{fmt(r.occurred_at)}</TableCell>
                        <TableCell><Chip size="small" label={KIND_LABEL[r.kind]} />
                          {r.event_type && r.kind !== 'PLATE_READ' && r.kind !== 'FACE_MATCH' && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                              {pretty(r.event_type)}</Typography>)}</TableCell>
                        <TableCell sx={{ maxWidth: 420 }}>
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>{r.title}</Typography>
                          {r.summary && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                            {r.summary}</Typography>}
                          {about && about !== r.title && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                              About: {about}</Typography>)}
                          {r.confidence !== null && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                              Confidence {Math.round(r.confidence * 100)}%{r.status ? ` · ${pretty(r.status)}` : ''}</Typography>)}
                        </TableCell>
                        <TableCell>{r.site_name ?? '—'}
                          {r.camera_name && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                            {r.camera_name}</Typography>}</TableCell>
                        <TableCell>
                          {r.severity && SEVERITIES.includes(r.severity as Severity)
                            ? <SeverityChip severity={r.severity as AlertSeverity} /> : '—'}
                          {r.risk_level && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                            Risk {pretty(r.risk_level)}</Typography>}</TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>
                          {follow && (
                            <Button size="small" startIcon={<RouteIcon />} onClick={() => setFollowing(follow)}>
                              Where seen</Button>)}
                          <Link component="button" variant="caption" sx={{ display: 'block' }}
                                onClick={() => navigate(at.path)}>
                            {at.exact ? 'Open' : `In ${at.screen}`}</Link>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
          <TablePagination component="div" count={answer.total} page={page} rowsPerPage={ROWS} rowsPerPageOptions={[ROWS]}
                           onPageChange={(_, p) => ran && run.mutate({ body: ran, page: p, fill: false })} />
          <Typography variant="caption" color="text.secondary">{answer.note}</Typography>
        </GlassCard>
      )}
      {!answer && !run.isPending && !run.isError && (
        <Alert severity="info">Type what you are looking for, or set the filters and press Search. Every search is
          recorded in the audit log with who made it and what was asked.</Alert>)}

      <FileDialog open={filing} records={pickedList} onClose={() => setFiling(false)}
                  onFiled={(id, added, already) => { setFiling(false); setPicked({}); setFiled({ id, added, already }) }} />
      <TrailDialog subject={following} onClose={() => setFollowing(null)} />
    </Box>
  )
}

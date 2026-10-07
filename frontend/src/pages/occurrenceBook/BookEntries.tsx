/**
 * The book itself: searched, reviewed, and corrected by a further entry.
 *
 * Nothing on this screen edits an entry. "Correct" writes another entry and
 * records which one it corrects and why; both stay in the book.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, MenuItem, Skeleton, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { getSites } from '@/api/sites'
import { apiError, correctEntry, getEntry, reviewEntries, reviewEntry, searchEntries } from '@/api/occurrenceBook'
import type { Entry, Kinds, ReviewOutcome, ReviewState } from '@/api/occurrenceBook'
import { REVIEW_COLOUR, REVIEW_LABEL, correctionMark, fmt, kindLabel, since } from '@/components/occurrenceBook/bookFormat'

type Period = 'today' | 'day' | 'week' | 'all'
const PERIODS: { key: Period; label: string }[] = [
  { key: 'today', label: 'Today' }, { key: 'day', label: 'The last 24 hours' }, { key: 'week', label: 'The last 7 days' },
  { key: 'all', label: 'Any time' },
]
const PAGE = 50
const shown = { select: { displayEmpty: true }, inputLabel: { shrink: true } }

interface AskProps { entry: Entry | null; onClose: () => void; onDone: () => Promise<unknown> }

function ReviewDialog(props: AskProps) {
  return props.entry ? <ReviewForm {...props} entry={props.entry} /> : null
}

function ReviewForm({ entry, onClose, onDone }: AskProps & { entry: Entry }) {
  const toFollow = entry.review?.state === 'follow_up'
  const [outcome, setOutcome] = useState<ReviewOutcome>(toFollow ? 'CLOSED' : 'NOTED')
  const [note, setNote] = useState('')
  const act = useMutation({ mutationFn: () => reviewEntry(entry.id, outcome, note.trim()).then(onDone), onSuccess: onClose })
  const needsNote = outcome !== 'NOTED'
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Review this entry</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>“{entry.body}”</Typography>
          <Typography variant="caption" color="text.secondary">
            {entry.author_name ?? 'Somebody'} · {fmt(entry.occurred_at)}
            {toFollow && entry.review?.note ? ` · to follow up: ${entry.review.note}` : ''}
          </Typography>
          <TextField select label="What you make of it" value={outcome}
                     onChange={(e) => setOutcome(e.target.value as ReviewOutcome)}>
            {!toFollow && <MenuItem value="NOTED">Noted</MenuItem>}
            {!toFollow && <MenuItem value="FOLLOW_UP">To be followed up</MenuItem>}
            {toFollow && <MenuItem value="CLOSED">Followed up: say what was done</MenuItem>}
          </TextField>
          <TextField label={outcome === 'FOLLOW_UP' ? 'What is to be done' : outcome === 'CLOSED' ? 'What was done' : 'A note (optional)'}
                     value={note} multiline minRows={2} onChange={(e) => setNote(e.target.value)}
                     helperText="Seen by the people who keep the book. Not by a client."
                     slotProps={{ htmlInput: { maxLength: 2000 } }} />
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={(needsNote && !note.trim()) || act.isPending} onClick={() => act.mutate()}>
          Record the review</Button>
      </DialogActions>
    </Dialog>
  )
}

function CorrectDialog(props: AskProps) {
  return props.entry ? <CorrectForm {...props} entry={props.entry} /> : null
}

function CorrectForm({ entry, onClose, onDone }: AskProps & { entry: Entry }) {
  const [body, setBody] = useState(entry.body)
  const [reason, setReason] = useState('')
  const act = useMutation({
    mutationFn: () => correctEntry(entry.id, body.trim(), reason.trim()).then(onDone), onSuccess: onClose })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Correct this entry</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <Alert severity="info">
            An entry is never changed. This writes a further entry that says what is right, and records that it
            corrects this one. Both stay in the book.
          </Alert>
          <TextField label="What is right" value={body} multiline minRows={3} onChange={(e) => setBody(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 10000 } }} />
          <TextField label="Why it is being corrected" value={reason} onChange={(e) => setReason(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 2000 } }} />
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => act.mutate()}
                disabled={!body.trim() || !reason.trim() || body.trim() === entry.body.trim() || act.isPending}>
          Write the correction</Button>
      </DialogActions>
    </Dialog>
  )
}

function EntryDialog({ entry, onClose }: { entry: Entry | null; onClose: () => void }) {
  return entry ? <EntryView entry={entry} onClose={onClose} /> : null
}

function EntryView({ entry, onClose }: { entry: Entry; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['dob-entry', entry.id], queryFn: () => getEntry(entry.id) })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{kindLabel(entry.entry_type)} · {fmt(entry.occurred_at)}</DialogTitle>
      <DialogContent>
        {isLoading && <Skeleton height={140} />}
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {data && (
          <Stack sx={{ gap: 1.5 }}>
            <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>{data.body}</Typography>
            <Typography variant="caption" color="text.secondary">
              {[data.author_name ?? 'Somebody no longer on the system', data.site_name ?? 'No site'].join(' · ')}
            </Typography>
            {data.corrects && (
              <Alert severity="info" data-testid="corrects">
                This corrects the entry of {fmt(data.corrects.occurred_at)}: “{data.corrects.body}”.
                {data.correction_reason ? ` Why: ${data.correction_reason}` : ''}
              </Alert>)}
            {data.corrected_by.map((c) => (
              <Alert key={c.id} severity="warning" data-testid="corrected-by">
                Corrected {fmt(c.occurred_at)} by {c.author_name ?? 'somebody'}: “{c.body}”.
                {c.correction_reason ? ` Why: ${c.correction_reason}` : ''}
              </Alert>))}
            {data.review && (
              <>
                <Divider />
                <Typography variant="subtitle2">Reviews</Typography>
                {!data.reviews.length && <Typography variant="body2" color="text.secondary">Not reviewed.</Typography>}
                {data.reviews.map((r) => (
                  <Typography key={r.id} variant="body2" data-testid="review-line">
                    <b>{fmt(r.reviewed_at)}</b> · {r.reviewed_by_name ?? 'Somebody'} ·{' '}
                    {r.outcome === 'NOTED' ? 'noted' : r.outcome === 'FOLLOW_UP' ? 'to be followed up' : 'followed up'}
                    {r.note ? ` — ${r.note}` : ''}
                  </Typography>))}
              </>)}
          </Stack>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

export default function BookEntries({ kinds }: { kinds: Kinds }) {
  const qc = useQueryClient()
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [siteId, setSiteId] = useState('')
  const [review, setReview] = useState<ReviewState | ''>('')
  const [period, setPeriod] = useState<Period>('day')
  const [offset, setOffset] = useState(0)
  const [reviewing, setReviewing] = useState<Entry | null>(null)
  const [correcting, setCorrecting] = useState<Entry | null>(null)
  const [reading, setReading] = useState<Entry | null>(null)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data, isLoading, error } = useQuery({
    queryKey: ['dob-entries', q, kind, siteId, review, period, offset],
    queryFn: () => searchEntries({
      q: q || undefined, entry_type: kind ? [kind] : undefined, site_id: siteId || undefined,
      review: review || undefined, date_from: since(period), limit: PAGE, offset }),
  })
  const again = () => Promise.all([
    qc.invalidateQueries({ queryKey: ['dob-entries'] }), qc.invalidateQueries({ queryKey: ['dob-entry'] }),
    qc.invalidateQueries({ queryKey: ['dob'] })])
  const narrow = <T,>(set: (v: T) => void) => (v: T) => { set(v); setOffset(0) }
  const items = data?.items ?? []
  const toNote = items.filter((e) => e.review?.state === 'unreviewed' && !e.mine)
  const noteAll = useMutation({ mutationFn: () => reviewEntries(toNote.map((e) => e.id)).then(again) })
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}
               component="form" onSubmit={(e: React.FormEvent) => { e.preventDefault(); narrow(setQ)(typed.trim()) }}>
          <TextField size="small" label="Words in an entry" value={typed} sx={{ minWidth: 220, flex: 1 }}
                     onChange={(e) => setTyped(e.target.value)} slotProps={{ htmlInput: { maxLength: 200 } }} />
          <Button type="submit" variant="outlined">Search</Button>
          <TextField select size="small" label="Kind" value={kind} sx={{ minWidth: 170 }} slotProps={shown}
                     onChange={(e) => narrow(setKind)(e.target.value)}>
            <MenuItem value="">Every kind</MenuItem>
            {kinds.kinds.map((k) => <MenuItem key={k.key} value={k.key}>{k.label}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 170 }} slotProps={shown}
                     onChange={(e) => narrow(setSiteId)(e.target.value)}>
            <MenuItem value="">Every site</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Written" value={period} sx={{ minWidth: 170 }}
                     onChange={(e) => narrow(setPeriod)(e.target.value as Period)}>
            {PERIODS.map((p) => <MenuItem key={p.key} value={p.key}>{p.label}</MenuItem>)}
          </TextField>
          {kinds.review_states.length > 0 && (
            <TextField select size="small" label="Review" value={review} sx={{ minWidth: 180 }} slotProps={shown}
                       onChange={(e) => narrow(setReview)(e.target.value as ReviewState | '')}>
              <MenuItem value="">Any</MenuItem>
              {kinds.review_states.map((s) => <MenuItem key={s} value={s}>{REVIEW_LABEL[s]}</MenuItem>)}
            </TextField>)}
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {kinds.can_review && toNote.length > 0 && (
          <Stack direction="row" sx={{ gap: 1.5, alignItems: 'center', mb: 1.5, flexWrap: 'wrap' }}>
            <Typography variant="body2">{toNote.length} on this page {toNote.length === 1 ? 'has' : 'have'} not been reviewed.</Typography>
            <Button size="small" variant="outlined" disabled={noteAll.isPending} onClick={() => noteAll.mutate()}>
              Note {toNote.length === 1 ? 'it' : `all ${toNote.length}`} as read</Button>
            {noteAll.isError && <Typography variant="caption" color="error">{apiError(noteAll.error)}</Typography>}
          </Stack>)}
        {isLoading ? <Skeleton height={220} /> : !items.length && !error ? (
          <Alert severity="info">No entry matches. Entries are written from Guard Ops, or on the phone.</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>When</TableCell><TableCell>Entry</TableCell><TableCell>By</TableCell>
                  {kinds.review_states.length > 0 && <TableCell>Review</TableCell>}<TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((e) => {
                  const mark = correctionMark(e)
                  return (
                    <TableRow key={e.id} data-testid="entry-row" hover sx={{ opacity: e.corrected_by_entry_id ? 0.7 : 1 }}>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{fmt(e.occurred_at)}</TableCell>
                      <TableCell sx={{ maxWidth: 520 }}>
                        <Stack direction="row" sx={{ gap: 0.75, alignItems: 'center', flexWrap: 'wrap', mb: 0.25 }}>
                          <Chip size="small" variant="outlined" label={kindLabel(e.entry_type)} />
                          {e.severity && <Chip size="small" label={e.severity} />}
                          {mark && <Chip size="small" color="warning" variant="outlined" label={mark} />}
                        </Stack>
                        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{e.body}</Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2">{e.author_name ?? '—'}</Typography>
                        <Typography variant="caption" color="text.secondary">{e.site_name ?? 'No site'}</Typography>
                      </TableCell>
                      {kinds.review_states.length > 0 && (
                        <TableCell sx={{ maxWidth: 220 }}>
                          {e.review && <Chip size="small" color={REVIEW_COLOUR[e.review.state]} label={REVIEW_LABEL[e.review.state]} />}
                          {e.review?.note && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{e.review.note}</Typography>)}
                        </TableCell>)}
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        {kinds.can_review && !e.mine && e.review && e.review.state !== 'noted' && e.review.state !== 'closed' && (
                          <Button size="small" onClick={() => setReviewing(e)}>
                            {e.review.state === 'follow_up' ? 'Close follow-up' : 'Review'}</Button>)}
                        {kinds.can_write && (e.mine || kinds.can_review) && (
                          <Button size="small" onClick={() => setCorrecting(e)}>Correct</Button>)}
                        <Button size="small" onClick={() => setReading(e)}>Open</Button>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        {(offset > 0 || data?.has_more) && (
          <Stack direction="row" sx={{ gap: 1, mt: 1.5, alignItems: 'center' }}>
            <Button size="small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</Button>
            <Button size="small" disabled={!data?.has_more} onClick={() => setOffset(offset + PAGE)}>Older</Button>
            <Box sx={{ flex: 1 }} />
            <Typography variant="caption" color="text.secondary">Entries {offset + 1} to {offset + items.length}</Typography>
          </Stack>)}
      </GlassCard>
      <ReviewDialog entry={reviewing} onClose={() => setReviewing(null)} onDone={again} />
      <CorrectDialog entry={correcting} onClose={() => setCorrecting(null)} onDone={again} />
      <EntryDialog entry={reading} onClose={() => setReading(null)} />
    </>
  )
}

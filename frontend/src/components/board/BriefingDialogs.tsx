/**
 * The daily briefing's dialogs: drafting one for a day, and reading, reviewing
 * and publishing it.
 *
 * The lines are the server's: counts in fixed sentences. They are shown, not
 * edited. A reviewer leaves a whole section out and writes a note that is
 * shown as theirs. Publishing is a person's act, and is said to be final
 * before it is done.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Skeleton,
  Switch, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import {
  apiError, discardBriefing, draftBriefing, getBriefing, publishBriefing, recountBriefing, reviewBriefing,
} from '@/api/operationsBoard'
import type { Briefing, BriefingSection } from '@/api/operationsBoard'
import { AS_AT_LABEL, STATE_COLOUR, STATE_LABEL, briefingFor, briefingLine, dayBefore, fmt, fmtDay } from './boardFormat'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }

interface DraftProps {
  open: boolean
  onClose: () => void
  onDrafted: (b: Briefing) => void
  sites: { id: string; name: string }[]
  maxDaysBack: number
  /** The site and day to begin with: a correction begins with the day it corrects. */
  start?: { siteId: string; day: string }
}

function DraftForm({ onClose, onDrafted, sites, maxDaysBack, start }: DraftProps) {
  const [siteId, setSiteId] = useState(start?.siteId ?? '')
  const [day, setDay] = useState(start?.day ?? dayBefore(1))
  const act = useMutation({
    mutationFn: () => draftBriefing({ site_id: siteId || null, briefing_date: day }),
    onSuccess: onDrafted,
  })
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Draft a briefing</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          The day's records are counted now and put into sentences. It is a draft until you publish it.
        </Typography>
        <Stack sx={{ gap: 2 }}>
          <TextField select size="small" label="For" value={siteId} slotProps={shrunk} onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">Every site together</MenuItem>
            {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField size="small" type="date" label="Day" value={day} onChange={(e) => setDay(e.target.value)}
                     slotProps={{ inputLabel: { shrink: true }, htmlInput: { min: dayBefore(maxDaysBack), max: dayBefore(0) } }}
                     helperText="A day that is not over is counted so far." />
        </Stack>
        {act.isError && <Alert severity="error" sx={{ mt: 2 }}>{apiError(act.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Not now</Button>
        <Button variant="contained" disabled={!day || act.isPending} onClick={() => act.mutate()}>Draft it</Button>
      </DialogActions>
    </Dialog>
  )
}

export function DraftDialog(props: DraftProps) {
  return props.open ? <DraftForm {...props} /> : null
}

/** One section: its lines as they were counted, each saying when it is true of when that is not the day. */
function Section({ s, edit, onLeaveOut }: { s: BriefingSection; edit: boolean; onLeaveOut: (out: boolean) => void }) {
  return (
    <Box data-testid="briefing-section" data-key={s.key} sx={{ py: 1.25, borderTop: 1, borderColor: 'divider', opacity: s.left_out ? 0.55 : 1 }}>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 700, flex: 1 }}>{s.title}</Typography>
        {s.left_out && !edit && <Chip size="small" label="Left out" />}
        {edit && (
          <FormControlLabel label="Leave out" sx={{ mr: 0 }}
                            control={<Switch size="small" checked={s.left_out} onChange={(e) => onLeaveOut(e.target.checked)}
                                             slotProps={{ input: { 'aria-label': `Leave out ${s.title}` } }} />} />)}
      </Stack>
      {s.note && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>{s.note}</Typography>}
      {s.lines.map((line) => (
        <Typography key={line.text} variant="body2" data-testid="briefing-line">
          {line.text}
          {AS_AT_LABEL[line.as_at] && (
            <Typography component="span" variant="caption" color="text.secondary"> ({AS_AT_LABEL[line.as_at]})</Typography>)}
        </Typography>))}
    </Box>
  )
}

interface ReadProps {
  id: string | null
  onClose: () => void
  /** Something about it changed: the list is read again. */
  onChanged: () => void
  /** A correction is asked for: a new draft for the same site and day. */
  onCorrect: (b: Briefing) => void
}

function BriefingView({ id, onClose, onChanged, onCorrect }: ReadProps & { id: string }) {
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ['briefing', id], queryFn: () => getBriefing(id) })
  const [note, setNote] = useState<string | null>(null)
  const [publishing, setPublishing] = useState(false)
  const after = () => { onChanged(); setPublishing(false); setNote(null); return refetch() }
  const review = useMutation({ mutationFn: (body: { left_out?: string[]; note?: string | null }) => reviewBriefing(id, body).then(after) })
  const recount = useMutation({ mutationFn: () => recountBriefing(id).then(after) })
  const publish = useMutation({ mutationFn: () => publishBriefing(id).then(after) })
  const discard = useMutation({ mutationFn: () => discardBriefing(id).then(() => { onChanged(); onClose() }) })
  const failed = [review, recount, publish, discard].find((m) => m.isError)
  const busy = review.isPending || recount.isPending || publish.isPending || discard.isPending
  const edit = !!data?.may.edit
  const written = note ?? data?.note ?? ''
  const leaveOut = (key: string, out: boolean) => {
    const now = (data?.left_out ?? []).map((s) => s.key).filter((k) => k !== key)
    review.mutate({ left_out: out ? [...now, key] : now })
  }
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        {data ? `${briefingFor(data)} — ${fmtDay(data.briefing_date)}` : 'Briefing'}
      </DialogTitle>
      <DialogContent data-testid="briefing">
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {isLoading && <Skeleton height={220} />}
        {data && (
          <>
            <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
              <Chip size="small" color={STATE_COLOUR[data.state]} label={STATE_LABEL[data.state]} />
              {data.revision > 1 && <Chip size="small" variant="outlined" label={`Revision ${data.revision}`} />}
              <Typography variant="caption" color="text.secondary">{briefingLine(data)}</Typography>
            </Stack>
            {data.replaced_by && (
              <Alert severity="warning" sx={{ mb: 1 }}>A later revision of this day's briefing has been published. This one is kept as it was.</Alert>)}
            {data.state === 'DRAFT' && (
              <Alert severity="info" sx={{ mb: 1 }}>
                A draft, counted {fmt(data.drafted_at)}. Only people who manage briefings can read it until it is published.
              </Alert>)}
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
              Counted from {fmt(data.period.from)} to {fmt(data.period.to)}, as in {data.period.timezone}
              {data.period.whole_day ? '' : ' — the day was not over'}. {data.drafting_note}
            </Typography>
            {data.sections.map((s) => (
              <Section key={s.key} s={s} edit={edit && !busy} onLeaveOut={(out) => leaveOut(s.key, out)} />))}
            {!data.sections.length && <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>No counted section is in it.</Typography>}
            {!edit && !!data.left_out.length && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }} data-testid="left-out">
                Left out by the reviewer: {data.left_out.map((s) => s.title).join(', ')}.
              </Typography>)}
            {!!data.not_read.length && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }} data-testid="not-counted">
                Not counted — whoever drafted it may not read them: {data.not_read.map((n) => `${n.title} (${n.needs})`).join(', ')}.
              </Typography>)}
            <Box sx={{ mt: 2 }} data-testid="reviewer-note">
              {edit ? (
                <Stack direction="row" sx={{ gap: 1, alignItems: 'flex-start' }}>
                  <TextField size="small" multiline minRows={2} fullWidth label="Your note" value={written}
                             onChange={(e) => setNote(e.target.value)} slotProps={{ htmlInput: { maxLength: 4000 } }}
                             helperText="Your own words. They are shown as yours, apart from what was counted." />
                  <Button size="small" variant="outlined" disabled={busy || note === null || note.trim() === (data.note ?? '')}
                          onClick={() => review.mutate({ note })}>Keep the note</Button>
                </Stack>
              ) : data.note && (
                <>
                  <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                    Note from {data.published_by_name ?? 'the reviewer'}</Typography>
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{data.note}</Typography>
                </>)}
            </Box>
            {publishing && (
              <Alert severity="warning" sx={{ mt: 2 }}
                     action={<Button color="inherit" size="small" disabled={busy} onClick={() => publish.mutate()}>Publish it</Button>}>
                Once published it is not changed, and everybody who may read briefings can read it. A correction would be
                a new revision.
              </Alert>)}
            {failed && <Alert severity="error" sx={{ mt: 2 }}>{apiError(failed.error)}</Alert>}
          </>)}
      </DialogContent>
      <DialogActions>
        {data?.may.discard && <Button color="inherit" disabled={busy} onClick={() => discard.mutate()}>Set aside</Button>}
        {data?.may.recount && <Button disabled={busy} onClick={() => recount.mutate()}>Count again</Button>}
        {data?.may.correct && <Button disabled={busy} onClick={() => onCorrect(data)}>Draft a correction</Button>}
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose}>Close</Button>
        {data?.may.publish && !publishing && (
          <Button variant="contained" disabled={busy} onClick={() => setPublishing(true)}>Publish…</Button>)}
      </DialogActions>
    </Dialog>
  )
}

export function BriefingDialog(props: ReadProps) {
  return props.id ? <BriefingView {...props} id={props.id} /> : null
}

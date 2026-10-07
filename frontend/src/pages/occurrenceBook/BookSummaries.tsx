/**
 * A shift's summary: drafted by the platform from what was recorded, read and
 * corrected by a person, then confirmed.
 *
 * The draft counts and quotes; it does not interpret, and no language model
 * writes it. What the platform drafted is kept beside what the person made of
 * it. Once confirmed it cannot be changed, and it is read by whoever reads the
 * handover.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Skeleton, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { getShifts } from '@/api/guards'
import { useAuthStore } from '@/store/auth'
import { apiError, confirmSummary, discardSummary, draftSummary, editSummary, listSummaries } from '@/api/occurrenceBook'
import type { ShiftSummary } from '@/api/occurrenceBook'
import { fmt, summaryState } from '@/components/occurrenceBook/bookFormat'

interface ShiftRow {
  id: string; guard_user_id: string | null; guard_name: string | null; site_name: string | null; scheduled_start: string
  actual_start: string | null; actual_end: string | null; status: string
}

interface DraftProps { open: boolean; canManage: boolean; onClose: () => void; onDrafted: (s: ShiftSummary) => void }

function DraftDialog(props: DraftProps) {
  return props.open ? <DraftForm {...props} /> : null
}

function DraftForm({ canManage, onClose, onDrafted }: DraftProps) {
  const [shiftId, setShiftId] = useState('')
  const me = useAuthStore((s) => s.user?.id)
  const { data: shifts, isLoading } = useQuery<ShiftRow[]>({ queryKey: ['shifts', 'for-summary'], queryFn: () => getShifts() })
  // A shift that has not started has nothing to summarise. Somebody who does not
  // manage handovers drafts the summary of their own shift and nobody else's. Newest first.
  const started = (shifts ?? []).filter((s) => s.actual_start && (canManage || s.guard_user_id === me))
    .sort((a, b) => (b.actual_start ?? '').localeCompare(a.actual_start ?? '')).slice(0, 50)
  const act = useMutation({ mutationFn: () => draftSummary(shiftId), onSuccess: (made) => { onDrafted(made); onClose() } })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Draft a shift's summary</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <Alert severity="info">
            The platform writes a draft from what was recorded during the shift. It counts and quotes; it does not
            interpret. You read it, correct it, and confirm it.
          </Alert>
          {isLoading ? <Skeleton height={56} /> : (
            <TextField select label="Shift" value={shiftId} onChange={(e) => setShiftId(e.target.value)}
                       helperText={started.length ? undefined
                         : canManage ? 'No shift has started.' : 'You have no shift that has started.'}>
              {started.map((s) => (
                <MenuItem key={s.id} value={s.id}>
                  {s.guard_name ?? 'A guard'} · {s.site_name ?? 'No site'} · {fmt(s.actual_start)}
                  {s.actual_end ? '' : ' (still running)'}
                </MenuItem>))}
            </TextField>)}
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!shiftId || act.isPending} onClick={() => act.mutate()}>Draft it</Button>
      </DialogActions>
    </Dialog>
  )
}

function SummaryDialog({ summary, onClose, onDone }: {
  summary: ShiftSummary | null; onClose: () => void; onDone: () => Promise<unknown> }) {
  // Keyed, so that opening another summary starts from that summary's text.
  return summary ? <SummaryView key={summary.id} summary={summary} onClose={onClose} onDone={onDone} /> : null
}

function SummaryView({ summary, onClose, onDone }: {
  summary: ShiftSummary; onClose: () => void; onDone: () => Promise<unknown> }) {
  const [words, setWords] = useState(summary.final_text)
  const [asDrafted, setAsDrafted] = useState(false)
  const changed = words.trim() !== summary.final_text.trim()
  const save = useMutation({ mutationFn: () => editSummary(summary.id, words.trim()).then(onDone) })
  const confirm = useMutation({
    // What is on the screen is what is confirmed: an unsaved correction is saved first.
    mutationFn: async () => {
      if (changed) await editSummary(summary.id, words.trim())
      await confirmSummary(summary.id)
      await onDone()
    },
    onSuccess: onClose,
  })
  const discard = useMutation({ mutationFn: () => discardSummary(summary.id).then(onDone), onSuccess: onClose })
  const failed = save.error ?? confirm.error ?? discard.error
  const busy = save.isPending || confirm.isPending || discard.isPending
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>
        {summary.site_name ?? 'No site'} · {summary.guard_name ?? 'A guard'} · {fmt(summary.period_start)}
      </DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 1.5 }}>
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <Chip size="small" color={summary.state === 'CONFIRMED' ? 'success' : 'warning'}
                  label={summary.state === 'CONFIRMED' ? 'Confirmed' : 'Draft'} />
            <Chip size="small" variant="outlined" label="Drafted by the platform, from fixed sentences" />
            <Typography variant="caption" color="text.secondary">{summaryState(summary)}</Typography>
          </Stack>
          {summary.may.edit && <Alert severity="info">{summary.note}</Alert>}
          {summary.may.edit && !asDrafted ? (
            <TextField label="The summary" value={words} multiline minRows={14} onChange={(e) => setWords(e.target.value)}
                       slotProps={{ htmlInput: { maxLength: 20000, style: { fontFamily: 'monospace', fontSize: 13 } } }} />
          ) : (
            <Typography component="pre" data-testid="summary-text"
                        sx={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 13, m: 0 }}>
              {asDrafted ? summary.drafted_text : summary.final_text}
            </Typography>
          )}
          {summary.edited && (
            <Button size="small" sx={{ alignSelf: 'flex-start' }} onClick={() => setAsDrafted((v) => !v)}>
              {asDrafted ? 'Show it as corrected' : 'Show it as the platform drafted it'}</Button>)}
          {!!failed && <Alert severity="error">{apiError(failed)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        {summary.may.discard && <Button color="warning" disabled={busy} onClick={() => discard.mutate()}>Set it aside</Button>}
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose}>Close</Button>
        {summary.may.edit && <Button disabled={!changed || !words.trim() || busy} onClick={() => save.mutate()}>Save</Button>}
        {summary.may.confirm && (
          <Button variant="contained" disabled={!words.trim() || busy} onClick={() => confirm.mutate()}>
            Confirm it</Button>)}
      </DialogActions>
    </Dialog>
  )
}

export default function BookSummaries({ canManage }: { canManage: boolean }) {
  const qc = useQueryClient()
  const [drafting, setDrafting] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const { data, isLoading, error } = useQuery({ queryKey: ['dob-summaries'], queryFn: () => listSummaries() })
  const again = () => qc.invalidateQueries({ queryKey: ['dob-summaries'] })
  const items = data?.items ?? []
  const reading = items.find((s) => s.id === openId) ?? null
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <Typography variant="body2" color="text.secondary" sx={{ flex: 1, minWidth: 260 }}>
            A summary is drafted from what was recorded during a shift, corrected and confirmed by a person, and then
            read with the handover. Until it is confirmed it is shown only to whoever is writing it.
          </Typography>
          <Button variant="contained" onClick={() => setDrafting(true)}>Draft a summary</Button>
        </Stack>
      </GlassCard>
      {!!error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(error)}</Alert>}
      {isLoading ? <Skeleton height={160} /> : !items.length && !error ? (
        <Alert severity="info">No shift has a summary yet.</Alert>
      ) : (
        <Stack sx={{ gap: 1.5 }}>
          {items.map((s) => (
            <GlassCard key={s.id} sx={{ p: 2 }} data-testid="summary">
              <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                  {s.site_name ?? 'No site'} · {s.guard_name ?? 'A guard'}</Typography>
                <Chip size="small" color={s.state === 'CONFIRMED' ? 'success' : 'warning'}
                      label={s.state === 'CONFIRMED' ? 'Confirmed' : 'Draft'} />
                {s.handover_id && <Chip size="small" variant="outlined" label="Handed over" />}
                <Box sx={{ flex: 1 }} />
                <Button size="small" onClick={() => setOpenId(s.id)}>{s.may.edit ? 'Read and correct' : 'Read'}</Button>
              </Stack>
              <Typography variant="caption" color="text.secondary">
                {fmt(s.period_start)} to {fmt(s.period_end)} · {summaryState(s)}
              </Typography>
            </GlassCard>))}
        </Stack>
      )}
      <DraftDialog open={drafting} canManage={canManage} onClose={() => setDrafting(false)}
                   onDrafted={(made) => { void again().then(() => setOpenId(made.id)) }} />
      <SummaryDialog summary={reading} onClose={() => setOpenId(null)} onDone={again} />
    </>
  )
}

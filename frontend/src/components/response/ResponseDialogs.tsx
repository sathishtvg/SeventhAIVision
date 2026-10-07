/**
 * The dialogs of the response desk: who to send, one incident's response, and
 * calling a guard off.
 *
 * "Who to send" is a suggestion with its reasons beside it. The button that
 * sends a guard calls the dispatch the platform has always had — a person's
 * act, by the person reading the suggestion.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, Skeleton, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { SeverityChip } from '@/components/common/SeverityChip'
import { dispatchGuard } from '@/api/guards'
import { apiError, getResponse, recommend, standDown } from '@/api/incidentResponses'
import type { ClockName, Clocks, DeskItem, RankedGuard } from '@/api/incidentResponses'
import {
  CLOCK_LABEL, STATE_COLOUR, STATE_LABEL, STEP_LABEL, TONE_COLOUR, fmt, reached, readClock, span, toldSentence,
} from './responseFormat'

type Incident = Pick<DeskItem, 'id' | 'title' | 'severity' | 'site_name' | 'camera_name'>

const CLOCKS: ClockName[] = ['ACKNOWLEDGE', 'ARRIVAL', 'RESOLVE']

/** The three clocks, each as a chip that says how long is left or how late it is. */
export function ClockChips({ clocks, quiet = false }: { clocks: Clocks; quiet?: boolean }) {
  return (
    <Stack direction="row" sx={{ gap: 0.5, flexWrap: 'wrap' }}>
      {CLOCKS.map((name) => {
        const reading = readClock(clocks[name])
        if (reading.tone === 'none') return null
        return (
          <Chip key={name} size="small" data-testid={`clock-${name}`}
                variant={quiet || reading.tone === 'ok' ? 'outlined' : 'filled'}
                color={quiet ? 'default' : TONE_COLOUR[reading.tone]} label={`${CLOCK_LABEL[name]}: ${reading.text}`} />
        )
      })}
    </Stack>
  )
}

function where(guard: RankedGuard): string {
  if (guard.distance_m === null) return guard.position_source ? 'Distance not known' : 'No position recorded this shift'
  const far = guard.distance_m < 1000 ? `${guard.distance_m} m` : `${(guard.distance_m / 1000).toFixed(1)} km`
  const age = guard.position_age_s === null ? '' : `, ${span(guard.position_age_s)} ago`
  return `${far} away by ${guard.position_source}${age}`
}

interface RecommendProps { incident: Incident | null; onClose: () => void; onSent: () => Promise<unknown> }

export function RecommendDialog(props: RecommendProps) {
  return props.incident ? <RecommendForm {...props} incident={props.incident} /> : null
}

function RecommendForm({ incident, onClose, onSent }: RecommendProps & { incident: Incident }) {
  const [chosen, setChosen] = useState<RankedGuard | null>(null)
  const [notes, setNotes] = useState('')
  const { data, isLoading, error } = useQuery({
    queryKey: ['response-recommend', incident.id], queryFn: () => recommend(incident.id) })
  const send = useMutation({
    // The dispatch the platform has always had: this dialog only helped choose.
    mutationFn: () => dispatchGuard(incident.id, { guard_user_id: chosen!.user_id, dispatch_notes: notes.trim() || undefined })
      .then(onSent),
    onSuccess: onClose,
  })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Who to send to “{incident.title}”</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 1.5, mt: 0.5 }}>
          {isLoading && <Skeleton height={160} />}
          {!!error && <Alert severity="error">{apiError(error)}</Alert>}
          {data && (
            <>
              <Alert severity="info">{data.note}</Alert>
              {!data.located && !data.why_nobody && (
                <Alert severity="warning">This incident has no position, so nobody's distance from it is known.</Alert>)}
              {data.why_nobody && <Alert severity="warning">{data.why_nobody}</Alert>}
              {!!data.site_requires?.length && (
                <Typography variant="body2" color="text.secondary">
                  This site requires: {data.site_requires.join(', ')}.</Typography>)}
              {data.guards.map((guard, index) => {
                const picked = chosen?.user_id === guard.user_id
                return (
                  <Box key={guard.user_id} data-testid="ranked-guard"
                       sx={{ p: 1.5, borderRadius: 1.5, border: 1, borderColor: picked ? 'primary.main' : 'divider' }}>
                    <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>{index + 1}. {guard.full_name ?? 'A guard'}</Typography>
                      <Chip size="small" label={`Score ${guard.score}`} />
                      <Chip size="small" variant="outlined" color={guard.available ? 'success' : 'warning'}
                            label={guard.emergency_id ? 'Has an emergency open' : guard.available ? 'Free' : 'Already sent somewhere'} />
                      {guard.stale && <Chip size="small" variant="outlined" color="warning" label="Old position" />}
                      <Box sx={{ flex: 1 }} />
                      <Button size="small" variant={picked ? 'contained' : 'outlined'} onClick={() => setChosen(guard)}>
                        {picked ? 'Chosen' : 'Choose'}</Button>
                    </Stack>
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{where(guard)}</Typography>
                    <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2.5 }}>
                      {guard.parts.map((part) => (
                        <Typography key={part.factor} component="li" variant="caption" color="text.secondary">
                          {part.points > 0 ? `+${part.points}` : part.points} · {part.detail}</Typography>))}
                    </Box>
                  </Box>
                )
              })}
              {chosen && (
                <TextField label={`Instructions for ${chosen.full_name ?? 'the guard'} (optional)`} value={notes} multiline
                           minRows={2} onChange={(e) => setNotes(e.target.value)}
                           slotProps={{ htmlInput: { maxLength: 1000 } }} />)}
            </>
          )}
          {send.isError && <Alert severity="error">{apiError(send.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" disabled={!chosen || send.isPending} onClick={() => send.mutate()}>
          {chosen ? `Send ${chosen.full_name ?? 'this guard'}` : 'Choose who to send'}</Button>
      </DialogActions>
    </Dialog>
  )
}

interface StandDownProps { incident: Incident | null; guardName: string | null; onClose: () => void; onDone: () => Promise<unknown> }

export function StandDownDialog(props: StandDownProps) {
  return props.incident ? <StandDownForm {...props} incident={props.incident} /> : null
}

function StandDownForm({ incident, guardName, onClose, onDone }: StandDownProps & { incident: Incident }) {
  const [reason, setReason] = useState('')
  const act = useMutation({ mutationFn: () => standDown(incident.id, reason.trim()).then(onDone), onSuccess: onClose })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Stand down {guardName ?? 'the guard'}</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 1.5, mt: 0.5 }}>
          <Typography variant="body2">
            {guardName ?? 'The guard'} is told they are no longer needed at “{incident.title}”. The incident goes back to
            having nobody sent. Nobody else is sent by this: you choose who, if anybody.
          </Typography>
          <TextField label="Why" value={reason} autoFocus multiline minRows={2} onChange={(e) => setReason(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 2000 } }} />
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="warning" disabled={!reason.trim() || act.isPending} onClick={() => act.mutate()}>
          Stand down</Button>
      </DialogActions>
    </Dialog>
  )
}

interface DetailProps { incident: Incident | null; onClose: () => void }

export function ResponseDetailDialog(props: DetailProps) {
  return props.incident ? <ResponseDetailView {...props} incident={props.incident} /> : null
}

function ResponseDetailView({ incident, onClose }: DetailProps & { incident: Incident }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['response-detail', incident.id], queryFn: () => getResponse(incident.id) })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>
        <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
          <span>{incident.title}</span><SeverityChip severity={incident.severity} />
        </Stack>
      </DialogTitle>
      <DialogContent>
        {isLoading && <Skeleton height={200} />}
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {data && (
          <Stack sx={{ gap: 2 }}>
            <Typography variant="body2" color="text.secondary">
              {[data.incident.site_name, data.incident.camera_name, `opened ${fmt(data.incident.created_at)}`]
                .filter(Boolean).join(' · ')}
            </Typography>
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>The clocks</Typography>
              <ClockChips clocks={data.clocks} quiet={!data.judged} />
              {!data.judged && (
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                  {data.sla_enabled ? 'Opened before the clocks were switched on, so it is not judged against them.'
                    : 'The clocks are not switched on: nothing is recorded as late and nobody is told.'}
                </Typography>)}
            </Box>
            <Divider />
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>The response</Typography>
              {!data.responses.length && <Typography variant="body2" color="text.secondary">Nobody has been sent.</Typography>}
              {data.responses.map((r) => (
                <Box key={r.id} data-testid="response-block" sx={{ mb: 1.5 }}>
                  <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 0.5 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{r.guard_name ?? 'A guard no longer on the system'}</Typography>
                    <Chip size="small" color={STATE_COLOUR[r.state]} label={STATE_LABEL[r.state]} />
                    <Typography variant="caption" color="text.secondary">sent {fmt(r.dispatched_at)}</Typography>
                  </Stack>
                  {data.steps.filter((s) => s.response_id === r.id).map((s) => (
                    <Typography key={s.id} variant="body2" data-testid="response-step" sx={{ pl: 1.5 }}>
                      <b>{fmt(s.occurred_at)}</b> · {STEP_LABEL[s.step]}
                      {s.step === 'SENT' ? '' : ` by ${s.actor_name ?? (s.actor_user_id ? 'somebody' : 'the platform')}`}
                      {s.note ? ` — ${s.note}` : ''}
                    </Typography>))}
                </Box>))}
            </Box>
            <Divider />
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Who was told</Typography>
              {!data.escalations.length && (
                <Typography variant="body2" color="text.secondary">Nobody has been told anything about this incident.</Typography>)}
              {data.escalations.map((e) => (
                <Typography key={e.id} variant="body2" data-testid="told-line">
                  <b>{fmt(e.created_at)}</b> · {toldSentence(e)} · {reached(e)}
                  {e.recipients > 0 && !e.notification_sent ? ' · could not be delivered' : ''}
                </Typography>))}
            </Box>
          </Stack>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

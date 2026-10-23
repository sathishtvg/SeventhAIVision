/**
 * A person's verdict on a possible altercation: confirmed, or not an altercation.
 *
 * Nothing else follows from it - no alert, no incident. Verdicts are what the
 * worker's thresholds are tuned against, and each is audited with what it
 * replaced.
 */
import { useState } from 'react'
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField, Typography } from '@mui/material'
import { useMutation } from '@tanstack/react-query'
import { errorText } from '@/components/states'
import { clock, judgeFinding } from '@/api/footage'
import type { Finding } from '@/api/footage'

interface Props {
  videoId: string
  finding: Finding | null
  onClose: () => void
  onJudged: () => void
}

export function VerdictDialog(props: Props) {
  return props.finding ? <VerdictForm {...props} finding={props.finding} /> : null
}

function VerdictForm({ videoId, finding, onClose, onJudged }: Props & { finding: Finding }) {
  const [note, setNote] = useState(finding.verdict_note ?? '')
  const judge = useMutation({
    mutationFn: (verdict: 'CONFIRMED' | 'REJECTED') => judgeFinding(videoId, finding.id, verdict, note.trim() || null),
    onSuccess: () => { onJudged(); onClose() },
  })
  const looked = typeof finding.reasons.looked_like === 'string' ? finding.reasons.looked_like : null
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Possible altercation, {clock(finding.start_second)} – {clock(finding.end_second)}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 1 }}>
          Watch it before deciding. It was flagged because the frames looked more like
          {looked ? ` “${looked}”` : ' a fight'} than like people hugging, dancing or walking, and the bodies of people
          within reach of each other showed fast arm movement
          {typeof finding.reasons.moments_with_fast_arms_within_reach === 'number'
            ? ` (in ${finding.reasons.moments_with_fast_arms_within_reach} of the frames checked, five a second)` : ''}.
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Your verdict is kept, with your name, and is what the detection is tuned against. It raises no alert and no
          incident.
        </Typography>
        <TextField fullWidth multiline minRows={2} label="Note (optional)" value={note}
                   onChange={(e) => setNote(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
        {judge.isError && <Alert severity="error" sx={{ mt: 2 }}>{errorText(judge.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button disabled={judge.isPending} onClick={() => judge.mutate('REJECTED')}>Not an altercation</Button>
        <Button variant="contained" color="error" disabled={judge.isPending} onClick={() => judge.mutate('CONFIRMED')}>
          Confirm</Button>
      </DialogActions>
    </Dialog>
  )
}

/**
 * Analysing a saved camera recording in Footage Review.
 *
 * The finished recordings the person may see are listed; the one chosen is
 * copied into Footage Review, where it is analysed like an upload and kept 30
 * days. The recording itself keeps its own retention - a camera's recordings
 * are usually kept for days, not weeks, which is why a copy is made.
 */
import { useState } from 'react'
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, List, ListItemButton, ListItemText, Skeleton,
  TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ErrorState, errorText } from '@/components/states'
import { listAllRecordings } from '@/api/recordings'
import { analyseRecording, clock } from '@/api/footage'
import type { Recording } from '@/types/api'

type Listed = Recording & { camera_name?: string | null; site_name?: string | null }

interface Props {
  open: boolean
  onClose: () => void
  onStarted: () => void
}

export default function RecordingDialog(props: Props) {
  return props.open ? <RecordingForm {...props} /> : null
}

function RecordingForm({ onClose, onStarted }: Props) {
  const [chosen, setChosen] = useState<Listed | null>(null)
  const [title, setTitle] = useState('')
  const recordings = useQuery({
    queryKey: ['recordings', 'completed', 'for-footage'],
    queryFn: () => listAllRecordings({ status_filter: 'completed', limit: 100 }) as Promise<Listed[]>,
  })
  const start = useMutation({
    mutationFn: () => analyseRecording(chosen!.id, title.trim() || null),
    onSuccess: () => { onStarted(); onClose() },
  })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Analyse a saved recording</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          The recording is copied into Footage Review, analysed like an upload, and the copy is kept 30 days. The
          recording itself is kept as long as its camera's recordings are.
        </Typography>
        {recordings.isLoading ? <Skeleton height={160} /> : recordings.isError ? (
          <ErrorState compact error={recordings.error} onRetry={() => { void recordings.refetch() }} />
        ) : !recordings.data?.length ? (
          <Alert severity="info">No finished recording is kept that you may see.</Alert>
        ) : (
          <List dense sx={{ maxHeight: 280, overflow: 'auto', border: 1, borderColor: 'divider', borderRadius: 1 }}>
            {recordings.data.map((r) => (
              <ListItemButton key={r.id} selected={chosen?.id === r.id} onClick={() => setChosen(r)} data-testid="recording">
                <ListItemText
                  primary={`${r.camera_name ?? 'Camera'} · ${new Date(r.started_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}`}
                  secondary={[r.site_name, r.duration_seconds ? clock(r.duration_seconds) : null].filter(Boolean).join(' · ') || undefined} />
              </ListItemButton>
            ))}
          </List>
        )}
        {chosen && (
          <TextField fullWidth size="small" label="Title" value={title} sx={{ mt: 2 }}
                     placeholder={`${chosen.camera_name ?? 'Camera'} recording`} onChange={(e) => setTitle(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 200 }, inputLabel: { shrink: true } }} />
        )}
        {start.isError && <Alert severity="error" sx={{ mt: 2 }}>{errorText(start.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" disabled={!chosen || start.isPending} onClick={() => start.mutate()}>Analyse it</Button>
      </DialogActions>
    </Dialog>
  )
}

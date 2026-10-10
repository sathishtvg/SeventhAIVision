/**
 * Smart Investigation — the dialogs the investigation screens share.
 *
 *   WhyDialog       one box that has to be filled in: why it is being closed,
 *                   reopened or set aside, or what a note says
 *   OpenDialog      open an investigation: a title, and the reason for it
 *   FileDialog      put records found by a search into an investigation
 *   TrailDialog     every place one plate, or one watchlist entry, was seen
 *
 * Every one of them shows the server's own refusal when there is one. Each
 * form exists only while its dialog is open, so it always opens empty.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem,
  TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { getSites } from '@/api/sites'
import { getIncidents } from '@/api/incidents'
import {
  addRecords, apiError, getTrail, listInvestigations, openInvestigation,
} from '@/api/investigations'
import type { Found } from '@/api/investigations'
import { distance, fmt, gap, refOf } from './investigationFormat'
import { ErrorState } from '@/components/states'

// ── One reason ───────────────────────────────────────────────────────────────

interface WhyProps {
  open: boolean; title: string; label: string; hint?: string; confirm: string; min?: number
  onClose: () => void; onConfirm: (text: string) => Promise<unknown>
}

export function WhyDialog(props: WhyProps) {
  return props.open ? <WhyForm {...props} /> : null
}

function WhyForm({ title, label, hint, confirm, min = 3, onClose, onConfirm }: WhyProps) {
  const [text, setText] = useState('')
  const act = useMutation({ mutationFn: () => onConfirm(text.trim()), onSuccess: onClose })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        {hint && <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>{hint}</Typography>}
        <TextField autoFocus fullWidth multiline minRows={3} label={label} value={text} sx={{ mt: 0.5 }}
                   onChange={(e) => setText(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
        {act.isError && <Alert severity="error" sx={{ mt: 1.5 }}>{apiError(act.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={text.trim().length < min || act.isPending} onClick={() => act.mutate()}>
          {confirm}</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Open an investigation ────────────────────────────────────────────────────

interface OpenProps {
  open: boolean; onClose: () => void; onOpened: (id: string) => void; fromIncident?: boolean
}

export function OpenDialog(props: OpenProps) {
  return props.open ? <OpenForm {...props} /> : null
}

function OpenForm({ onClose, onOpened, fromIncident = true }: OpenProps) {
  const [title, setTitle] = useState('')
  const [reason, setReason] = useState('')
  const [siteId, setSiteId] = useState('')
  const [incidentId, setIncidentId] = useState('')
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data: incidents } = useQuery({
    queryKey: ['incidents', 'for-investigation'], queryFn: () => getIncidents(undefined, undefined, undefined, 50, 0),
    enabled: fromIncident,
  })
  const qc = useQueryClient()
  const act = useMutation({
    mutationFn: () => openInvestigation({
      title: title.trim(), reason: reason.trim(), site_id: siteId || undefined, incident_id: incidentId || undefined }),
    onSuccess: (made) => { qc.invalidateQueries({ queryKey: ['investigations'] }); onOpened(made.id) },
  })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Open an investigation</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <TextField autoFocus label="What it is about" value={title} onChange={(e) => setTitle(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 200 } }} />
          <TextField label="Why it is being opened" multiline minRows={2} value={reason}
                     helperText="Kept with the investigation and never changed."
                     onChange={(e) => setReason(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
          <TextField select label="Site" value={siteId} onChange={(e) => setSiteId(e.target.value)}
                     helperText="Leave empty when it spans sites. Then only people not held to certain sites can see it.">
            <MenuItem value="">More than one site</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          {fromIncident && (
            <TextField select label="Opened from an incident (optional)" value={incidentId}
                       onChange={(e) => setIncidentId(e.target.value)}
                       helperText="The incident and its alert become the first things in the investigation.">
              <MenuItem value="">None</MenuItem>
              {(incidents?.items ?? []).map((i) => (
                <MenuItem key={i.id} value={i.id}>{i.title} — {fmt(i.created_at)}</MenuItem>))}
            </TextField>
          )}
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={title.trim().length < 3 || reason.trim().length < 5 || act.isPending}
                onClick={() => act.mutate()}>Open</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── File what a search found ─────────────────────────────────────────────────

interface FileProps {
  open: boolean; records: Found[]; onClose: () => void; onFiled: (id: string, added: number, already: number) => void
}

export function FileDialog(props: FileProps) {
  return props.open ? <FileForm {...props} /> : null
}

function FileForm({ records, onClose, onFiled }: FileProps) {
  const [fileId, setFileId] = useState('')
  const [note, setNote] = useState('')
  const [opening, setOpening] = useState(false)
  const { data: files, isLoading, isLoadingError: filesFailed, error: filesError, refetch: refetchFiles } = useQuery({
    queryKey: ['investigations', 'open-ones'], queryFn: () => listInvestigations({ status: 'OPEN', limit: 100 }),
  })
  const qc = useQueryClient()
  const act = useMutation({
    mutationFn: (id: string) => addRecords(id, records.map(refOf), note.trim()).then((r) => ({ id, ...r })),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['investigations'] })
      qc.invalidateQueries({ queryKey: ['investigation', r.id] })
      onFiled(r.id, r.added.length, r.already_filed.length)
    },
  })
  const none = !isLoading && !filesFailed && !(files?.items.length)
  return (
    <>
      <Dialog open={!opening} onClose={onClose} fullWidth maxWidth="sm">
        <DialogTitle>File {records.length} record{records.length === 1 ? '' : 's'} in an investigation</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            The investigation keeps a reference to each record, not a copy. Each one stays where it is and is read
            there every time the investigation is opened.</Typography>
          <Stack sx={{ gap: 2 }}>
            {filesFailed && (
              <ErrorState compact error={filesError} onRetry={refetchFiles} title="Could not load the open investigations" />
            )}
            {none ? <Alert severity="info">There is no open investigation. Open one for these records.</Alert> : (
              <TextField select label="Investigation" value={fileId} onChange={(e) => setFileId(e.target.value)}>
                {(files?.items ?? []).map((f) => (
                  <MenuItem key={f.id} value={f.id}>{f.investigation_number} — {f.title}</MenuItem>))}
              </TextField>
            )}
            <TextField label="Why these matter (optional)" multiline minRows={2} value={note}
                       onChange={(e) => setNote(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
            {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpening(true)}>Open a new investigation</Button>
          <Box sx={{ flex: 1 }} />
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="contained" disabled={!fileId || act.isPending} onClick={() => act.mutate(fileId)}>
            File</Button>
        </DialogActions>
      </Dialog>
      <OpenDialog open={opening} fromIncident={false} onClose={() => setOpening(false)}
                  onOpened={(id) => { setOpening(false); act.mutate(id) }} />
    </>
  )
}

// ── Where was this seen ──────────────────────────────────────────────────────

export function TrailDialog({ subject, onClose }: {
  subject: { plate?: string; watchlist_entry_id?: string } | null; onClose: () => void
}) {
  const { data, isLoading, error, refetch: refetchData } = useQuery({
    queryKey: ['investigation-trail', subject], queryFn: () => getTrail(subject ?? {}), enabled: !!subject,
  })
  const who = !data ? '' : data.subject.kind === 'VEHICLE' ? data.subject.plate
    : data.subject.name ?? 'a watchlist entry'
  return (
    <Dialog open={!!subject} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Where {who || 'this'} was seen</DialogTitle>
      <DialogContent>
        {isLoading && <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}><CircularProgress /></Box>}
        {error && <ErrorState compact error={error} onRetry={refetchData} />}
        {data && (
          <>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              {fmt(data.from)} to {fmt(data.to)} · {data.summary.sightings} sighting{data.summary.sightings === 1 ? '' : 's'}
              {' '}at {data.summary.cameras} camera{data.summary.cameras === 1 ? '' : 's'} on {data.summary.sites}
              {' '}site{data.summary.sites === 1 ? '' : 's'}</Typography>
            <Alert severity="info" sx={{ mb: 1.5 }}>{data.basis}</Alert>
            {!data.complete && (
              <Alert severity="warning" sx={{ mb: 1.5 }}>
                There were more sightings than one trail shows. Narrow the period to see the rest.</Alert>)}
            {!data.sightings.length && <Alert severity="info" sx={{ mb: 1.5 }}>Not seen in this period.</Alert>}
            {data.sightings.map((s, i) => {
              const leg = i > 0 ? data.legs[i - 1] : null
              return (
                <Box key={`${s.kind}:${s.id}`} data-testid="trail-sighting">
                  {leg && (
                    <Typography variant="caption" color="text.secondary" data-testid="trail-leg"
                                sx={{ display: 'block', pl: 2, py: 0.5, borderLeft: '2px dashed', borderColor: 'divider', ml: 1 }}>
                      {gap(leg.seconds)} later · {leg.same_camera ? 'the same camera' : distance(leg.metres)}
                      {!leg.same_site && ' · another site'}</Typography>)}
                  <Stack direction="row" sx={{ gap: 1.5, alignItems: 'center', py: 0.75 }}>
                    <Chip size="small" label={i + 1} />
                    <Box sx={{ minWidth: 0 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {s.camera_name ?? s.title} <Typography component="span" variant="body2" color="text.secondary">
                          · {s.site_name ?? 'no site'}</Typography></Typography>
                      <Typography variant="caption" color="text.secondary">
                        {fmt(s.occurred_at)}{s.confidence !== null && ` · confidence ${Math.round(s.confidence * 100)}%`}
                        {s.kind === 'VISITOR' && ' · registered at the gate as a visitor’s vehicle'}</Typography>
                    </Box>
                  </Stack>
                </Box>
              )
            })}
            {data.not_searched.map((n) => (
              <Alert key={n.kind} severity="warning" sx={{ mt: 1.5 }}>Not searched: {n.label}. {n.reason}</Alert>))}
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>
              {data.not_followed}</Typography>
          </>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

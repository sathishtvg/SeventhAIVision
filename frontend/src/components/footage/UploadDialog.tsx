/**
 * Uploading a video to Footage Review: choose or drop the file, say what it is,
 * and watch it arrive part by part.
 *
 * The progress bar is the server's: it moves when the server says it has a
 * part, never before. Closing the dialog during an upload stops it and leaves
 * what arrived on the server, so it can be carried on later with the same file
 * ("Carry on" on the list); Cancel throws it away.
 */
import { useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, TextField, Typography,
} from '@mui/material'
import CloudUploadIcon from '@mui/icons-material/CloudUpload'
import { useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { ProgressState, errorText } from '@/components/states'
import { getSites } from '@/api/sites'
import {
  cancelUpload, refusalOf, sendRest, sizeText, startUpload, uploadState,
} from '@/api/footage'
import type { FootageRules, FootageVideo } from '@/api/footage'
import { instantOf, titleOf } from './footageFormat'

type Phase = 'choose' | 'sending' | 'done' | 'failed'

interface Props {
  open: boolean
  rules: FootageRules
  /** An upload that stopped, to be carried on with the same file chosen again. */
  resume?: FootageVideo | null
  onClose: () => void
  onUploaded: (video: FootageVideo) => void
}

export default function UploadDialog(props: Props) {
  return props.open ? <UploadForm {...props} /> : null
}

function UploadForm({ rules, resume, onClose, onUploaded }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [place, setPlace] = useState('')
  const [siteId, setSiteId] = useState('')
  const [filmedAt, setFilmedAt] = useState('')
  const [phase, setPhase] = useState<Phase>('choose')
  const [received, setReceived] = useState(0)
  const [error, setError] = useState<unknown>(null)
  const [dragging, setDragging] = useState(false)
  // The upload as the server last described it: state for what is shown, a ref for the handlers.
  const [video, setVideo] = useState<FootageVideo | null>(resume ?? null)
  const current = useRef<FootageVideo | null>(resume ?? null)
  const stop = useRef<AbortController | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const sites = useQuery({ queryKey: ['sites', 'active'], queryFn: () => getSites(true), enabled: !resume })

  useEffect(() => () => stop.current?.abort(), [])

  const choose = (chosen: File | null | undefined) => {
    if (!chosen) return
    let why = refusalOf(chosen, rules)
    if (!why && resume && (chosen.name !== resume.original_filename || chosen.size !== resume.size_bytes)) {
      why = `Choose the same file as before: ${resume.original_filename}, ${sizeText(resume.size_bytes)}.`
    }
    setRefusal(why)
    setFile(why ? null : chosen)
    if (!why && !resume) setTitle(titleOf(chosen.name))
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    choose(e.dataTransfer.files?.[0])
  }

  const send = async () => {
    if (!file) return
    setError(null)
    setPhase('sending')
    stop.current = new AbortController()
    try {
      const known = current.current
      const v = known ? { ...known, ...(await uploadState(known.id)) } : await startUpload({
        filename: file.name, size_bytes: file.size, title: title.trim() || undefined, place: place.trim() || undefined,
        site_id: siteId || null, filmed_at: instantOf(filmedAt),
      })
      current.current = v
      setVideo(v)
      const done = await sendRest(v, file, setReceived, { signal: stop.current.signal })
      setPhase('done')
      onUploaded(done)
    } catch (err) {
      if (stop.current?.signal.aborted) return
      setError(err)
      setPhase('failed')
    }
  }

  const discard = async () => {
    stop.current?.abort()
    if (current.current) await cancelUpload(current.current.id).catch(() => undefined)
    onClose()
  }

  const size = video?.size_bytes ?? file?.size ?? 0
  const percent = size ? (received / size) * 100 : 0
  const choosing = phase === 'choose'

  return (
    <Dialog open onClose={phase === 'sending' ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>{resume ? `Carry on uploading ${resume.title}` : 'Upload a video'}</DialogTitle>
      <DialogContent>
        {choosing && (
          <Box data-testid="drop-zone" onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
               onDragLeave={() => setDragging(false)} onDrop={onDrop} onClick={() => input.current?.click()}
               sx={{ border: 2, borderStyle: 'dashed', borderColor: dragging ? 'primary.main' : 'divider', borderRadius: 2,
                     p: 3, mb: 2, textAlign: 'center', cursor: 'pointer', bgcolor: dragging ? 'action.hover' : 'transparent' }}>
            <CloudUploadIcon color="primary" sx={{ fontSize: 40 }} />
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {file ? `${file.name} · ${sizeText(file.size)}` : resume ? `Choose ${resume.original_filename} again` : 'Drop a video here, or choose one'}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              MP4, MOV, MKV or AVI · up to {Math.floor(rules.max_bytes / 1024 ** 3)} GB · kept {rules.retention_days} days
            </Typography>
            <input ref={input} type="file" hidden accept={rules.extensions.join(',')} data-testid="file-input"
                   onChange={(e) => choose(e.target.files?.[0])} />
          </Box>
        )}
        {refusal && <Alert severity="warning" sx={{ mb: 2 }}>{refusal}</Alert>}
        {choosing && !resume && (
          <Stack sx={{ gap: 2 }}>
            <TextField label="Title" value={title} onChange={(e) => setTitle(e.target.value)} size="small"
                       slotProps={{ htmlInput: { maxLength: 200 } }} />
            <Stack direction="row" sx={{ gap: 2 }}>
              <TextField select label="Site" value={siteId} onChange={(e) => setSiteId(e.target.value)} size="small"
                         sx={{ flex: 1 }} helperText={sites.isError ? errorText(sites.error) : 'Who may see it follows the site'}
                         slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}>
                <MenuItem value="">No site</MenuItem>
                {(sites.data ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
              </TextField>
              <TextField label="Place" value={place} onChange={(e) => setPlace(e.target.value)} size="small"
                         sx={{ flex: 1 }} slotProps={{ htmlInput: { maxLength: 200 } }} helperText="Where it was filmed, if known" />
            </Stack>
            <TextField label="Filmed at" type="datetime-local" value={filmedAt} size="small"
                       onChange={(e) => setFilmedAt(e.target.value)} slotProps={{ inputLabel: { shrink: true } }}
                       helperText="When the video begins, if known. Times found in it are counted from here." />
          </Stack>
        )}
        {phase !== 'choose' && (
          <ProgressState
            status={phase === 'sending' ? 'working' : phase === 'done' ? 'done' : 'failed'}
            label={phase === 'done' ? 'Uploaded. It is waiting to be analysed.' : `Uploading ${video?.title ?? file?.name}`}
            value={percent}
            detail={phase === 'sending' ? `${sizeText(received)} of ${sizeText(size)}. Closing this stops the upload; it can be carried on later with the same file.` : undefined}
            error={error}
            onRetry={() => { void send() }}
          />
        )}
      </DialogContent>
      <DialogActions>
        {phase === 'sending' && <Button onClick={() => { stop.current?.abort(); onClose() }}>Stop for now</Button>}
        {(phase === 'sending' || phase === 'failed') && <Button color="error" onClick={() => { void discard() }}>Cancel the upload</Button>}
        {choosing && <Button onClick={onClose}>Close</Button>}
        {choosing && <Button variant="contained" disabled={!file} onClick={() => { void send() }}>
          {resume ? 'Carry on' : 'Upload'}</Button>}
        {(phase === 'done' || phase === 'failed') && <Button onClick={onClose}>Close</Button>}
      </DialogActions>
    </Dialog>
  )
}

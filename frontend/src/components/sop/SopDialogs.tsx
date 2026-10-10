/**
 * The dialogs of the SOP library: writing a procedure, and one procedure with
 * its versions — drafted, submitted, approved by somebody else, in force.
 *
 * What is shown as "in force" is what was approved, word for word. A version
 * is corrected only while it is a draft; once it is approved the screen offers
 * no way to change it, because the server has none.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, MenuItem, Skeleton, TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { getSites } from '@/api/sites'
import {
  apiError, approveVersion, attachDocument, changeVersion, downloadAttachment, draftNextVersion, getIncidentTypes,
  getProcedure, getProceduresForIncident, getProceduresForSituation, rejectVersion, restoreProcedure, retireProcedure,
  setIncidentTypes,
  submitVersion, withdrawVersion, writeProcedure,
} from '@/api/sop'
import type { ProcedureDetail, RelevantProcedure, Version } from '@/api/sop'
import { STATE_COLOUR, STATE_LABEL, VERSION_LABEL, label, standing, typedKinds, versionLine } from './sopFormat'
import { ErrorState } from '@/components/states'

const mono = { fontFamily: 'monospace', fontSize: 13 }
const HEADINGS = 'A line that starts with #, or is written in capitals, is a heading. Each paragraph under it is a passage the library can find.'

interface WriteProps { open: boolean; categories: string[]; onClose: () => void; onDone: (made: ProcedureDetail) => void }

export function WriteDialog(props: WriteProps) {
  return props.open ? <WriteForm {...props} /> : null
}

function WriteForm({ categories, onClose, onDone }: WriteProps) {
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState('general')
  const [siteId, setSiteId] = useState('')
  const [kinds, setKinds] = useState('')
  const [body, setBody] = useState('')
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data: known } = useQuery({ queryKey: ['sop-incident-types'], queryFn: getIncidentTypes })
  const act = useMutation({
    mutationFn: () => writeProcedure({ title: title.trim(), category, site_id: siteId || null, body: body.trim(),
                                       incident_types: typedKinds(kinds) }),
    onSuccess: (made) => { onDone(made); onClose() },
  })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Write a procedure</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <Alert severity="info">
            It starts as a draft. It is in force — and shown to the people who only read procedures — once you submit
            it and somebody else approves it.
          </Alert>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField label="Title" value={title} sx={{ flex: 2, minWidth: 240 }} autoFocus
                       onChange={(e) => setTitle(e.target.value)} slotProps={{ htmlInput: { maxLength: 200 } }} />
            <TextField select label="Category" value={category} sx={{ flex: 1, minWidth: 180 }}
                       onChange={(e) => setCategory(e.target.value)}>
              {categories.map((c) => <MenuItem key={c} value={c}>{label(c)}</MenuItem>)}
            </TextField>
            <TextField select label="For" value={siteId} sx={{ flex: 1, minWidth: 180 }}
                       slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
                       onChange={(e) => setSiteId(e.target.value)}>
              <MenuItem value="">Every site</MenuItem>
              {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
          </Stack>
          <TextField label="Kinds of incident it is for" value={kinds} onChange={(e) => setKinds(e.target.value)}
                     helperText={`Separated by commas. It is put beside incidents of these kinds. Known: ${(known ?? []).join(', ')}`} />
          <TextField label="The procedure" value={body} multiline minRows={12} helperText={HEADINGS}
                     onChange={(e) => setBody(e.target.value)} slotProps={{ htmlInput: { maxLength: 60000, style: mono } }} />
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!title.trim() || !body.trim() || act.isPending} onClick={() => act.mutate()}>
          Save the draft</Button>
      </DialogActions>
    </Dialog>
  )
}

/** One version: its text, and what this person may do with it. */
function VersionPanel({ version, onDone }: { version: Version; onDone: () => Promise<unknown> }) {
  const [body, setBody] = useState(version.body)
  const [note, setNote] = useState(version.change_note ?? '')
  const [deciding, setDeciding] = useState<'approve' | 'reject' | null>(null)
  const [from, setFrom] = useState('')
  const [until, setUntil] = useState('')
  const [reason, setReason] = useState('')
  const changed = body.trim() !== version.body.trim() || note.trim() !== (version.change_note ?? '').trim()
  const iso = (local: string) => (local ? new Date(local).toISOString() : null)
  const run = useMutation({ mutationFn: (what: () => Promise<unknown>) => what().then(onDone) })
  const save = () => changeVersion(version.id, { body: body.trim(), change_note: note.trim() || null })
  const open = async () => {
    const blob = await downloadAttachment(version.id)
    window.open(URL.createObjectURL(blob), '_blank', 'noopener')
  }
  return (
    <Stack sx={{ gap: 1.5 }} data-testid="version-panel">
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Version {version.version_no}</Typography>
        <Chip size="small" color={version.in_force ? 'success' : version.state === 'REJECTED' ? 'error' : 'default'}
              label={version.in_force ? 'In force' : VERSION_LABEL[version.state]} />
        <Typography variant="caption" color="text.secondary">{versionLine(version)}</Typography>
      </Stack>
      {version.change_note && !version.may.edit && (
        <Typography variant="body2" color="text.secondary">What changed: {version.change_note}</Typography>)}
      {version.may.edit ? (
        <>
          <TextField label="The procedure" value={body} multiline minRows={10} helperText={HEADINGS}
                     onChange={(e) => setBody(e.target.value)} slotProps={{ htmlInput: { maxLength: 60000, style: mono } }} />
          {version.version_no > 1 && (
            <TextField label="What is different from the version before" value={note}
                       helperText="Asked before it is submitted, so that whoever approves it knows what they are approving"
                       onChange={(e) => setNote(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />)}
        </>
      ) : (
        <Typography component="pre" data-testid="version-text" sx={{ whiteSpace: 'pre-wrap', m: 0, ...mono }}>
          {version.body}</Typography>
      )}
      <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        {version.may.edit && (
          <Button size="small" variant="outlined" disabled={!changed || !body.trim() || run.isPending}
                  onClick={() => run.mutate(save)}>Save</Button>)}
        {version.may.submit && (
          // What is on the screen is what is submitted: an unsaved correction is saved first.
          <Button size="small" variant="contained" disabled={!body.trim() || run.isPending}
                  onClick={() => run.mutate(async () => { if (changed) await save(); return submitVersion(version.id) })}>
            Submit for approval</Button>)}
        {version.may.withdraw && (
          <Button size="small" disabled={run.isPending} onClick={() => run.mutate(() => withdrawVersion(version.id))}>
            Withdraw to correct</Button>)}
        {version.may.decide && !deciding && (
          <>
            <Button size="small" variant="contained" color="success" onClick={() => setDeciding('approve')}>Approve…</Button>
            <Button size="small" color="error" onClick={() => setDeciding('reject')}>Reject…</Button>
          </>)}
        {version.state === 'SUBMITTED' && !version.may.decide && !version.may.withdraw && (
          <Typography variant="caption" color="text.secondary">Awaiting somebody who may approve it.</Typography>)}
        {version.may.edit && (
          <Button size="small" component="label" disabled={run.isPending}>
            {version.has_attachment ? 'Replace the attached document' : 'Attach the document as issued'}
            <input hidden type="file" accept=".pdf,.docx,.png,.jpg,.jpeg,.txt" data-testid="attach"
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) run.mutate(() => attachDocument(version.id, f)) }} />
          </Button>)}
        {version.has_attachment && (
          <Button size="small" onClick={() => run.mutate(open)}>Open “{version.attachment_name}”</Button>)}
      </Stack>
      {version.may.submit && version.state === 'DRAFT' && (
        <Typography variant="caption" color="text.secondary">
          Once submitted it is approved by somebody other than who drafted it.</Typography>)}
      {deciding === 'approve' && (
        <Stack sx={{ gap: 1.5, p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1.5 }}>
          <Typography variant="body2">
            From its date this version is the procedure in force, in place of the one before. It cannot be changed
            afterwards.
          </Typography>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField label="Comes into force" type="datetime-local" value={from} helperText="Left empty: now"
                       onChange={(e) => setFrom(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
            <TextField label="Runs out" type="datetime-local" value={until} helperText="Left empty: when the next version is in force"
                       onChange={(e) => setUntil(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
          </Stack>
          <Stack direction="row" sx={{ gap: 1 }}>
            <Button size="small" onClick={() => setDeciding(null)}>Not yet</Button>
            <Button size="small" variant="contained" color="success" disabled={run.isPending}
                    onClick={() => run.mutate(() => approveVersion(version.id, { effective_from: iso(from), effective_until: iso(until) }))}>
              Approve version {version.version_no}</Button>
          </Stack>
        </Stack>)}
      {deciding === 'reject' && (
        <Stack sx={{ gap: 1.5, p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1.5 }}>
          <TextField label="Why it is rejected" value={reason} autoFocus multiline minRows={2}
                     onChange={(e) => setReason(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
          <Stack direction="row" sx={{ gap: 1 }}>
            <Button size="small" onClick={() => setDeciding(null)}>Not yet</Button>
            <Button size="small" variant="contained" color="error" disabled={!reason.trim() || run.isPending}
                    onClick={() => run.mutate(() => rejectVersion(version.id, reason.trim()))}>Reject it</Button>
          </Stack>
        </Stack>)}
      {run.isError && <Alert severity="error">{apiError(run.error)}</Alert>}
    </Stack>
  )
}

interface ProcedureProps { id: string | null; onClose: () => void; onChanged: () => Promise<unknown> }

export function ProcedureDialog(props: ProcedureProps) {
  return props.id ? <ProcedureView {...props} id={props.id} /> : null
}

function ProcedureView({ id, onClose, onChanged }: ProcedureProps & { id: string }) {
  const [chosen, setChosen] = useState<string | null>(null)
  const [kinds, setKinds] = useState<string | null>(null)
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ['sop-procedure', id], queryFn: () => getProcedure(id) })
  const again = () => Promise.all([refetch(), onChanged()])
  const run = useMutation({ mutationFn: (what: () => Promise<unknown>) => what().then(again) })
  // The version being written or considered if there is one; else the one in force; else the newest.
  const shown = data?.versions.find((v) => v.id === chosen)
    ?? data?.versions.find((v) => v.state === 'DRAFT' || v.state === 'SUBMITTED')
    ?? data?.versions.find((v) => v.in_force) ?? data?.versions[0]
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>
        {data ? (
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <span>{data.code} · {data.title}</span>
            <Chip size="small" color={STATE_COLOUR[data.state]} label={STATE_LABEL[data.state]} />
          </Stack>) : 'Procedure'}
      </DialogTitle>
      <DialogContent>
        {isLoading && <Skeleton height={240} />}
        {!!error && <ErrorState compact error={error} onRetry={refetch} />}
        {data && (
          <Stack sx={{ gap: 2 }}>
            <Typography variant="body2" color="text.secondary">
              {[label(data.category), data.site_name ?? 'Every site', standing(data)].join(' · ')}
            </Typography>
            <Stack direction="row" sx={{ gap: 0.75, alignItems: 'center', flexWrap: 'wrap' }}>
              <Typography variant="body2">Put beside incidents of the kind:</Typography>
              {!data.incident_types.length && kinds === null && (
                <Typography variant="body2" color="text.secondary">none — it is found by asking only</Typography>)}
              {kinds === null && data.incident_types.map((k) => <Chip key={k} size="small" variant="outlined" label={k} />)}
              {data.can_write && kinds === null && (
                <Button size="small" onClick={() => setKinds(data.incident_types.join(', '))}>Change</Button>)}
              {kinds !== null && (
                <>
                  <TextField size="small" value={kinds} sx={{ minWidth: 260 }} onChange={(e) => setKinds(e.target.value)}
                             slotProps={{ htmlInput: { 'aria-label': 'Kinds of incident, separated by commas' } }} />
                  <Button size="small" variant="outlined" disabled={run.isPending}
                          onClick={() => run.mutate(() => setIncidentTypes(id, typedKinds(kinds)).then(() => setKinds(null)))}>
                    Save</Button>
                  <Button size="small" onClick={() => setKinds(null)}>Cancel</Button>
                </>)}
            </Stack>
            {data.versions.length > 1 && (
              <Stack direction="row" sx={{ gap: 0.75, flexWrap: 'wrap' }}>
                {data.versions.map((v) => (
                  <Chip key={v.id} size="small" clickable color={v.id === shown?.id ? 'primary' : 'default'}
                        variant={v.id === shown?.id ? 'filled' : 'outlined'} onClick={() => setChosen(v.id)}
                        label={`Version ${v.version_no} · ${v.in_force ? 'in force' : VERSION_LABEL[v.state].toLowerCase()}`} />))}
              </Stack>)}
            <Divider />
            {/* Keyed, so that another version starts from that version's text. */}
            {shown && <VersionPanel key={`${shown.id}:${shown.state}:${shown.body.length}`} version={shown} onDone={again} />}
            {run.isError && <Alert severity="error">{apiError(run.error)}</Alert>}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        {data?.can_approve && (
          <Button color={data.is_retired ? 'primary' : 'warning'} disabled={run.isPending}
                  onClick={() => run.mutate(() => (data.is_retired ? restoreProcedure(id) : retireProcedure(id)))}>
            {data.is_retired ? 'Restore' : 'Retire'}</Button>)}
        {data?.can_write && !data.open_version && !data.is_retired && (
          <Button disabled={run.isPending} onClick={() => run.mutate(() => draftNextVersion(id).then(() => setChosen(null)))}>
            Draft the next version</Button>)}
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}

/** The procedures in force for one incident, beside the incident. Says so when there is none, and why. */
export function ProcedureForIncident({ incidentId }: { incidentId: string }) {
  const { data, error } = useQuery({
    queryKey: ['sop-for-incident', incidentId], queryFn: () => getProceduresForIncident(incidentId), retry: false })
  // Somebody who may not read procedures is shown nothing here, not an error.
  if (error || !data) return null
  return (
    <Box data-testid="procedure-for">
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>The procedure</Typography>
      {!data.procedures.length && <Typography variant="body2" color="text.secondary">{data.why_none}</Typography>}
      <ProcedureTexts procedures={data.procedures} />
    </Box>
  )
}

/** Each procedure word for word, under which it is, which version, and who approved it. */
function ProcedureTexts({ procedures }: { procedures: RelevantProcedure[] }) {
  return (
    <>
      {procedures.map((p) => (
        <Box key={p.id} sx={{ mb: 1.5 }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {p.code} · {p.title} <Typography component="span" variant="caption" color="text.secondary">
              version {p.version.version_no}, approved{p.version.approved_by_name ? ` by ${p.version.approved_by_name}` : ''}
              {p.site_name ? ` · ${p.site_name}` : ' · every site'}</Typography>
          </Typography>
          <Typography component="pre" sx={{ whiteSpace: 'pre-wrap', m: 0, mt: 0.5, ...mono }}>{p.text}</Typography>
        </Box>))}
    </>
  )
}

/**
 * The procedures in force for what a situation is made of, as a card on the
 * situation. It is there only when there is a procedure to show: a situation
 * screen with nothing to add says nothing.
 */
export function ProcedureForSituation({ situationId }: { situationId: string }) {
  const { data } = useQuery({
    queryKey: ['sop-for-situation', situationId], queryFn: () => getProceduresForSituation(situationId), retry: false })
  if (!data?.procedures?.length) return null
  return (
    <GlassCard sx={{ p: 2, mb: 2 }} data-testid="procedure-for-situation">
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>The procedure</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        The approved procedure for what this situation is made of ({data.incident_types.join(', ')}), word for word.
        It is the organisation's procedure, not a recommendation.
      </Typography>
      <ProcedureTexts procedures={data.procedures} />
    </GlassCard>
  )
}

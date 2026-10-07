/**
 * Evidence packages — the dialogs the evidence screens share.
 *
 *   CreatePackageDialog   open a package for an investigation or an incident
 *   SealDialog            seal a draft, having said what sealing does
 *   ExportDialog          export a sealed package, saying why it is leaving
 *   DisclosureDialog      record that an export was shared or released
 *
 * Each form exists only while its dialog is open, so it always opens empty, and
 * each shows the server's own refusal when there is one.
 */
import { useState } from 'react'
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Switch, TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { getIncidents } from '@/api/incidents'
import { listInvestigations } from '@/api/investigations'
import {
  apiError, createPackage, exportPackage, recordDisclosure, saveFile, sealPackage,
} from '@/api/evidencePackages'
import type { ExportResult, PackageDetail } from '@/api/evidencePackages'
import { fmt } from './evidenceFormat'

// ── Open a package ───────────────────────────────────────────────────────────

interface CreateProps {
  open: boolean; onClose: () => void; onCreated: (id: string) => void
  /** Opened from an investigation's own page: that investigation, and no choosing. */
  investigation?: { id: string; label: string }
}

export function CreatePackageDialog(props: CreateProps) {
  return props.open ? <CreateForm {...props} /> : null
}

function CreateForm({ onClose, onCreated, investigation }: CreateProps) {
  const [title, setTitle] = useState('')
  const [purpose, setPurpose] = useState('')
  const [from, setFrom] = useState<'investigation' | 'incident'>('investigation')
  const [investigationId, setInvestigationId] = useState(investigation?.id ?? '')
  const [incidentId, setIncidentId] = useState('')
  const { data: investigations } = useQuery({
    queryKey: ['investigations', 'for-evidence'], queryFn: () => listInvestigations({ limit: 100 }),
    enabled: !investigation && from === 'investigation',
  })
  const { data: incidents } = useQuery({
    queryKey: ['incidents', 'for-evidence'], queryFn: () => getIncidents(undefined, undefined, undefined, 50, 0),
    enabled: !investigation && from === 'incident',
  })
  const qc = useQueryClient()
  const act = useMutation({
    mutationFn: () => createPackage({
      title: title.trim(), purpose: purpose.trim(),
      ...(from === 'investigation' ? { investigation_id: investigationId } : { incident_id: incidentId }) }),
    onSuccess: (made) => { qc.invalidateQueries({ queryKey: ['evidence-packages'] }); onCreated(made.id) },
  })
  const chosen = from === 'investigation' ? !!investigationId : !!incidentId
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Put evidence together</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          A package starts empty. What the platform kept about the records of the investigation or incident is then
          offered to you, and you choose what goes in.</Typography>
        <Stack sx={{ gap: 2 }}>
          <TextField autoFocus label="What it is" value={title} onChange={(e) => setTitle(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 200 } }} />
          <TextField label="What it is for" multiline minRows={2} value={purpose}
                     helperText="Kept with the package, written into its manifest, and never changed."
                     onChange={(e) => setPurpose(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
          {investigation ? (
            <TextField label="Investigation" value={investigation.label} disabled />
          ) : (
            <>
              <TextField select label="Evidence of" value={from}
                         onChange={(e) => setFrom(e.target.value as 'investigation' | 'incident')}>
                <MenuItem value="investigation">An investigation</MenuItem>
                <MenuItem value="incident">An incident</MenuItem>
              </TextField>
              {from === 'investigation' ? (
                <TextField select label="Investigation" value={investigationId}
                           onChange={(e) => setInvestigationId(e.target.value)}>
                  {(investigations?.items ?? []).map((v) => (
                    <MenuItem key={v.id} value={v.id}>{v.investigation_number} — {v.title}</MenuItem>))}
                </TextField>
              ) : (
                <TextField select label="Incident" value={incidentId} onChange={(e) => setIncidentId(e.target.value)}>
                  {(incidents?.items ?? []).map((i) => (
                    <MenuItem key={i.id} value={i.id}>{i.title} — {fmt(i.created_at)}</MenuItem>))}
                </TextField>
              )}
            </>
          )}
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => act.mutate()}
                disabled={title.trim().length < 3 || purpose.trim().length < 5 || !chosen || act.isPending}>
          Create</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Seal ─────────────────────────────────────────────────────────────────────

interface SealProps { open: boolean; file: PackageDetail; onClose: () => void; onSealed: () => Promise<unknown> }

export function SealDialog(props: SealProps) {
  return props.open ? <SealForm {...props} /> : null
}

function SealForm({ file, onClose, onSealed }: SealProps) {
  const act = useMutation({ mutationFn: () => sealPackage(file.id).then(onSealed), onSuccess: onClose })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Seal {file.package_number}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 1.5 }}>
          Sealing writes down everything in the package — each of its {file.counts.items} item
          {file.counts.items === 1 ? '' : 's'}, when it was captured and its checksum — and the hash of that list.
        </Typography>
        <Alert severity="warning" sx={{ mb: 1.5 }}>
          A sealed package cannot be changed by anybody: nothing can be added, taken out or corrected. A wrong package
          is replaced by a new one.</Alert>
        <Typography variant="body2" sx={{ mb: 1.5 }}>
          Every item is placed under a hold, so that it is kept past its retention period until the hold is lifted.
        </Typography>
        {file.counts.without_checksum > 0 && (
          <Alert severity="info" sx={{ mb: 1.5 }}>
            {file.counts.without_checksum} item{file.counts.without_checksum === 1 ? ' has' : 's have'} no checksum
            recorded. The manifest will say so, and an export will not be able to verify
            {file.counts.without_checksum === 1 ? ' it' : ' them'}.</Alert>)}
        {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="warning" disabled={act.isPending} onClick={() => act.mutate()}>
          Seal it</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Export ───────────────────────────────────────────────────────────────────

interface ExportProps {
  open: boolean; file: PackageDetail; onClose: () => void; onExported: (result: ExportResult) => Promise<unknown>
}

export function ExportDialog(props: ExportProps) {
  return props.open ? <ExportForm {...props} /> : null
}

function ExportForm({ file, onClose, onExported }: ExportProps) {
  const [reason, setReason] = useState('')
  const [marked, setMarked] = useState(true)
  const act = useMutation({
    mutationFn: () => exportPackage(file.id, file.package_number, reason.trim(), marked),
    onSuccess: async (result) => { saveFile(result.file, result.filename); await onExported(result); onClose() },
  })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Export {file.package_number}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          The archive holds the manifest as it was sealed, each original file exactly as the platform holds it, and
          a report of whether each file still matches its checksum. The export is written into the chain of custody
          with your name and the reason.</Typography>
        <TextField autoFocus fullWidth multiline minRows={2} label="Why it is leaving the platform" value={reason}
                   onChange={(e) => setReason(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
        <FormControlLabel sx={{ mt: 1 }} control={<Switch checked={marked} onChange={(_, v) => setMarked(v)} />}
                          label="Add a marked viewing copy of each picture" />
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          An original is never marked: a mark changes the file, and a changed file no longer matches its checksum.
          The marked copy says on its face that it is not the original.</Typography>
        {act.isError && <Alert severity="error" sx={{ mt: 1.5 }}>{apiError(act.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={reason.trim().length < 5 || act.isPending} onClick={() => act.mutate()}>
          {act.isPending ? 'Building the archive…' : 'Export'}</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Shared, or released ──────────────────────────────────────────────────────

interface DisclosureProps { open: boolean; file: PackageDetail; onClose: () => void; onRecorded: () => Promise<unknown> }

export function DisclosureDialog(props: DisclosureProps) {
  return props.open ? <DisclosureForm {...props} /> : null
}

function DisclosureForm({ file, onClose, onRecorded }: DisclosureProps) {
  const [step, setStep] = useState<'SHARED' | 'RELEASED'>('SHARED')
  const [recipient, setRecipient] = useState('')
  const [organisation, setOrganisation] = useState('')
  const [reason, setReason] = useState('')
  const act = useMutation({
    mutationFn: () => recordDisclosure(file.id, {
      step, recipient: recipient.trim(), organisation: organisation.trim() || undefined, reason: reason.trim(),
    }).then(onRecorded),
    onSuccess: onClose,
  })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Record who {file.package_number} went to</DialogTitle>
      <DialogContent>
        <Alert severity="info" sx={{ mb: 2 }}>
          This records what you did with an export. The platform sends nothing to anybody.</Alert>
        <Stack sx={{ gap: 2 }}>
          <TextField select label="What was done" value={step}
                     onChange={(e) => setStep(e.target.value as 'SHARED' | 'RELEASED')}>
            <MenuItem value="SHARED">Shared — given to somebody to look at</MenuItem>
            <MenuItem value="RELEASED">Released — handed over for good</MenuItem>
          </TextField>
          <TextField label="Who it went to" value={recipient} onChange={(e) => setRecipient(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 200 } }} />
          <TextField label="Their organisation (optional)" value={organisation}
                     onChange={(e) => setOrganisation(e.target.value)} slotProps={{ htmlInput: { maxLength: 200 } }} />
          <TextField label="Why" multiline minRows={2} value={reason} onChange={(e) => setReason(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 2000 } }} />
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => act.mutate()}
                disabled={recipient.trim().length < 2 || reason.trim().length < 5 || act.isPending}>Record</Button>
      </DialogActions>
    </Dialog>
  )
}

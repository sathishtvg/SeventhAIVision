/**
 * Instructions in force at a site: what the next shift, and the one after,
 * have to know until somebody closes it.
 *
 * An instruction is carried into every shift's summary while it is in force.
 * It is closed with a reason, or runs out on its date; it is never removed.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Skeleton, TextField,
  ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { getSites } from '@/api/sites'
import { apiError, closeInstruction, issueInstruction, listInstructions, readInstruction } from '@/api/occurrenceBook'
import type { Instruction } from '@/api/occurrenceBook'
import { fmt, standsUntil } from '@/components/occurrenceBook/bookFormat'
import { ErrorState } from '@/components/states'

const shown = { select: { displayEmpty: true }, inputLabel: { shrink: true } }

function IssueDialog({ open, siteId, onClose, onDone }: {
  open: boolean; siteId: string; onClose: () => void; onDone: () => Promise<unknown> }) {
  return open ? <IssueForm siteId={siteId} onClose={onClose} onDone={onDone} /> : null
}

function IssueForm({ siteId, onClose, onDone }: { siteId: string; onClose: () => void; onDone: () => Promise<unknown> }) {
  const [site, setSite] = useState(siteId)
  const [body, setBody] = useState('')
  const [until, setUntil] = useState('')
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const act = useMutation({
    mutationFn: () => issueInstruction({ site_id: site, body: body.trim(), expires_at: until ? new Date(until).toISOString() : null })
      .then(onDone),
    onSuccess: onClose,
  })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Issue an instruction</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <TextField select label="Site" value={site} onChange={(e) => setSite(e.target.value)}>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField label="The instruction" value={body} multiline minRows={3} autoFocus
                     onChange={(e) => setBody(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
          <TextField label="In force until (optional)" type="datetime-local" value={until}
                     helperText="Left empty, it stands until somebody closes it"
                     onChange={(e) => setUntil(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
          <Typography variant="caption" color="text.secondary">
            The guards on shift at the site are told now, and it is carried into every shift's summary until it ends.
          </Typography>
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!site || !body.trim() || act.isPending} onClick={() => act.mutate()}>
          Issue it</Button>
      </DialogActions>
    </Dialog>
  )
}

function CloseDialog({ instruction, onClose, onDone }: {
  instruction: Instruction | null; onClose: () => void; onDone: () => Promise<unknown> }) {
  return instruction ? <CloseForm instruction={instruction} onClose={onClose} onDone={onDone} /> : null
}

function CloseForm({ instruction, onClose, onDone }: {
  instruction: Instruction; onClose: () => void; onDone: () => Promise<unknown> }) {
  const [note, setNote] = useState('')
  const act = useMutation({ mutationFn: () => closeInstruction(instruction.id, note.trim()).then(onDone), onSuccess: onClose })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Close this instruction</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>“{instruction.body}”</Typography>
          <TextField label="Why it no longer stands" value={note} autoFocus multiline minRows={2}
                     onChange={(e) => setNote(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="warning" disabled={!note.trim() || act.isPending} onClick={() => act.mutate()}>
          Close it</Button>
      </DialogActions>
    </Dialog>
  )
}

export default function BookInstructions() {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState('')
  const [state, setState] = useState<'in_force' | 'ended'>('in_force')
  const [issuing, setIssuing] = useState(false)
  const [closing, setClosing] = useState<Instruction | null>(null)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data, isLoading, error, refetch: refetchData } = useQuery({
    queryKey: ['dob-instructions', siteId, state], queryFn: () => listInstructions({ site_id: siteId || undefined, state }) })
  const again = () => qc.invalidateQueries({ queryKey: ['dob-instructions'] })
  const read = useMutation({ mutationFn: (id: string) => readInstruction(id).then(again) })
  const items = data?.items ?? []
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 220 }} slotProps={shown}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">Every site</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <ToggleButtonGroup size="small" exclusive value={state}
                             onChange={(_, v: 'in_force' | 'ended' | null) => v && setState(v)}>
            <ToggleButton value="in_force">In force</ToggleButton>
            <ToggleButton value="ended">Ended</ToggleButton>
          </ToggleButtonGroup>
          <Box sx={{ flex: 1 }} />
          {data?.can_issue && (
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => setIssuing(true)}>Issue an instruction</Button>)}
        </Stack>
      </GlassCard>
      {!!error && <ErrorState compact error={error} onRetry={refetchData} sx={{ mb: 2 }} />}
      {isLoading ? <Skeleton height={160} /> : !items.length && !error ? (
        <Alert severity="info">
          {state === 'in_force' ? 'No instruction is in force. A shift\'s summary will say so.' : 'No instruction has ended.'}
        </Alert>
      ) : (
        <Stack sx={{ gap: 1.5 }}>
          {items.map((n) => (
            <GlassCard key={n.id} sx={{ p: 2 }} data-testid="instruction">
              <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 0.5 }}>
                <Chip size="small" color="info" variant="outlined" label={n.site_name} />
                <Typography variant="caption" color="text.secondary">
                  {n.issued_by_name ?? 'Somebody no longer on the system'} · {fmt(n.issued_at)}
                </Typography>
                <Box sx={{ flex: 1 }} />
                <Typography variant="caption" color="text.secondary">
                  Read by {n.reads} {n.reads === 1 ? 'person' : 'people'}</Typography>
              </Stack>
              <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>{n.body}</Typography>
              <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mt: 1 }}>
                <Typography variant="caption" color={n.in_force ? 'text.secondary' : 'warning.main'}>{standsUntil(n)}</Typography>
                <Box sx={{ flex: 1 }} />
                {n.in_force && (n.read_by_me ? <Chip size="small" color="success" variant="outlined" label="You have read it" />
                  : <Button size="small" variant="outlined" disabled={read.isPending} onClick={() => read.mutate(n.id)}>
                    I have read it</Button>)}
                {n.in_force && data?.can_issue && (
                  <Button size="small" color="warning" onClick={() => setClosing(n)}>Close</Button>)}
              </Stack>
            </GlassCard>))}
        </Stack>
      )}
      {read.isError && <Alert severity="error" sx={{ mt: 1.5 }}>{apiError(read.error)}</Alert>}
      <IssueDialog open={issuing} siteId={siteId} onClose={() => setIssuing(false)} onDone={again} />
      <CloseDialog instruction={closing} onClose={() => setClosing(null)} onDone={again} />
    </>
  )
}

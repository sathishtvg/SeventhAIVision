/**
 * The dialogs of maintenance: raising a work order, one order opened, and a
 * schedule.
 *
 * An order the platform put forward is shown as that — with what it was put
 * forward from, in the server's words, and the note that it is not yet work.
 * Which buttons are there is the server's `may`.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Skeleton, TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import {
  acceptOrder, addSchedule, apiError, cancelOrder, changeOrder, completeOrder, dismissOrder, getMaintenanceOptions,
  getOrder, raiseOrder, startOrder,
} from '@/api/maintenance'
import type { MaintenanceOptions, OrderKind, Priority, Schedule, WorkOrder } from '@/api/maintenance'
import {
  KIND_LABEL, ORDER_COLOUR, ORDER_LABEL, ORIGIN_LABEL, PRIORITY_LABEL, fmt, heldBy, iso,
} from './assetFormat'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }
const boxed = { gap: 1.5, p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1.5 }

/** Who an order is given to: one of the organisation's people, or a vendor named in words. */
function WhoHasIt({ people, user, name, onUser, onName }: {
  people: MaintenanceOptions['people']; user: string; name: string; onUser: (v: string) => void; onName: (v: string) => void
}) {
  return (
    <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
      <TextField select label="Given to" value={user} sx={{ minWidth: 240 }} slotProps={shrunk}
                 onChange={(e) => onUser(e.target.value)}>
        <MenuItem value="">Nobody yet</MenuItem>
        {people.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
      </TextField>
      <TextField label="Or a vendor or technician" value={name} sx={{ flex: 1, minWidth: 220 }}
                 onChange={(e) => onName(e.target.value)} slotProps={{ htmlInput: { maxLength: 200 } }} />
    </Stack>
  )
}

interface RaiseProps { open: boolean; assetId?: string; onClose: () => void; onDone: (made: WorkOrder) => void }

export function RaiseDialog(props: RaiseProps) {
  return props.open ? <RaiseForm {...props} /> : null
}

function RaiseForm({ assetId, onClose, onDone }: RaiseProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [kind, setKind] = useState<OrderKind>('CORRECTIVE')
  const [priority, setPriority] = useState<Priority>('NORMAL')
  const [about, setAbout] = useState(assetId ? `asset:${assetId}` : '')
  const [due, setDue] = useState('')
  const [user, setUser] = useState('')
  const [name, setName] = useState('')
  const { data: options, isLoading, error } = useQuery({
    queryKey: ['maintenance-options'], queryFn: () => getMaintenanceOptions() })
  const act = useMutation({
    mutationFn: () => {
      const [what, id] = about.split(':')
      return raiseOrder({
        title: title.trim(), description: description.trim() || null, kind, priority, due_at: iso(due),
        asset_id: what === 'asset' ? id : null, defect_id: what === 'defect' ? id : null,
        assigned_to_user_id: user || null, assigned_to_name: name.trim() || null })
    },
    onSuccess: (made) => { onDone(made); onClose() },
  })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Raise a work order</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          {isLoading && <Skeleton height={60} />}
          {!!error && <Alert severity="error">{apiError(error)}</Alert>}
          <TextField label="What is to be done" value={title} autoFocus onChange={(e) => setTitle(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 200 } }} />
          <TextField select label="On" value={about} slotProps={shrunk} onChange={(e) => setAbout(e.target.value)}
                     helperText="An order raised for a facility defect does not change the defect: that is resolved where it always was.">
            <MenuItem value="">Nothing in particular</MenuItem>
            {(options?.assets ?? []).map((a) => (
              <MenuItem key={a.id} value={`asset:${a.id}`}>{a.asset_code} · {a.name}{a.site_name ? ` — ${a.site_name}` : ''}</MenuItem>))}
            {(options?.defects ?? []).map((d) => (
              <MenuItem key={d.id} value={`defect:${d.id}`}>
                Defect: {d.description}{d.site_name ? ` — ${d.site_name}` : ''}</MenuItem>))}
          </TextField>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField select label="Kind of work" value={kind} sx={{ minWidth: 180 }}
                       onChange={(e) => setKind(e.target.value as OrderKind)}>
              {(Object.keys(KIND_LABEL) as OrderKind[]).map((k) => <MenuItem key={k} value={k}>{KIND_LABEL[k]}</MenuItem>)}
            </TextField>
            <TextField select label="Priority" value={priority} sx={{ minWidth: 160 }}
                       onChange={(e) => setPriority(e.target.value as Priority)}>
              {(Object.keys(PRIORITY_LABEL) as Priority[]).map((p) => <MenuItem key={p} value={p}>{PRIORITY_LABEL[p]}</MenuItem>)}
            </TextField>
            <TextField label="Due" type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)}
                       slotProps={{ inputLabel: { shrink: true } }} />
          </Stack>
          <TextField label="More about it" value={description} multiline minRows={2}
                     onChange={(e) => setDescription(e.target.value)} slotProps={{ htmlInput: { maxLength: 5000 } }} />
          <WhoHasIt people={options?.people ?? []} user={user} name={name} onUser={setUser} onName={setName} />
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" disabled={!title.trim() || act.isPending} onClick={() => act.mutate()}>Raise it</Button>
      </DialogActions>
    </Dialog>
  )
}

type Doing = 'accept' | 'dismiss' | 'cancel' | 'complete' | 'give' | null

interface OrderProps { id: string | null; onClose: () => void; onChanged: () => Promise<unknown> }

export function OrderDialog(props: OrderProps) {
  return props.id ? <OrderView {...props} id={props.id} /> : null
}

function OrderView({ id, onClose, onChanged }: OrderProps & { id: string }) {
  const [doing, setDoing] = useState<Doing>(null)
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const [parts, setParts] = useState('')
  const [downtime, setDowntime] = useState('')
  const [user, setUser] = useState('')
  const [name, setName] = useState('')
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ['work-order', id], queryFn: () => getOrder(id) })
  // The people an order can be given to, asked for only by somebody who is about to give it.
  const { data: options } = useQuery({
    queryKey: ['maintenance-options'], queryFn: () => getMaintenanceOptions(), enabled: doing === 'accept' || doing === 'give' })
  const again = () => Promise.all([refetch(), onChanged()])
  const run = useMutation({
    mutationFn: (what: () => Promise<unknown>) => what().then(again),
    onSuccess: () => { setDoing(null); setReason('') },
  })
  const start = (what: Doing, o: WorkOrder) => {
    run.reset()
    setReason('')
    setUser(o.assigned_to_user_id ?? '')
    setName(o.assigned_to_name ?? '')
    setDoing(what)
  }
  const o = data
  const minutes = downtime.trim() === '' ? null : Number(downtime)
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>
        {o ? (
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <span>{o.number} · {o.title}</span>
            <Chip size="small" color={ORDER_COLOUR[o.state]} label={ORDER_LABEL[o.state]} />
            {o.overdue && <Chip size="small" color="error" label="Overdue" />}
          </Stack>) : 'Work order'}
      </DialogTitle>
      <DialogContent>
        {isLoading && <Skeleton height={200} />}
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {o && (
          <Stack sx={{ gap: 1.5, mt: 0.5 }}>
            <Typography variant="body2" color="text.secondary">
              {KIND_LABEL[o.kind]} · {PRIORITY_LABEL[o.priority]} priority
              {o.asset_code ? ` · ${o.asset_code} ${o.asset_name}` : ''}{o.site_name ? ` · ${o.site_name}` : ''}
              {o.due_at ? ` · due ${fmt(o.due_at)}` : ''}
            </Typography>
            <Box sx={boxed} data-testid="origin">
              <Typography variant="body2" sx={{ fontWeight: 600 }}>{ORIGIN_LABEL[o.origin]}</Typography>
              {o.suggestion_reason && <Typography variant="body2">{o.suggestion_reason}</Typography>}
              {o.note && <Typography variant="caption" color="text.secondary">{o.note}</Typography>}
              {o.accepted_by_name && (
                <Typography variant="caption" color="text.secondary">
                  Accepted as work by {o.accepted_by_name}, {fmt(o.accepted_at)}</Typography>)}
              {o.raised_by_name && (
                <Typography variant="caption" color="text.secondary">Raised by {o.raised_by_name}, {fmt(o.raised_at)}</Typography>)}
            </Box>
            {o.description && <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{o.description}</Typography>}
            <Typography variant="body2">Who has it: {heldBy(o)}</Typography>
            {o.started_at && (
              <Typography variant="caption" color="text.secondary">
                Started by {o.started_by_name ?? 'somebody no longer on the system'}, {fmt(o.started_at)}</Typography>)}
            {o.state === 'DONE' && (
              <Box sx={boxed} data-testid="done">
                <Typography variant="body2">{o.completion_note}</Typography>
                <Typography variant="caption" color="text.secondary">
                  Done by {o.completed_by_name ?? 'somebody no longer on the system'}, {fmt(o.completed_at)}
                  {o.parts_used ? ` · parts: ${o.parts_used}` : ''}
                  {o.downtime_minutes !== null ? ` · out of use for ${o.downtime_minutes} min, as stated` : ''}
                </Typography>
              </Box>)}
            {(o.state === 'CANCELLED' || o.state === 'DISMISSED') && (
              <Typography variant="body2">
                {ORDER_LABEL[o.state]} by {o.closed_by_name ?? 'somebody no longer on the system'}: {o.closed_reason}
              </Typography>)}

            <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap' }}>
              {o.may.accept && (
                <Button size="small" variant="contained" color="success" onClick={() => start('accept', o)}>Accept as work</Button>)}
              {o.may.dismiss && <Button size="small" color="error" onClick={() => start('dismiss', o)}>Dismiss</Button>}
              {o.may.start && (
                <Button size="small" variant="contained" disabled={run.isPending}
                        onClick={() => run.mutate(() => startOrder(o.id))}>Start the work</Button>)}
              {o.may.complete && <Button size="small" onClick={() => start('complete', o)}>It is done</Button>}
              {o.may.change && <Button size="small" onClick={() => start('give', o)}>Give it to somebody</Button>}
              {o.may.cancel && <Button size="small" color="error" onClick={() => start('cancel', o)}>Cancel it</Button>}
            </Stack>

            {(doing === 'accept' || doing === 'give') && (
              <Stack sx={boxed}>
                {doing === 'accept' && (
                  <Typography variant="body2">
                    From now it is work, and you are recorded as having accepted it. It can be given to somebody now or later.
                  </Typography>)}
                <WhoHasIt people={options?.people ?? []} user={user} name={name} onUser={setUser} onName={setName} />
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button size="small" onClick={() => setDoing(null)}>Not yet</Button>
                  <Button size="small" variant="contained" disabled={run.isPending}
                          onClick={() => run.mutate(() => (doing === 'accept' ? acceptOrder : changeOrder)(o.id, {
                            assigned_to_user_id: user || null, assigned_to_name: name.trim() || null }))}>
                    {doing === 'accept' ? 'Accept it' : 'Save'}</Button>
                </Stack>
              </Stack>)}
            {(doing === 'dismiss' || doing === 'cancel') && (
              <Stack sx={boxed}>
                <TextField label={doing === 'dismiss' ? 'Why it is dismissed' : 'Why it is cancelled'} value={reason} autoFocus
                           multiline minRows={2} onChange={(e) => setReason(e.target.value)}
                           helperText={doing === 'dismiss' ? 'It is kept, and the same thing is not put forward again.' : 'It is kept.'}
                           slotProps={{ htmlInput: { maxLength: 2000 } }} />
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button size="small" onClick={() => setDoing(null)}>Not yet</Button>
                  <Button size="small" variant="contained" color="error" disabled={!reason.trim() || run.isPending}
                          onClick={() => run.mutate(() => (doing === 'dismiss' ? dismissOrder : cancelOrder)(o.id, reason.trim()))}>
                    {doing === 'dismiss' ? 'Dismiss it' : 'Cancel the order'}</Button>
                </Stack>
              </Stack>)}
            {doing === 'complete' && (
              <Stack sx={boxed}>
                <TextField label="What was done" value={note} autoFocus multiline minRows={2}
                           onChange={(e) => setNote(e.target.value)} slotProps={{ htmlInput: { maxLength: 5000 } }} />
                <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
                  <TextField label="Parts used" value={parts} sx={{ flex: 1, minWidth: 220 }}
                             onChange={(e) => setParts(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
                  <TextField label="Minutes out of use" type="number" value={downtime} sx={{ width: 190 }}
                             helperText="As you know it; left empty if you do not"
                             onChange={(e) => setDowntime(e.target.value)} slotProps={{ htmlInput: { min: 0 } }} />
                </Stack>
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button size="small" onClick={() => setDoing(null)}>Not yet</Button>
                  <Button size="small" variant="contained" color="success"
                          disabled={!note.trim() || run.isPending || (minutes !== null && (!Number.isInteger(minutes) || minutes < 0))}
                          onClick={() => run.mutate(() => completeOrder(o.id, {
                            completion_note: note.trim(), parts_used: parts.trim() || null, downtime_minutes: minutes }))}>
                    Record it as done</Button>
                </Stack>
              </Stack>)}
            {run.isError && <Alert severity="error">{apiError(run.error)}</Alert>}
          </Stack>)}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

interface ScheduleProps { open: boolean; onClose: () => void; onDone: (made: Schedule) => void }

export function ScheduleDialog(props: ScheduleProps) {
  return props.open ? <ScheduleForm {...props} /> : null
}

function ScheduleForm({ onClose, onDone }: ScheduleProps) {
  const [title, setTitle] = useState('')
  const [instructions, setInstructions] = useState('')
  const [assetId, setAssetId] = useState('')
  const [every, setEvery] = useState('90')
  const [lead, setLead] = useState('7')
  const [due, setDue] = useState('')
  const { data: options } = useQuery({ queryKey: ['maintenance-options'], queryFn: () => getMaintenanceOptions() })
  const days = Number(every)
  const ahead = Number(lead)
  const act = useMutation({
    mutationFn: () => addSchedule({ title: title.trim(), instructions: instructions.trim() || null,
                                    asset_id: assetId || null, every_days: days, lead_days: ahead, next_due_on: due }),
    onSuccess: (made) => { onDone(made); onClose() },
  })
  const sound = title.trim() && due && Number.isInteger(days) && days >= 1 && days <= 3650
    && Number.isInteger(ahead) && ahead >= 0 && ahead <= 90
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Add a schedule</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <Alert severity="info">
            So many days before each date, the work is put forward for somebody to accept. It is not raised by itself.
          </Alert>
          <TextField label="What is to be done" value={title} autoFocus onChange={(e) => setTitle(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 200 } }} />
          <TextField select label="On" value={assetId} slotProps={shrunk} onChange={(e) => setAssetId(e.target.value)}>
            <MenuItem value="">Nothing in particular</MenuItem>
            {(options?.assets ?? []).map((a) => (
              <MenuItem key={a.id} value={a.id}>{a.asset_code} · {a.name}{a.site_name ? ` — ${a.site_name}` : ''}</MenuItem>))}
          </TextField>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField label="Every (days)" type="number" value={every} sx={{ width: 150 }} onChange={(e) => setEvery(e.target.value)} />
            <TextField label="Put forward (days before)" type="number" value={lead} sx={{ width: 210 }}
                       onChange={(e) => setLead(e.target.value)} />
            <TextField label="Next due on" type="date" value={due} onChange={(e) => setDue(e.target.value)}
                       slotProps={{ inputLabel: { shrink: true } }} />
          </Stack>
          <TextField label="How it is done" value={instructions} multiline minRows={2}
                     onChange={(e) => setInstructions(e.target.value)} slotProps={{ htmlInput: { maxLength: 5000 } }} />
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" disabled={!sound || act.isPending} onClick={() => act.mutate()}>Add it</Button>
      </DialogActions>
    </Dialog>
  )
}

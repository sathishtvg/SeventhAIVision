/**
 * A case: opening one, and the case itself — who is on it, its tasks, what is
 * linked to it, who is named in it, its history, and its closing.
 *
 * What the reader may do is the server's to say (`may`): a button is there
 * only when the server offers the act. A linked record the reader may not
 * read is shown as that. Closing takes two people, and the screen says so.
 * Being named in a case is not an accusation, and the screen says that too.
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Skeleton, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import {
  addInvestigator, addLink, addNote, addParty, addTask, apiError, approveClose, declineClose, downloadReport, finishTask,
  getCase, getCaseOptions, openCase, reopenCase, removeInvestigator, removeLink, removeParty, requestClose, setLead,
} from '@/api/cases'
import type { CaseFile, CaseOptions, Category, Connection, LinkKind, PartyKind, Priority } from '@/api/cases'
import { PRIORITY_LABEL, STATUS_COLOUR, TASK_LABEL, entryLine, linkLine, named, standing, taskLine } from './caseFormat'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }

interface OpenProps {
  open: boolean
  onClose: () => void
  onOpened: (c: CaseFile) => void
  sites: { id: string; name: string }[]
  options: CaseOptions | undefined
}

function OpenForm({ onClose, onOpened, sites, options }: OpenProps) {
  const [title, setTitle] = useState('')
  const [summary, setSummary] = useState('')
  const [siteId, setSiteId] = useState('')
  const [category, setCategory] = useState<Category>('OTHER')
  const [priority, setPriority] = useState<Priority>('NORMAL')
  const [from, setFrom] = useState('')
  const kinds = (options?.link_kinds ?? []).filter((k) => k.may)
  const records = kinds.flatMap((k) => (options?.recent[k.key] ?? []).map((r) => ({ value: `${k.key}:${r.id}`, label: `${k.label}: ${r.label}` })))
  const act = useMutation({
    mutationFn: () => {
      const [fromKind, fromId] = from ? from.split(':') : []
      return openCase({ title: title.trim(), summary: summary.trim(), site_id: siteId || null, category, priority,
                        ...(from ? { from_kind: fromKind as LinkKind, from_id: fromId } : {}) })
    },
    onSuccess: onOpened,
  })
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Open a case</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <TextField size="small" label="Title" value={title} onChange={(e) => setTitle(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 200 } }} />
          <TextField size="small" multiline minRows={3} label="What it is about" value={summary}
                     onChange={(e) => setSummary(e.target.value)} helperText="What happened, and why a case is opened." />
          <TextField select size="small" label="Opened from" value={from} slotProps={shrunk} onChange={(e) => setFrom(e.target.value)}
                     helperText="The record becomes the case's first link. It stays where it is.">
            <MenuItem value="">Nothing — a case by itself</MenuItem>
            {records.map((r) => <MenuItem key={r.value} value={r.value}>{r.label}</MenuItem>)}
          </TextField>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField select size="small" label="Site" value={siteId} sx={{ flex: 1, minWidth: 160 }} slotProps={shrunk}
                       onChange={(e) => setSiteId(e.target.value)}>
              <MenuItem value="">{from ? 'The site of that record' : 'No one site'}</MenuItem>
              {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
            <TextField select size="small" label="Kind" value={category} sx={{ flex: 1, minWidth: 140 }}
                       onChange={(e) => setCategory(e.target.value as Category)}>
              {(options?.categories ?? []).map((k) => <MenuItem key={k.key} value={k.key}>{k.label}</MenuItem>)}
            </TextField>
            <TextField select size="small" label="Priority" value={priority} sx={{ flex: 1, minWidth: 140 }}
                       onChange={(e) => setPriority(e.target.value as Priority)}>
              {(options?.priorities ?? []).map((p) => <MenuItem key={p} value={p}>{PRIORITY_LABEL[p]}</MenuItem>)}
            </TextField>
          </Stack>
          <Typography variant="caption" color="text.secondary">You lead a case you open, until somebody who manages cases says otherwise.</Typography>
        </Stack>
        {act.isError && <Alert severity="error" sx={{ mt: 2 }}>{apiError(act.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Not now</Button>
        <Button variant="contained" disabled={!title.trim() || !summary.trim() || act.isPending} onClick={() => act.mutate()}>Open it</Button>
      </DialogActions>
    </Dialog>
  )
}

export function OpenCaseDialog(props: OpenProps) {
  return props.open ? <OpenForm {...props} /> : null
}

/** Something to write and a button to do it with: a note, a reason, what was found. */
function Ask({ label, button, onDone, busy, rows = 1, helper }: {
  label: string; button: string; onDone: (said: string) => Promise<unknown>; busy: boolean; rows?: number; helper?: string
}) {
  const [said, setSaid] = useState('')
  return (
    <Stack direction="row" sx={{ gap: 1, alignItems: 'flex-start', mt: 1, flexWrap: 'wrap' }}>
      <TextField size="small" label={label} value={said} multiline={rows > 1} minRows={rows} sx={{ flex: 1, minWidth: 240 }}
                 onChange={(e) => setSaid(e.target.value)} helperText={helper} slotProps={{ htmlInput: { maxLength: 4000 } }} />
      <Button size="small" variant="outlined" disabled={busy || !said.trim()}
              onClick={() => { void onDone(said.trim()).then(() => setSaid(''), () => undefined) }}>{button}</Button>
    </Stack>
  )
}

function Part({ title, testId, children }: { title: string; testId: string; children: ReactNode }) {
  return (
    <Box data-testid={testId} sx={{ py: 1.5, borderTop: 1, borderColor: 'divider' }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>{title}</Typography>
      {children}
    </Box>
  )
}

function CaseView({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const qc = useQueryClient()
  const { data: c, isLoading, error } = useQuery({ queryKey: ['case', id], queryFn: () => getCase(id) })
  const { data: options } = useQuery({ queryKey: ['case-options'], queryFn: getCaseOptions })
  const [person, setPerson] = useState('')
  const [taskFor, setTaskFor] = useState('')
  const [link, setLink] = useState('')
  const [party, setParty] = useState<{ kind: PartyKind; connection: Connection }>({ kind: 'PERSON', connection: 'WITNESS' })
  const [removing, setRemoving] = useState<{ what: 'link' | 'party' | 'task'; id: string } | null>(null)
  const run = useMutation({
    mutationFn: (step: () => Promise<CaseFile>) => step(),
    onSuccess: (next) => { qc.setQueryData(['case', id], next); setRemoving(null); onChanged() },
  })
  const report = useMutation({ mutationFn: () => downloadReport(id, c?.case_number ?? 'case') })
  const go = (step: () => Promise<CaseFile>) => run.mutateAsync(step)
  /** A button's step: what went wrong is shown below, not thrown. */
  const tap = (step: () => Promise<CaseFile>) => { void go(step).catch(() => undefined) }
  const busy = run.isPending
  const people = options?.people ?? []
  const linkable = (options?.link_kinds ?? []).filter((k) => k.may)
    .flatMap((k) => (options?.recent[k.key] ?? []).map((r) => ({ value: `${k.key}:${r.id}`, label: `${k.label}: ${r.label}` })))
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{c ? `${c.case_number} — ${c.title}` : 'Case'}</DialogTitle>
      <DialogContent data-testid="case">
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {isLoading && <Skeleton height={260} />}
        {c && (
          <>
            <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
              <Chip size="small" color={STATUS_COLOUR[c.status]} label={c.status_label} />
              <Chip size="small" variant="outlined" label={c.category_label} />
              <Chip size="small" variant="outlined" label={PRIORITY_LABEL[c.priority]} />
              <Chip size="small" variant="outlined" label={c.site?.name ?? 'No one site'} />
            </Stack>
            <Typography variant="body2" data-testid="standing">{standing(c)}</Typography>
            <Typography variant="body2" sx={{ mt: 1, whiteSpace: 'pre-wrap' }}>{c.summary}</Typography>
            {c.outcome && (
              <Alert severity="info" sx={{ mt: 1 }} data-testid="outcome"><strong>What was found:</strong> {c.outcome}</Alert>)}
            {!c.may.work && c.status === 'OPEN' && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                You are not on this case. It is worked on by its lead and its investigators, or by whoever manages cases.
              </Typography>)}

            <Part title="Who is on it" testId="people">
              <Typography variant="body2">Lead: {c.lead_name ?? 'nobody'}. Opened by {named(c.opened_by_name)}.</Typography>
              {c.investigators.map((i) => (
                <Stack key={i.user_id} direction="row" sx={{ gap: 1, alignItems: 'center' }} data-testid="investigator">
                  <Typography variant="body2">Investigator: {named(i.name)}</Typography>
                  {c.may.assign && (
                    <Button size="small" disabled={busy} onClick={() => tap(() => removeInvestigator(id, i.user_id))}>Take off</Button>)}
                </Stack>))}
              {c.may.assign && (
                <Stack direction="row" sx={{ gap: 1, mt: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                  <TextField select size="small" label="Somebody" value={person} sx={{ minWidth: 220 }} slotProps={shrunk}
                             onChange={(e) => setPerson(e.target.value)}>
                    <MenuItem value="">Choose somebody</MenuItem>
                    {people.map((p) => <MenuItem key={p.id} value={p.id}>{named(p.name)}</MenuItem>)}
                  </TextField>
                  <Button size="small" variant="outlined" disabled={busy || !person} onClick={() => tap(() => addInvestigator(id, person))}>
                    Put on the case</Button>
                  <Button size="small" disabled={busy || !person} onClick={() => tap(() => setLead(id, person))}>Make lead</Button>
                </Stack>)}
            </Part>

            <Part title="Tasks" testId="tasks">
              {!c.tasks.length && <Typography variant="body2" color="text.secondary">None.</Typography>}
              {c.tasks.map((t) => (
                <Box key={t.id} data-testid="task" sx={{ mb: 0.5 }}>
                  <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                    <Chip size="small" variant={t.state === 'OPEN' ? 'filled' : 'outlined'} label={TASK_LABEL[t.state]} />
                    <Typography variant="body2" sx={{ flex: 1 }}>{taskLine(t)}</Typography>
                    {t.may_finish && (
                      <>
                        <Button size="small" disabled={busy} onClick={() => tap(() => finishTask(id, t.id, 'done', null))}>Done</Button>
                        <Button size="small" disabled={busy} onClick={() => setRemoving({ what: 'task', id: t.id })}>Drop</Button>
                      </>)}
                  </Stack>
                  {removing?.what === 'task' && removing.id === t.id && (
                    <Ask label="Why it is dropped" button="Drop it" busy={busy} onDone={(said) => go(() => finishTask(id, t.id, 'drop', said))} />)}
                </Box>))}
              {c.may.work && (
                <Stack direction="row" sx={{ gap: 1, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <TextField select size="small" label="Given to" value={taskFor} sx={{ minWidth: 200, mt: 1 }} slotProps={shrunk}
                             onChange={(e) => setTaskFor(e.target.value)}>
                    <MenuItem value="">Nobody yet</MenuItem>
                    {people.map((p) => <MenuItem key={p.id} value={p.id}>{named(p.name)}</MenuItem>)}
                  </TextField>
                  <Box sx={{ flex: 1, minWidth: 260 }}>
                    <Ask label="A task" button="Add the task" busy={busy}
                         onDone={(said) => go(() => addTask(id, { title: said, assigned_to_user_id: taskFor || null }))} />
                  </Box>
                </Stack>)}
            </Part>

            <Part title="Linked records" testId="links">
              <Typography variant="caption" color="text.secondary">
                Referred to, not copied: each stays where it is, under its own permission.</Typography>
              {!c.links.length && <Typography variant="body2" color="text.secondary">None.</Typography>}
              {c.links.map((x) => (
                <Box key={x.id} data-testid="link">
                  <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                    <Chip size="small" variant="outlined" label={x.kind_label} />
                    <Typography variant="body2" sx={{ flex: 1 }} color={x.state === 'SHOWN' ? 'text.primary' : 'text.secondary'}>
                      {linkLine(x)}{x.note ? ` — ${x.note}` : ''}</Typography>
                    {c.may.work && <Button size="small" disabled={busy} onClick={() => setRemoving({ what: 'link', id: x.id })}>Take off</Button>}
                  </Stack>
                  {removing?.what === 'link' && removing.id === x.id && (
                    <Ask label="Why it is taken off the case" button="Take it off" busy={busy}
                         helper="The record itself is not touched." onDone={(said) => go(() => removeLink(id, x.id, said))} />)}
                </Box>))}
              {c.may.work && (
                <Stack direction="row" sx={{ gap: 1, mt: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                  <TextField select size="small" label="A record to link" value={link} sx={{ flex: 1, minWidth: 260 }} slotProps={shrunk}
                             onChange={(e) => setLink(e.target.value)}>
                    <MenuItem value="">Choose a recent record</MenuItem>
                    {linkable.map((r) => <MenuItem key={r.value} value={r.value}>{r.label}</MenuItem>)}
                  </TextField>
                  <Button size="small" variant="outlined" disabled={busy || !link}
                          onClick={() => { const [kind, ref] = link.split(':'); void go(() => addLink(id, { kind: kind as LinkKind, ref_id: ref })).then(() => setLink(''), () => undefined) }}>
                    Link it</Button>
                </Stack>)}
            </Part>

            <Part title="People and vehicles named in it" testId="parties">
              <Typography variant="caption" color="text.secondary">{c.party_note}</Typography>
              {!c.parties.length && <Typography variant="body2" color="text.secondary">None.</Typography>}
              {c.parties.map((x) => (
                <Box key={x.id} data-testid="party">
                  <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                    <Chip size="small" variant="outlined" label={x.kind === 'PERSON' ? 'Person' : 'Vehicle'} />
                    <Typography variant="body2" sx={{ flex: 1 }}>{x.label} — {x.connection_label.toLowerCase()}{x.note ? `. ${x.note}` : ''}</Typography>
                    {c.may.work && <Button size="small" disabled={busy} onClick={() => setRemoving({ what: 'party', id: x.id })}>Take off</Button>}
                  </Stack>
                  {removing?.what === 'party' && removing.id === x.id && (
                    <Ask label="Why it is taken off the case" button="Take it off" busy={busy} onDone={(said) => go(() => removeParty(id, x.id, said))} />)}
                </Box>))}
              {c.may.work && (
                <Stack direction="row" sx={{ gap: 1, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <TextField select size="small" label="A person or a vehicle" value={party.kind} sx={{ minWidth: 150, mt: 1 }}
                             onChange={(e) => setParty({ ...party, kind: e.target.value as PartyKind })}>
                    <MenuItem value="PERSON">Person</MenuItem>
                    <MenuItem value="VEHICLE">Vehicle</MenuItem>
                  </TextField>
                  <TextField select size="small" label="How it is connected" value={party.connection} sx={{ minWidth: 190, mt: 1 }}
                             onChange={(e) => setParty({ ...party, connection: e.target.value as Connection })}>
                    {(options?.connections ?? []).map((k) => <MenuItem key={k.key} value={k.key}>{k.label}</MenuItem>)}
                  </TextField>
                  <Box sx={{ flex: 1, minWidth: 240 }}>
                    <Ask label={party.kind === 'PERSON' ? 'Name' : 'Plate'} button="Add" busy={busy}
                         onDone={(said) => go(() => addParty(id, { kind: party.kind, label: said, connection: party.connection }))} />
                  </Box>
                </Stack>)}
            </Part>

            <Part title="History" testId="history">
              {c.entries.map((e) => (
                <Box key={e.id} data-testid="entry" sx={{ mb: 0.5 }}>
                  <Typography variant="caption" color="text.secondary">{entryLine(e)}</Typography>
                  {e.body && <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{e.body}</Typography>}
                </Box>))}
              {c.may.work && (
                <Ask label="A note" button="Add the note" busy={busy} rows={2} helper="A note is not changed or removed afterwards."
                     onDone={(said) => go(() => addNote(id, said))} />)}
            </Part>

            <Part title="Closing" testId="closing">
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{c.two_people_note}</Typography>
              {c.may.request_close && (
                <Ask label="What was found, and what was done" button="Ask for it to be closed" busy={busy} rows={2}
                     helper={c.tasks_open ? `${c.tasks_open} task${c.tasks_open === 1 ? ' is' : 's are'} still to do: finish or drop ${c.tasks_open === 1 ? 'it' : 'them'} first.` : 'Somebody else approves.'}
                     onDone={(said) => go(() => requestClose(id, said))} />)}
              {c.status === 'AWAITING_APPROVAL' && !c.may.approve_close && !c.may.decline_close && (
                <Typography variant="body2" color="text.secondary">It is with whoever manages cases to approve or decline.</Typography>)}
              {c.status === 'AWAITING_APPROVAL' && c.may.decline_close && !c.may.approve_close && (
                <Typography variant="body2" color="text.secondary">You asked for it to be closed, so somebody else approves. You may decline it.</Typography>)}
              {c.may.approve_close && (
                <Button size="small" variant="contained" sx={{ mt: 1 }} disabled={busy} onClick={() => tap(() => approveClose(id))}>
                  Approve its closing</Button>)}
              {c.may.decline_close && (
                <Ask label="Why its closing is declined" button="Decline" busy={busy} onDone={(said) => go(() => declineClose(id, said))} />)}
              {c.may.reopen && (
                <Ask label="Why it is reopened" button="Reopen it" busy={busy} onDone={(said) => go(() => reopenCase(id, said))} />)}
            </Part>
            {run.isError && <Alert severity="error" sx={{ mt: 1 }}>{apiError(run.error)}</Alert>}
            {report.isError && <Alert severity="error" sx={{ mt: 1 }}>{apiError(report.error)}</Alert>}
          </>)}
      </DialogContent>
      <DialogActions>
        {c && <Button disabled={report.isPending} onClick={() => report.mutate()}>The report, as a PDF</Button>}
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}

export function CaseDialog({ id, onClose, onChanged }: { id: string | null; onClose: () => void; onChanged: () => void }) {
  return id ? <CaseView id={id} onClose={onClose} onChanged={onChanged} /> : null
}

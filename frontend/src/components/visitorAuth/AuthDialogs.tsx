/**
 * The dialogs of visitor authorisation: asking for one, and one opened — what
 * stands, the answer, the places, the escort, the ID and where a badge was used.
 *
 * What stands is shown in the server's own sentences. Which buttons are there
 * is the server's `may`: this screen never works out for itself who may answer.
 *
 * A door event outside what a visit is authorised for is shown as something to
 * look at, with what a person made of it — never as a finding.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel,
  MenuItem, Skeleton, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { getSites } from '@/api/sites'
import {
  apiError, approveAuthorisation, askForAuthorisation, cancelAuthorisation, declineAuthorisation, extendAuthorisation,
  getAuthorisation, getOptions, recordIdSeen, reviewMovement, setEscort, setPlaces,
} from '@/api/visitorAuth'
import type { Authorisation, AuthorisationDetail, Movement, Options, SubjectKind } from '@/api/visitorAuth'
import {
  REVIEW_LABEL, STANDING_COLOUR, STANDING_LABEL, about, against, door, fmt, iso, local, period, who,
} from './authFormat'

const shrunk = { select: { displayEmpty: true }, inputLabel: { shrink: true } }
const boxed = { gap: 1.5, p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1.5 }

/** The places of a site, each ticked or not. None ticked is the site in general. */
function PlacePicker({ places, chosen, onChange }: { places: Options['places']; chosen: string[]
                                                     onChange: (ids: string[]) => void }) {
  if (!places.length) {
    return (
      <Typography variant="caption" color="text.secondary">
        This site has no places on its map, so the authorisation is for the site in general.
      </Typography>)
  }
  const flip = (id: string) => onChange(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id])
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">
        Which places it is for. None ticked: the site in general.
      </Typography>
      <Box sx={{ maxHeight: 180, overflowY: 'auto', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
        {places.map((p) => (
          <FormControlLabel key={p.id} label={p.part_of ? `${p.name} (${p.part_of})` : p.name}
                            control={<Checkbox size="small" checked={chosen.includes(p.id)} onChange={() => flip(p.id)} />} />))}
      </Box>
    </Box>
  )
}

interface AskProps { open: boolean; onClose: () => void; onDone: (made: Authorisation) => void }

export function AskDialog(props: AskProps) {
  return props.open ? <AskForm {...props} /> : null
}

function AskForm({ onClose, onDone }: AskProps) {
  const [siteId, setSiteId] = useState('')
  const [kind, setKind] = useState<SubjectKind>('visit')
  const [subjectId, setSubjectId] = useState('')
  const [host, setHost] = useState('')
  const [from, setFrom] = useState('')
  const [until, setUntil] = useState('')
  const [purpose, setPurpose] = useState('')
  const [escort, setEscort] = useState(false)
  const [escortWho, setEscortWho] = useState('')
  const [escortNote, setEscortNote] = useState('')
  const [places, setChosenPlaces] = useState<string[]>([])
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data: options, isLoading, error } = useQuery({
    queryKey: ['visitor-auth-options', siteId], queryFn: () => getOptions(siteId), enabled: !!siteId })
  const visit = options?.visits.find((v) => v.id === subjectId)
  const permit = options?.permits.find((p) => p.id === subjectId)
  const choose = (id: string) => {
    // The host and the period are the visit's own until somebody says otherwise.
    const v = options?.visits.find((x) => x.id === id)
    const p = options?.permits.find((x) => x.id === id)
    setSubjectId(id)
    setHost(v?.host_user_id ?? '')
    setFrom(local(v?.expected_from ?? p?.start_at))
    setUntil(local(v?.expected_until ?? p?.end_at))
    setPurpose(v?.purpose ?? p?.work_description ?? '')
  }
  const act = useMutation({
    mutationFn: () => askForAuthorisation({
      ...(kind === 'visit' ? { visitor_id: subjectId } : { work_permit_id: subjectId }),
      ...(visit && visit.site_id === null ? { site_id: siteId } : {}),
      host_user_id: host || null, purpose: purpose.trim() || null, valid_from: iso(from), valid_until: iso(until),
      escort_required: escort, escort_user_id: escort && escortWho ? escortWho : null,
      escort_note: escort && escortNote.trim() ? escortNote.trim() : null, place_ids: places,
    }),
    onSuccess: (made) => { onDone(made); onClose() },
  })
  const subjects = kind === 'visit' ? options?.visits ?? [] : options?.permits ?? []
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Ask for an authorisation</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <Alert severity="info">
            The host says yes or no. Without a host, somebody who manages visits does. An authorisation informs the
            gate: it checks nobody in and refuses nobody.
          </Alert>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField select label="Site" value={siteId} sx={{ minWidth: 220 }} slotProps={shrunk}
                       onChange={(e) => { setSiteId(e.target.value); setSubjectId(''); setChosenPlaces([]) }}>
              <MenuItem value="" disabled>Choose a site</MenuItem>
              {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
            <TextField select label="For" value={kind} sx={{ minWidth: 220 }}
                       onChange={(e) => { setKind(e.target.value as SubjectKind); setSubjectId('') }}>
              <MenuItem value="visit">A visit</MenuItem>
              <MenuItem value="work_permit">A contractor's work permit</MenuItem>
            </TextField>
          </Stack>
          {isLoading && <Skeleton height={80} />}
          {!!error && <Alert severity="error">{apiError(error)}</Alert>}
          {options && (
            <>
              {!subjects.length ? (
                <Alert severity="warning">
                  {kind === 'visit' ? 'No visit at this site is expected or on site. Register the visitor first.'
                    : 'No work permit at this site is open. Raise the permit first.'}
                </Alert>
              ) : (
                <TextField select label={kind === 'visit' ? 'Visit' : 'Work permit'} value={subjectId} slotProps={shrunk}
                           onChange={(e) => choose(e.target.value)}>
                  <MenuItem value="" disabled>Choose one</MenuItem>
                  {kind === 'visit'
                    ? options.visits.map((v) => (
                      <MenuItem key={v.id} value={v.id}>{v.company ? `${v.name} (${v.company})` : v.name}</MenuItem>))
                    : options.permits.map((p) => (
                      <MenuItem key={p.id} value={p.id}>
                        {p.name}{p.permit_number ? ` — permit ${p.permit_number}` : ''}: {p.work_description}</MenuItem>))}
                </TextField>)}
              {(visit || permit) && (
                <>
                  <TextField select label="Host — who says yes or no" value={host} slotProps={shrunk}
                             helperText={visit?.host_name && !visit.host_user_id
                               ? `The visit names “${visit.host_name}”, who is not one of the organisation's people on the system.`
                               : 'Left empty, it is answered by somebody who manages visits'}
                             onChange={(e) => setHost(e.target.value)}>
                    <MenuItem value="">Nobody in particular</MenuItem>
                    {options.people.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
                  </TextField>
                  <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
                    <TextField label="Valid from" type="datetime-local" value={from} helperText="Left empty: now"
                               onChange={(e) => setFrom(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
                    <TextField label="Valid until" type="datetime-local" value={until}
                               onChange={(e) => setUntil(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
                  </Stack>
                  <TextField label="Purpose" value={purpose} onChange={(e) => setPurpose(e.target.value)}
                             slotProps={{ htmlInput: { maxLength: 2000 } }} />
                  <PlacePicker places={options.places} chosen={places} onChange={setChosenPlaces} />
                  <FormControlLabel label="To be escorted while on site"
                                    control={<Checkbox checked={escort} onChange={(e) => setEscort(e.target.checked)} />} />
                  {escort && (
                    <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
                      <TextField select label="Escort" value={escortWho} sx={{ minWidth: 240 }} slotProps={shrunk}
                                 onChange={(e) => setEscortWho(e.target.value)}>
                        <MenuItem value="">Named later, at the gate</MenuItem>
                        {options.people.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
                      </TextField>
                      <TextField label="About the escort" value={escortNote} sx={{ flex: 1, minWidth: 220 }}
                                 onChange={(e) => setEscortNote(e.target.value)}
                                 slotProps={{ htmlInput: { maxLength: 500 } }} />
                    </Stack>)}
                </>)}
            </>)}
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" disabled={!subjectId || !until || act.isPending} onClick={() => act.mutate()}>
          Ask for it</Button>
      </DialogActions>
    </Dialog>
  )
}

type Doing = 'decline' | 'cancel' | 'extend' | 'places' | 'escort' | 'id' | null

/** One door event: where, when, what can be said of it, and what a person made of it. */
function MovementRow({ m, canReview, onReview, busy }: {
  m: Movement; canReview: boolean; busy: boolean
  onReview: (m: Movement, outcome: 'IN_ORDER' | 'FOLLOWED_UP', note: string) => void
}) {
  const [following, setFollowing] = useState(false)
  const [note, setNote] = useState('')
  const said = against(m)
  return (
    <Box data-testid="movement" sx={{ py: 1, borderBottom: 1, borderColor: 'divider' }}>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>{door(m)}</Typography>
        <Typography variant="caption" color="text.secondary">{fmt(m.occurred_at)} · {m.event_type} · badge {m.badge}</Typography>
        <Chip size="small" variant={said.tone === 'look' ? 'filled' : 'outlined'}
              color={said.tone === 'look' ? 'warning' : said.tone === 'ok' ? 'success' : 'default'} label={said.text} />
      </Stack>
      {m.review ? (
        <Typography variant="caption" color="text.secondary">
          {REVIEW_LABEL[m.review.outcome]}, by {m.review.reviewed_by_name ?? 'somebody no longer on the system'}
          {m.review.note ? `: ${m.review.note}` : ''}
        </Typography>
      ) : canReview && m.to_look_at && (
        // Only what is there to be looked at is asked about: a door within what was authorised needs nobody's word.
        following ? (
          <Stack direction="row" sx={{ gap: 1, mt: 0.75, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <TextField size="small" label="What was done about it" value={note} autoFocus sx={{ flex: 1, minWidth: 240 }}
                       onChange={(e) => setNote(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
            <Button size="small" onClick={() => setFollowing(false)}>Not yet</Button>
            <Button size="small" variant="contained" disabled={!note.trim() || busy}
                    onClick={() => onReview(m, 'FOLLOWED_UP', note.trim())}>Record it</Button>
          </Stack>
        ) : (
          <Stack direction="row" sx={{ gap: 1, mt: 0.5 }}>
            <Button size="small" disabled={busy} onClick={() => onReview(m, 'IN_ORDER', '')}>It was in order</Button>
            <Button size="small" disabled={busy} onClick={() => setFollowing(true)}>It was followed up</Button>
          </Stack>))}
    </Box>
  )
}

interface OpenProps { id: string | null; onClose: () => void; onChanged: () => Promise<unknown> }

export function AuthorisationDialog(props: OpenProps) {
  return props.id ? <AuthorisationView {...props} id={props.id} /> : null
}

function AuthorisationView({ id, onClose, onChanged }: OpenProps & { id: string }) {
  const [doing, setDoing] = useState<Doing>(null)
  const [reason, setReason] = useState('')
  const [until, setUntil] = useState('')
  const [places, setChosenPlaces] = useState<string[]>([])
  const [escort, setEscortOn] = useState(false)
  const [escortWho, setEscortWho] = useState('')
  const [escortNote, setEscortNote] = useState('')
  const [idKind, setIdKind] = useState('')
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['visitor-auth', id], queryFn: () => getAuthorisation(id) })
  // The site's places and people, asked for only when somebody goes to change them.
  const wants = doing === 'places' || doing === 'escort' || doing === 'id'
  const { data: options } = useQuery({
    queryKey: ['visitor-auth-options', data?.site_id], queryFn: () => getOptions(data!.site_id),
    enabled: wants && !!data })
  const again = () => Promise.all([refetch(), onChanged()])
  const run = useMutation({
    mutationFn: (what: () => Promise<unknown>) => what().then(again),
    onSuccess: () => { setDoing(null); setReason('') },
  })
  const start = (what: Doing, a: AuthorisationDetail) => {
    run.reset()
    setReason('')
    setUntil(local(a.valid_until))
    setChosenPlaces(a.places.map((p) => p.id))
    setEscortOn(a.escort_required)
    setEscortWho(a.escort_user_id ?? '')
    setEscortNote(a.escort_note ?? '')
    setIdKind('')
    setDoing(what)
  }
  const a = data
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>
        {a ? (
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <span>{who(a)}</span>
            <Chip size="small" color={STANDING_COLOUR[a.standing]} label={STANDING_LABEL[a.standing]} />
          </Stack>) : 'Authorisation'}
      </DialogTitle>
      <DialogContent>
        {isLoading && <Skeleton height={200} />}
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {a && (
          <Stack sx={{ gap: 1.5, mt: 0.5 }}>
            <Typography variant="body2" color="text.secondary">
              {a.site_name} · {period(a)}{about(a) ? ` · ${about(a)}` : ''}
            </Typography>
            <Box data-testid="says" sx={boxed}>
              {a.says.map((line) => <Typography key={line} variant="body2">{line}</Typography>)}
            </Box>
            <Typography variant="caption" color="text.secondary">{a.note}</Typography>
            <Typography variant="caption" color="text.secondary">
              Asked for by {a.requested_by_name ?? 'somebody no longer on the system'}, {fmt(a.requested_at)}
              {a.decision_note && a.state === 'APPROVED' ? ` · Approved with: ${a.decision_note}` : ''}
              {a.extended_at ? ` · Extended by ${a.extended_by_name ?? 'somebody no longer on the system'}: ${a.extend_reason}` : ''}
              {!a.is_latest ? ' · A newer authorisation of this visit has been asked for since.' : ''}
            </Typography>

            <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap' }}>
              {a.may.approve && (
                <Button size="small" variant="contained" color="success" disabled={run.isPending}
                        onClick={() => run.mutate(() => approveAuthorisation(a.id))}>Approve</Button>)}
              {a.may.decline && <Button size="small" color="error" onClick={() => start('decline', a)}>Decline</Button>}
              {a.may.extend && <Button size="small" onClick={() => start('extend', a)}>Extend</Button>}
              {a.may.places && <Button size="small" onClick={() => start('places', a)}>Places</Button>}
              {a.may.escort && <Button size="small" onClick={() => start('escort', a)}>Escort</Button>}
              {a.may.id_seen && <Button size="small" onClick={() => start('id', a)}>ID seen</Button>}
              {a.may.cancel && <Button size="small" color="error" onClick={() => start('cancel', a)}>Cancel it</Button>}
            </Stack>

            {(doing === 'decline' || doing === 'cancel') && (
              <Stack sx={boxed}>
                <TextField label={doing === 'decline' ? 'Why it is declined' : 'Why it is cancelled'} value={reason} autoFocus
                           multiline minRows={2} onChange={(e) => setReason(e.target.value)}
                           slotProps={{ htmlInput: { maxLength: 2000 } }} />
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button size="small" onClick={() => setDoing(null)}>Not yet</Button>
                  <Button size="small" variant="contained" color="error" disabled={!reason.trim() || run.isPending}
                          onClick={() => run.mutate(() => (doing === 'decline' ? declineAuthorisation : cancelAuthorisation)(a.id, reason.trim()))}>
                    {doing === 'decline' ? 'Decline it' : 'Cancel the authorisation'}</Button>
                </Stack>
              </Stack>)}
            {doing === 'extend' && (
              <Stack sx={boxed}>
                <TextField label="Valid until" type="datetime-local" value={until}
                           onChange={(e) => setUntil(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
                <TextField label="Why it is extended" value={reason} multiline minRows={2}
                           onChange={(e) => setReason(e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button size="small" onClick={() => setDoing(null)}>Not yet</Button>
                  <Button size="small" variant="contained" disabled={!reason.trim() || !until || run.isPending}
                          onClick={() => run.mutate(() => extendAuthorisation(a.id, iso(until)!, reason.trim()))}>
                    Extend it</Button>
                </Stack>
              </Stack>)}
            {doing === 'places' && (
              <Stack sx={boxed}>
                {!options ? <Skeleton height={60} /> : (
                  <PlacePicker places={options.places} chosen={places} onChange={setChosenPlaces} />)}
                {a.state === 'APPROVED' && (
                  <Typography variant="caption" color="text.secondary">
                    This changes what was approved, and is recorded as yours.</Typography>)}
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button size="small" onClick={() => setDoing(null)}>Not yet</Button>
                  <Button size="small" variant="contained" disabled={!options || run.isPending}
                          onClick={() => run.mutate(() => setPlaces(a.id, places))}>Set the places</Button>
                </Stack>
              </Stack>)}
            {doing === 'escort' && (
              <Stack sx={boxed}>
                <FormControlLabel label="To be escorted while on site"
                                  control={<Checkbox checked={escort} onChange={(e) => setEscortOn(e.target.checked)} />} />
                {escort && (
                  <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
                    <TextField select label="Escort" value={escortWho} sx={{ minWidth: 240 }} slotProps={shrunk}
                               onChange={(e) => setEscortWho(e.target.value)}>
                      <MenuItem value="">Nobody named yet</MenuItem>
                      {(options?.people ?? []).map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
                    </TextField>
                    <TextField label="About the escort" value={escortNote} sx={{ flex: 1, minWidth: 220 }}
                               onChange={(e) => setEscortNote(e.target.value)} slotProps={{ htmlInput: { maxLength: 500 } }} />
                  </Stack>)}
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button size="small" onClick={() => setDoing(null)}>Not yet</Button>
                  <Button size="small" variant="contained" disabled={!options || run.isPending}
                          onClick={() => run.mutate(() => setEscort(a.id, {
                            escort_required: escort, escort_user_id: escort && escortWho ? escortWho : null,
                            escort_note: escort && escortNote.trim() ? escortNote.trim() : null }))}>
                    Record it</Button>
                </Stack>
              </Stack>)}
            {doing === 'id' && (
              <Stack sx={boxed}>
                <TextField select label="The kind of document you saw" value={idKind} slotProps={shrunk}
                           helperText="Only the kind is kept, with your name and the time. Its number is not taken."
                           onChange={(e) => setIdKind(e.target.value)}>
                  <MenuItem value="" disabled>Choose the kind</MenuItem>
                  {(options?.id_kinds ?? []).map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
                </TextField>
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button size="small" onClick={() => setDoing(null)}>Not yet</Button>
                  <Button size="small" variant="contained" disabled={!idKind || run.isPending}
                          onClick={() => run.mutate(() => recordIdSeen(a.id, idKind))}>I saw it</Button>
                </Stack>
              </Stack>)}
            {run.isError && <Alert severity="error">{apiError(run.error)}</Alert>}

            {a.movements && (
              <>
                <Divider />
                <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Where the visitor's badge was used</Typography>
                <Typography variant="caption" color="text.secondary">{a.movements.note}</Typography>
                {!a.movements.available ? <Alert severity="info">{a.movements.why}</Alert>
                  : !a.movements.items.length ? (
                    <Alert severity="info">
                      No door event of badge {a.movements.badges.join(', ')} is recorded while the visitor had it.
                    </Alert>
                  ) : a.movements.items.map((m) => (
                    <MovementRow key={m.access_event_id} m={m} canReview={a.may.review_movements} busy={run.isPending}
                                 onReview={(event, outcome, note) => run.mutate(() => reviewMovement(
                                   a.id, event.access_event_id, { outcome, note: note || null }))} />))}
              </>)}
          </Stack>)}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

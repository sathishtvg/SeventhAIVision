/**
 * Response settings: whether the clocks run, the times they run to, and who
 * else is told when something has still not happened.
 *
 * Three things, each of which only tells people:
 *   the switch    off until somebody turns it on; then it judges the incidents
 *                 opened from that moment, and nothing older
 *   the times     per severity — saved through the endpoint that has always
 *                 held them, which until now had no screen
 *   the policies  "still not acknowledged after ten minutes: tell the supervisors"
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Skeleton,
  Switch, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { SeverityChip } from '@/components/common/SeverityChip'
import { getSites } from '@/api/sites'
import { getUsers } from '@/api/users'
import {
  apiError, changePolicy, getResponseSettings, listEscalations, listPolicies, restorePolicy, retirePolicy, saveTimes,
  switchClocks, writePolicy,
} from '@/api/incidentResponses'
import type { Policy, ResponseSettings as Settings, Severity, SeverityTimes, Trigger } from '@/api/incidentResponses'
import { TRIGGER_LABEL, addressedTo, fmt, policySentence, reached, toldSentence } from '@/components/response/responseFormat'

interface Person { id: string; name: string }

const minutes = (seconds: number | undefined) => (seconds === undefined ? '' : String(+(seconds / 60).toFixed(2)))
const seconds = (text: string) => Math.round(Number(text) * 60)
const aTime = (text: string) => text.trim() !== '' && Number.isFinite(Number(text)) && Number(text) > 0

function TimesRow({ times, people, canManage, onSaved }: {
  times: SeverityTimes; people: Person[]; canManage: boolean; onSaved: () => Promise<unknown>
}) {
  const [ack, setAck] = useState(minutes(times.ack_within_seconds))
  const [arrive, setArrive] = useState(minutes(times.dispatch_within_seconds))
  const [resolve, setResolve] = useState(minutes(times.resolve_within_seconds))
  const [tell, setTell] = useState(times.escalation_user_id ?? '')
  const save = useMutation({
    mutationFn: () => saveTimes(times.severity, {
      ack_within_seconds: seconds(ack), dispatch_within_seconds: seconds(arrive), resolve_within_seconds: seconds(resolve),
      escalation_user_id: tell || null,
    }).then(onSaved),
  })
  const changed = ack !== minutes(times.ack_within_seconds) || arrive !== minutes(times.dispatch_within_seconds)
    || resolve !== minutes(times.resolve_within_seconds) || tell !== (times.escalation_user_id ?? '')
  const whole = aTime(ack) && aTime(arrive) && aTime(resolve)
  // Somebody named who is not in the list the caller may read is still shown by name.
  const named = tell && !people.some((p) => p.id === tell)
    ? [{ id: tell, name: times.escalation_user_name ?? 'The person named' }, ...people] : people
  const field = (label: string, value: string, set: (v: string) => void) => (
    <TextField size="small" type="number" value={value} disabled={!canManage} sx={{ width: 110 }}
               onChange={(e) => set(e.target.value)}
               slotProps={{ htmlInput: { min: 0.5, step: 0.5, 'aria-label': `${label} for ${times.severity}, in minutes` } }} />
  )
  return (
    <TableRow data-testid="times-row">
      <TableCell><SeverityChip severity={times.severity} />{!times.set && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>No times set: never late</Typography>)}
      </TableCell>
      <TableCell>{field('Acknowledge within', ack, setAck)}</TableCell>
      <TableCell>{field('Arrive within', arrive, setArrive)}</TableCell>
      <TableCell>{field('Resolve within', resolve, setResolve)}</TableCell>
      <TableCell>
        <TextField select size="small" value={tell} disabled={!canManage} sx={{ minWidth: 190 }}
                   onChange={(e) => setTell(e.target.value)}
                   slotProps={{ select: { displayEmpty: true },
                                htmlInput: { 'aria-label': `Who is told for ${times.severity}` } }}>
          <MenuItem value="">Nobody in particular</MenuItem>
          {named.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
        </TextField>
      </TableCell>
      <TableCell align="right">
        {canManage && (
          <Button size="small" variant="contained" disabled={!changed || !whole || save.isPending}
                  onClick={() => save.mutate()}>Save</Button>)}
        {save.isError && <Typography variant="caption" color="error" sx={{ display: 'block' }}>{apiError(save.error)}</Typography>}
      </TableCell>
    </TableRow>
  )
}

interface PolicyDialogProps {
  open: boolean; editing: Policy | null; settings: Settings; people: Person[]; onClose: () => void
  onSaved: () => Promise<unknown>
}

function PolicyDialog(props: PolicyDialogProps) {
  return props.open ? <PolicyForm {...props} /> : null
}

function PolicyForm({ editing, settings, people, onClose, onSaved }: PolicyDialogProps) {
  const [name, setName] = useState(editing?.name ?? '')
  const [trigger, setTrigger] = useState<Trigger>(editing?.trigger ?? 'NOT_ACKNOWLEDGED')
  const [after, setAfter] = useState(editing ? minutes(editing.after_seconds) : '10')
  const [siteId, setSiteId] = useState(editing?.site_id ?? '')
  const [severity, setSeverity] = useState<Severity | ''>(editing?.severity ?? '')
  // "role:3" or "user:<id>": one of the two, as the server requires.
  const [whom, setWhom] = useState(editing?.notify_user_id ? `user:${editing.notify_user_id}`
    : editing?.notify_role_id ? `role:${editing.notify_role_id}` : 'role:3')
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const act = useMutation({
    mutationFn: () => {
      const [kind, id] = whom.split(':')
      const shared = {
        name: name.trim(), site_id: siteId || null, severity: severity || null, after_seconds: seconds(after),
        notify_role_id: kind === 'role' ? Number(id) : null, notify_user_id: kind === 'user' ? id : null,
      }
      return (editing ? changePolicy(editing.id, shared) : writePolicy({ ...shared, trigger })).then(onSaved)
    },
    onSuccess: onClose,
  })
  const named = editing?.notify_user_id && !people.some((p) => p.id === editing.notify_user_id)
    ? [{ id: editing.notify_user_id, name: editing.notify_user_name ?? 'The person named' }, ...people] : people
  const valid = name.trim() !== '' && aTime(after) && seconds(after) >= 30 && seconds(after) <= 604800
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{editing ? `Change “${editing.name}”` : 'Write a policy'}</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <Alert severity="info">A policy tells people. It does not send a guard, reassign the incident or change it.</Alert>
          <TextField label="Name" value={name} autoFocus onChange={(e) => setName(e.target.value)}
                     slotProps={{ htmlInput: { maxLength: 120 } }} />
          <TextField select label="When" value={trigger} disabled={!!editing}
                     helperText={editing ? 'What a policy watches does not change. Retire it and write another.' : undefined}
                     onChange={(e) => setTrigger(e.target.value as Trigger)}>
            {settings.triggers.map((t) => <MenuItem key={t} value={t}>{TRIGGER_LABEL[t]}</MenuItem>)}
          </TextField>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField label="After (minutes)" type="number" value={after} sx={{ width: 160 }}
                       helperText={trigger === 'NOT_ARRIVED' ? 'From when the guard was sent' : 'From when it was opened'}
                       onChange={(e) => setAfter(e.target.value)} slotProps={{ htmlInput: { min: 0.5, step: 0.5 } }} />
            <TextField select label="Tell" value={whom} sx={{ flex: 1, minWidth: 220 }} onChange={(e) => setWhom(e.target.value)}>
              {settings.notify_roles.map((r) => <MenuItem key={`role:${r.role_id}`} value={`role:${r.role_id}`}>{r.name}</MenuItem>)}
              {named.map((p) => <MenuItem key={`user:${p.id}`} value={`user:${p.id}`}>{p.name}</MenuItem>)}
            </TextField>
          </Stack>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField select label="At" value={siteId} sx={{ flex: 1, minWidth: 200 }} onChange={(e) => setSiteId(e.target.value)}
                       slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}>
              <MenuItem value="">Every site</MenuItem>
              {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
            <TextField select label="Of severity" value={severity} sx={{ flex: 1, minWidth: 180 }}
                       slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
                       onChange={(e) => setSeverity(e.target.value as Severity | '')}>
              <MenuItem value="">Any severity</MenuItem>
              {settings.severities.map((s) => <MenuItem key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</MenuItem>)}
            </TextField>
          </Stack>
          <Typography variant="caption" color="text.secondary">
            Whoever is told must be able to see the incident's site: somebody held to other sites is not told.
          </Typography>
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!valid || act.isPending} onClick={() => act.mutate()}>
          {editing ? 'Save' : 'Write it'}</Button>
      </DialogActions>
    </Dialog>
  )
}

export default function ResponseSettings() {
  const qc = useQueryClient()
  const [retired, setRetired] = useState(false)
  const [asking, setAsking] = useState<{ editing: Policy | null } | null>(null)
  const { data: settings, isLoading, error } = useQuery({ queryKey: ['response-settings'], queryFn: getResponseSettings })
  const canManage = !!settings?.can_manage
  const { data: policies } = useQuery({
    queryKey: ['response-policies', retired], queryFn: () => listPolicies(retired) })
  const { data: told } = useQuery({ queryKey: ['response-told'], queryFn: () => listEscalations({ hours: 24, limit: 50 }) })
  // Somebody who may not read the list of staff still sets times: they pick a role instead.
  const { data: users } = useQuery({ queryKey: ['users'], queryFn: getUsers, enabled: canManage, retry: false })
  const people: Person[] = (users ?? []).filter((u) => u.is_active && u.role_id !== 1 && u.role_id !== 7)
    .map((u) => ({ id: u.id, name: u.full_name ?? u.email }))
  const again = () => Promise.all([
    qc.invalidateQueries({ queryKey: ['response-settings'] }), qc.invalidateQueries({ queryKey: ['response-policies'] }),
    qc.invalidateQueries({ queryKey: ['response-desk'] })])
  const flip = useMutation({ mutationFn: (on: boolean) => switchClocks(on).then(again) })
  const active = useMutation({
    mutationFn: (p: Policy) => (p.is_active ? retirePolicy(p.id) : restorePolicy(p.id)).then(again) })
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Response Settings"
                  subtitle="Whether the response clocks run, the times they run to, and who else is told when something has still not happened" />
      {!!error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(error)}</Alert>}
      {isLoading && <Skeleton height={240} />}
      {settings && (
        <Stack sx={{ gap: 2 }}>
          <GlassCard sx={{ p: 2 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>The clocks</Typography>
            <FormControlLabel sx={{ mt: 0.5 }} label="Judge incidents against the times below"
                              control={<Switch checked={settings.sla_enabled} disabled={!canManage || flip.isPending}
                                               onChange={(_, on) => flip.mutate(on)} />} />
            <Typography variant="body2" color="text.secondary">
              {settings.sla_enabled
                ? `Switched on ${fmt(settings.sla_since)}. Incidents opened since then are judged; nothing older is marked late.`
                : 'Switched off. Switched on, they judge the incidents opened from that moment: nothing older is marked late.'}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{settings.note}</Typography>
            {flip.isError && <Alert severity="error" sx={{ mt: 1 }}>{apiError(flip.error)}</Alert>}
          </GlassCard>

          <GlassCard sx={{ p: 2 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>The times, by severity</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              In minutes. To acknowledge and to resolve run from when the incident is opened; to arrive runs from when a
              guard is sent. The person named is told when any of the three runs out.
            </Typography>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Severity</TableCell><TableCell>Acknowledge within</TableCell><TableCell>Arrive within</TableCell>
                    <TableCell>Resolve within</TableCell><TableCell>Who is told</TableCell><TableCell />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {settings.times.map((t) => (
                    // Keyed by what is saved, so that a saved row starts again from what the server now holds.
                    <TimesRow key={`${t.severity}:${t.ack_within_seconds}:${t.dispatch_within_seconds}:${t.resolve_within_seconds}:${t.escalation_user_id}`}
                              times={t} people={people} canManage={canManage} onSaved={again} />))}
                </TableBody>
              </Table>
            </TableContainer>
          </GlassCard>

          <GlassCard sx={{ p: 2 }}>
            <Stack direction="row" sx={{ gap: 1.5, alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Who else is told</Typography>
              <Box sx={{ flex: 1 }} />
              {canManage && (
                <FormControlLabel control={<Switch checked={retired} onChange={(_, v) => setRetired(v)} />}
                                  label="Show retired policies" />)}
              {canManage && (
                <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAsking({ editing: null })}>
                  Write a policy</Button>)}
            </Stack>
            {!policies?.items.length ? (
              <Alert severity="info">No policy has been written. With the clocks on, the person named for a severity is
                still told when one of its times runs out.</Alert>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow><TableCell>Policy</TableCell><TableCell>Tells</TableCell><TableCell /><TableCell /></TableRow>
                  </TableHead>
                  <TableBody>
                    {policies.items.map((p) => (
                      <TableRow key={p.id} data-testid="policy-row" sx={{ opacity: p.is_active ? 1 : 0.55 }}>
                        <TableCell>
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>{p.name}</Typography>
                          <Typography variant="caption" color="text.secondary">{policySentence(p)}</Typography>
                        </TableCell>
                        <TableCell>{addressedTo(p, settings.notify_roles)}</TableCell>
                        <TableCell>{!p.is_active && <Chip size="small" variant="outlined" label="Retired" />}</TableCell>
                        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                          {canManage && p.is_active && <Button size="small" onClick={() => setAsking({ editing: p })}>Change</Button>}
                          {canManage && (
                            <Button size="small" color={p.is_active ? 'warning' : 'primary'} disabled={active.isPending}
                                    onClick={() => active.mutate(p)}>{p.is_active ? 'Retire' : 'Restore'}</Button>)}
                        </TableCell>
                      </TableRow>))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
            {active.isError && <Alert severity="error" sx={{ mt: 1.5 }}>{apiError(active.error)}</Alert>}
          </GlassCard>

          <GlassCard sx={{ p: 2 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>What was told, in the last 24 hours</Typography>
            {!told?.items.length ? <Typography variant="body2" color="text.secondary">Nobody has been told anything.</Typography>
              : told.items.map((e) => (
                <Typography key={e.id} variant="body2" data-testid="told-row" sx={{ mb: 0.5 }}>
                  <b>{fmt(e.created_at)}</b> · {toldSentence(e)}{e.site_name ? ` (${e.site_name})` : ''} · {reached(e)}
                  {e.recipients > 0 && !e.notification_sent ? ' · could not be delivered' : ''}
                </Typography>))}
          </GlassCard>
        </Stack>
      )}
      {settings && (
        <PolicyDialog open={!!asking} editing={asking?.editing ?? null} settings={settings} people={people}
                      onClose={() => setAsking(null)} onSaved={again} />)}
    </Box>
  )
}

/**
 * Sent by email (4 November 2026; Phase 4 of the known limits): who a published briefing is emailed to, the
 * operations reports' schedules, and what became of each email. Only the organisation's own people are chosen, by
 * name or by role; the server decides, person by person, who may have what (backend/app/services/ops_delivery.py)
 * and records whoever is left out, with why. Shown to report:schedule (Admin and Manager).
 */
import { useState } from 'react'
import {
  Alert, Autocomplete, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, MenuItem, Skeleton, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { ErrorState, errorText } from '@/components/states'
import {
  changeSchedule, getBriefingEmails, getBriefingLists, getDeliveryOptions, getScheduleEmails, listSchedules,
  makeSchedule, removeSchedule, setBriefingList,
} from '@/api/opsDelivery'
import type { BriefingList, DeliveryOptions, Frequency, OpsEmail, Schedule } from '@/api/opsDelivery'
import { fmt } from './boardFormat'
import { STATUS_COLOUR, STATUS_LABEL, WEEKDAYS, hourWords, whenWords } from './deliveryFormat'

const toWords = (users: { name: string }[], roles: { name: string }[]) =>
  [...roles.map((r) => `everyone who is ${r.name}`), ...users.map((u) => u.name)].join(', ') || 'Nobody'

function PeoplePicker({ options, userIds, roleIds, onChange }: {
  options: DeliveryOptions; userIds: string[]; roleIds: number[]
  onChange: (userIds: string[], roleIds: number[]) => void
}) {
  const chosen = options.people.filter((p) => userIds.includes(p.id))
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>Roles</Typography>
      <Stack direction="row" sx={{ flexWrap: 'wrap', columnGap: 1 }}>
        {options.roles.map((r) => (
          <FormControlLabel key={r.id} label={r.name} control={
            <Checkbox size="small" checked={roleIds.includes(r.id)}
                      onChange={(e) => onChange(userIds, e.target.checked ? [...roleIds, r.id] : roleIds.filter((x) => x !== r.id))} />} />
        ))}
      </Stack>
      <Autocomplete multiple size="small" options={options.people} value={chosen} sx={{ mt: 1 }}
                    getOptionLabel={(p) => `${p.name} (${p.role})`} isOptionEqualToValue={(a, b) => a.id === b.id}
                    onChange={(_, v) => onChange(v.map((p) => p.id), roleIds)}
                    renderInput={(params) => <TextField {...params} label="People, by name" />} />
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
        Only the organisation's own people. Each gets only what they could read on screen; whoever may not is left out
        and the record says why.
      </Typography>
    </Box>
  )
}

/** What became of each email, person by person. */
export function EmailList({ emails, empty }: { emails: OpsEmail[]; empty: string }) {
  if (!emails.length) return <Typography variant="body2" color="text.secondary">{empty}</Typography>
  return (
    <Table size="small" data-testid="email-list">
      <TableBody>
        {emails.map((e) => (
          <TableRow key={e.id} data-testid="email-row">
            <TableCell>{e.name ?? '—'}</TableCell>
            <TableCell><Chip size="small" color={STATUS_COLOUR[e.status]} label={STATUS_LABEL[e.status]} /></TableCell>
            <TableCell>{e.sent_at ? fmt(e.sent_at) : fmt(e.created_at)}</TableCell>
            <TableCell sx={{ color: 'text.secondary' }}>
              {e.last_error ?? (e.rows_sent != null ? `${e.rows_sent} row${e.rows_sent === 1 ? '' : 's'}` : '')}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** A published briefing's emails, in its dialog. */
export function BriefingEmails({ briefingId }: { briefingId: string }) {
  const { data, isLoading, isLoadingError: emailsFailed, error: emailsError, refetch: refetchEmails } = useQuery({
    queryKey: ['briefing-emails', briefingId], queryFn: () => getBriefingEmails(briefingId),
  })
  if (isLoading) return <Skeleton height={40} />
  if (emailsFailed || !data) return <ErrorState compact error={emailsError} onRetry={refetchEmails} />
  return <EmailList emails={data.emails} empty="It was emailed to nobody: no list names anybody for it." />
}

function ListDialog({ list, options, siteId, onClose }: {
  list: BriefingList | null; options: DeliveryOptions; siteId: string | null; onClose: () => void
}) {
  const qc = useQueryClient()
  const [users, setUsers] = useState<string[]>(list?.users.map((u) => u.id) ?? [])
  const [roles, setRoles] = useState<number[]>(list?.roles.map((r) => r.id) ?? [])
  const save = useMutation({
    mutationFn: () => setBriefingList({ site_id: siteId, user_ids: users, role_ids: roles }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['briefing-lists'] }); onClose() },
  })
  const where = siteId ? options.sites.find((s) => s.id === siteId)?.name ?? 'This site' : 'Every briefing'
  return (
    <>
      <DialogTitle>Who the briefing is emailed to: {where}</DialogTitle>
      <DialogContent>
        <PeoplePicker options={options} userIds={users} roleIds={roles} onChange={(u, r) => { setUsers(u); setRoles(r) }} />
        {save.error && <Alert severity="error" sx={{ mt: 2 }}>{errorText(save.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={save.isPending}>Cancel</Button>
        <Button variant="contained" disabled={save.isPending} onClick={() => save.mutate()}>Save</Button>
      </DialogActions>
    </>
  )
}

function BriefingRecipients({ options }: { options: DeliveryOptions }) {
  const [editing, setEditing] = useState<{ list: BriefingList | null; siteId: string | null } | null>(null)
  const [newSite, setNewSite] = useState('')
  const { data, isLoading, isLoadingError: listsFailed, error: listsError, refetch: refetchLists } = useQuery({
    queryKey: ['briefing-lists'], queryFn: getBriefingLists,
  })
  if (isLoading) return <Skeleton height={80} />
  if (listsFailed || !data) return <ErrorState compact error={listsError} onRetry={refetchLists} />
  const every = data.lists.find((l) => l.site_id === null) ?? null
  const bySite = data.lists.filter((l) => l.site_id !== null)
  const free = options.sites.filter((s) => !bySite.some((l) => l.site_id === s.id))
  return (
    <Box data-testid="briefing-lists">
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{data.note}</Typography>
      {options.every_site_allowed && (
        <Stack direction="row" sx={{ alignItems: 'center', gap: 1, py: 0.75 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, minWidth: 160 }}>Every briefing</Typography>
          <Typography variant="body2" sx={{ flex: 1 }}>{every ? toWords(every.users, every.roles) : 'Nobody'}</Typography>
          <Button size="small" onClick={() => setEditing({ list: every, siteId: null })}>Change</Button>
        </Stack>
      )}
      {bySite.map((l) => (
        <Stack key={l.site_id} direction="row" sx={{ alignItems: 'center', gap: 1, py: 0.75 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, minWidth: 160 }}>{l.site_name}</Typography>
          <Typography variant="body2" sx={{ flex: 1 }}>{toWords(l.users, l.roles)}</Typography>
          <Button size="small" onClick={() => setEditing({ list: l, siteId: l.site_id })}>Change</Button>
        </Stack>
      ))}
      {free.length > 0 && (
        <Stack direction="row" sx={{ alignItems: 'center', gap: 1, mt: 1 }}>
          <TextField select size="small" label="A list for a site" value={newSite} sx={{ minWidth: 220 }}
                     onChange={(e) => setNewSite(e.target.value)}>
            {free.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <Button size="small" disabled={!newSite} onClick={() => { setEditing({ list: null, siteId: newSite }); setNewSite('') }}>
            Make it
          </Button>
        </Stack>
      )}
      <Dialog open={!!editing} onClose={() => setEditing(null)} maxWidth="sm" fullWidth>
        {editing && <ListDialog list={editing.list} siteId={editing.siteId} options={options} onClose={() => setEditing(null)} />}
      </Dialog>
    </Box>
  )
}

function ScheduleDialog({ options, schedule, onClose }: { options: DeliveryOptions; schedule: Schedule | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [reportKey, setReportKey] = useState(schedule?.report_key ?? '')
  const [siteId, setSiteId] = useState(schedule ? schedule.site_id ?? '' : options.every_site_allowed ? '' : options.sites[0]?.id ?? '')
  const [frequency, setFrequency] = useState<Frequency>(schedule?.frequency ?? 'DAILY')
  const [hour, setHour] = useState(schedule?.hour ?? 7)
  const [weekday, setWeekday] = useState(schedule?.weekday ?? 0)
  const [monthDay, setMonthDay] = useState(schedule?.month_day ?? 1)
  const [users, setUsers] = useState<string[]>(schedule?.users.map((u) => u.id) ?? [])
  const [roles, setRoles] = useState<number[]>(schedule?.roles.map((r) => r.id) ?? [])
  const timing = { frequency, hour, weekday: frequency === 'WEEKLY' ? weekday : null, month_day: frequency === 'MONTHLY' ? monthDay : null }
  const save = useMutation({
    mutationFn: () => schedule
      ? changeSchedule(schedule.id, { ...timing, user_ids: users, role_ids: roles })
      : makeSchedule({ report_key: reportKey, site_id: siteId || null, ...timing, user_ids: users, role_ids: roles }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['ops-schedules'] }); onClose() },
  })
  const ready = !!reportKey && (users.length > 0 || roles.length > 0) && (siteId !== '' || options.every_site_allowed)
  return (
    <>
      <DialogTitle>{schedule ? `Change: ${schedule.report_title}` : 'A report on a schedule'}</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 1.5, mt: 0.5 }}>
          <TextField select size="small" label="Report" value={reportKey} disabled={!!schedule}
                     onChange={(e) => setReportKey(e.target.value)}>
            {options.reports.map((r) => <MenuItem key={r.key} value={r.key}>{r.title}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Where" value={siteId} disabled={!!schedule}
                     onChange={(e) => setSiteId(e.target.value)} slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}>
            {options.every_site_allowed && <MenuItem value="">Every site each person is shown</MenuItem>}
            {options.sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap' }}>
            <TextField select size="small" label="How often" value={frequency} sx={{ minWidth: 150 }}
                       onChange={(e) => setFrequency(e.target.value as Frequency)}>
              <MenuItem value="DAILY">Every day</MenuItem>
              <MenuItem value="WEEKLY">Every week</MenuItem>
              <MenuItem value="MONTHLY">Every month</MenuItem>
            </TextField>
            {frequency === 'WEEKLY' && (
              <TextField select size="small" label="On" value={weekday} sx={{ minWidth: 150 }}
                         onChange={(e) => setWeekday(Number(e.target.value))}>
                {WEEKDAYS.map((d, n) => <MenuItem key={d} value={n}>{d}</MenuItem>)}
              </TextField>)}
            {frequency === 'MONTHLY' && (
              <TextField select size="small" label="On day" value={monthDay} sx={{ minWidth: 110 }}
                         onChange={(e) => setMonthDay(Number(e.target.value))}>
                {Array.from({ length: 28 }, (_, n) => n + 1).map((d) => <MenuItem key={d} value={d}>{d}</MenuItem>)}
              </TextField>)}
            <TextField select size="small" label="At" value={hour} sx={{ minWidth: 110 }}
                       onChange={(e) => setHour(Number(e.target.value))}>
              {Array.from({ length: 24 }, (_, h) => h).map((h) => <MenuItem key={h} value={h}>{hourWords(h)}</MenuItem>)}
            </TextField>
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {whenWords(timing)}, where the organisation is. As CSV, as it is downloaded, and as a PDF.
          </Typography>
          <PeoplePicker options={options} userIds={users} roleIds={roles} onChange={(u, r) => { setUsers(u); setRoles(r) }} />
        </Stack>
        {save.error && <Alert severity="error" sx={{ mt: 2 }}>{errorText(save.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={save.isPending}>Cancel</Button>
        <Button variant="contained" disabled={!ready || save.isPending} onClick={() => save.mutate()}>Save</Button>
      </DialogActions>
    </>
  )
}

function ScheduleEmails({ schedule, onClose }: { schedule: Schedule; onClose: () => void }) {
  const { data, isLoading, isLoadingError: sentFailed, error: sentError, refetch: refetchSent } = useQuery({
    queryKey: ['ops-schedule-emails', schedule.id], queryFn: () => getScheduleEmails(schedule.id),
  })
  return (
    <>
      <DialogTitle>What was sent: {schedule.report_title}</DialogTitle>
      <DialogContent>
        {isLoading ? <Skeleton height={80} /> : sentFailed || !data
          ? <ErrorState compact error={sentError} onRetry={refetchSent} />
          : <EmailList emails={data.emails} empty="Nothing has been sent yet." />}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </>
  )
}

function Schedules({ options }: { options: DeliveryOptions }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Schedule | 'new' | null>(null)
  const [sent, setSent] = useState<Schedule | null>(null)
  const [removing, setRemoving] = useState<Schedule | null>(null)
  const { data, isLoading, isLoadingError: schedulesFailed, error: schedulesError, refetch: refetchSchedules } = useQuery({
    queryKey: ['ops-schedules'], queryFn: listSchedules,
  })
  const again = () => qc.invalidateQueries({ queryKey: ['ops-schedules'] })
  const pause = useMutation({ mutationFn: (s: Schedule) => changeSchedule(s.id, { is_active: !s.is_active }), onSuccess: again })
  const remove = useMutation({ mutationFn: (s: Schedule) => removeSchedule(s.id), onSuccess: () => { setRemoving(null); again() } })
  if (isLoading) return <Skeleton height={120} />
  if (schedulesFailed || !data) return <ErrorState compact error={schedulesError} onRetry={refetchSchedules} />
  return (
    <Box data-testid="ops-schedules">
      <Stack direction="row" sx={{ alignItems: 'center', mb: 1, gap: 1 }}>
        <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>{data.note}</Typography>
        <Button size="small" variant="contained" startIcon={<AddIcon />} onClick={() => setEditing('new')}>A schedule</Button>
      </Stack>
      {!data.schedules.length ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 2 }}>No report is on a schedule.</Typography>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Report</TableCell><TableCell>When</TableCell><TableCell>To</TableCell>
                <TableCell>Next</TableCell><TableCell>Sent</TableCell><TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {data.schedules.map((s) => (
                <TableRow key={s.id} data-testid="schedule-row">
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{s.report_title}</Typography>
                    <Typography variant="caption" color="text.secondary">{s.site_name ?? 'Every site each person is shown'}</Typography>
                  </TableCell>
                  <TableCell>{whenWords(s)}</TableCell>
                  <TableCell>{toWords(s.users, s.roles)}</TableCell>
                  <TableCell>{s.is_active ? fmt(s.next_run_at) : <Chip size="small" label="Paused" />}</TableCell>
                  <TableCell>
                    {`${s.sent} sent`}{s.failed ? `, ${s.failed} not sent yet` : ''}{s.left_out ? `, ${s.left_out} left out` : ''}
                  </TableCell>
                  <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                    <Button size="small" onClick={() => setSent(s)}>What was sent</Button>
                    <Button size="small" onClick={() => setEditing(s)}>Change</Button>
                    <Button size="small" disabled={pause.isPending} onClick={() => pause.mutate(s)}>{s.is_active ? 'Pause' : 'Start again'}</Button>
                    <Button size="small" color="error" onClick={() => setRemoving(s)}>Take away</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {pause.error && <Alert severity="error" sx={{ mt: 1 }}>{errorText(pause.error)}</Alert>}
      <Dialog open={!!editing} onClose={() => setEditing(null)} maxWidth="sm" fullWidth>
        {editing && <ScheduleDialog options={options} schedule={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />}
      </Dialog>
      <Dialog open={!!sent} onClose={() => setSent(null)} maxWidth="md" fullWidth>
        {sent && <ScheduleEmails schedule={sent} onClose={() => setSent(null)} />}
      </Dialog>
      <Dialog open={!!removing} onClose={() => setRemoving(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Take this schedule away?</DialogTitle>
        <DialogContent>
          <Typography variant="body2">Nothing more is sent. What it sent is kept, with who took it away and when.</Typography>
          {remove.error && <Alert severity="error" sx={{ mt: 2 }}>{errorText(remove.error)}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRemoving(null)} disabled={remove.isPending}>Cancel</Button>
          <Button color="error" variant="contained" disabled={remove.isPending} onClick={() => removing && remove.mutate(removing)}>Take it away</Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

/** The "Sent by email" tab: the briefing's lists, and the reports' schedules. */
export function DeliveryTab() {
  const { data, isLoading, isLoadingError: optionsFailed, error: optionsError, refetch: refetchOptions } = useQuery({
    queryKey: ['ops-delivery-options'], queryFn: getDeliveryOptions,
  })
  if (isLoading) return <Skeleton height={200} />
  if (optionsFailed || !data) return <ErrorState compact error={optionsError} onRetry={refetchOptions} />
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 0.5 }}>The daily briefing, when it is published</Typography>
        <BriefingRecipients options={data} />
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 0.5 }}>Reports on a schedule</Typography>
        <Schedules options={data} />
      </GlassCard>
    </>
  )
}

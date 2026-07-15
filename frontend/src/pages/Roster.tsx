/**
 * Roster (Gap 86) — recurring shift patterns per guard × site × weekdays,
 * one-click generation of the next week's shifts, and a coverage grid.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, IconButton, InputLabel, MenuItem, Select, Stack, Switch, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography, Paper,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import DeleteIcon from '@mui/icons-material/Delete'
import EventRepeatIcon from '@mui/icons-material/EventRepeat'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  autoSchedule, createLeaveBlock, createShiftPattern, deleteLeaveBlock, deleteShiftPattern,
  discardBatch, generateRoster, getBatch, getLeaveBlocks, getPreferences, getRosterCoverage,
  listShiftPatterns, publishBatch, setPreferences, updateDraftShift, updateShift, updateShiftPattern,
  type CoverageShift, type DraftShift, type RosterBatch,
} from '@/api/roster'
import { getSites } from '@/api/sites'
import { getUsers } from '@/api/users'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { usePermission } from '@/hooks/usePermission'
import { fadeUpSx } from '@/lib/motion'

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
// Manual-assignment guard pickers include Manager(8) — an admin can put a
// Manager on a shift for oversight even though auto-schedule won't do it on
// its own (Manager isn't in the backend's auto-fill candidate pool).
const GUARD_ROLES = new Set([3, 4, 5, 8])
const WARNING_LABELS: Record<string, string> = {
  unfilled_slot: 'Unfilled',
  below_min_coverage: 'Below Min Coverage',
  no_supervisor_present: 'No Supervisor',
  no_manager_present: 'No Manager',
}

function toDatetimeLocal(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

interface EditableShift {
  id: string
  guard_user_id: string | null
  scheduled_start: string
  scheduled_end: string
  site_name?: string | null
}

function PatternDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState('')
  const [guardId, setGuardId] = useState('')
  const [days, setDays] = useState<number[]>([0, 1, 2, 3, 4])
  const [startTime, setStartTime] = useState('08:00')
  const [durationH, setDurationH] = useState(8)
  const [label, setLabel] = useState('')

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(), enabled: open })
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: getUsers, enabled: open })
  const guards = users.filter((u) => GUARD_ROLES.has(u.role_id) && u.is_active)

  const { mutate: save, isPending } = useMutation({
    mutationFn: () => createShiftPattern({
      site_id: siteId, guard_user_id: guardId, days_of_week: days,
      start_time: startTime, duration_minutes: Math.round(durationH * 60),
      label: label || undefined,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['shift-patterns'] }); onClose() },
  })

  const toggleDay = (d: number) =>
    setDays((prev) => prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort())

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>New Roster Pattern</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1 }}>
        <TextField label="Label (optional)" size="small" value={label}
                   onChange={(e) => setLabel(e.target.value)} placeholder="Day Shift" />
        <Select size="small" displayEmpty value={siteId}
                onChange={(e) => setSiteId(e.target.value)}
                renderValue={(v) => sites.find((s: any) => s.id === v)?.name ?? 'Site…'}>
          {sites.map((s: any) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
        </Select>
        <Select size="small" displayEmpty value={guardId}
                onChange={(e) => setGuardId(e.target.value)}
                renderValue={(v) => {
                  const g = guards.find((u) => u.id === v)
                  return g ? (g.full_name ?? g.email) : 'Guard…'
                }}>
          {guards.map((g) => (
            <MenuItem key={g.id} value={g.id}>{g.full_name ?? g.email}</MenuItem>
          ))}
        </Select>
        <Box>
          <Typography variant="caption" color="text.secondary">Days of week</Typography>
          <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
            {DAY_LABELS.map((lbl, i) => (
              <Chip key={lbl} label={lbl} size="small" clickable
                    color={days.includes(i) ? 'primary' : 'default'}
                    variant={days.includes(i) ? 'filled' : 'outlined'}
                    onClick={() => toggleDay(i)} />
            ))}
          </Stack>
        </Box>
        <Stack direction="row" spacing={1.5}>
          <TextField label="Start time" type="time" size="small" value={startTime}
                     onChange={(e) => setStartTime(e.target.value)} sx={{ flex: 1 }} />
          <TextField label="Hours" type="number" size="small" value={durationH}
                     onChange={(e) => setDurationH(Number(e.target.value))}
                     inputProps={{ min: 1, max: 24 }} sx={{ width: 90 }} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>Cancel</Button>
        <Button variant="contained" onClick={() => save()}
                disabled={isPending || !siteId || !guardId || days.length === 0}>
          Create
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function AutoScheduleDialog({ open, onClose, onGenerated }: {
  open: boolean; onClose: () => void; onGenerated: (batch: RosterBatch) => void
}) {
  const [siteId, setSiteId] = useState('')
  const [periodStart, setPeriodStart] = useState(() => new Date().toISOString().slice(0, 10))
  const [periodEnd, setPeriodEnd] = useState(() => {
    const d = new Date(); d.setDate(d.getDate() + 29); return d.toISOString().slice(0, 10)
  })

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(), enabled: open })

  const { mutate: run, isPending, isError } = useMutation({
    mutationFn: () => autoSchedule({ site_id: siteId || undefined, period_start: periodStart, period_end: periodEnd }),
    onSuccess: (batch) => { onGenerated(batch); onClose() },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>AI Auto-Schedule</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1 }}>
        <Typography variant="caption" color="text.secondary">
          Generates a draft roster from your active patterns, respecting rest hours,
          consecutive-day limits, leave, preferences, coverage minimums, and supervisor
          presence. Review and publish before it takes effect.
        </Typography>
        <Select size="small" displayEmpty value={siteId} onChange={(e) => setSiteId(e.target.value)}
                renderValue={(v) => sites.find((s: any) => s.id === v)?.name ?? 'All Sites'}>
          <MenuItem value="">All Sites</MenuItem>
          {sites.map((s: any) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
        </Select>
        <Stack direction="row" spacing={1.5}>
          <TextField label="From" type="date" size="small" value={periodStart}
                     onChange={(e) => setPeriodStart(e.target.value)} InputLabelProps={{ shrink: true }} sx={{ flex: 1 }} />
          <TextField label="To" type="date" size="small" value={periodEnd}
                     onChange={(e) => setPeriodEnd(e.target.value)} InputLabelProps={{ shrink: true }} sx={{ flex: 1 }} />
        </Stack>
        {isError && (
          <Typography variant="caption" color="error">Failed to generate — check the period and try again.</Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>Cancel</Button>
        <Button variant="contained" onClick={() => run()} disabled={isPending || !periodStart || !periodEnd}>
          {isPending ? 'Generating…' : 'Generate Draft'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function DraftReviewPanel({ batchId, onResolved }: { batchId: string; onResolved: () => void }) {
  const qc = useQueryClient()
  const { data: batch, isLoading } = useQuery({ queryKey: ['roster-batch', batchId], queryFn: () => getBatch(batchId) })
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: getUsers })
  const guards = users.filter((u) => GUARD_ROLES.has(u.role_id) && u.is_active)

  const { mutate: reassign } = useMutation({
    mutationFn: ({ id, guardUserId }: { id: string; guardUserId: string }) => updateDraftShift(id, { guard_user_id: guardUserId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['roster-batch', batchId] }),
  })
  const { mutate: publish, isPending: publishing } = useMutation({
    mutationFn: () => publishBatch(batchId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['roster-coverage'] }); onResolved() },
  })
  const { mutate: discard, isPending: discarding } = useMutation({
    mutationFn: () => discardBatch(batchId),
    onSuccess: () => onResolved(),
  })

  const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  const fmtDate = (iso: string) => new Date(iso).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })

  const byDay = useMemo(() => {
    const map = new Map<string, DraftShift[]>()
    for (const s of batch?.draft_shifts ?? []) {
      const day = new Date(s.scheduled_start).toDateString()
      if (!map.has(day)) map.set(day, [])
      map.get(day)!.push(s)
    }
    return map
  }, [batch])

  if (isLoading || !batch) {
    return (
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Typography variant="body2" color="text.secondary">Loading draft…</Typography>
      </GlassCard>
    )
  }

  const summary = batch.rules_summary
  const hasWarnings = !!summary && (
    summary.unfilled_slots + summary.coverage_shortfalls +
    summary.missing_supervisor_days + summary.missing_manager_days > 0
  )

  return (
    <GlassCard sx={{ p: 2, mb: 2, ...fadeUpSx(0) }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="subtitle2" fontWeight={700}>
          Draft Roster — {new Date(batch.period_start).toLocaleDateString()} to {new Date(batch.period_end).toLocaleDateString()}
        </Typography>
        <Chip label={batch.status} size="small" color={batch.status === 'draft' ? 'warning' : 'success'} />
      </Stack>
      {hasWarnings && (
        <Typography variant="caption" color="warning.main" sx={{ display: 'block', mb: 1.5 }}>
          {summary!.unfilled_slots > 0 && `${summary!.unfilled_slots} unfilled slot(s). `}
          {summary!.coverage_shortfalls > 0 && `${summary!.coverage_shortfalls} coverage shortfall(s). `}
          {summary!.missing_supervisor_days > 0 && `${summary!.missing_supervisor_days} day(s) missing a supervisor. `}
          {summary!.missing_manager_days > 0 && `${summary!.missing_manager_days} day(s) missing a manager.`}
        </Typography>
      )}
      <Stack spacing={1.5} sx={{ maxHeight: 420, overflow: 'auto' }}>
        {Array.from(byDay.entries()).map(([day, shifts]) => (
          <Box key={day}>
            <Typography variant="caption" fontWeight={700} color="text.secondary">
              {fmtDate(shifts[0].scheduled_start)}
            </Typography>
            <Stack spacing={0.5} sx={{ mt: 0.5 }}>
              {shifts.map((s) => (
                <Box key={s.id} sx={{
                  display: 'flex', alignItems: 'center', gap: 1, p: 1, borderRadius: '8px',
                  backgroundColor: 'rgba(255,255,255,0.04)', flexWrap: 'wrap',
                }}>
                  <Typography variant="caption" sx={{ width: 190, flexShrink: 0 }}>
                    {s.site_name} · {fmtTime(s.scheduled_start)}–{fmtTime(s.scheduled_end)}
                  </Typography>
                  <Chip label={s.shift_type} size="small" variant="outlined" sx={{ height: 18, fontSize: '0.6rem' }} />
                  <Select size="small" value={s.guard_user_id ?? ''} displayEmpty
                          disabled={batch.status !== 'draft'}
                          onChange={(e) => reassign({ id: s.id, guardUserId: e.target.value })}
                          sx={{ flex: 1, minWidth: 140, height: 28, fontSize: '0.75rem' }}>
                    <MenuItem value="" disabled><em>Unfilled</em></MenuItem>
                    {guards.map((g) => <MenuItem key={g.id} value={g.id}>{g.full_name ?? g.email}</MenuItem>)}
                  </Select>
                  {s.warnings.map((w) => (
                    <Chip key={w} label={WARNING_LABELS[w] ?? w} size="small" color="warning"
                          sx={{ height: 18, fontSize: '0.6rem' }} />
                  ))}
                </Box>
              ))}
            </Stack>
          </Box>
        ))}
      </Stack>
      {batch.status === 'draft' && (
        <Stack direction="row" spacing={1} sx={{ mt: 2, justifyContent: 'flex-end' }}>
          <Button size="small" color="error" variant="outlined" disabled={discarding} onClick={() => discard()}>
            Discard
          </Button>
          <Button size="small" color="success" variant="contained" disabled={publishing} onClick={() => publish()}>
            {publishing ? 'Publishing…' : 'Publish & Notify'}
          </Button>
        </Stack>
      )}
    </GlassCard>
  )
}

function EditShiftDialog({ shift, extraNote, onClose }: {
  shift: EditableShift | null
  extraNote?: string
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [guardId, setGuardId] = useState('')
  const [startLocal, setStartLocal] = useState('')
  const [endLocal, setEndLocal] = useState('')

  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: getUsers, enabled: !!shift })
  const guards = users.filter((u) => GUARD_ROLES.has(u.role_id) && u.is_active)

  useEffect(() => {
    if (shift) {
      setGuardId(shift.guard_user_id ?? '')
      setStartLocal(toDatetimeLocal(shift.scheduled_start))
      setEndLocal(toDatetimeLocal(shift.scheduled_end))
    }
  }, [shift])

  const { mutate: save, isPending } = useMutation({
    mutationFn: () => updateShift(shift!.id, {
      guard_user_id: guardId || undefined,
      scheduled_start: new Date(startLocal).toISOString(),
      scheduled_end: new Date(endLocal).toISOString(),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['roster-coverage'] })
      onClose()
    },
  })

  return (
    <Dialog open={!!shift} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Edit Shift{shift?.site_name ? ` — ${shift.site_name}` : ''}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1 }}>
        {extraNote && (
          <Typography variant="caption" color="warning.main">{extraNote}</Typography>
        )}
        <FormControl size="small" fullWidth>
          <InputLabel>Guard</InputLabel>
          <Select label="Guard" value={guardId} onChange={(e) => setGuardId(e.target.value)}>
            <MenuItem value=""><em>Unassigned</em></MenuItem>
            {guards.map((g) => <MenuItem key={g.id} value={g.id}>{g.full_name ?? g.email}</MenuItem>)}
          </Select>
        </FormControl>
        <TextField label="Start" type="datetime-local" size="small" value={startLocal}
                   onChange={(e) => setStartLocal(e.target.value)} InputLabelProps={{ shrink: true }} />
        <TextField label="End" type="datetime-local" size="small" value={endLocal}
                   onChange={(e) => setEndLocal(e.target.value)} InputLabelProps={{ shrink: true }} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>Cancel</Button>
        <Button variant="contained" onClick={() => save()} disabled={isPending || !startLocal || !endLocal}>
          {isPending ? 'Saving…' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function LeavePreferencesCard({ onLeaveCreated }: {
  onLeaveCreated: (shift: EditableShift, extraNote: string) => void
}) {
  const qc = useQueryClient()
  const [guardId, setGuardId] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [reason, setReason] = useState('')

  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: getUsers })
  const guards = users.filter((u) => GUARD_ROLES.has(u.role_id) && u.is_active)
  const { data: leaveBlocks = [] } = useQuery({ queryKey: ['leave-blocks'], queryFn: () => getLeaveBlocks() })

  const { mutate: addLeave, isPending } = useMutation({
    mutationFn: () => createLeaveBlock({ guard_user_id: guardId, start_date: startDate, end_date: endDate, reason: reason || undefined }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ['leave-blocks'] })
      setStartDate(''); setEndDate(''); setReason('')
      if (created.affected_shifts.length > 0) {
        const guardName = guards.find((g) => g.id === guardId)?.full_name ?? 'This guard'
        const first = created.affected_shifts[0]
        const more = created.affected_shifts.length - 1
        onLeaveCreated(
          { id: first.id, guard_user_id: null, scheduled_start: first.scheduled_start, scheduled_end: first.scheduled_end, site_name: first.site_name },
          `${guardName} is now on leave for this published shift — reassign it.` +
            (more > 0 ? ` ${more} more shift${more === 1 ? '' : 's'} also affected.` : ''),
        )
      }
    },
  })
  const { mutate: removeLeave } = useMutation({
    mutationFn: (id: string) => deleteLeaveBlock(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['leave-blocks'] }),
  })

  return (
    <GlassCard sx={{ p: 2 }}>
      <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>Guard Leave</Typography>
      <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: 'wrap' }}>
        <Select size="small" displayEmpty value={guardId} onChange={(e) => setGuardId(e.target.value)}
                renderValue={(v) => guards.find((g) => g.id === v)?.full_name ?? 'Guard…'} sx={{ minWidth: 160 }}>
          {guards.map((g) => <MenuItem key={g.id} value={g.id}>{g.full_name ?? g.email}</MenuItem>)}
        </Select>
        <TextField label="From" type="date" size="small" value={startDate}
                   onChange={(e) => setStartDate(e.target.value)} InputLabelProps={{ shrink: true }} />
        <TextField label="To" type="date" size="small" value={endDate}
                   onChange={(e) => setEndDate(e.target.value)} InputLabelProps={{ shrink: true }} />
        <TextField label="Reason (optional)" size="small" value={reason}
                   onChange={(e) => setReason(e.target.value)} sx={{ flex: 1, minWidth: 140 }} />
        <Button size="small" variant="contained" disabled={isPending || !guardId || !startDate || !endDate}
                onClick={() => addLeave()}>
          Add
        </Button>
      </Stack>
      {leaveBlocks.length === 0 ? (
        <Typography variant="body2" color="text.secondary">No leave on record.</Typography>
      ) : (
        <Stack spacing={0.5}>
          {leaveBlocks.map((lb) => (
            <Box key={lb.id} sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', py: 0.5 }}>
              <Typography variant="body2">
                {lb.guard_name} · {new Date(lb.start_date).toLocaleDateString()} – {new Date(lb.end_date).toLocaleDateString()}
                {lb.reason && ` · ${lb.reason}`}
              </Typography>
              <IconButton size="small" color="error" onClick={() => removeLeave(lb.id)}>
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Box>
          ))}
        </Stack>
      )}
    </GlassCard>
  )
}

function PreferencesEditor() {
  const qc = useQueryClient()
  const [guardId, setGuardId] = useState('')
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: getUsers })
  const guards = users.filter((u) => GUARD_ROLES.has(u.role_id) && u.is_active)
  const { data: prefs } = useQuery({
    queryKey: ['guard-preferences', guardId],
    queryFn: () => getPreferences(guardId),
    enabled: !!guardId,
  })

  const { mutate: save } = useMutation({
    mutationFn: (data: { preferred_shift_type?: string | null; preferred_off_days?: number[] | null }) => setPreferences(guardId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['guard-preferences', guardId] }),
  })

  const toggleOffDay = (d: number) => {
    const current = prefs?.preferred_off_days ?? []
    const next = current.includes(d) ? current.filter((x) => x !== d) : [...current, d].sort()
    save({ preferred_off_days: next })
  }

  return (
    <GlassCard sx={{ p: 2, mt: 2 }}>
      <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>Guard Preferences</Typography>
      <Select size="small" displayEmpty value={guardId} onChange={(e) => setGuardId(e.target.value)}
              renderValue={(v) => guards.find((g) => g.id === v)?.full_name ?? 'Select a guard…'}
              sx={{ minWidth: 200, mb: 1.5 }}>
        {guards.map((g) => <MenuItem key={g.id} value={g.id}>{g.full_name ?? g.email}</MenuItem>)}
      </Select>
      {guardId && (
        <Stack spacing={1.5}>
          <FormControl size="small" sx={{ maxWidth: 220 }}>
            <InputLabel>Preferred Shift Type</InputLabel>
            <Select label="Preferred Shift Type" value={prefs?.preferred_shift_type ?? ''}
                    onChange={(e) => save({ preferred_shift_type: e.target.value || null })}>
              <MenuItem value="">No preference</MenuItem>
              <MenuItem value="day">Day</MenuItem>
              <MenuItem value="night">Night</MenuItem>
            </Select>
          </FormControl>
          <Box>
            <Typography variant="caption" color="text.secondary">Preferred off days</Typography>
            <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
              {DAY_LABELS.map((lbl, i) => (
                <Chip key={lbl} label={lbl} size="small" clickable
                      color={(prefs?.preferred_off_days ?? []).includes(i) ? 'primary' : 'default'}
                      variant={(prefs?.preferred_off_days ?? []).includes(i) ? 'filled' : 'outlined'}
                      onClick={() => toggleOffDay(i)} />
              ))}
            </Stack>
          </Box>
        </Stack>
      )}
    </GlassCard>
  )
}

export function RosterPage() {
  const qc = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [genResult, setGenResult] = useState<number | null>(null)
  const [viewMode, setViewMode] = useState<'site' | 'employee'>('site')
  const [autoScheduleOpen, setAutoScheduleOpen] = useState(false)
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null)
  const [editShift, setEditShift] = useState<EditableShift | null>(null)
  const [editExtraNote, setEditExtraNote] = useState<string | undefined>(undefined)

  const openEditShift = (shift: EditableShift, note?: string) => {
    setEditShift(shift)
    setEditExtraNote(note)
  }
  const canEditShift = usePermission('shift:manage')

  const { data: patterns = [] } = useQuery({ queryKey: ['shift-patterns'], queryFn: listShiftPatterns })
  const { data: coverage } = useQuery({
    queryKey: ['roster-coverage'],
    queryFn: () => getRosterCoverage(7),
  })

  const { mutate: generate, isPending: generating } = useMutation({
    mutationFn: () => generateRoster(7),
    onSuccess: (res) => {
      setGenResult(res.created)
      qc.invalidateQueries({ queryKey: ['roster-coverage'] })
    },
  })

  const { mutate: toggleActive } = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      updateShiftPattern(id, { is_active: active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['shift-patterns'] }),
  })

  const { mutate: remove } = useMutation({
    mutationFn: (id: string) => deleteShiftPattern(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['shift-patterns'] }),
  })

  // Coverage grid: next 7 dates × sites
  const nextDates = useMemo(() => Array.from({ length: 7 }).map((_, i) => {
    const d = new Date()
    d.setDate(d.getDate() + i)
    return d
  }), [])

  const bySiteAndDay = useMemo(() => {
    const map = new Map<string, Map<string, CoverageShift[]>>()
    for (const sh of coverage?.shifts ?? []) {
      const site = sh.site_name ?? 'No site'
      const day = new Date(sh.scheduled_start).toDateString()
      if (!map.has(site)) map.set(site, new Map())
      const dayMap = map.get(site)!
      if (!dayMap.has(day)) dayMap.set(day, [])
      dayMap.get(day)!.push(sh)
    }
    return map
  }, [coverage])

  const byGuardAndDay = useMemo(() => {
    const map = new Map<string, Map<string, CoverageShift[]>>()
    for (const sh of coverage?.shifts ?? []) {
      const guard = sh.guard_name ?? 'Unassigned'
      const day = new Date(sh.scheduled_start).toDateString()
      if (!map.has(guard)) map.set(guard, new Map())
      const dayMap = map.get(guard)!
      if (!dayMap.has(day)) dayMap.set(day, [])
      dayMap.get(day)!.push(sh)
    }
    return map
  }, [coverage])

  const activeGrid = viewMode === 'site' ? bySiteAndDay : byGuardAndDay

  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })

  return (
    <Box>
      <PageHeader title="Roster" subtitle="Recurring shift patterns and weekly coverage" />

      {activeBatchId && (
        <DraftReviewPanel batchId={activeBatchId} onResolved={() => setActiveBatchId(null)} />
      )}

      <Stack direction="row" spacing={1.5} sx={{ mb: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        <PermissionGuard permission="shift:manage">
          <Button variant="contained" size="small" startIcon={<AddIcon />}
                  onClick={() => setDialogOpen(true)}>
            Add Pattern
          </Button>
          <Button variant="outlined" size="small" startIcon={<EventRepeatIcon />}
                  disabled={generating} onClick={() => generate()}>
            Generate next 7 days
          </Button>
        </PermissionGuard>
        <PermissionGuard permission="roster:autoschedule">
          <Button variant="outlined" size="small" color="secondary" startIcon={<AutoAwesomeIcon />}
                  onClick={() => setAutoScheduleOpen(true)}>
            Auto-Schedule
          </Button>
        </PermissionGuard>
        {genResult !== null && (
          <Typography variant="caption" color="text.secondary">
            {genResult} shift{genResult === 1 ? '' : 's'} created
          </Typography>
        )}
      </Stack>

      {/* Patterns table */}
      <GlassCard sx={{ mb: 2 }}>
        <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Label</TableCell>
                <TableCell>Site</TableCell>
                <TableCell>Guard</TableCell>
                <TableCell>Days</TableCell>
                <TableCell>Time</TableCell>
                <TableCell>Active</TableCell>
                <TableCell align="right" />
              </TableRow>
            </TableHead>
            <TableBody>
              {patterns.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7}>
                    <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
                      No roster patterns yet — add one, then generate the week.
                    </Typography>
                  </TableCell>
                </TableRow>
              ) : patterns.map((p) => (
                <TableRow key={p.id} hover>
                  <TableCell>{p.label ?? '—'}</TableCell>
                  <TableCell>{p.site_name}</TableCell>
                  <TableCell>{p.guard_name}</TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={0.25}>
                      {p.days_of_week.map((d) => (
                        <Chip key={d} label={DAY_LABELS[d]} size="small"
                              sx={{ height: 16, fontSize: '0.6rem' }} />
                      ))}
                    </Stack>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                      {p.start_time} +{Math.round(p.duration_minutes / 60)}h
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <PermissionGuard permission="shift:manage"
                                     fallback={<Chip label={p.is_active ? 'On' : 'Off'} size="small" />}>
                      <Switch size="small" checked={p.is_active}
                              onChange={(e) => toggleActive({ id: p.id, active: e.target.checked })} />
                    </PermissionGuard>
                  </TableCell>
                  <TableCell align="right">
                    <PermissionGuard permission="shift:manage">
                      <Tooltip title="Delete pattern">
                        <IconButton size="small" color="error" onClick={() => remove(p.id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </PermissionGuard>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </GlassCard>

      {/* Coverage grid */}
      <GlassCard sx={{ mb: 2 }}>
        <Box sx={{ p: 1.5 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="subtitle2" fontWeight={700}>
              Coverage — next 7 days
            </Typography>
            <ToggleButtonGroup size="small" value={viewMode} exclusive
                                onChange={(_, v) => v && setViewMode(v)}>
              <ToggleButton value="site">By Site</ToggleButton>
              <ToggleButton value="employee">By Employee</ToggleButton>
            </ToggleButtonGroup>
          </Stack>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{viewMode === 'site' ? 'Site' : 'Employee'}</TableCell>
                  {nextDates.map((d) => (
                    <TableCell key={d.toDateString()} align="center">
                      <Typography variant="caption" fontWeight={700}>
                        {DAY_LABELS[(d.getDay() + 6) % 7]} {d.getDate()}
                      </Typography>
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {activeGrid.size === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8}>
                      <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
                        No upcoming shifts — generate the roster to populate coverage.
                      </Typography>
                    </TableCell>
                  </TableRow>
                ) : Array.from(activeGrid.entries()).map(([rowKey, dayMap]) => (
                  <TableRow key={rowKey} hover>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600}>{rowKey}</Typography>
                    </TableCell>
                    {nextDates.map((d) => {
                      const shifts = dayMap.get(d.toDateString()) ?? []
                      return (
                        <TableCell key={d.toDateString()} align="center" sx={{ px: 0.5 }}>
                          {shifts.length === 0 ? (
                            <Typography variant="caption" color="error.main">—</Typography>
                          ) : shifts.map((sh) => (
                            <Tooltip key={sh.id}
                                     title={`${viewMode === 'site' ? sh.guard_name ?? 'Guard' : sh.site_name ?? 'No site'} · ${fmtTime(sh.scheduled_start)}–${fmtTime(sh.scheduled_end)} · ${sh.status}${canEditShift ? ' · click to edit' : ''}`}>
                              <Chip
                                label={`${(viewMode === 'site' ? sh.guard_name : sh.site_name)?.split(' ')[0] ?? '?'} ${fmtTime(sh.scheduled_start)}`}
                                size="small"
                                color={sh.status === 'active' ? 'success' : 'default'}
                                variant="outlined"
                                clickable={canEditShift}
                                onClick={canEditShift ? () => openEditShift({
                                  id: sh.id, guard_user_id: sh.guard_user_id,
                                  scheduled_start: sh.scheduled_start, scheduled_end: sh.scheduled_end,
                                  site_name: sh.site_name,
                                }) : undefined}
                                sx={{ height: 18, fontSize: '0.6rem', m: 0.15, cursor: canEditShift ? 'pointer' : 'default' }}
                              />
                            </Tooltip>
                          ))}
                        </TableCell>
                      )
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      </GlassCard>

      <LeavePreferencesCard onLeaveCreated={openEditShift} />
      <PreferencesEditor />

      <PatternDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
      <AutoScheduleDialog
        open={autoScheduleOpen}
        onClose={() => setAutoScheduleOpen(false)}
        onGenerated={(batch) => setActiveBatchId(batch.id)}
      />
      <EditShiftDialog
        shift={editShift}
        extraNote={editExtraNote}
        onClose={() => { setEditShift(null); setEditExtraNote(undefined) }}
      />
    </Box>
  )
}

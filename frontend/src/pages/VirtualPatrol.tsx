/**
 * Virtual Patrolling — supervisor side.
 *
 * Configuration lives here; execution lives on the officer screen. The split
 * mirrors the permissions: everything on this page needs vpatrol:manage, and a
 * duty officer holding only vpatrol:execute never reaches it.
 *
 * The camera order is edited with explicit up/down controls rather than
 * drag-and-drop. The whole order is sent in one request, because the sequence
 * constraint is deferred and only the final arrangement has to be valid — a
 * per-row save would reject the intermediate state where two cameras briefly
 * share a position.
 */
import { useState } from 'react'
import {
  Box, Typography, Chip, Button, IconButton, Tabs, Tab, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, Dialog, DialogTitle,
  DialogContent, DialogActions, TextField, MenuItem, Select, FormControl,
  InputLabel, Switch, FormControlLabel, Skeleton, Divider, Alert, ToggleButton,
  ToggleButtonGroup,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import VideocamIcon from '@mui/icons-material/Videocam'
import DownloadIcon from '@mui/icons-material/Download'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { usePermission } from '@/hooks/usePermission'
import { useAuthStore } from '@/store/auth'
import { getSites, getSiteCameras } from '@/api/sites'
import {
  listSchedules, createSchedule, setScheduleEnabled, deleteSchedule, getSchedule,
  listScheduleCameras, addScheduleCamera, removeScheduleCamera,
  reorderScheduleCameras, listQuestions, createQuestion, deleteQuestion,
  listSessions, listRecipients, addRecipient, deleteRecipient,
  patrolReportPdfUrl,
} from '@/api/virtualPatrol'
import type {
  ScheduleCamera, QuestionType, FailureAction, SessionStatus,
} from '@/api/virtualPatrol'

const WEEKDAYS = [
  { v: 1, label: 'Mon' }, { v: 2, label: 'Tue' }, { v: 3, label: 'Wed' },
  { v: 4, label: 'Thu' }, { v: 5, label: 'Fri' }, { v: 6, label: 'Sat' },
  { v: 7, label: 'Sun' },
]

const QUESTION_TYPES: { v: QuestionType; label: string }[] = [
  { v: 'YES_NO', label: 'Yes / No' },
  { v: 'PASS_FAIL', label: 'Pass / Fail' },
  { v: 'TEXT', label: 'Free text' },
  { v: 'NUMBER', label: 'Number' },
  { v: 'SINGLE_CHOICE', label: 'Choose one' },
  { v: 'MULTI_CHOICE', label: 'Choose several' },
]

const FAILURE_ACTIONS: { v: FailureAction; label: string }[] = [
  { v: 'NONE', label: 'Record only' },
  { v: 'CREATE_INCIDENT', label: 'Create an incident' },
  { v: 'RAISE_ALERT', label: 'Raise an alert' },
  { v: 'NOTIFY_SUPERVISOR', label: 'Notify the supervisor' },
]

const STATUS_COLOR: Record<SessionStatus, 'success' | 'error' | 'warning' | 'info' | 'default'> = {
  COMPLETED: 'success',
  PARTIALLY_COMPLETED: 'warning',
  IN_PROGRESS: 'info',
  STARTED: 'info',
  SCHEDULED: 'default',
  MISSED: 'error',
  FAILED: 'error',
  CANCELLED: 'default',
}

const pretty = (s: string) => s.replace(/_/g, ' ').toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())

// ── Schedules ────────────────────────────────────────────────────────────────

function ScheduleList({ onOpen }: { onOpen: (id: string) => void }) {
  const qc = useQueryClient()
  const canManage = usePermission('vpatrol:manage')
  const [creating, setCreating] = useState(false)

  const { data: schedules, isLoading } = useQuery({
    queryKey: ['vp-schedules'], queryFn: () => listSchedules(),
  })
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      setScheduleEnabled(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vp-schedules'] }),
  })
  const remove = useMutation({
    mutationFn: (id: string) => deleteSchedule(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vp-schedules'] }),
  })

  if (isLoading) return <Box sx={{ p: 3 }}><Skeleton height={220} /></Box>

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', mb: 2 }}>
        <Typography variant="h6">Patrol Schedules</Typography>
        {canManage && (
          <Button startIcon={<AddIcon />} variant="contained"
                  onClick={() => setCreating(true)}>
            New schedule
          </Button>
        )}
      </Stack>

      {!schedules?.length ? (
        <Alert severity="info">
          No patrol schedules yet. A schedule says which cameras to inspect, in
          what order, and what to ask about each one.
        </Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Patrol</TableCell>
                <TableCell>Site</TableCell>
                <TableCell>When</TableCell>
                <TableCell align="right">Cameras</TableCell>
                <TableCell>Enabled</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {schedules.map((s) => (
                <TableRow key={s.id} hover sx={{ cursor: 'pointer' }}>
                  <TableCell onClick={() => onOpen(s.id)}>
                    <Typography variant="body2">{s.name}</Typography>
                    {s.description && (
                      <Typography variant="caption" color="text.secondary">
                        {s.description}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell onClick={() => onOpen(s.id)}>{s.site_name}</TableCell>
                  <TableCell onClick={() => onOpen(s.id)}>
                    {pretty(s.schedule_type)} at {s.patrol_time?.slice(0, 5)}
                    {s.schedule_type === 'WEEKLY' && s.weekdays?.length > 0 && (
                      <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                        {s.weekdays.map((d) => WEEKDAYS.find((w) => w.v === d)?.label).join(', ')}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell align="right" onClick={() => onOpen(s.id)}>
                    {s.camera_count ?? 0}
                  </TableCell>
                  <TableCell>
                    <Switch size="small" checked={s.enabled} disabled={!canManage}
                            onChange={(e) =>
                              toggle.mutate({ id: s.id, enabled: e.target.checked })} />
                  </TableCell>
                  <TableCell align="right">
                    {canManage && (
                      <IconButton size="small" onClick={() => remove.mutate(s.id)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <CreateScheduleDialog open={creating} onClose={() => setCreating(false)} />
    </Box>
  )
}

function CreateScheduleDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const [form, setForm] = useState({
    site_id: '', name: '', description: '', schedule_type: 'DAILY',
    start_date: new Date().toISOString().slice(0, 10), patrol_time: '07:00',
    weekdays: [] as number[], grace_minutes: 15, email_frequency: 'IMMEDIATE',
  })
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () => createSchedule({
      ...form,
      patrol_time: `${form.patrol_time}:00`,
    } as never),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vp-schedules'] })
      setError(null)
      onClose()
    },
    // The server refuses a weekly patrol with no weekday, among other things.
    // Showing its sentence beats inventing a second copy of the rule here.
    onError: (e: never) =>
      setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Could not create the schedule.'),
  })

  const weeklyWithoutDays = form.schedule_type === 'WEEKLY' && form.weekdays.length === 0

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>New patrol schedule</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <FormControl fullWidth size="small">
            <InputLabel>Site</InputLabel>
            <Select label="Site" value={form.site_id}
                    onChange={(e) => setForm({ ...form, site_id: e.target.value })}>
              {(sites ?? []).map((s: { id: string; name: string }) => (
                <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <TextField label="Patrol name" size="small" fullWidth value={form.name}
                     onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <TextField label="Description" size="small" fullWidth value={form.description}
                     onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <Stack direction="row" spacing={2}>
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel>Repeats</InputLabel>
              <Select label="Repeats" value={form.schedule_type}
                      onChange={(e) => setForm({ ...form, schedule_type: e.target.value })}>
                <MenuItem value="ONCE">Once</MenuItem>
                <MenuItem value="DAILY">Daily</MenuItem>
                <MenuItem value="WEEKLY">Weekly</MenuItem>
              </Select>
            </FormControl>
            <TextField label="Start date" type="date" size="small"
                       slotProps={{ inputLabel: { shrink: true } }}
                       value={form.start_date}
                       onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
            <TextField label="Time" type="time" size="small"
                       slotProps={{ inputLabel: { shrink: true } }}
                       value={form.patrol_time}
                       onChange={(e) => setForm({ ...form, patrol_time: e.target.value })} />
          </Stack>

          {form.schedule_type === 'WEEKLY' && (
            <Box>
              <Typography variant="caption" color="text.secondary">Days</Typography>
              <ToggleButtonGroup size="small" value={form.weekdays} sx={{ display: 'flex', mt: 0.5 }}
                                 onChange={(_, v: number[]) => setForm({ ...form, weekdays: v })}>
                {WEEKDAYS.map((d) => (
                  <ToggleButton key={d.v} value={d.v} sx={{ flex: 1 }}>{d.label}</ToggleButton>
                ))}
              </ToggleButtonGroup>
              {weeklyWithoutDays && (
                <Typography variant="caption" color="error">
                  A weekly patrol needs at least one day, or it never runs.
                </Typography>
              )}
            </Box>
          )}

          <Stack direction="row" spacing={2}>
            <TextField label="Grace (minutes)" type="number" size="small"
                       value={form.grace_minutes}
                       onChange={(e) =>
                         setForm({ ...form, grace_minutes: Number(e.target.value) })}
                       helperText="Marked missed after this" />
            <FormControl size="small" sx={{ minWidth: 170 }}>
              <InputLabel>Email report</InputLabel>
              <Select label="Email report" value={form.email_frequency}
                      onChange={(e) =>
                        setForm({ ...form, email_frequency: e.target.value })}>
                <MenuItem value="IMMEDIATE">On completion</MenuItem>
                <MenuItem value="DAILY">Daily digest</MenuItem>
                <MenuItem value="WEEKLY">Weekly digest</MenuItem>
                <MenuItem value="MONTHLY">Monthly digest</MenuItem>
              </Select>
            </FormControl>
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.site_id || !form.name || weeklyWithoutDays}
                onClick={() => create.mutate()}>
          Create
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Schedule detail: cameras, order, questions ───────────────────────────────

function ScheduleDetail({ scheduleId, onBack }: { scheduleId: string; onBack: () => void }) {
  const qc = useQueryClient()
  const canManage = usePermission('vpatrol:manage')
  const [selectedCamera, setSelectedCamera] = useState<ScheduleCamera | null>(null)

  const { data: schedule } = useQuery({
    queryKey: ['vp-schedule', scheduleId], queryFn: () => getSchedule(scheduleId),
  })
  const { data: cameras } = useQuery({
    queryKey: ['vp-schedule-cameras', scheduleId],
    queryFn: () => listScheduleCameras(scheduleId),
  })
  // Cameras for THIS site only. The API refuses a camera from another site
  // anyway; offering them in the picker would just invite the rejection.
  const { data: available } = useQuery({
    queryKey: ['site-cameras', schedule?.site_id],
    queryFn: () => getSiteCameras(schedule!.site_id),
    enabled: Boolean(schedule?.site_id),
  })

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ['vp-schedule-cameras', scheduleId] })

  const addCamera = useMutation({
    mutationFn: (cameraId: string) => addScheduleCamera(scheduleId, cameraId),
    onSuccess: invalidate,
  })
  const dropCamera = useMutation({
    mutationFn: (id: string) => removeScheduleCamera(scheduleId, id),
    onSuccess: invalidate,
  })
  const reorder = useMutation({
    mutationFn: (items: { schedule_camera_id: string; sequence_no: number }[]) =>
      reorderScheduleCameras(scheduleId, items),
    onSuccess: invalidate,
  })

  /** Swap two positions and send the ENTIRE order. */
  const move = (index: number, delta: number) => {
    if (!cameras) return
    const next = [...cameras]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    reorder.mutate(next.map((c, i) => ({ schedule_camera_id: c.id, sequence_no: i + 1 })))
  }

  const unused = (available ?? []).filter(
    (c: { id: string }) => !(cameras ?? []).some((sc) => sc.camera_id === c.id))

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 2 }}>
        <IconButton size="small" onClick={onBack}><ArrowBackIcon /></IconButton>
        <Typography variant="h6">{schedule?.name ?? 'Patrol'}</Typography>
        <Chip size="small" label={schedule?.site_name ?? ''} />
      </Stack>

      <Typography variant="subtitle2" sx={{ mb: 1 }}>Camera sequence</Typography>
      <Typography variant="caption" color="text.secondary">
        The officer is walked through these in order.
      </Typography>

      <TableContainer sx={{ mt: 1 }}>
        <Table size="small">
          <TableBody>
            {(cameras ?? []).map((c, i) => (
              <TableRow key={c.id} hover selected={selectedCamera?.id === c.id}>
                <TableCell width={40}>{c.sequence_no}</TableCell>
                <TableCell onClick={() => setSelectedCamera(c)} sx={{ cursor: 'pointer' }}>
                  <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                    <VideocamIcon fontSize="small" />
                    <span>{c.camera_name}</span>
                    {c.location && (
                      <Typography variant="caption" color="text.secondary">
                        {c.location}
                      </Typography>
                    )}
                    <Chip size="small" variant="outlined"
                          label={`${c.question_count ?? 0} question${c.question_count === 1 ? '' : 's'}`} />
                  </Stack>
                </TableCell>
                <TableCell align="right" width={140}>
                  {canManage && (
                    <>
                      <IconButton size="small" disabled={i === 0}
                                  onClick={() => move(i, -1)}>
                        <ArrowUpwardIcon fontSize="small" />
                      </IconButton>
                      <IconButton size="small"
                                  disabled={i === (cameras?.length ?? 0) - 1}
                                  onClick={() => move(i, 1)}>
                        <ArrowDownwardIcon fontSize="small" />
                      </IconButton>
                      <IconButton size="small" onClick={() => dropCamera.mutate(c.id)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

      {canManage && unused.length > 0 && (
        <FormControl size="small" sx={{ mt: 2, minWidth: 260 }}>
          <InputLabel>Add a camera</InputLabel>
          <Select label="Add a camera" value=""
                  onChange={(e) => addCamera.mutate(e.target.value)}>
            {unused.map((c: { id: string; name: string }) => (
              <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>
            ))}
          </Select>
        </FormControl>
      )}

      {selectedCamera && (
        <>
          <Divider sx={{ my: 3 }} />
          <QuestionBuilder camera={selectedCamera} canManage={canManage} />
        </>
      )}

      <Divider sx={{ my: 3 }} />
      <EmailRecipients scheduleId={scheduleId} />
    </Box>
  )
}

function QuestionBuilder({ camera, canManage }: { camera: ScheduleCamera; canManage: boolean }) {
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const [type, setType] = useState<QuestionType>('YES_NO')
  const [required, setRequired] = useState(true)
  const [action, setAction] = useState<FailureAction>('NONE')
  const [options, setOptions] = useState('')
  const [error, setError] = useState<string | null>(null)

  const { data: questions } = useQuery({
    queryKey: ['vp-questions', camera.id], queryFn: () => listQuestions(camera.id),
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['vp-questions', camera.id] })

  const add = useMutation({
    mutationFn: () => createQuestion(camera.id, {
      question_text: text, question_type: type, is_required: required,
      failure_action: action,
      options: type === 'SINGLE_CHOICE' || type === 'MULTI_CHOICE'
        ? options.split(',').map((o) => o.trim()).filter(Boolean)
        : null,
    }),
    onSuccess: () => { setText(''); setOptions(''); setError(null); invalidate() },
    onError: (e: never) =>
      setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Could not add the question.'),
  })
  const drop = useMutation({
    mutationFn: (id: string) => deleteQuestion(id), onSuccess: invalidate,
  })

  const needsOptions = type === 'SINGLE_CHOICE' || type === 'MULTI_CHOICE'

  return (
    <Box>
      <Typography variant="subtitle2">Questions for {camera.camera_name}</Typography>
      <Typography variant="caption" color="text.secondary">
        Asked at this camera, in order. Required questions must be answered before
        the officer can move on.
      </Typography>

      <TableContainer sx={{ mt: 1 }}>
        <Table size="small">
          <TableBody>
            {(questions ?? []).map((q) => (
              <TableRow key={q.id}>
                <TableCell width={30}>{q.sequence_no}</TableCell>
                <TableCell>{q.question_text}</TableCell>
                <TableCell width={120}>
                  <Chip size="small" variant="outlined"
                        label={QUESTION_TYPES.find((t) => t.v === q.question_type)?.label} />
                </TableCell>
                <TableCell width={60}>
                  {q.is_required && <Chip size="small" label="Required" />}
                </TableCell>
                <TableCell width={150}>
                  {q.failure_action !== 'NONE' && (
                    <Chip size="small" color="warning" variant="outlined"
                          label={FAILURE_ACTIONS.find((a) => a.v === q.failure_action)?.label} />
                  )}
                </TableCell>
                <TableCell align="right" width={50}>
                  {canManage && (
                    <IconButton size="small" onClick={() => drop.mutate(q.id)}>
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

      {canManage && (
        <Stack spacing={1.5} sx={{ mt: 2 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <Stack direction="row" spacing={1.5}>
            <TextField label="Question" size="small" fullWidth value={text}
                       onChange={(e) => setText(e.target.value)} />
            <FormControl size="small" sx={{ minWidth: 150 }}>
              <InputLabel>Type</InputLabel>
              <Select label="Type" value={type}
                      onChange={(e) => setType(e.target.value as QuestionType)}>
                {QUESTION_TYPES.map((t) => (
                  <MenuItem key={t.v} value={t.v}>{t.label}</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Stack>
          {needsOptions && (
            <TextField label="Options (comma separated)" size="small" fullWidth
                       value={options} onChange={(e) => setOptions(e.target.value)}
                       helperText="A choice question with no options cannot be answered." />
          )}
          <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
            <FormControlLabel
              control={<Switch checked={required}
                               onChange={(e) => setRequired(e.target.checked)} />}
              label="Required" />
            <FormControl size="small" sx={{ minWidth: 210 }}>
              <InputLabel>If the answer is negative</InputLabel>
              <Select label="If the answer is negative" value={action}
                      onChange={(e) => setAction(e.target.value as FailureAction)}>
                {FAILURE_ACTIONS.map((a) => (
                  <MenuItem key={a.v} value={a.v}>{a.label}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <Button variant="contained" startIcon={<AddIcon />}
                    disabled={!text || (needsOptions && !options.trim())}
                    onClick={() => add.mutate()}>
              Add question
            </Button>
          </Stack>
        </Stack>
      )}
    </Box>
  )
}

function EmailRecipients({ scheduleId }: { scheduleId: string }) {
  const qc = useQueryClient()
  const canEmail = usePermission('vpatrol:email')
  const [email, setEmail] = useState('')
  const { data: recipients } = useQuery({
    queryKey: ['vp-recipients', scheduleId], queryFn: () => listRecipients(scheduleId),
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['vp-recipients', scheduleId] })
  const add = useMutation({
    mutationFn: () => addRecipient(scheduleId, email),
    onSuccess: () => { setEmail(''); invalidate() },
  })
  const drop = useMutation({
    mutationFn: (id: string) => deleteRecipient(id), onSuccess: invalidate,
  })

  return (
    <Box>
      <Typography variant="subtitle2">Report recipients</Typography>
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', mt: 1 }}>
        {(recipients ?? []).map((r) => (
          <Chip key={r.id} label={r.email} size="small"
                onDelete={canEmail ? () => drop.mutate(r.id) : undefined} />
        ))}
        {!recipients?.length && (
          <Typography variant="caption" color="text.secondary">
            Nobody is receiving this patrol's reports.
          </Typography>
        )}
      </Stack>
      {canEmail && (
        <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
          <TextField label="Add email" size="small" value={email}
                     onChange={(e) => setEmail(e.target.value)} />
          <Button onClick={() => add.mutate()} disabled={!email.includes('@')}>Add</Button>
        </Stack>
      )}
    </Box>
  )
}

// ── History ──────────────────────────────────────────────────────────────────

function PatrolHistory() {
  const token = useAuthStore((s) => s.accessToken)
  const { data: sessions, isLoading } = useQuery({
    queryKey: ['vp-sessions'], queryFn: () => listSessions(),
  })
  if (isLoading) return <Box sx={{ p: 3 }}><Skeleton height={200} /></Box>

  return (
    <Box sx={{ p: 3 }}>
      {!sessions?.length ? (
        <Alert severity="info">
          No patrols have run yet. Sessions appear here once the scheduler creates
          them, or once one is started manually.
        </Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Patrol</TableCell>
                <TableCell>Site</TableCell>
                <TableCell>Officer</TableCell>
                <TableCell>Scheduled</TableCell>
                <TableCell align="right">Cameras</TableCell>
                <TableCell align="right">Exceptions</TableCell>
                <TableCell>Status</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {sessions.map((s) => {
                const url = patrolReportPdfUrl(s.id, token)
                return (
                  <TableRow key={s.id} hover>
                    <TableCell>
                      <Typography variant="body2">{s.schedule_name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {s.patrol_number}
                      </Typography>
                    </TableCell>
                    <TableCell>{s.site_name}</TableCell>
                    <TableCell>{s.officer_name ?? 'Unassigned'}</TableCell>
                    <TableCell>{new Date(s.scheduled_for).toLocaleString()}</TableCell>
                    <TableCell align="right">
                      {s.completed_camera_count} / {s.camera_count}
                    </TableCell>
                    <TableCell align="right">
                      {s.exception_count ? (
                        <Chip size="small" color="error" label={s.exception_count} />
                      ) : '—'}
                    </TableCell>
                    <TableCell>
                      <Chip size="small" color={STATUS_COLOR[s.status] ?? 'default'}
                            variant={s.status === 'COMPLETED' ? 'filled' : 'outlined'}
                            label={pretty(s.status)} />
                    </TableCell>
                    <TableCell align="right">
                      {url && (
                        <IconButton size="small" component="a" href={url} target="_blank">
                          <DownloadIcon fontSize="small" />
                        </IconButton>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function VirtualPatrolPage() {
  const [tab, setTab] = useState(0)
  const [openSchedule, setOpenSchedule] = useState<string | null>(null)

  return (
    <Box>
      <PageHeader pageKey="virtual-patrol" />
      <GlassCard>
        <Box sx={{ borderBottom: 1, borderColor: 'rgba(255,255,255,0.1)' }}>
          <Tabs value={tab} onChange={(_, v) => { setTab(v); setOpenSchedule(null) }}>
            <Tab label="Schedules" />
            <Tab label="History" />
          </Tabs>
        </Box>
        {tab === 0 && (openSchedule
          ? <ScheduleDetail scheduleId={openSchedule} onBack={() => setOpenSchedule(null)} />
          : <ScheduleList onOpen={setOpenSchedule} />)}
        {tab === 1 && <PatrolHistory />}
      </GlassCard>
    </Box>
  )
}

export default function VirtualPatrolGuarded() {
  return (
    <PermissionGuard permission="vpatrol:read">
      <VirtualPatrolPage />
    </PermissionGuard>
  )
}

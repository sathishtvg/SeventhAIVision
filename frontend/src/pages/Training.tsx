import { useState } from 'react'
import {
  Box,
  Typography,
  Tab,
  Tabs,
  Grid,
  Chip,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  CircularProgress,
  Skeleton,
  IconButton,
  Tooltip,
  Alert,
  LinearProgress,
  Radio,
  RadioGroup,
  FormControl,
  FormControlLabel,
  FormLabel,
  Divider,
  Card,
  CardContent,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import EditIcon from '@mui/icons-material/Edit'
import SchoolIcon from '@mui/icons-material/School'
import QuizIcon from '@mui/icons-material/Quiz'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import VerifiedIcon from '@mui/icons-material/Verified'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import ErrorIcon from '@mui/icons-material/Error'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import CancelIcon from '@mui/icons-material/Cancel'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getTrainingDashboard, listCourses, createCourse,
  listRecords, createRecord, deleteRecord,
  listCertifications, createCertification, revokeCertification,
  getQuestions, createQuestion, updateQuestion, deleteQuestion,
  startAttempt, getAttempt, answerQuestion, submitAttempt, listAttempts,
  type TrainingQuestion, type TrainingAttemptQuestion,
} from '@/api/training'
import { getUsers } from '@/api/users'
import { useAuthStore } from '@/store/auth'
import GlassCard from '@/components/common/GlassCard'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import { usePermission } from '@/hooks/usePermission'
import { PageHeader } from '@/components/common/PageHeader'

const CATEGORY_COLOR: Record<string, string> = {
  general:            '#6C63FF',
  fire_safety:        '#FF9800',
  first_aid:          '#FF4560',
  security:           '#00D9C0',
  cctv:               '#2196F3',
  legal:              '#9C27B0',
  physical:           '#00E396',
  emergency_response: '#FF4560',
}

const EXPIRY_STATUS_CONFIG = {
  valid:        { color: '#00E396', label: 'Valid',         icon: <CheckCircleIcon sx={{ fontSize: 14 }} /> },
  expiring_soon:{ color: '#FF9800', label: 'Expiring Soon', icon: <WarningAmberIcon sx={{ fontSize: 14 }} /> },
  expired:      { color: '#FF4560', label: 'Expired',       icon: <ErrorIcon sx={{ fontSize: 14 }} /> },
  no_expiry:    { color: '#6C63FF', label: 'No Expiry',     icon: <VerifiedIcon sx={{ fontSize: 14 }} /> },
}

// ── Dashboard tab ─────────────────────────────────────────────────────────────

function DashboardTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['training-dashboard'],
    queryFn: getTrainingDashboard,
    refetchInterval: 60_000,
  })

  const passRate = data
    ? data.record_stats.this_month > 0
      ? Math.round((data.record_stats.passed_this_month / data.record_stats.this_month) * 100)
      : 0
    : 0

  const kpis = [
    { label: 'Active Courses',     value: data?.course_stats.active_courses ?? 0,    color: '#6C63FF' },
    { label: 'Total Records',      value: data?.record_stats.total_records ?? 0,      color: '#2196F3' },
    { label: 'Valid Certifications', value: data?.cert_stats.valid_certs ?? 0,        color: '#00E396' },
    { label: 'Expired Certs',      value: data?.cert_stats.expired_certs ?? 0,        color: '#FF4560' },
    { label: 'Expiring ≤ 30d',     value: data?.cert_stats.expiring_30d ?? 0,         color: '#FF9800' },
    { label: 'Expiring ≤ 7d',      value: data?.cert_stats.expiring_7d ?? 0,          color: '#FF4560' },
    { label: 'This Month',         value: data?.record_stats.this_month ?? 0,          color: '#00D9C0' },
    { label: 'Pass Rate (month)',   value: `${passRate}%`,                            color: passRate >= 70 ? '#00E396' : '#FF9800' },
  ]

  return (
    <Box>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {kpis.map(kpi => (
          <Grid size={{ xs: 6, sm: 3 }} key={kpi.label}>
            <GlassCard sx={{ p: 2, borderTop: `3px solid ${kpi.color}` }}>
              {isLoading ? <Skeleton height={40} /> : (
                <>
                  <Typography variant="h4" fontWeight={700}
                    sx={{ color: typeof kpi.value === 'string' || (kpi.value as number) > 0 ? kpi.color : 'rgba(255,255,255,0.25)' }}>
                    {kpi.value}
                  </Typography>
                  <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.5)', fontSize: '0.7rem' }}>
                    {kpi.label}
                  </Typography>
                </>
              )}
            </GlassCard>
          </Grid>
        ))}
      </Grid>

      {!isLoading && (data?.cert_stats.expiring_30d ?? 0) > 0 && (
        <Alert severity="warning" sx={{ mb: 2, bgcolor: 'rgba(255,152,0,0.1)', color: '#FF9800' }}>
          {data!.cert_stats.expiring_7d > 0
            ? `${data!.cert_stats.expiring_7d} certification(s) expire within 7 days.`
            : `${data!.cert_stats.expiring_30d} certification(s) expire within 30 days.`
          }
        </Alert>
      )}

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Typography variant="subtitle2" sx={{ mb: 1, color: 'rgba(255,255,255,0.6)' }}>
            Certifications Expiring Soon
          </Typography>
          <GlassCard sx={{ p: 0 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Guard</TableCell>
                  <TableCell>Certification</TableCell>
                  <TableCell>Expires</TableCell>
                  <TableCell>Days Left</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {isLoading && [...Array(3)].map((_, i) => (
                  <TableRow key={i}>{[...Array(4)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
                ))}
                {data?.expiring_certifications.map(c => (
                  <TableRow key={c.id} hover>
                    <TableCell fontWeight={500}>{c.guard_name}</TableCell>
                    <TableCell sx={{ fontSize: '0.8rem' }}>
                      {c.certification_type.replace(/_/g, ' ')}
                    </TableCell>
                    <TableCell sx={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.6)' }}>
                      {c.expires_at}
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={`${c.days_remaining}d`}
                        size="small"
                        sx={{
                          bgcolor: c.days_remaining <= 7 ? 'rgba(255,69,96,0.15)' : 'rgba(255,152,0,0.15)',
                          color: c.days_remaining <= 7 ? '#FF4560' : '#FF9800',
                          fontSize: '0.7rem',
                        }}
                      />
                    </TableCell>
                  </TableRow>
                ))}
                {!isLoading && !data?.expiring_certifications.length && (
                  <TableRow>
                    <TableCell colSpan={4} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 2 }}>
                      No certifications expiring in the next 30 days
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </GlassCard>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Typography variant="subtitle2" sx={{ mb: 1, color: 'rgba(255,255,255,0.6)' }}>
            Expired Training Records
          </Typography>
          <GlassCard sx={{ p: 0 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Guard</TableCell>
                  <TableCell>Course</TableCell>
                  <TableCell>Expired</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {isLoading && [...Array(3)].map((_, i) => (
                  <TableRow key={i}>{[...Array(3)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
                ))}
                {data?.expired_training_records.map(r => (
                  <TableRow key={r.id} hover>
                    <TableCell fontWeight={500}>{r.guard_name}</TableCell>
                    <TableCell sx={{ fontSize: '0.8rem' }}>{r.course_name}</TableCell>
                    <TableCell sx={{ fontSize: '0.78rem', color: '#FF4560' }}>{r.expires_at}</TableCell>
                  </TableRow>
                ))}
                {!isLoading && !data?.expired_training_records.length && (
                  <TableRow>
                    <TableCell colSpan={3} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 2 }}>
                      No expired training records
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </GlassCard>
        </Grid>
      </Grid>
    </Box>
  )
}

// ── Courses tab ───────────────────────────────────────────────────────────────

function CoursesTab() {
  const canManage = usePermission('training:manage')
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [questionsCourse, setQuestionsCourse] = useState<{ id: string; name: string } | null>(null)
  const [form, setForm] = useState({
    name: '', category: 'general', duration_hours: '',
    passing_score: '70', validity_months: '',
  })

  const { data = [], isLoading } = useQuery({
    queryKey: ['training-courses'],
    queryFn: () => listCourses(),
  })

  const createMut = useMutation({
    mutationFn: createCourse,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['training-courses'] }); setOpen(false) },
  })

  const CATEGORIES = ['general', 'fire_safety', 'first_aid', 'security', 'cctv', 'legal', 'physical', 'emergency_response']

  return (
    <Box>
      {canManage && (
        <Button startIcon={<AddIcon />} variant="contained" size="small" sx={{ mb: 2 }}
          onClick={() => setOpen(true)}>
          Add Course
        </Button>
      )}
      <GlassCard sx={{ p: 0 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Course Name</TableCell>
              <TableCell>Category</TableCell>
              <TableCell>Duration</TableCell>
              <TableCell>Pass Score</TableCell>
              <TableCell>Valid For</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Quiz</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading && [...Array(4)].map((_, i) => (
              <TableRow key={i}>{[...Array(7)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
            ))}
            {data.map(c => (
              <TableRow key={c.id} hover>
                <TableCell>
                  <Typography fontWeight={600} fontSize="0.85rem">{c.name}</Typography>
                  {c.description && (
                    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)', display: 'block' }}>
                      {c.description.slice(0, 60)}{c.description.length > 60 ? '…' : ''}
                    </Typography>
                  )}
                </TableCell>
                <TableCell>
                  <Chip label={c.category.replace(/_/g, ' ')} size="small"
                    sx={{ bgcolor: `${CATEGORY_COLOR[c.category] ?? '#888'}22`,
                          color: CATEGORY_COLOR[c.category] ?? '#888', fontSize: '0.7rem' }} />
                </TableCell>
                <TableCell sx={{ fontSize: '0.8rem', color: 'rgba(255,255,255,0.6)' }}>
                  {c.duration_hours ? `${c.duration_hours}h` : '—'}
                </TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <LinearProgress variant="determinate" value={c.passing_score}
                      sx={{ width: 40, height: 4, borderRadius: 2,
                            '& .MuiLinearProgress-bar': { bgcolor: '#00E396' } }} />
                    <Typography fontSize="0.78rem">{c.passing_score}%</Typography>
                  </Box>
                </TableCell>
                <TableCell sx={{ fontSize: '0.8rem', color: 'rgba(255,255,255,0.6)' }}>
                  {c.validity_months ? `${c.validity_months} months` : 'No expiry'}
                </TableCell>
                <TableCell>
                  <Chip label={c.is_active ? 'Active' : 'Inactive'} size="small"
                    sx={{ bgcolor: c.is_active ? 'rgba(0,227,150,0.15)' : 'rgba(255,69,96,0.15)',
                          color: c.is_active ? '#00E396' : '#FF4560', fontSize: '0.7rem' }} />
                </TableCell>
                <TableCell>
                  {canManage ? (
                    <Button size="small" startIcon={<QuizIcon fontSize="small" />}
                      onClick={() => setQuestionsCourse({ id: c.id, name: c.name })}>
                      {c.question_count} question{c.question_count === 1 ? '' : 's'}
                    </Button>
                  ) : (
                    <Typography variant="caption" color="text.secondary">
                      {c.question_count} question{c.question_count === 1 ? '' : 's'}
                    </Typography>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {!isLoading && !data.length && (
              <TableRow>
                <TableCell colSpan={7} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 3 }}>
                  No training courses configured
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </GlassCard>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add Training Course</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="Course Name" value={form.name} required size="small"
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          <TextField label="Category" value={form.category} size="small" select
            onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
            {CATEGORIES.map(c => <MenuItem key={c} value={c}>{c.replace(/_/g, ' ')}</MenuItem>)}
          </TextField>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <TextField label="Duration (hours)" value={form.duration_hours} size="small" type="number"
              onChange={e => setForm(f => ({ ...f, duration_hours: e.target.value }))} />
            <TextField label="Pass Score (%)" value={form.passing_score} size="small" type="number"
              onChange={e => setForm(f => ({ ...f, passing_score: e.target.value }))} />
          </Box>
          <TextField label="Valid for (months, blank = no expiry)" value={form.validity_months}
            size="small" type="number"
            onChange={e => setForm(f => ({ ...f, validity_months: e.target.value }))} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={!form.name || createMut.isPending}
            onClick={() => createMut.mutate({
              name: form.name, category: form.category,
              duration_hours: form.duration_hours ? Number(form.duration_hours) : undefined,
              passing_score: Number(form.passing_score) || 70,
              validity_months: form.validity_months ? Number(form.validity_months) : undefined,
            })}>
            {createMut.isPending ? <CircularProgress size={18} /> : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      {questionsCourse && (
        <QuestionsDialog
          courseId={questionsCourse.id}
          courseName={questionsCourse.name}
          onClose={() => setQuestionsCourse(null)}
        />
      )}
    </Box>
  )
}

// ── Question bank dialog ──────────────────────────────────────────────────────

function QuestionsDialog({ courseId, courseName, onClose }: {
  courseId: string; courseName: string; onClose: () => void
}) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<TrainingQuestion | null>(null)
  const [adding, setAdding] = useState(false)
  const [qForm, setQForm] = useState({ question_text: '', options: ['', ''], correct_index: 0, points: '1' })

  const { data: questions = [], isLoading } = useQuery({
    queryKey: ['training-questions', courseId],
    queryFn: () => getQuestions(courseId),
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['training-questions', courseId] })
    qc.invalidateQueries({ queryKey: ['training-courses'] })
  }

  const createMut = useMutation({
    mutationFn: () => createQuestion(courseId, {
      question_text: qForm.question_text,
      options: qForm.options.filter(o => o.trim()),
      correct_index: qForm.correct_index,
      points: Number(qForm.points) || 1,
    }),
    onSuccess: () => { invalidate(); resetForm() },
  })
  const updateMut = useMutation({
    mutationFn: () => updateQuestion(editing!.id, {
      question_text: qForm.question_text,
      options: qForm.options.filter(o => o.trim()),
      correct_index: qForm.correct_index,
      points: Number(qForm.points) || 1,
    }),
    onSuccess: () => { invalidate(); resetForm() },
  })
  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteQuestion(id),
    onSuccess: () => invalidate(),
  })

  const resetForm = () => {
    setAdding(false); setEditing(null)
    setQForm({ question_text: '', options: ['', ''], correct_index: 0, points: '1' })
  }
  const startEdit = (q: TrainingQuestion) => {
    setEditing(q); setAdding(true)
    setQForm({ question_text: q.question_text, options: [...q.options], correct_index: q.correct_index, points: String(q.points) })
  }

  const validOptions = qForm.options.filter(o => o.trim()).length >= 2

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Questions — {courseName}</DialogTitle>
      <DialogContent>
        {isLoading ? <Skeleton height={80} /> : (
          <Stack divider={<Divider />} spacing={1.5} sx={{ mb: 2 }}>
            {questions.map((q, i) => (
              <Box key={q.id} sx={{ display: 'flex', justifyContent: 'space-between', gap: 1, py: 0.5 }}>
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" fontWeight={600}>{i + 1}. {q.question_text}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Correct: {q.options[q.correct_index]} · {q.points} pt(s)
                  </Typography>
                </Box>
                <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
                  <IconButton size="small" onClick={() => startEdit(q)}><EditIcon fontSize="small" /></IconButton>
                  <IconButton size="small" onClick={() => deleteMut.mutate(q.id)}><DeleteIcon fontSize="small" /></IconButton>
                </Stack>
              </Box>
            ))}
            {questions.length === 0 && (
              <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 2 }}>
                No questions yet.
              </Typography>
            )}
          </Stack>
        )}

        {adding ? (
          <Box sx={{ p: 2, borderRadius: '8px', backgroundColor: 'rgba(255,255,255,0.04)' }}>
            <Stack spacing={1.5}>
              <TextField
                label="Question" size="small" fullWidth multiline minRows={2}
                value={qForm.question_text} onChange={e => setQForm(f => ({ ...f, question_text: e.target.value }))}
              />
              <FormControl>
                <FormLabel sx={{ fontSize: '0.8rem' }}>Options (select the correct one)</FormLabel>
                <RadioGroup
                  value={qForm.correct_index}
                  onChange={e => setQForm(f => ({ ...f, correct_index: Number(e.target.value) }))}
                >
                  {qForm.options.map((opt, i) => (
                    <Stack key={i} direction="row" spacing={1} alignItems="center">
                      <Radio value={i} size="small" />
                      <TextField
                        size="small" fullWidth placeholder={`Option ${i + 1}`} value={opt}
                        onChange={e => setQForm(f => ({ ...f, options: f.options.map((o, j) => j === i ? e.target.value : o) }))}
                      />
                      {qForm.options.length > 2 && (
                        <IconButton size="small" onClick={() => setQForm(f => ({
                          ...f,
                          options: f.options.filter((_, j) => j !== i),
                          correct_index: f.correct_index >= f.options.length - 1 ? 0 : f.correct_index,
                        }))}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      )}
                    </Stack>
                  ))}
                </RadioGroup>
                <Button size="small" onClick={() => setQForm(f => ({ ...f, options: [...f.options, ''] }))} sx={{ alignSelf: 'flex-start', mt: 0.5 }}>
                  + Add Option
                </Button>
              </FormControl>
              <TextField
                label="Points" type="number" size="small" sx={{ width: 100 }}
                value={qForm.points} onChange={e => setQForm(f => ({ ...f, points: e.target.value }))}
              />
              <Stack direction="row" spacing={1} justifyContent="flex-end">
                <Button onClick={resetForm}>Cancel</Button>
                <Button
                  variant="contained"
                  disabled={!qForm.question_text || !validOptions || createMut.isPending || updateMut.isPending}
                  onClick={() => editing ? updateMut.mutate() : createMut.mutate()}
                >
                  {editing ? 'Save' : 'Add Question'}
                </Button>
              </Stack>
            </Stack>
          </Box>
        ) : (
          <Button startIcon={<AddIcon />} onClick={() => setAdding(true)}>Add Question</Button>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Records tab ───────────────────────────────────────────────────────────────

function RecordsTab() {
  const canWrite = usePermission('training:write')
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [filterPassed, setFilterPassed] = useState<string>('all')
  const [form, setForm] = useState({
    user_id: '', course_id: '', completed_at: '', score: '', passed: 'true', notes: '',
  })

  const { data: records = [], isLoading } = useQuery({
    queryKey: ['training-records', filterPassed],
    queryFn: () => listRecords(filterPassed !== 'all' ? { passed: filterPassed === 'true' } : undefined),
  })
  const { data: courses = [] } = useQuery({ queryKey: ['training-courses'], queryFn: () => listCourses({ is_active: true }) })
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => getUsers() })

  const createMut = useMutation({
    mutationFn: createRecord,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['training-records'] }); setOpen(false) },
  })
  const deleteMut = useMutation({
    mutationFn: deleteRecord,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['training-records'] }),
  })

  const filterGroups: FilterGroup[] = [{
    key: 'passed',
    label: 'Result',
    allValue: 'all',
    value: filterPassed,
    onChange: setFilterPassed,
    options: [
      { value: 'all', label: 'All' },
      { value: 'true', label: 'Passed' },
      { value: 'false', label: 'Failed' },
    ],
  }]

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 2, flexWrap: 'wrap' }}>
        {canWrite && (
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setOpen(true)}>
            Record Training
          </Button>
        )}
      </Box>

      <GlassCard sx={{ p: 0 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Guard</TableCell>
              <TableCell>Course</TableCell>
              <TableCell>Completed</TableCell>
              <TableCell>Score</TableCell>
              <TableCell>Result</TableCell>
              <TableCell>Expires</TableCell>
              {canWrite && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading && [...Array(5)].map((_, i) => (
              <TableRow key={i}>{[...Array(6)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
            ))}
            {records.map(r => {
              const isExpired = r.expires_at && new Date(r.expires_at) < new Date()
              return (
                <TableRow key={r.id} hover>
                  <TableCell>
                    <Typography fontWeight={500} fontSize="0.85rem">{r.guard_name}</Typography>
                    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)' }}>{r.guard_email}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography fontSize="0.82rem">{r.course_name}</Typography>
                    <Chip label={r.course_category.replace(/_/g, ' ')} size="small"
                      sx={{ mt: 0.3, bgcolor: `${CATEGORY_COLOR[r.course_category] ?? '#888'}22`,
                            color: CATEGORY_COLOR[r.course_category] ?? '#888', fontSize: '0.65rem' }} />
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.8rem', color: 'rgba(255,255,255,0.6)' }}>
                    {r.completed_at}
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.8rem' }}>
                    {r.score != null ? (
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                        <LinearProgress variant="determinate" value={r.score}
                          sx={{ width: 36, height: 4, borderRadius: 2,
                                '& .MuiLinearProgress-bar': {
                                  bgcolor: r.score >= r.course_passing_score ? '#00E396' : '#FF4560'
                                } }} />
                        <Typography fontSize="0.78rem">{r.score}%</Typography>
                      </Box>
                    ) : '—'}
                  </TableCell>
                  <TableCell>
                    <Chip
                      icon={r.passed ? <CheckCircleIcon sx={{ fontSize: '14px !important' }} /> : <CancelIcon sx={{ fontSize: '14px !important' }} />}
                      label={r.passed ? 'Passed' : 'Failed'} size="small"
                      sx={{ bgcolor: r.passed ? 'rgba(0,227,150,0.15)' : 'rgba(255,69,96,0.15)',
                            color: r.passed ? '#00E396' : '#FF4560', fontSize: '0.7rem' }} />
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.78rem', color: isExpired ? '#FF4560' : 'rgba(255,255,255,0.5)' }}>
                    {r.expires_at ?? 'No expiry'}
                  </TableCell>
                  {canWrite && (
                    <TableCell align="right">
                      <Tooltip title="Delete record">
                        <IconButton size="small" color="error" onClick={() => deleteMut.mutate(r.id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  )}
                </TableRow>
              )
            })}
            {!isLoading && !records.length && (
              <TableRow>
                <TableCell colSpan={7} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 3 }}>
                  No training records found
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </GlassCard>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Record Training Completion</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="Guard" value={form.user_id} required size="small" select
            onChange={e => setForm(f => ({ ...f, user_id: e.target.value }))}>
            {(users as any[]).map((u: any) => (
              <MenuItem key={u.id} value={u.id}>{u.full_name} ({u.email})</MenuItem>
            ))}
          </TextField>
          <TextField label="Course" value={form.course_id} required size="small" select
            onChange={e => setForm(f => ({ ...f, course_id: e.target.value }))}>
            {courses.map(c => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
          </TextField>
          <TextField label="Completed Date" value={form.completed_at} size="small" type="date"
            slotProps={{ inputLabel: { shrink: true } }}
            onChange={e => setForm(f => ({ ...f, completed_at: e.target.value }))} />
          <Box sx={{ display: 'flex', gap: 1 }}>
            <TextField label="Score (%)" value={form.score} size="small" type="number"
              sx={{ flex: 1 }}
              onChange={e => setForm(f => ({ ...f, score: e.target.value }))} />
            <TextField label="Result" value={form.passed} size="small" select sx={{ flex: 1 }}
              onChange={e => setForm(f => ({ ...f, passed: e.target.value }))}>
              <MenuItem value="true">Passed</MenuItem>
              <MenuItem value="false">Failed</MenuItem>
            </TextField>
          </Box>
          <TextField label="Notes" value={form.notes} size="small" multiline rows={2}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained"
            disabled={!form.user_id || !form.course_id || createMut.isPending}
            onClick={() => createMut.mutate({
              user_id: form.user_id, course_id: form.course_id,
              completed_at: form.completed_at || undefined,
              score: form.score ? Number(form.score) : undefined,
              passed: form.passed === 'true',
              notes: form.notes || undefined,
            })}>
            {createMut.isPending ? <CircularProgress size={18} /> : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>
      </Box>

      <FilterRail groups={filterGroups} storageKey="training-records" />
    </Box>
  )
}

// ── Certifications tab ────────────────────────────────────────────────────────

function CertificationsTab() {
  const canWrite = usePermission('training:write')
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState<string>('all')

  const filterGroups: FilterGroup[] = [{
    key: 'expiry',
    label: 'Expiry',
    allValue: 'all',
    value: filter,
    onChange: setFilter,
    options: [
      { value: 'all', label: 'All' },
      { value: 'expiring', label: 'Expiring ≤ 30d' },
      { value: 'expired', label: 'Expired' },
    ],
  }]
  const [form, setForm] = useState({
    user_id: '', certification_type: '', issuing_body: '',
    certificate_number: '', issued_at: '', expires_at: '',
  })

  const params =
    filter === 'expiring' ? { expiring_days: 30 } :
    filter === 'expired'  ? { is_valid: true as const } : // we fetch valid only then filter client-side for expired
    undefined

  const { data: certs = [], isLoading } = useQuery({
    queryKey: ['guard-certifications', filter],
    queryFn: () => listCertifications(filter === 'expiring' ? { expiring_days: 30 } : undefined),
    refetchInterval: 60_000,
  })

  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => getUsers() })

  const createMut = useMutation({
    mutationFn: createCertification,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['guard-certifications'] }); setOpen(false) },
  })

  const revokeMut = useMutation({
    mutationFn: revokeCertification,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['guard-certifications'] }),
  })

  const displayed = filter === 'expired'
    ? certs.filter(c => c.expiry_status === 'expired')
    : certs

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 2, flexWrap: 'wrap' }}>
        {canWrite && (
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setOpen(true)}>
            Add Certification
          </Button>
        )}
      </Box>

      <GlassCard sx={{ p: 0 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Guard</TableCell>
              <TableCell>Certification</TableCell>
              <TableCell>Issuing Body</TableCell>
              <TableCell>Cert No.</TableCell>
              <TableCell>Issued</TableCell>
              <TableCell>Expires</TableCell>
              <TableCell>Status</TableCell>
              {canWrite && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading && [...Array(4)].map((_, i) => (
              <TableRow key={i}>{[...Array(7)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}</TableRow>
            ))}
            {displayed.map(c => {
              const cfg = EXPIRY_STATUS_CONFIG[c.expiry_status] ?? EXPIRY_STATUS_CONFIG.valid
              return (
                <TableRow key={c.id} hover>
                  <TableCell>
                    <Typography fontWeight={500} fontSize="0.85rem">{c.guard_name}</Typography>
                    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)' }}>{c.guard_email}</Typography>
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.82rem' }}>
                    {c.certification_type.replace(/_/g, ' ')}
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.6)' }}>
                    {c.issuing_body ?? '—'}
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.75rem', fontFamily: 'monospace', color: 'rgba(255,255,255,0.5)' }}>
                    {c.certificate_number ?? '—'}
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.5)' }}>
                    {c.issued_at ?? '—'}
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.78rem', color: c.expiry_status === 'expired' ? '#FF4560' : 'inherit' }}>
                    {c.expires_at ?? '—'}
                  </TableCell>
                  <TableCell>
                    <Chip icon={cfg.icon as any} label={cfg.label} size="small"
                      sx={{ bgcolor: `${cfg.color}22`, color: cfg.color, fontSize: '0.7rem' }} />
                  </TableCell>
                  {canWrite && (
                    <TableCell align="right">
                      <Tooltip title="Revoke">
                        <IconButton size="small" color="error"
                          disabled={!c.is_valid}
                          onClick={() => revokeMut.mutate(c.id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  )}
                </TableRow>
              )
            })}
            {!isLoading && !displayed.length && (
              <TableRow>
                <TableCell colSpan={8} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 3 }}>
                  No certifications found
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </GlassCard>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add Certification</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="Guard" value={form.user_id} required size="small" select
            onChange={e => setForm(f => ({ ...f, user_id: e.target.value }))}>
            {(users as any[]).map((u: any) => (
              <MenuItem key={u.id} value={u.id}>{u.full_name} ({u.email})</MenuItem>
            ))}
          </TextField>
          <TextField label="Certification Type" value={form.certification_type} required size="small"
            placeholder="e.g. security_officer_license, first_aid, fire_warden"
            onChange={e => setForm(f => ({ ...f, certification_type: e.target.value }))} />
          <TextField label="Issuing Body" value={form.issuing_body} size="small"
            placeholder="e.g. Singapore Police Force, St John"
            onChange={e => setForm(f => ({ ...f, issuing_body: e.target.value }))} />
          <TextField label="Certificate Number" value={form.certificate_number} size="small"
            onChange={e => setForm(f => ({ ...f, certificate_number: e.target.value }))} />
          <Box sx={{ display: 'flex', gap: 1 }}>
            <TextField label="Issued Date" value={form.issued_at} size="small" type="date"
              slotProps={{ inputLabel: { shrink: true } }} sx={{ flex: 1 }}
              onChange={e => setForm(f => ({ ...f, issued_at: e.target.value }))} />
            <TextField label="Expiry Date" value={form.expires_at} size="small" type="date"
              slotProps={{ inputLabel: { shrink: true } }} sx={{ flex: 1 }}
              onChange={e => setForm(f => ({ ...f, expires_at: e.target.value }))} />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained"
            disabled={!form.user_id || !form.certification_type || createMut.isPending}
            onClick={() => createMut.mutate({
              user_id: form.user_id,
              certification_type: form.certification_type,
              issuing_body: form.issuing_body || undefined,
              certificate_number: form.certificate_number || undefined,
              issued_at: form.issued_at || undefined,
              expires_at: form.expires_at || undefined,
            })}>
            {createMut.isPending ? <CircularProgress size={18} /> : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>
      </Box>

      <FilterRail groups={filterGroups} storageKey="training-certifications" />
    </Box>
  )
}

// ── My Training tab (quiz taking) ────────────────────────────────────────────

function QuizDialog({ courseId, courseName, onClose }: {
  courseId: string; courseName: string; onClose: () => void
}) {
  const qc = useQueryClient()
  const [attemptId, setAttemptId] = useState<string | null>(null)
  const [questions, setQuestions] = useState<TrainingAttemptQuestion[]>([])
  const [answers, setAnswers] = useState<Record<string, number>>({})
  const [result, setResult] = useState<{ score: number; passed: boolean; correct_count: number; total_count: number } | null>(null)

  const { isLoading } = useQuery({
    queryKey: ['training-start-attempt', courseId],
    queryFn: async () => {
      const data = await startAttempt(courseId)
      setAttemptId(data.attempt_id)
      setQuestions(data.questions)
      const existing = await getAttempt(data.attempt_id)
      setAnswers(existing.answers ?? {})
      return data
    },
  })

  const answerMut = useMutation({
    mutationFn: ({ questionId, selectedIndex }: { questionId: string; selectedIndex: number }) =>
      answerQuestion(attemptId!, questionId, selectedIndex),
  })

  const submitMut = useMutation({
    mutationFn: () => submitAttempt(attemptId!),
    onSuccess: (data) => {
      setResult(data)
      qc.invalidateQueries({ queryKey: ['training-attempts'] })
      qc.invalidateQueries({ queryKey: ['training-records'] })
      qc.invalidateQueries({ queryKey: ['training-dashboard'] })
    },
  })

  const handleSelect = (questionId: string, selectedIndex: number) => {
    setAnswers(a => ({ ...a, [questionId]: selectedIndex }))
    answerMut.mutate({ questionId, selectedIndex })
  }

  const allAnswered = questions.length > 0 && questions.every(q => answers[q.id] !== undefined)

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{courseName}</DialogTitle>
      <DialogContent>
        {isLoading ? (
          <Skeleton height={200} />
        ) : result ? (
          <Alert severity={result.passed ? 'success' : 'warning'} sx={{ mb: 1 }}>
            <Typography fontWeight={700}>
              {result.passed ? 'Passed!' : 'Not Passed'} — {result.score}% ({result.correct_count}/{result.total_count} correct)
            </Typography>
            {result.passed && (
              <Typography variant="caption">A training record has been added to your history.</Typography>
            )}
          </Alert>
        ) : (
          <Stack divider={<Divider />} spacing={2}>
            {questions.map((q, i) => (
              <FormControl key={q.id}>
                <FormLabel sx={{ fontSize: '0.85rem', fontWeight: 600, color: 'text.primary' }}>
                  {i + 1}. {q.question_text}
                </FormLabel>
                <RadioGroup
                  value={answers[q.id] ?? ''}
                  onChange={e => handleSelect(q.id, Number(e.target.value))}
                >
                  {q.options.map((opt, oi) => (
                    <FormControlLabel key={oi} value={oi} control={<Radio size="small" />} label={opt} />
                  ))}
                </RadioGroup>
              </FormControl>
            ))}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{result ? 'Close' : 'Save & Exit'}</Button>
        {!result && (
          <Button
            variant="contained" disabled={!allAnswered || submitMut.isPending}
            onClick={() => submitMut.mutate()}
          >
            {submitMut.isPending ? <CircularProgress size={18} /> : 'Submit Quiz'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}

function MyTrainingTab() {
  const user = useAuthStore(s => s.user)
  const [quizCourse, setQuizCourse] = useState<{ id: string; name: string } | null>(null)

  const { data: courses = [], isLoading: coursesLoading } = useQuery({
    queryKey: ['training-courses'],
    queryFn: () => listCourses({ is_active: true }),
  })
  const quizCourses = courses.filter(c => c.question_count > 0)

  const { data: inProgress = [] } = useQuery({
    queryKey: ['training-attempts', 'in_progress', user?.id],
    queryFn: () => listAttempts({ guard_user_id: user?.id, attempt_status: 'in_progress' }),
    enabled: !!user?.id,
  })
  const { data: myResults = [], isLoading: resultsLoading } = useQuery({
    queryKey: ['training-attempts', 'submitted', user?.id],
    queryFn: () => listAttempts({ guard_user_id: user?.id, attempt_status: 'submitted' }),
    enabled: !!user?.id,
  })

  const inProgressByCourse = new Set(inProgress.map(a => a.course_id))

  return (
    <Box>
      <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1.5 }}>Available Quizzes</Typography>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {coursesLoading && [...Array(3)].map((_, i) => (
          <Grid size={{ xs: 12, sm: 6, md: 4 }} key={i}><Skeleton height={140} /></Grid>
        ))}
        {quizCourses.map(c => (
          <Grid size={{ xs: 12, sm: 6, md: 4 }} key={c.id}>
            <Card sx={{ height: '100%', bgcolor: 'rgba(255,255,255,0.03)' }}>
              <CardContent>
                <Chip label={c.category.replace(/_/g, ' ')} size="small"
                  sx={{ bgcolor: `${CATEGORY_COLOR[c.category] ?? '#888'}22`,
                        color: CATEGORY_COLOR[c.category] ?? '#888', fontSize: '0.7rem', mb: 1 }} />
                <Typography fontWeight={700} sx={{ mb: 0.5 }}>{c.name}</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
                  {c.question_count} question{c.question_count === 1 ? '' : 's'} · Pass at {c.passing_score}%
                </Typography>
                <Button
                  size="small" variant="contained" startIcon={<PlayArrowIcon fontSize="small" />}
                  onClick={() => setQuizCourse({ id: c.id, name: c.name })}
                >
                  {inProgressByCourse.has(c.id) ? 'Resume Quiz' : 'Start Quiz'}
                </Button>
              </CardContent>
            </Card>
          </Grid>
        ))}
        {!coursesLoading && quizCourses.length === 0 && (
          <Grid size={{ xs: 12 }}>
            <Typography color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>
              No quizzes available yet.
            </Typography>
          </Grid>
        )}
      </Grid>

      <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1.5 }}>My Results</Typography>
      <GlassCard sx={{ p: 2 }}>
        {resultsLoading ? <Skeleton height={60} /> : myResults.length === 0 ? (
          <Typography color="text.secondary" sx={{ textAlign: 'center', py: 2 }}>No quiz attempts yet.</Typography>
        ) : (
          <Stack divider={<Divider />} spacing={1}>
            {myResults.map(a => (
              <Box key={a.id} sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', py: 0.5 }}>
                <Box>
                  <Typography variant="body2" fontWeight={600}>{a.course_name}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {a.submitted_at ? new Date(a.submitted_at).toLocaleDateString() : ''}
                  </Typography>
                </Box>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2">{a.score}%</Typography>
                  <Chip
                    label={a.passed ? 'Passed' : 'Failed'} size="small"
                    icon={a.passed ? <CheckCircleIcon sx={{ fontSize: 14 }} /> : <CancelIcon sx={{ fontSize: 14 }} />}
                    sx={{ bgcolor: a.passed ? 'rgba(0,227,150,0.15)' : 'rgba(255,69,96,0.15)',
                          color: a.passed ? '#00E396' : '#FF4560', fontSize: '0.7rem' }}
                  />
                </Stack>
              </Box>
            ))}
          </Stack>
        )}
      </GlassCard>

      {quizCourse && (
        <QuizDialog
          courseId={quizCourse.id} courseName={quizCourse.name}
          onClose={() => setQuizCourse(null)}
        />
      )}
    </Box>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function TrainingPage() {
  const [tab, setTab] = useState(0)

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader pageKey="training" />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 3 }}>
        <SchoolIcon sx={{ color: '#6C63FF', fontSize: 28 }} />
      </Box>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 3,
        '& .MuiTab-root': { fontSize: '0.85rem', minWidth: 110 } }}>
        <Tab label="Overview" />
        <Tab label="My Training" />
        <Tab label="Courses" />
        <Tab label="Records" />
        <Tab label="Certifications" />
      </Tabs>

      {tab === 0 && <DashboardTab />}
      {tab === 1 && <MyTrainingTab />}
      {tab === 2 && <CoursesTab />}
      {tab === 3 && <RecordsTab />}
      {tab === 4 && <CertificationsTab />}
    </Box>
  )
}

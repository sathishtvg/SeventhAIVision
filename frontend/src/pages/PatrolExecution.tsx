/**
 * Virtual Patrolling — the duty officer's side.
 *
 * One camera at a time, in the order the schedule set. For each: capture a real
 * frame, answer the questions, add a note, move on.
 *
 * THE SCREEN NEVER DECIDES WHETHER A CAMERA IS DONE. It shows what is
 * outstanding and lets the officer try; the server refuses and says why. A
 * browser that made that call itself would be a browser anybody with curl could
 * skip, and a patrol is evidence.
 *
 * A FAILED SNAPSHOT IS NOT A DEAD END, AND NOT A SHRUG EITHER. The officer sees
 * the reason and a retry. What they cannot do is mark the camera done with no
 * image — a completed camera with no evidence is a patrol that proves nothing
 * while claiming otherwise.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, LinearProgress,
  MenuItem, Select, Skeleton, TextField, ToggleButton, ToggleButtonGroup,
  Typography, FormControl, InputLabel, Checkbox, FormControlLabel,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import CameraAltIcon from '@mui/icons-material/CameraAlt'
import RefreshIcon from '@mui/icons-material/Refresh'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { useAuthStore } from '@/store/auth'
import {
  myPatrols, startSession, currentCamera, captureSnapshot, submitAnswers,
  completeCamera, completePatrol, getSession, snapshotImageUrl,
} from '@/api/virtualPatrol'
import type { SessionQuestion } from '@/api/virtualPatrol'

type AnswerMap = Record<string, unknown>

const isAnswered = (q: SessionQuestion, answers: AnswerMap) => {
  const v = answers[q.id] ?? q.answer_json ?? q.answer_text
  if (Array.isArray(v)) return v.length > 0
  return v !== undefined && v !== null && String(v).trim() !== ''
}

// ── One question ─────────────────────────────────────────────────────────────

function QuestionField({
  question, value, onChange,
}: { question: SessionQuestion; value: unknown; onChange: (v: unknown) => void }) {
  const current = value ?? question.answer_json ?? question.answer_text ?? ''

  if (question.question_type === 'YES_NO' || question.question_type === 'PASS_FAIL') {
    const opts = question.question_type === 'YES_NO' ? ['YES', 'NO'] : ['PASS', 'FAIL']
    return (
      <ToggleButtonGroup exclusive size="small" value={current}
                         onChange={(_, v) => v && onChange(v)}>
        {opts.map((o) => (
          <ToggleButton key={o} value={o}
                        // The negative answer is the one that raises an
                        // exception, so it is coloured — the officer should see
                        // they are reporting a problem, not discover it later.
                        color={o === 'NO' || o === 'FAIL' ? 'error' : 'success'}>
            {o}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
    )
  }

  if (question.question_type === 'NUMBER') {
    return <TextField size="small" type="number" value={current}
                      onChange={(e) => onChange(e.target.value)} />
  }

  if (question.question_type === 'SINGLE_CHOICE') {
    return (
      <FormControl size="small" sx={{ minWidth: 200 }}>
        <InputLabel>Choose</InputLabel>
        <Select label="Choose" value={current}
                onChange={(e) => onChange(e.target.value)}>
          {(question.options ?? []).map((o) => (
            <MenuItem key={o} value={o}>{o}</MenuItem>
          ))}
        </Select>
      </FormControl>
    )
  }

  if (question.question_type === 'MULTI_CHOICE') {
    const selected: string[] = Array.isArray(current) ? current as string[] : []
    return (
      <Stack direction="row" sx={{ flexWrap: 'wrap' }}>
        {(question.options ?? []).map((o) => (
          <FormControlLabel key={o} label={o}
            control={
              <Checkbox size="small" checked={selected.includes(o)}
                        onChange={(e) =>
                          onChange(e.target.checked
                            ? [...selected, o]
                            : selected.filter((x) => x !== o))} />
            } />
        ))}
      </Stack>
    )
  }

  return <TextField size="small" fullWidth multiline value={current}
                    onChange={(e) => onChange(e.target.value)} />
}

// ── The active camera ────────────────────────────────────────────────────────

function CameraStep({ sessionId, onAdvance }: { sessionId: string; onAdvance: () => void }) {
  const qc = useQueryClient()
  const token = useAuthStore((s) => s.accessToken)
  const [answers, setAnswers] = useState<AnswerMap>({})
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [snapError, setSnapError] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['vp-current-camera', sessionId],
    queryFn: () => currentCamera(sessionId),
  })

  const camera = data?.camera
  const questions = useMemo(() => data?.questions ?? [], [data])

  // Reset per camera, or the previous camera's answers bleed into the next one.
  useEffect(() => { setAnswers({}); setNotes(''); setError(null); setSnapError(null) },
            [camera?.id])

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['vp-current-camera', sessionId] })
    qc.invalidateQueries({ queryKey: ['vp-session', sessionId] })
  }

  const snap = useMutation({
    mutationFn: () => captureSnapshot(sessionId, camera!.id),
    onSuccess: (r) => {
      setSnapError(r.ok ? null : (r.error ?? 'The capture failed.'))
      invalidate()
    },
  })

  const save = useMutation({
    mutationFn: () => submitAnswers(sessionId, camera!.id, {
      answers: Object.entries(answers).map(([session_question_id, answer]) =>
        ({ session_question_id, answer })),
      officer_notes: notes || undefined,
    }),
    onError: (e: never) =>
      setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Could not save the answers.'),
  })

  const finish = useMutation({
    mutationFn: async () => { await save.mutateAsync(); return completeCamera(sessionId, camera!.id) },
    onSuccess: () => { setError(null); invalidate(); onAdvance() },
    // The server is the authority on whether a camera is done. It answers with
    // the exact reason — a missing snapshot, N unanswered questions — and that
    // sentence is what the officer reads.
    onError: (e: never) =>
      setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'This camera cannot be completed yet.'),
  })

  if (isLoading) return <Skeleton height={320} />
  if (!camera) {
    return (
      <Alert severity="success" icon={<CheckCircleIcon />}>
        {data?.message ?? 'Every camera on this patrol is done.'}
      </Alert>
    )
  }

  const imgUrl = camera.snapshot_path ? snapshotImageUrl(sessionId, camera.id, token) : null
  const outstanding = questions.filter((q) => q.is_required && !isAnswered(q, answers)).length

  return (
    <Box>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'baseline', mb: 1 }}>
        <Typography variant="h6">
          {camera.sequence_no}. {camera.camera_name}
        </Typography>
        {camera.location && (
          <Typography variant="caption" color="text.secondary">{camera.location}</Typography>
        )}
      </Stack>

      {/* Evidence first: the snapshot is the point of the camera step. */}
      <Box sx={{
        border: '1px solid rgba(255,255,255,0.12)', borderRadius: 1, p: 1.5, mb: 2,
      }}>
        {imgUrl ? (
          <>
            <Box component="img" src={imgUrl} alt={`Snapshot of ${camera.camera_name}`}
                 sx={{ width: '100%', maxHeight: 380, objectFit: 'contain',
                       borderRadius: 1, display: 'block' }} />
            <Typography variant="caption" color="text.secondary">
              Captured {camera.snapshot_taken_at
                ? new Date(camera.snapshot_taken_at).toLocaleTimeString()
                : 'just now'}
            </Typography>
          </>
        ) : (
          <Stack spacing={1} sx={{ alignItems: 'flex-start', py: 3 }}>
            <Typography variant="body2" color="text.secondary">
              No snapshot captured yet. The patrol needs a real frame from this
              camera before it can move on.
            </Typography>
          </Stack>
        )}

        {(snapError || camera.snapshot_error) && (
          <Alert severity="warning" sx={{ mt: 1 }}>
            {snapError ?? camera.snapshot_error}
          </Alert>
        )}

        <Button sx={{ mt: 1 }} variant={imgUrl ? 'outlined' : 'contained'}
                startIcon={snap.isPending
                  ? <CircularProgress size={16} />
                  : imgUrl ? <RefreshIcon /> : <CameraAltIcon />}
                disabled={snap.isPending}
                onClick={() => snap.mutate()}>
          {snap.isPending ? 'Capturing…' : imgUrl ? 'Retake' : 'Capture snapshot'}
        </Button>
      </Box>

      <Divider sx={{ mb: 2 }} />

      <Stack spacing={2.5}>
        {questions.map((q) => (
          <Box key={q.id}>
            <Typography variant="body2" sx={{ mb: 0.75 }}>
              {q.sequence_no}. {q.question_text}
              {q.is_required && <Chip size="small" label="Required" sx={{ ml: 1 }} />}
            </Typography>
            <QuestionField question={q} value={answers[q.id]}
                           onChange={(v) => setAnswers({ ...answers, [q.id]: v })} />
          </Box>
        ))}
        {questions.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            No questions were configured for this camera.
          </Typography>
        )}
      </Stack>

      <TextField label="Officer notes" size="small" fullWidth multiline sx={{ mt: 2 }}
                 value={notes} onChange={(e) => setNotes(e.target.value)} />

      {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}

      <Stack direction="row" spacing={2} sx={{ mt: 2, alignItems: 'center' }}>
        <Button variant="contained" disabled={finish.isPending}
                onClick={() => finish.mutate()}>
          {finish.isPending ? 'Saving…' : 'Complete camera'}
        </Button>
        {/* Advisory, not a gate. The button stays enabled so the server gives
            the authoritative answer rather than the browser guessing. */}
        {(outstanding > 0 || !imgUrl) && (
          <Typography variant="caption" color="text.secondary">
            {!imgUrl && 'Snapshot still needed. '}
            {outstanding > 0 && `${outstanding} required question${outstanding === 1 ? '' : 's'} left.`}
          </Typography>
        )}
      </Stack>
    </Box>
  )
}

// ── A running patrol ─────────────────────────────────────────────────────────

function ActivePatrol({ sessionId, onExit }: { sessionId: string; onExit: () => void }) {
  const qc = useQueryClient()
  const { data: session } = useQuery({
    queryKey: ['vp-session', sessionId], queryFn: () => getSession(sessionId),
  })
  const finishPatrol = useMutation({
    mutationFn: () => completePatrol(sessionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vp-my-patrols'] })
      onExit()
    },
  })

  const done = session?.completed_camera_count ?? 0
  const total = session?.camera_count ?? 0
  const allDone = total > 0 && done >= total

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', mb: 1 }}>
        <Box>
          <Typography variant="h6">{session?.schedule_name}</Typography>
          <Typography variant="caption" color="text.secondary">
            {session?.site_name} · {session?.patrol_number}
          </Typography>
        </Box>
        <Chip label={`${done} / ${total} cameras`} />
      </Stack>
      <LinearProgress variant="determinate"
                      value={total ? (done / total) * 100 : 0}
                      sx={{ mb: 3, height: 6, borderRadius: 3 }} />

      <CameraStep sessionId={sessionId}
                  onAdvance={() =>
                    qc.invalidateQueries({ queryKey: ['vp-session', sessionId] })} />

      {allDone && (
        <Box sx={{ mt: 3 }}>
          <Divider sx={{ mb: 2 }} />
          <Button variant="contained" color="success"
                  startIcon={<CheckCircleIcon />}
                  disabled={finishPatrol.isPending}
                  onClick={() => finishPatrol.mutate()}>
            Complete patrol
          </Button>
        </Box>
      )}
    </Box>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function PatrolExecutionPage() {
  const qc = useQueryClient()
  const [active, setActive] = useState<string | null>(null)
  const { data: patrols, isLoading } = useQuery({
    queryKey: ['vp-my-patrols'], queryFn: () => myPatrols(),
  })
  const start = useMutation({
    mutationFn: (id: string) => startSession(id),
    onSuccess: (_r, id) => {
      qc.invalidateQueries({ queryKey: ['vp-my-patrols'] })
      setActive(id)
    },
  })

  return (
    <Box>
      <PageHeader pageKey="my-patrols" />
      <GlassCard>
        {active ? (
          <ActivePatrol sessionId={active} onExit={() => setActive(null)} />
        ) : (
          <Box sx={{ p: 3 }}>
            {isLoading ? <Skeleton height={160} /> : !patrols?.length ? (
              <Alert severity="info">
                You have no patrols waiting. One will appear here when it is due.
              </Alert>
            ) : (
              <Stack spacing={1.5}>
                {patrols.map((p) => (
                  <Stack key={p.id} direction="row"
                         sx={{ alignItems: 'center', justifyContent: 'space-between',
                               border: '1px solid rgba(255,255,255,0.12)',
                               borderRadius: 1, p: 1.5 }}>
                    <Box>
                      <Typography variant="body2">{p.schedule_name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {p.site_name} · due {new Date(p.scheduled_for).toLocaleString()}
                        {' · '}{p.completed_camera_count}/{p.camera_count} cameras
                      </Typography>
                    </Box>
                    <Button variant="contained" startIcon={<PlayArrowIcon />}
                            onClick={() => start.mutate(p.id)}>
                      {p.status === 'SCHEDULED' ? 'Start' : 'Continue'}
                    </Button>
                  </Stack>
                ))}
              </Stack>
            )}
          </Box>
        )}
      </GlassCard>
    </Box>
  )
}

export default function PatrolExecutionGuarded() {
  return (
    <PermissionGuard permission="vpatrol:execute">
      <PatrolExecutionPage />
    </PermissionGuard>
  )
}

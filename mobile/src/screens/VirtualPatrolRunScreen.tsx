/**
 * Running a virtual patrol: one camera at a time.
 *
 * THE SERVER DECIDES WHERE WE ARE, not this screen. Every step asks
 * current-camera again rather than walking a local index, because a patrol can
 * be picked up on another device, and an officer who reopens the app mid-patrol
 * must land on the camera that still needs attention rather than start again.
 *
 * A FAILED SNAPSHOT BLOCKS THE CAMERA, and this screen used to claim otherwise.
 * The server refuses to complete a camera whose snapshot failed and left no
 * image — "a completed camera with no image is a patrol that proves nothing
 * while claiming otherwise" — so an officer who filled in every answer got a
 * dialog at the end telling them to retry. The reason is shown up front now,
 * and Save is disabled until a frame exists, which is the same rule stated
 * before the work instead of after it.
 *
 * REQUIRED QUESTIONS ARE ENFORCED HERE ONLY AS A COURTESY. The server validates
 * the same rules when answers land; this screen just avoids a round trip that
 * ends in a red toast.
 */
import { useMemo, useState } from 'react'
import {
  ActivityIndicator, Image, Pressable, ScrollView, StyleSheet, Text,
  TextInput, View, Alert as RNAlert,
} from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useNavigation, useRoute, type RouteProp } from '@react-navigation/native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  captureSnapshot, completeCamera, completePatrol, getCurrentCamera, getPatrolSession,
  snapshotUrl, startPatrol, submitAnswers, markCameraUnavailable, isNegative, VERDICT_OPTIONS,
  type AnswerIn, type SessionQuestion,
} from '@/api/virtualPatrol'
import type { PatrolStackParamList } from '@/navigation'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, radius, spacing } from '@/theme'

type ScreenRoute = RouteProp<PatrolStackParamList, 'VirtualPatrolRun'>

/** Answers keyed by question id, as the widgets produce them. */
type AnswerMap = Record<string, unknown>

/** Which required questions are still unanswered. Pure, and exported for the
 *  tests: "required" is the rule an officer will meet at the end of a patrol,
 *  and getting it wrong either blocks a finished patrol or lets an empty one
 *  through. */
export function unansweredRequired(questions: SessionQuestion[], answers: AnswerMap): string[] {
  return questions
    .filter((q) => q.is_required)
    .filter((q) => {
      const a = answers[q.id]
      if (a === undefined || a === null) return true
      if (typeof a === 'string') return a.trim() === ''
      // MULTI_CHOICE sends a list, and the server counts an empty one as empty.
      if (Array.isArray(a)) return a.length === 0
      return false
    })
    .map((q) => q.id)
}

function QuestionField({
  question, value, onChange,
}: { question: SessionQuestion; value: unknown; onChange: (v: unknown) => void }) {
  const verdict = VERDICT_OPTIONS[question.question_type]
  const options = question.options ?? []
  const chosen = Array.isArray(value) ? value : []

  return (
    <View style={styles.question}>
      <Text style={styles.questionText}>
        {question.question_text}
        {question.is_required && <Text style={styles.required}> *</Text>}
      </Text>

      {/* YES_NO and PASS_FAIL: two explicit buttons sending the exact strings
          the server validates against — str(answer).upper() in ('YES','NO') or
          ('PASS','FAIL'). A switch was tried first and was wrong twice over: it
          sent a boolean, which the server rejects, and it started in the off
          position, so an unanswered question read as a deliberate "No". */}
      {verdict && (
        <View style={styles.choiceRow}>
          {verdict.map((v) => (
            <Pressable
              key={v}
              style={[
                styles.choice,
                value === v && (isNegative(question.question_type, v) ? styles.choiceNo : styles.choiceYes),
              ]}
              onPress={() => onChange(v)}
              accessibilityRole="radio"
              accessibilityState={{ selected: value === v }}
            >
              <Text style={[styles.choiceText, value === v && styles.choiceTextActive]}>
                {v[0] + v.slice(1).toLowerCase()}
              </Text>
            </Pressable>
          ))}
        </View>
      )}

      {question.question_type === 'SINGLE_CHOICE' && (
        <View style={styles.choiceRow}>
          {options.map((opt) => (
            <Pressable
              key={opt}
              style={[styles.choice, value === opt && styles.choiceActive]}
              onPress={() => onChange(opt)}
            >
              <Text style={[styles.choiceText, value === opt && styles.choiceTextActive]}>{opt}</Text>
            </Pressable>
          ))}
        </View>
      )}

      {/* MULTI_CHOICE answers travel as a list; the server rejects anything else. */}
      {question.question_type === 'MULTI_CHOICE' && (
        <View style={styles.choiceRow}>
          {options.map((opt) => {
            const on = chosen.includes(opt)
            return (
              <Pressable
                key={opt}
                style={[styles.choice, on && styles.choiceActive]}
                onPress={() => onChange(on ? chosen.filter((o) => o !== opt) : [...chosen, opt])}
                accessibilityRole="checkbox"
                accessibilityState={{ checked: on }}
              >
                <Text style={[styles.choiceText, on && styles.choiceTextActive]}>{opt}</Text>
              </Pressable>
            )
          })}
        </View>
      )}

      {(question.question_type === 'TEXT' || question.question_type === 'NUMBER') && (
        <TextInput
          style={styles.input}
          value={value == null ? '' : String(value)}
          onChangeText={(t) => onChange(question.question_type === 'NUMBER' ? (t === '' ? null : Number(t)) : t)}
          keyboardType={question.question_type === 'NUMBER' ? 'numeric' : 'default'}
          placeholder={question.question_type === 'NUMBER' ? '0' : 'Your answer'}
          placeholderTextColor={colors.textDisabled}
          multiline={question.question_type === 'TEXT'}
        />
      )}
    </View>
  )
}

export function VirtualPatrolRunScreen() {
  const { params } = useRoute<ScreenRoute>()
  const nav = useNavigation()
  const qc = useQueryClient()
  const accessToken = useAuthStore((s) => s.accessToken)
  const sessionId = params.sessionId

  const [answers, setAnswers] = useState<AnswerMap>({})
  const [notes, setNotes] = useState('')

  const { data: session } = useQuery({
    queryKey: ['virtual-patrol', sessionId],
    queryFn: () => getPatrolSession(sessionId),
  })

  const { data: current, isLoading, refetch } = useQuery({
    queryKey: ['virtual-patrol-current', sessionId],
    queryFn: () => getCurrentCamera(sessionId),
  })

  /** Starting is idempotent on the server for an already-running patrol, so it
   *  is safe to offer whenever the session is still pending. */
  const startMut = useMutation({
    mutationFn: () => startPatrol(sessionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['virtual-patrol', sessionId] })
      void refetch()
    },
    onError: () => RNAlert.alert('Error', 'Could not start this patrol.'),
  })

  const snapshotMut = useMutation({
    mutationFn: () => captureSnapshot(sessionId, current!.camera!.id),
    onSuccess: () => { void refetch() },
    onError: () => RNAlert.alert('Error', 'Could not reach the server to take a snapshot.'),
  })

  const nextCameraMut = useMutation({
    mutationFn: async () => {
      const cameraId = current!.camera!.id
      const payload: AnswerIn[] = Object.entries(answers).map(([id, answer]) => ({
        session_question_id: id, answer,
      }))
      if (payload.length > 0 || notes.trim()) {
        await submitAnswers(sessionId, cameraId, payload, notes.trim() || undefined)
      }
      return completeCamera(sessionId, cameraId)
    },
    onSuccess: () => {
      setAnswers({})
      setNotes('')
      qc.invalidateQueries({ queryKey: ['virtual-patrol', sessionId] })
      void refetch()
    },
    onError: (err: any) => {
      const detail = err?.response?.data?.detail
      RNAlert.alert('Not saved', typeof detail === 'string' ? detail : 'Could not save this camera.')
    },
  })

  /** "This camera is not working." Records it as unavailable with the reason
   *  and moves to the next one; the patrol ends PARTIALLY_COMPLETED. */
  const unavailableMut = useMutation({
    mutationFn: () => markCameraUnavailable(sessionId, current!.camera!.id,
                                            notes.trim() || undefined),
    onSuccess: () => {
      setAnswers({})
      setNotes('')
      qc.invalidateQueries({ queryKey: ['virtual-patrol', sessionId] })
      void refetch()
    },
    onError: (err: any) => {
      const detail = err?.response?.data?.detail
      RNAlert.alert('Not recorded', typeof detail === 'string' ? detail : 'Could not report this camera.')
    },
  })

  const reportNotWorking = () =>
    RNAlert.alert(
      'Camera not working?',
      'This camera will be recorded as not working, with the reason, and the patrol will be marked partially completed.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Report', style: 'destructive', onPress: () => unavailableMut.mutate() },
      ],
    )

  const finishMut = useMutation({
    mutationFn: () => completePatrol(sessionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-virtual-patrols'] })
      nav.goBack()
    },
    onError: (err: any) => {
      const detail = err?.response?.data?.detail
      RNAlert.alert('Not finished', typeof detail === 'string' ? detail : 'Could not complete the patrol.')
    },
  })

  const questions = current?.questions ?? []
  const missing = useMemo(() => unansweredRequired(questions, answers), [questions, answers])

  if (isLoading) {
    return <View style={styles.centre}><ActivityIndicator color={colors.primary} /></View>
  }

  const camera = current?.camera ?? null
  const notStarted = session?.status === 'SCHEDULED'
  // Mirrors the server's blocker: snapshot_error and no snapshot_path.
  const needsSnapshot = !!camera?.snapshot_error && !camera?.snapshot_path

  // Every camera done: the patrol itself still has to be closed, which is what
  // writes the report and stamps the completion time.
  if (!camera) {
    return (
      <View style={styles.centre}>
        <Ionicons name="checkmark-circle-outline" size={52} color={colors.success} />
        <Text style={styles.doneTitle}>{current?.message ?? 'Every camera on this patrol is done.'}</Text>
        {session?.status !== 'COMPLETED' ? (
          <Pressable
            style={[styles.primaryBtn, finishMut.isPending && styles.btnDisabled]}
            onPress={() => finishMut.mutate()}
            disabled={finishMut.isPending}
          >
            <Text style={styles.primaryBtnText}>
              {finishMut.isPending ? 'Finishing…' : 'Complete Patrol'}
            </Text>
          </Pressable>
        ) : (
          <Text style={styles.meta}>This patrol is complete.</Text>
        )}
      </View>
    )
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.headerRow}>
        <Text style={styles.cameraName}>{camera.camera_name}</Text>
        <Text style={styles.meta}>
          {session ? `${session.completed_camera_count + 1} of ${session.camera_count}` : ''}
        </Text>
      </View>
      {!!camera.location && <Text style={styles.meta}>{camera.location}</Text>}

      {notStarted && (
        <Pressable
          style={[styles.primaryBtn, startMut.isPending && styles.btnDisabled]}
          onPress={() => startMut.mutate()}
          disabled={startMut.isPending}
        >
          <Ionicons name="play" size={18} color="#fff" />
          <Text style={styles.primaryBtnText}>Start Patrol</Text>
        </Pressable>
      )}

      {/* The frame, the reason it is missing, or an invitation to take one. */}
      <View style={styles.snapshotBox}>
        {camera.snapshot_path && accessToken ? (
          <Image
            source={{ uri: snapshotUrl(sessionId, camera.id, accessToken) }}
            style={styles.snapshot}
            resizeMode="contain"
          />
        ) : camera.snapshot_error ? (
          <View style={styles.snapshotEmpty}>
            <Ionicons name="warning-outline" size={28} color={colors.warning} />
            <Text style={styles.snapshotError}>{camera.snapshot_error}</Text>
            <Text style={styles.meta}>Retake the snapshot before completing this camera.</Text>
          </View>
        ) : (
          <View style={styles.snapshotEmpty}>
            <Ionicons name="camera-outline" size={28} color={colors.textDisabled} />
            <Text style={styles.meta}>No snapshot taken yet.</Text>
          </View>
        )}
      </View>

      <Pressable
        style={[styles.secondaryBtn, snapshotMut.isPending && styles.btnDisabled]}
        onPress={() => snapshotMut.mutate()}
        disabled={snapshotMut.isPending || notStarted}
      >
        <Ionicons name="camera" size={18} color={colors.primary} />
        <Text style={styles.secondaryBtnText}>
          {snapshotMut.isPending ? 'Capturing…' : camera.snapshot_path ? 'Retake Snapshot' : 'Take Snapshot'}
        </Text>
      </Pressable>

      {questions.map((q) => (
        <QuestionField
          key={q.id}
          question={q}
          value={answers[q.id]}
          onChange={(v) => setAnswers((prev) => ({ ...prev, [q.id]: v }))}
        />
      ))}

      <Text style={styles.label}>Officer notes</Text>
      <TextInput
        style={[styles.input, styles.notes]}
        value={notes}
        onChangeText={setNotes}
        placeholder="Anything worth recording about this camera"
        placeholderTextColor={colors.textDisabled}
        multiline
      />

      <Pressable
        style={[styles.primaryBtn,
                (missing.length > 0 || needsSnapshot || nextCameraMut.isPending) && styles.btnDisabled]}
        onPress={() => nextCameraMut.mutate()}
        disabled={missing.length > 0 || needsSnapshot || nextCameraMut.isPending || notStarted}
      >
        <Text style={styles.primaryBtnText}>
          {nextCameraMut.isPending ? 'Saving…' : 'Save and Next Camera'}
        </Text>
      </Pressable>
      {/* The way past a camera that will not produce an image. Offered only
          when capture has actually failed, so it cannot become a quiet way to
          skip a working camera — the server refuses that too. */}
      {needsSnapshot && (
        <Pressable
          style={[styles.dangerBtn, unavailableMut.isPending && styles.btnDisabled]}
          onPress={reportNotWorking}
          disabled={unavailableMut.isPending || notStarted}
        >
          <Ionicons name="videocam-off-outline" size={18} color={colors.error} />
          <Text style={styles.dangerBtnText}>
            {unavailableMut.isPending ? 'Recording…' : 'Camera Not Working'}
          </Text>
        </Pressable>
      )}

      {needsSnapshot ? (
        <Text style={styles.hint}>
          Retake the snapshot, or report the camera as not working to carry on.
        </Text>
      ) : missing.length > 0 ? (
        <Text style={styles.hint}>
          {missing.length} required question{missing.length === 1 ? '' : 's'} still to answer.
        </Text>
      ) : null}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.md, paddingBottom: spacing.xl },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.md, backgroundColor: colors.background },
  headerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  cameraName: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, flex: 1 },
  meta: { fontSize: fontSize.sm, color: colors.textSecondary },
  doneTitle: { fontSize: fontSize.md, fontWeight: '700', color: colors.text, textAlign: 'center' },
  snapshotBox: {
    backgroundColor: '#000', borderRadius: radius.md, borderWidth: 1, borderColor: colors.cardBorder,
    marginTop: spacing.md, overflow: 'hidden', minHeight: 200,
    alignItems: 'center', justifyContent: 'center',
  },
  snapshot: { width: '100%', height: 220 },
  snapshotEmpty: { alignItems: 'center', gap: spacing.xs, padding: spacing.lg },
  snapshotError: { color: colors.warning, fontSize: fontSize.sm, textAlign: 'center' },
  question: { marginTop: spacing.md },
  questionText: { fontSize: fontSize.sm, fontWeight: '600', color: colors.text },
  required: { color: colors.error },
  choiceYes: { backgroundColor: colors.success, borderColor: colors.success },
  choiceNo: { backgroundColor: colors.error, borderColor: colors.error },
  choiceRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.xs },
  choice: {
    borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.sm,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs,
  },
  choiceActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  choiceText: { color: colors.textSecondary, fontSize: fontSize.sm },
  choiceTextActive: { color: '#fff', fontWeight: '700' },
  label: { fontSize: fontSize.sm, fontWeight: '600', color: colors.text, marginTop: spacing.md },
  input: {
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.cardBorder,
    borderRadius: radius.sm, padding: spacing.sm, color: colors.text, marginTop: spacing.xs,
  },
  notes: { minHeight: 70, textAlignVertical: 'top' },
  primaryBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs,
    backgroundColor: colors.primary, borderRadius: radius.sm,
    paddingVertical: spacing.md, marginTop: spacing.lg,
  },
  primaryBtnText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  secondaryBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs,
    borderWidth: 1, borderColor: colors.primary, borderRadius: radius.sm,
    paddingVertical: spacing.sm, marginTop: spacing.sm,
  },
  secondaryBtnText: { color: colors.primary, fontWeight: '700', fontSize: fontSize.sm },
  dangerBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs,
    borderWidth: 1, borderColor: colors.error, borderRadius: radius.sm,
    paddingVertical: spacing.sm, marginTop: spacing.sm,
  },
  dangerBtnText: { color: colors.error, fontWeight: '700', fontSize: fontSize.sm },
  btnDisabled: { opacity: 0.5 },
  hint: { color: colors.warning, fontSize: fontSize.xs, textAlign: 'center', marginTop: spacing.xs },
})

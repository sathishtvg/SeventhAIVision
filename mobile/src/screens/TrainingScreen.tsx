/**
 * Guard SOP training — course list, quiz taking, and past results.
 *
 * Answers are persisted to the server one at a time rather than batched at
 * submit. A guard takes this between rounds and gets interrupted constantly;
 * if the app is backgrounded and killed mid-quiz, everything answered so far
 * survives and "Resume" picks up exactly where they left off. Batching would
 * silently discard that work.
 */
import React, { useState } from 'react'
import {
  ActivityIndicator, Alert, FlatList, Modal, Pressable,
  ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import {
  answerQuestion, getAttempt, listCourses, listMyAttempts, startAttempt, submitAttempt,
  type SubmitResult, type TrainingAttempt, type TrainingAttemptQuestion, type TrainingCourse,
} from '@/api/training'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

interface QuizState {
  attemptId: string
  courseName: string
  passingScore: number
  questions: TrainingAttemptQuestion[]
  answers: Record<string, number>
}

export function TrainingScreen() {
  const qc = useQueryClient()
  const [quiz, setQuiz] = useState<QuizState | null>(null)
  const [result, setResult] = useState<(SubmitResult & { courseName: string }) | null>(null)

  const coursesQuery = useQuery({ queryKey: ['training-courses'], queryFn: listCourses })
  const attemptsQuery = useQuery({
    queryKey: ['my-training-attempts'],
    queryFn: () => listMyAttempts(),
  })
  const { isLoading, isError, refetch } = coursesQuery
  const courses: TrainingCourse[] = coursesQuery.data ?? []
  const attempts: TrainingAttempt[] = attemptsQuery.data ?? []

  // Only courses with a question bank are takeable; the rest are logged
  // manually by an admin (e.g. an in-person fire drill) and would dead-end here.
  const takeable = courses.filter((c) => c.question_count > 0)
  const inProgress = new Map<string, string>(
    attempts
      .filter((a) => a.status === 'in_progress')
      .map((a) => [a.course_id, a.id] as [string, string]),
  )

  const open = useMutation({
    mutationFn: async (course: TrainingCourse) => {
      const existing = inProgress.get(course.id)
      // Resume rather than restart. POSTing again would return the same
      // attempt (the server refuses to duplicate one), but only getAttempt
      // returns the answers already saved — so a resume must go through it or
      // the guard's earlier answers would appear blank.
      if (existing) {
        const d = await getAttempt(existing)
        return {
          attemptId: d.attempt_id,
          questions: d.questions,
          answers: d.answers ?? {},
          course,
        }
      }
      const d = await startAttempt(course.id)
      return {
        attemptId: d.attempt_id,
        questions: d.questions,
        answers: {} as Record<string, number>,
        course,
      }
    },
    onSuccess: ({ attemptId, questions, answers, course }) => {
      setQuiz({
        attemptId,
        courseName: course.name,
        passingScore: course.passing_score,
        questions,
        answers,
      })
    },
    onError: () => Alert.alert('Could not open quiz', 'Please try again.'),
  })

  const answer = useMutation({
    mutationFn: ({ questionId, index }: { questionId: string; index: number }) =>
      answerQuestion(quiz!.attemptId, questionId, index),
  })

  const submit = useMutation({
    mutationFn: () => submitAttempt(quiz!.attemptId),
    onSuccess: (r) => {
      setResult({ ...r, courseName: quiz!.courseName })
      setQuiz(null)
      qc.invalidateQueries({ queryKey: ['my-training-attempts'] })
      qc.invalidateQueries({ queryKey: ['training-courses'] })
    },
    onError: () => Alert.alert('Submit failed', 'Your answers are saved — try submitting again.'),
  })

  function pick(questionId: string, index: number) {
    setQuiz((q) => (q ? { ...q, answers: { ...q.answers, [questionId]: index } } : q))
    answer.mutate({ questionId, index })
  }

  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (isError) {
    // Distinct from the empty state on purpose: "no courses" and "we couldn't
    // load your courses" mean very different things to someone who has been
    // told to complete training today.
    return (
      <View style={styles.center}>
        <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
        <Text style={styles.errorText}>Couldn’t load training</Text>
        <Pressable style={styles.retryBtn} onPress={() => refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  const answeredCount = quiz ? Object.keys(quiz.answers).length : 0
  const allAnswered = quiz ? answeredCount === quiz.questions.length : false

  return (
    <View style={styles.container}>
      <FlatList
        data={takeable}
        keyExtractor={(c) => c.id}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <Card><Text style={styles.muted}>No training assigned right now.</Text></Card>
        }
        ListFooterComponent={
          attempts.length > 0 ? (
            <View style={styles.footer}>
              <Text style={styles.sectionTitle}>My Results</Text>
              {attempts
                .filter((a) => a.status === 'submitted')
                .map((a) => (
                  <Card key={a.id}>
                    <View style={styles.row}>
                      <Text style={styles.courseName}>{a.course_name}</Text>
                      <View
                        style={[
                          styles.badge,
                          { backgroundColor: a.passed ? colors.success : colors.error },
                        ]}
                      >
                        <Text style={styles.badgeText}>
                          {a.passed ? 'Passed' : 'Failed'} {a.score ?? 0}%
                        </Text>
                      </View>
                    </View>
                  </Card>
                ))}
            </View>
          ) : null
        }
        renderItem={({ item }) => {
          const resuming = inProgress.has(item.id)
          return (
            <Card>
              <Text style={styles.courseName}>{item.name}</Text>
              {!!item.description && <Text style={styles.muted}>{item.description}</Text>}
              <View style={styles.metaRow}>
                <Text style={styles.meta}>{item.category}</Text>
                <Text style={styles.meta}>{item.question_count} questions</Text>
                <Text style={styles.meta}>Pass {item.passing_score}%</Text>
              </View>
              <Pressable
                style={[styles.primaryBtn, resuming && styles.resumeBtn]}
                onPress={() => open.mutate(item)}
                disabled={open.isPending}
              >
                <Text style={styles.primaryText}>
                  {resuming ? 'Resume Quiz' : 'Start Quiz'}
                </Text>
              </Pressable>
            </Card>
          )
        }}
      />

      {/* Quiz */}
      <Modal visible={!!quiz} animationType="slide" onRequestClose={() => setQuiz(null)}>
        <View style={styles.modal}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle} numberOfLines={1}>{quiz?.courseName}</Text>
            <Pressable onPress={() => setQuiz(null)} hitSlop={12}>
              <Ionicons name="close" size={26} color={colors.text} />
            </Pressable>
          </View>
          <Text style={styles.progress}>
            {answeredCount} of {quiz?.questions.length ?? 0} answered
          </Text>
          <ScrollView contentContainerStyle={styles.quizBody}>
            {quiz?.questions.map((q, qi) => (
              <Card key={q.id}>
                <Text style={styles.question}>{qi + 1}. {q.question_text}</Text>
                {q.options.map((opt, oi) => {
                  const selected = quiz.answers[q.id] === oi
                  return (
                    <Pressable
                      key={oi}
                      style={[styles.option, selected && styles.optionSelected]}
                      onPress={() => pick(q.id, oi)}
                    >
                      <Ionicons
                        name={selected ? 'radio-button-on' : 'radio-button-off'}
                        size={20}
                        color={selected ? colors.primary : colors.textSecondary}
                      />
                      <Text style={styles.optionText}>{opt}</Text>
                    </Pressable>
                  )
                })}
              </Card>
            ))}
            <Pressable
              style={[styles.primaryBtn, !allAnswered && styles.disabledBtn]}
              onPress={() => submit.mutate()}
              disabled={!allAnswered || submit.isPending}
            >
              <Text style={styles.primaryText}>
                {submit.isPending ? 'Submitting…' : 'Submit Quiz'}
              </Text>
            </Pressable>
            {!allAnswered && (
              <Text style={styles.hint}>Answer every question to submit.</Text>
            )}
          </ScrollView>
        </View>
      </Modal>

      {/* Result */}
      <Modal visible={!!result} transparent animationType="fade">
        <View style={styles.resultBackdrop}>
          <View style={styles.resultCard}>
            <Ionicons
              name={result?.passed ? 'checkmark-circle' : 'close-circle'}
              size={56}
              color={result?.passed ? colors.success : colors.error}
            />
            <Text style={styles.resultScore}>{result?.score}%</Text>
            <Text style={styles.resultLabel}>
              {result?.passed ? 'Passed' : 'Not passed'} — {result?.correct_count} of{' '}
              {result?.total_count} correct
            </Text>
            <Text style={styles.muted}>{result?.courseName}</Text>
            <Pressable style={styles.primaryBtn} onPress={() => setResult(null)}>
              <Text style={styles.primaryText}>Done</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  list: { padding: spacing.md, gap: spacing.sm },
  footer: { marginTop: spacing.lg, gap: spacing.sm },
  sectionTitle: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700', marginBottom: spacing.xs },
  courseName: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  muted: { color: colors.textSecondary, fontSize: fontSize.sm, marginTop: 2 },
  metaRow: { flexDirection: 'row', gap: spacing.md, marginTop: spacing.xs, flexWrap: 'wrap' },
  meta: { color: colors.textSecondary, fontSize: fontSize.xs },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  badge: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.sm },
  badgeText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700' },
  primaryBtn: {
    backgroundColor: colors.primary, borderRadius: radius.md,
    paddingVertical: spacing.sm, alignItems: 'center', marginTop: spacing.sm,
  },
  resumeBtn: { backgroundColor: colors.warning },
  disabledBtn: { opacity: 0.4 },
  primaryText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  errorText: { color: colors.text, fontSize: fontSize.md },
  retryBtn: {
    borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.xs,
  },
  retryText: { color: colors.primary, fontWeight: '600' },
  modal: { flex: 1, backgroundColor: colors.background, paddingTop: spacing.xl },
  modalHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: spacing.md, paddingBottom: spacing.xs,
  },
  modalTitle: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700', flex: 1 },
  progress: { color: colors.textSecondary, fontSize: fontSize.sm, paddingHorizontal: spacing.md },
  quizBody: { padding: spacing.md, gap: spacing.sm, paddingBottom: spacing.xl },
  question: { color: colors.text, fontSize: fontSize.md, fontWeight: '600', marginBottom: spacing.xs },
  option: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    paddingVertical: spacing.xs, paddingHorizontal: spacing.xs, borderRadius: radius.sm,
  },
  optionSelected: { backgroundColor: colors.card },
  optionText: { color: colors.text, fontSize: fontSize.sm, flex: 1 },
  hint: { color: colors.textSecondary, fontSize: fontSize.xs, textAlign: 'center', marginTop: spacing.xs },
  resultBackdrop: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.6)',
    alignItems: 'center', justifyContent: 'center', padding: spacing.lg,
  },
  resultCard: {
    backgroundColor: colors.surface, borderRadius: radius.lg,
    padding: spacing.lg, alignItems: 'center', gap: spacing.xs, width: '100%',
  },
  resultScore: { color: colors.text, fontSize: 40, fontWeight: '800' },
  resultLabel: { color: colors.text, fontSize: fontSize.md, textAlign: 'center' },
})

/**
 * Guard-facing SOP training.
 *
 * Only the endpoints a guard actually needs on a phone: list courses, take or
 * resume a quiz, see past results. Course authoring and the question bank stay
 * web-only — that is an admin desk task, not something done one-handed on a
 * shift. Shapes mirror frontend/src/api/training.ts so the two clients cannot
 * drift apart silently.
 */
import { apiClient } from './client'

export interface TrainingCourse {
  id: string
  name: string
  description: string | null
  category: string
  duration_hours: number | null
  passing_score: number
  validity_months: number | null
  is_active: boolean
  question_count: number
}

export interface TrainingAttemptQuestion {
  id: string
  question_text: string
  options: string[]
  points: number
}

export interface TrainingAttempt {
  id: string
  course_id: string
  status: 'in_progress' | 'submitted'
  score: number | null
  passed: boolean | null
  started_at: string
  submitted_at: string | null
  course_name: string
}

export interface AttemptDetail {
  attempt_id: string
  status: string
  answers: Record<string, number>
  questions: TrainingAttemptQuestion[]
}

export interface SubmitResult {
  score: number
  passed: boolean
  correct_count: number
  total_count: number
}

export const listCourses = () =>
  apiClient
    .get<TrainingCourse[]>('/api/v1/training/courses', { params: { is_active: true } })
    .then((r) => r.data)

/** Server returns an existing in-progress attempt rather than duplicating one. */
export const startAttempt = (courseId: string) =>
  apiClient
    .post<{ attempt_id: string; questions: TrainingAttemptQuestion[] }>(
      `/api/v1/training/courses/${courseId}/attempts`,
    )
    .then((r) => r.data)

export const getAttempt = (attemptId: string) =>
  apiClient.get<AttemptDetail>(`/api/v1/training/attempts/${attemptId}`).then((r) => r.data)

/**
 * Answers are saved one at a time rather than batched on submit: a guard on a
 * phone gets interrupted mid-quiz constantly, and the server merges each answer
 * into the attempt's JSONB so progress survives the app being backgrounded.
 */
export const answerQuestion = (attemptId: string, questionId: string, selectedIndex: number) =>
  apiClient
    .put(`/api/v1/training/attempts/${attemptId}/answer`, {
      question_id: questionId,
      selected_index: selectedIndex,
    })
    .then((r) => r.data)

export const submitAttempt = (attemptId: string) =>
  apiClient
    .post<SubmitResult>(`/api/v1/training/attempts/${attemptId}/submit`)
    .then((r) => r.data)

/** Guard-scoped server-side — a guard only ever sees their own attempts. */
export const listMyAttempts = (params?: { status?: string }) =>
  apiClient.get<TrainingAttempt[]>('/api/v1/training/attempts', { params }).then((r) => r.data)

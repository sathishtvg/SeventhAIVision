import { apiClient as client } from './client'

export interface TrainingCourse {
  id: string
  name: string
  description: string | null
  category: string
  duration_hours: number | null
  passing_score: number
  validity_months: number | null
  is_active: boolean
  created_at: string
  updated_at: string
  question_count: number
}

export interface TrainingQuestion {
  id: string
  course_id: string
  question_text: string
  options: string[]
  correct_index: number
  points: number
  sort_order: number
  created_at: string
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
  guard_user_id: string
  status: 'in_progress' | 'submitted'
  score: number | null
  passed: boolean | null
  started_at: string
  submitted_at: string | null
  course_name: string
  guard_name: string
}

export interface TrainingRecord {
  id: string
  user_id: string
  course_id: string
  guard_name: string
  guard_email: string
  course_name: string
  course_category: string
  course_passing_score: number
  completed_at: string
  score: number | null
  passed: boolean
  expires_at: string | null
  notes: string | null
  created_at: string
}

export interface GuardCertification {
  id: string
  user_id: string
  guard_name: string
  guard_email: string
  certification_type: string
  issuing_body: string | null
  certificate_number: string | null
  issued_at: string | null
  expires_at: string | null
  is_valid: boolean
  notes: string | null
  expiry_status: 'valid' | 'expiring_soon' | 'expired' | 'no_expiry'
  created_at: string
  updated_at: string
}

export interface TrainingDashboard {
  course_stats: { total_courses: number; active_courses: number }
  record_stats: {
    total_records: number
    total_passed: number
    this_month: number
    passed_this_month: number
    expired_records: number
  }
  cert_stats: {
    valid_certs: number
    expired_certs: number
    expiring_30d: number
    expiring_7d: number
  }
  expiring_certifications: Array<{
    id: string; user_id: string; certification_type: string
    expires_at: string; guard_name: string; days_remaining: number
  }>
  expired_training_records: Array<{
    id: string; user_id: string; course_id: string
    expires_at: string; guard_name: string; course_name: string
  }>
}

export const getTrainingDashboard = () =>
  client.get<TrainingDashboard>('/api/v1/training/dashboard').then(r => r.data)

export const listCourses = (params?: { category?: string; is_active?: boolean }) =>
  client.get<TrainingCourse[]>('/api/v1/training/courses', { params }).then(r => r.data)

export const createCourse = (data: {
  name: string; description?: string; category?: string
  duration_hours?: number; passing_score?: number; validity_months?: number
}) => client.post<{ id: string }>('/api/v1/training/courses', data).then(r => r.data)

export const updateCourse = (id: string, data: Partial<TrainingCourse>) =>
  client.put(`/api/v1/training/courses/${id}`, data).then(r => r.data)

export const listRecords = (params?: { user_id?: string; course_id?: string; passed?: boolean }) =>
  client.get<TrainingRecord[]>('/api/v1/training/records', { params }).then(r => r.data)

export const createRecord = (data: {
  user_id: string; course_id: string; completed_at?: string
  score?: number; passed?: boolean; notes?: string
}) => client.post<{ id: string; expires_at: string | null }>('/api/v1/training/records', data).then(r => r.data)

export const deleteRecord = (id: string) =>
  client.delete(`/api/v1/training/records/${id}`).then(r => r.data)

export const listCertifications = (params?: {
  user_id?: string; is_valid?: boolean; expiring_days?: number
}) => client.get<GuardCertification[]>('/api/v1/training/certifications', { params }).then(r => r.data)

export const createCertification = (data: {
  user_id: string; certification_type: string; issuing_body?: string
  certificate_number?: string; issued_at?: string; expires_at?: string; notes?: string
}) => client.post<{ id: string }>('/api/v1/training/certifications', data).then(r => r.data)

export const updateCertification = (id: string, data: Partial<GuardCertification>) =>
  client.put(`/api/v1/training/certifications/${id}`, data).then(r => r.data)

export const revokeCertification = (id: string) =>
  client.delete(`/api/v1/training/certifications/${id}`).then(r => r.data)

// ── Question bank ──────────────────────────────────────────────────────────────

export const getQuestions = (courseId: string) =>
  client.get<TrainingQuestion[]>(`/api/v1/training/courses/${courseId}/questions`).then(r => r.data)

export const createQuestion = (courseId: string, data: {
  question_text: string; options: string[]; correct_index: number; points?: number; sort_order?: number
}) => client.post<{ id: string }>(`/api/v1/training/courses/${courseId}/questions`, data).then(r => r.data)

export const updateQuestion = (id: string, data: Partial<{
  question_text: string; options: string[]; correct_index: number; points: number; sort_order: number
}>) => client.put(`/api/v1/training/questions/${id}`, data).then(r => r.data)

export const deleteQuestion = (id: string) =>
  client.delete(`/api/v1/training/questions/${id}`).then(r => r.data)

// ── Quiz attempts ────────────────────────────────────────────────────────────

export const startAttempt = (courseId: string) =>
  client
    .post<{ attempt_id: string; questions: TrainingAttemptQuestion[] }>(`/api/v1/training/courses/${courseId}/attempts`)
    .then(r => r.data)

export const getAttempt = (attemptId: string) =>
  client
    .get<{ attempt_id: string; status: string; answers: Record<string, number>; questions: TrainingAttemptQuestion[] }>(
      `/api/v1/training/attempts/${attemptId}`
    )
    .then(r => r.data)

export const answerQuestion = (attemptId: string, questionId: string, selectedIndex: number) =>
  client
    .put(`/api/v1/training/attempts/${attemptId}/answer`, { question_id: questionId, selected_index: selectedIndex })
    .then(r => r.data)

export const submitAttempt = (attemptId: string) =>
  client
    .post<{ score: number; passed: boolean; correct_count: number; total_count: number }>(
      `/api/v1/training/attempts/${attemptId}/submit`
    )
    .then(r => r.data)

export const listAttempts = (params?: { course_id?: string; guard_user_id?: string; attempt_status?: string }) =>
  client.get<TrainingAttempt[]>('/api/v1/training/attempts', { params }).then(r => r.data)

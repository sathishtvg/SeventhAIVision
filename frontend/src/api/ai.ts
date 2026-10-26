/**
 * The AI module inside an organisation (backend/app/routers/ai.py): what it has, and the admin's controls.
 *
 * Three gates must all be open for a feature to do anything: the platform owner's licence, the admin's switch,
 * and the person's role among those the admin chose.
 */
import { apiClient } from './client'

export type AiSensitivity = 'LOW' | 'MEDIUM' | 'HIGH'

export interface AiFeatureStatus {
  key: string
  title: string
  group: 'learning' | 'assistant'
  description: string
  ready: boolean
  licensed: boolean
  enabled: boolean
  /** Whether the person asking may use it: licensed, switched on, built, and their role chosen. */
  mine: boolean
}

export interface AiStatus {
  licensed: boolean
  reason: string | null
  expires_at: string | null
  assistant_provider: 'LOCAL' | 'HOSTED' | null
  features: AiFeatureStatus[]
}

export interface AiFeatureSetting {
  key: string
  title: string
  group: 'learning' | 'assistant'
  description: string
  ready: boolean
  phase: number
  licensed: boolean
  allowed_roles: number[]
  enabled: boolean
  role_ids: number[]
  /** null: every site. */
  site_ids: string[] | null
  sensitivity: AiSensitivity
  learning_enabled: boolean
  updated_at: string | null
}

export interface AiSettings {
  licensed: boolean
  reason: string | null
  expires_at: string | null
  assistant_provider: 'LOCAL' | 'HOSTED' | null
  monthly_reply_limit: number | null
  features: AiFeatureSetting[]
}

export interface AiFeatureSettingBody {
  enabled: boolean
  role_ids: number[]
  site_ids: string[] | null
  sensitivity: AiSensitivity
  learning_enabled: boolean
}

export const getAiStatus = () => apiClient.get<AiStatus>('/api/v1/ai/status').then((r) => r.data)

export const getAiSettings = () => apiClient.get<AiSettings>('/api/v1/ai/settings').then((r) => r.data)

export const putAiSetting = (key: string, body: AiFeatureSettingBody) =>
  apiClient.put<AiFeatureSetting>(`/api/v1/ai/settings/${key}`, body).then((r) => r.data)

// ── Phase 2: warnings, decisions, and what each feature has learned (backend/app/routers/ai_insights.py) ──

export interface AiReason {
  factor: string
  /** The factor in words, as the guard's supervisor would say it. */
  words: string
  push: number
}

export type AiInsightStatus = 'OPEN' | 'ACCEPTED' | 'DISMISSED' | 'EXPIRED'

export interface AiInsight {
  id: string
  feature_key: string
  feature_title: string
  /** shift (a late or no-show warning), guard_month (overtime), guard_week (fatigue, by rule). */
  subject_type: string
  title: string
  detail: string | null
  /** A chance (0-1), or for the overtime forecast the share of the monthly limit (1 = at the limit). */
  score: number
  threshold: number | null
  reasons: AiReason[]
  evidence: Record<string, unknown>
  site_id: string | null
  site_name: string | null
  person_user_id: string | null
  person_name: string | null
  status: AiInsightStatus
  decided_by_name: string | null
  decided_at: string | null
  decision_note: string | null
  outcome: 'HAPPENED' | 'DID_NOT_HAPPEN' | null
  valid_until: string | null
  created_at: string
  updated_at: string
}

export interface AiInsightList {
  /** The features whose warnings the caller may see. */
  features: string[]
  items: AiInsight[]
}

export interface AiLearnedFactor { factor: string; title: string; weight: number; raises: boolean }

export interface AiLearning {
  key: string
  title: string
  description: string
  sensitivity: AiSensitivity
  learning_enabled: boolean
  threshold: number
  threshold_reason: string
  model: null | {
    status: 'LEARNING' | 'READY'
    version: number
    trained_at: string
    rows_used: number
    positives: number
    metrics: { auc_on_newest?: number | null; base_rate?: number | null; [k: string]: unknown }
    note: string | null
  }
  learned: AiLearnedFactor[]
  last_30_days: { open: number; accepted: number; dismissed: number; came_true: number; did_not: number }
}

export const getAiInsights = (params: { status?: 'OPEN' | 'DECIDED' | 'ALL'; feature?: string } = {}) =>
  apiClient.get<AiInsightList>('/api/v1/ai/insights', { params }).then((r) => r.data)

export const decideAiInsight = (id: string, body: { decision: 'ACCEPT' | 'DISMISS'; note?: string | null }) =>
  apiClient.post<AiInsight>(`/api/v1/ai/insights/${id}/decide`, body).then((r) => r.data)

export const getAiLearning = () =>
  apiClient.get<{ features: AiLearning[] }>('/api/v1/ai/learning').then((r) => r.data)

export const runAiLearning = (key: string) =>
  apiClient.post<Record<string, unknown>>(`/api/v1/ai/learning/${key}/run`).then((r) => r.data)

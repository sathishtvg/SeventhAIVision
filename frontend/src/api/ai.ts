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

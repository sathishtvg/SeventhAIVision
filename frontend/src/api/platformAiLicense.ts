/**
 * The platform owner's licence for the AI module, one organisation at a time
 * (backend/app/routers/platform_ai_licenses.py).
 *
 * Which features were bought, which chat model the assistant uses (the free
 * built-in one or the paid hosted one), and how many assistant replies a month
 * the organisation may have. A hosted model must have a limit; the free one may not.
 */
import { apiClient } from './client'

export type AiProvider = 'LOCAL' | 'HOSTED'

export interface AiFeatureOffer {
  key: string
  title: string
  group: 'learning' | 'assistant'
  description: string
  /** False until the release that builds it: it can be sold ahead, not switched on. */
  ready: boolean
  phase: number
}

export interface AiLicense {
  tenant_id: string
  tenant_name: string
  tenant_slug: string
  /** Whether the organisation has the module now: switched on, and not expired. */
  licensed: boolean
  reason: string | null
  is_enabled: boolean
  licensed_at: string | null
  expires_at: string | null
  features: string[]
  assistant_provider: AiProvider
  monthly_reply_limit: number | null
  notes: string | null
  updated_at: string | null
  catalogue: AiFeatureOffer[]
}

export interface AiLicenseBody {
  is_enabled: boolean
  expires_at: string | null
  features: string[]
  assistant_provider: AiProvider
  monthly_reply_limit: number | null
  notes: string | null
}

export const getAiLicense = (tenantId: string) =>
  apiClient.get<AiLicense>(`/api/v1/platform/tenants/${tenantId}/ai-license`).then((r) => r.data)

export const putAiLicense = (tenantId: string, body: AiLicenseBody) =>
  apiClient.put<AiLicense>(`/api/v1/platform/tenants/${tenantId}/ai-license`, body).then((r) => r.data)

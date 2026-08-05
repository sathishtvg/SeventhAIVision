/**
 * Per-site recording policy (Phase X-A).
 *
 * Retention is the reason this exists: how long footage is kept is a
 * contractual term that differs per client site, not a platform constant.
 *
 * `null` on a retention field means "inherit the tenant setting" — it is NOT
 * the same as `0`, which means "keep nothing centrally". The UI has to keep
 * those distinct or an admin clearing a field would silently start deleting
 * footage immediately.
 */
import { apiClient } from './client'

export type RecordMode = 'continuous' | 'motion' | 'ai_event' | 'scheduled' | 'off'
export type SyncMode = 'central' | 'local_only' | 'incident_only' | 'scheduled' | 'manual'
export type Compression = 'none' | 'h264' | 'h265'

export interface RecordingPolicy {
  id: string
  tenant_id: string
  site_id: string
  site_name?: string
  record_mode: RecordMode
  sync_mode: SyncMode
  local_retention_days: number | null
  central_retention_days: number | null
  sync_window_start: string | null
  sync_window_end: string | null
  bandwidth_limit_kbps: number | null
  clip_pre_seconds: number
  clip_post_seconds: number
  compression: Compression
  encrypt_archives: boolean
  verify_checksums: boolean
  is_active: boolean
  updated_by_user_id: string | null
  created_at: string
  updated_at: string
}

/** Server-resolved inheritance, so the UI never re-implements the chain. */
export interface EffectivePolicy {
  site_id: string
  has_policy: boolean
  record_mode: RecordMode
  sync_mode: SyncMode
  central_retention_days: number
  central_retention_inherited: boolean
  tenant_retention_days: number
  local_retention_days: number | null
  clip_pre_seconds: number
  clip_post_seconds: number
}

export interface PolicyUpsert {
  record_mode: RecordMode
  sync_mode: SyncMode
  local_retention_days?: number | null
  central_retention_days?: number | null
  sync_window_start?: string | null
  sync_window_end?: string | null
  bandwidth_limit_kbps?: number | null
  clip_pre_seconds: number
  clip_post_seconds: number
  compression: Compression
  encrypt_archives: boolean
  verify_checksums: boolean
  is_active: boolean
}

const BASE = '/api/v1/recording-policies'

export const listRecordingPolicies = () =>
  apiClient.get<RecordingPolicy[]>(BASE).then((r) => r.data)

export const getRecordingPolicy = (siteId: string) =>
  apiClient.get<RecordingPolicy>(`${BASE}/sites/${siteId}`).then((r) => r.data)

export const getEffectivePolicy = (siteId: string) =>
  apiClient.get<EffectivePolicy>(`${BASE}/sites/${siteId}/effective`).then((r) => r.data)

export const upsertRecordingPolicy = (siteId: string, body: PolicyUpsert) =>
  apiClient.put<RecordingPolicy>(`${BASE}/sites/${siteId}`, body).then((r) => r.data)

/** Removes the policy so the site inherits the tenant setting again. */
export const deleteRecordingPolicy = (siteId: string) =>
  apiClient.delete(`${BASE}/sites/${siteId}`).then((r) => r.data)

export const RECORD_MODE_LABELS: Record<RecordMode, string> = {
  continuous: 'Continuous — always recording',
  motion: 'Motion — record on movement',
  ai_event: 'AI event only — clips around detections',
  scheduled: 'Scheduled — record during set hours',
  off: 'Off — no recording at this site',
}

export const SYNC_MODE_LABELS: Record<SyncMode, string> = {
  central: 'Central — everything reaches HQ',
  local_only: 'Local only — nothing leaves the site',
  incident_only: 'Incidents only — clips of alerts reach HQ',
  scheduled: 'Scheduled — upload during a quiet window',
  manual: 'Manual — upload only when requested',
}

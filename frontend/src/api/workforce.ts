/**
 * Workforce readings and recommendations (backend/app/routers/workforce.py).
 *
 * A reading is counts of what is recorded of a guard's work, each beside how
 * much there was to do. It is not an appraisal: nothing in it is a score and
 * no list is in any order but that of name.
 *
 * A recommendation is advice for a manager — training for a guard, cover for a
 * site. Accepting one assigns no course and changes no roster.
 */
import { apiClient } from './client'

const BASE = '/api/v1/workforce'

export type SectionKey = 'SHIFTS' | 'PATROLS' | 'RESPONSES' | 'VIOLATIONS' | 'TRAINING' | 'HANDOVERS'
export type ViolationType = 'no_show' | 'late_checkin' | 'geofence_failure' | 'early_departure' | 'manual'
export type Kind = 'TRAINING' | 'COVERAGE'
export type Answer = 'ACCEPTED' | 'NOT_ACCEPTED'

export interface Figures {
  SHIFTS?: { shifts: number; worked: number; late: number; late_minutes: number; not_started: number }
  PATROLS?: { tours_done: number; tours_missed: number; walked: number; checkpoints_scanned: number; checkpoints_total: number }
  RESPONSES?: { sent: number; accepted: number; declined: number; arrived: number; arrive_seconds: number | null }
  VIOLATIONS?: { recorded: number; waived: number; disputed: number; by_type: Record<ViolationType, number> }
  /** A person's and not a site's: in a guard's reading and in no site's. */
  TRAINING?: { completed: number; courses_lapsed_now: number; courses_lapsing_now: number; certificates_lapsed_now: number
               certificates_lapsing_now: number; shifts_at_risk_now: number }
  HANDOVERS?: { given: number; accepted: number; disputed: number }
}

export interface Section { key: SectionKey; title: string; counted_from: string; personal: boolean }
export interface NotRead { key: string; title: string; needs: string }
export interface Person { id: string; name: string | null; role_id: number; is_active: boolean }
export interface Period { days: number; from: string; to: string }

export interface Readings {
  period: Period
  site: { id: string; name: string } | null
  sections: Section[]
  /** In order of name. Nothing else orders them. */
  guards: (Person & { figures: Figures })[]
  sites: { id: string; name: string; figures: Figures }[]
  no_site: Figures | null
  total: Figures
  not_read: NotRead[]
  note: string
}

export interface Recommendation {
  /** What the server knows it by. Sent back to answer it. */
  key: string
  kind: Kind
  code: string
  statement: string
  consider: string
  subject: { user_id: string; name: string | null } | null
  site: { id: string; name: string } | null
  rests_on: Record<string, unknown>
  is_advisory: true
  is_decision: false
  answer: { answer: Answer; reason: string | null; answered_at: string; answered_by_name: string | null
            /** What it said when it was answered, when that is not what it says now. */
            said_then: string | null } | null
  may_answer: boolean
}

export interface Reading {
  period: Period
  guard: Person
  sections: Section[]
  figures: Figures
  by_site: { id: string | null; name: string; figures: Figures }[]
  not_read: NotRead[]
  note: string
  recommendations: Recommendation[]
  recommendations_note: string
}

export interface Recommendations {
  weeks: number
  site: { id: string; name: string } | null
  training: Recommendation[]
  coverage: Recommendation[]
  not_read: { kind: Kind; needs: string }[]
  can_answer: boolean
  note: string
}

export interface GivenAnswer {
  id: string
  kind: Kind
  code: string
  statement: string
  answer: Answer
  reason: string | null
  answered_at: string
  answered_by_name: string | null
  guard_name: string | null
  site_name: string | null
}

export const getReadings = (params: { site_id?: string; days: number }) =>
  apiClient.get<Readings>(`${BASE}/readings`, { params }).then((r) => r.data)

export const getReading = (userId: string, days: number) =>
  apiClient.get<Reading>(`${BASE}/readings/${userId}`, { params: { days } }).then((r) => r.data)

export const getMyReading = (days: number) => apiClient.get<Reading>(`${BASE}/me`, { params: { days } }).then((r) => r.data)

export const getRecommendations = (siteId?: string) =>
  apiClient.get<Recommendations>(`${BASE}/recommendations`, { params: siteId ? { site_id: siteId } : {} }).then((r) => r.data)

/** The recommendation is counted again by the server first; what is kept is what it says then. */
export const answerRecommendation = (body: { key: string; answer: Answer; reason?: string | null }) =>
  apiClient.post<Recommendation>(`${BASE}/recommendations/answer`, body).then((r) => r.data)

export const getAnswers = () =>
  apiClient.get<{ items: GivenAnswer[] }>(`${BASE}/recommendations/answers`).then((r) => r.data.items)

/** The server's reason, in its own words: a refusal here says what to do instead. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

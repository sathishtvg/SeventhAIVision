/**
 * Risk patterns and advice API (backend/app/routers/security_advice.py).
 *
 * Everything here is a count over a stated period. Nothing is a forecast: the
 * server sends `is_forecast: false` and a note that says so, and the screens
 * show that note. Confidence is how much history a statement rests on, not a
 * probability.
 */
import { apiClient } from './client'

const BASE = '/api/v1/security-advice'

export type Source = 'INCIDENT' | 'ACCESS' | 'PATROL' | 'DEVICE' | 'SLA'
export type Level = 'HIGH' | 'MEDIUM' | 'LOW'
export type Answer = 'ACCEPTED' | 'NOT_ACCEPTED'

export interface Period { weeks: number; from: string; to: string; timezone: string }

export interface PatternSource {
  source: Source
  label: string
  /** What this kind is counted from, in the server's words. */
  counted_from: string
  total: number
  /** Seven rows, Monday first, of twenty-four hours each, where the organisation is. */
  grid: number[][]
  /** The weeks of the period, oldest first. */
  by_week: number[]
  places: { key: string; name: string; count: number; share: number }[]
  /** Set when there were more records than one answer counts. */
  cut_at: number | null
}

export interface Patterns {
  period: Period
  site: { id: string; name: string } | null
  sources: PatternSource[]
  weekdays: string[]
  band_hours: number
  note: string
  is_forecast: false
}

export interface Finding {
  /** What the server knows this piece of advice by. Sent back to answer it. */
  key: string
  code: string
  source: Source
  source_label: string
  statement: string
  consider: string
  rests_on: Record<string, string | number>
  confidence: { level: Level; why: string; records: number; weeks: number; held_in_weeks: number | null }
  is_advisory: true
  is_forecast: false
  answer: { answer: Answer; reason: string | null; answered_at: string; answered_by_name: string | null
            /** What it said when it was answered, when that is not what it says now. */
            said_then: string | null } | null
  may_answer: boolean
}

export interface Advice {
  period: Period
  site: { id: string; name: string } | null
  findings: Finding[]
  counted: { source: Source; label: string; total: number }[]
  can_answer: boolean
  /** Why advice cannot be answered here, when it cannot: it is answered for one site. */
  answer_note: string | null
  note: string
  confidence_note: string
  is_forecast: false
}

export interface GivenAnswer {
  id: string
  site_name: string
  source_label: string
  statement: string
  confidence: Level
  period_weeks: number
  answer: Answer
  reason: string | null
  answered_at: string
  answered_by_name: string | null
}

export const getPatterns = (params: { site_id?: string; weeks: number }) =>
  apiClient.get<Patterns>(`${BASE}/patterns`, { params }).then((r) => r.data)

export const getAdvice = (params: { site_id?: string; weeks: number }) =>
  apiClient.get<Advice>(`${BASE}/advice`, { params }).then((r) => r.data)

/** The advice is counted again by the server first; what is kept is what it says then. */
export const answerAdvice = (body: { site_id: string; weeks: number; key: string; answer: Answer; reason?: string | null }) =>
  apiClient.post<Finding>(`${BASE}/advice/answer`, body).then((r) => r.data)

export const getAnswers = (siteId?: string) =>
  apiClient.get<{ items: GivenAnswer[] }>(`${BASE}/advice/answers`, { params: siteId ? { site_id: siteId } : {} })
    .then((r) => r.data.items)

/** The server's reason, in its own words: a refusal here says what to do instead. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

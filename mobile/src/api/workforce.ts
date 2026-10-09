/**
 * A person's own reading: what is recorded of their own work
 * (backend/app/routers/workforce.py, GET /me).
 *
 * It is theirs to read, whole. It is counts, each beside how much there was to
 * do — not an appraisal — and the server's note saying so comes with it.
 * Nothing here reads anybody else's.
 */
import { apiClient } from './client'

const BASE = '/api/v1/workforce'

export type SectionKey = 'SHIFTS' | 'PATROLS' | 'RESPONSES' | 'VIOLATIONS' | 'TRAINING' | 'HANDOVERS'
export type ViolationType = 'no_show' | 'late_checkin' | 'geofence_failure' | 'early_departure' | 'manual'

export interface Figures {
  SHIFTS?: { shifts: number; worked: number; late: number; late_minutes: number; not_started: number }
  PATROLS?: { tours_done: number; tours_missed: number; walked: number; checkpoints_scanned: number; checkpoints_total: number }
  RESPONSES?: { sent: number; accepted: number; declined: number; arrived: number; arrive_seconds: number | null }
  VIOLATIONS?: { recorded: number; waived: number; disputed: number; by_type: Record<ViolationType, number> }
  TRAINING?: { completed: number; courses_lapsed_now: number; courses_lapsing_now: number; certificates_lapsed_now: number
               certificates_lapsing_now: number; shifts_at_risk_now: number }
  HANDOVERS?: { given: number; accepted: number; disputed: number }
}

export interface Section { key: SectionKey; title: string; counted_from: string; personal: boolean }

export interface Recommendation {
  key: string
  code: string
  statement: string
  consider: string
  answer: { answer: 'ACCEPTED' | 'NOT_ACCEPTED'; reason: string | null; answered_at: string } | null
}

export interface MyReading {
  period: { days: number; from: string; to: string }
  sections: Section[]
  figures: Figures
  not_read: { key: string; title: string; needs: string }[]
  /** That it is counts and not an appraisal, in the server's words. */
  note: string
  recommendations: Recommendation[]
  recommendations_note: string
}

/** The periods the server counts a reading over. */
export const READING_DAYS = [7, 28, 90] as const

export const getMyReading = (days: number) =>
  apiClient.get<MyReading>(`${BASE}/me`, { params: { days } }).then((r) => r.data)

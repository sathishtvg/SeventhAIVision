/**
 * Tour compliance: were the scheduled patrol tours actually done?
 *
 * THIS CLIENT DESCRIBED A SYSTEM THAT DOES NOT EXIST. It asked for
 * /compliance/summary, /compliance/policies and /compliance/checks, and typed
 * them as policies with categories and checks with pass rates. The server has
 * never had any of those routes: compliance here means tour schedules and the
 * occurrences generated from them — a tour due at a time, and whether it was
 * completed, missed, late or left incomplete. All three calls answered 404, and
 * the screen showed "No checks recorded", which reads as "nothing to report"
 * rather than "this screen has never worked".
 *
 * The names below are the server's own, so the next person comparing the two
 * can see they match.
 */
import { apiClient } from './client'

const BASE = '/api/v1/compliance'

/** One day of the 14-day trend on the dashboard. */
export interface TourTrendDay {
  day: string
  total: number
  completed: number
  missed: number
  partial: number
}

/** A tour that was not done, named so somebody can ask why. */
export interface MissedTour {
  schedule_name: string | null
  route_name: string | null
  assigned_guard: string | null
  scheduled_at?: string | null
}

export interface ComplianceDashboard {
  tours_today: number
  completed_today: number
  missed_today: number
  late_today: number
  incomplete_today: number
  total_7d: number
  completed_7d: number
  total_30d: number
  completed_30d: number
  avg_score_7d: number | null
  /** Percentages the server computes, null when nothing was scheduled. */
  compliance_rate_7d: number | null
  compliance_rate_30d: number | null
  active_schedules: number
  daily_trend: TourTrendDay[]
  recent_missed: MissedTour[]
}

/** A recurring tour: this route, at this time, on these days. */
export interface TourSchedule {
  id: string
  route_id: string | null
  name: string
  description: string | null
  recurrence: string | null
  days_of_week: number[] | null
  scheduled_time: string | null
  window_minutes: number | null
  assigned_guard_user_id: string | null
  assigned_guard_name: string | null
  route_name: string | null
  site_name: string | null
  is_active: boolean
  total_30d?: number
  completed_30d?: number
  avg_score_30d?: number | null
  created_at: string
}

/** One instance of a schedule: due at a time, with what became of it. */
export interface TourOccurrence {
  id: string
  schedule_id: string
  session_id: string | null
  scheduled_at: string
  window_end: string | null
  status: string
  schedule_name: string | null
  route_name: string | null
  site_name: string | null
  assigned_guard_name: string | null
  session_started_at: string | null
  session_status: string | null
}

export const getComplianceDashboard = () =>
  apiClient.get<ComplianceDashboard>(`${BASE}/dashboard`).then((r) => r.data)

export const getTourSchedules = (params?: { site_id?: string; is_active?: boolean }) =>
  apiClient.get<TourSchedule[]>(`${BASE}/schedules`, { params }).then((r) => r.data)

export const getTourOccurrences = (params?: {
  schedule_id?: string
  status?: string
  guard_id?: string
  limit?: number
}) =>
  apiClient
    .get<TourOccurrence[]>(`${BASE}/occurrences`, { params: { limit: 50, ...params } })
    .then((r) => r.data)

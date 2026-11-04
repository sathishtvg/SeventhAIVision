/**
 * Briefings and operations reports by email (4 November 2026; Phase 4 of the known limits) — every call the "Sent by
 * email" screens make, typed to what backend/app/routers/ops_delivery.py returns. Everything needs report:schedule.
 */
import { apiClient } from './client'

const BASE = '/api/v1/ops-delivery'

export type Frequency = 'DAILY' | 'WEEKLY' | 'MONTHLY'
export type EmailStatus = 'PENDING' | 'PROCESSING' | 'SENT' | 'FAILED' | 'LEFT_OUT'

export interface Named { id: string; name: string; is_active?: boolean }
export interface RoleNamed { id: number; name: string }

export interface DeliveryOptions {
  reports: { key: string; title: string; periodic: boolean; needs: string[] }[]
  frequencies: Frequency[]
  people: { id: string; name: string; role: string }[]
  roles: RoleNamed[]
  sites: { id: string; name: string }[]
  /** Whether the caller may set what is for every site (they are held to no sites). */
  every_site_allowed: boolean
}

export interface BriefingList {
  site_id: string | null
  site_name: string | null
  users: Named[]
  roles: RoleNamed[]
  updated_at: string
  updated_by_name: string | null
}

export interface Schedule {
  id: string
  report_key: string
  report_title: string
  site_id: string | null
  site_name: string | null
  frequency: Frequency
  hour: number
  weekday: number | null
  month_day: number | null
  users: Named[]
  roles: RoleNamed[]
  is_active: boolean
  next_run_at: string
  last_run_at: string | null
  created_at: string
  created_by_name: string | null
  sent: number
  failed: number
  left_out: number
}

export interface ScheduleInput {
  report_key: string
  site_id?: string | null
  frequency: Frequency
  hour: number
  weekday?: number | null
  month_day?: number | null
  user_ids: string[]
  role_ids: number[]
}

export interface OpsEmail {
  id: string
  user_id: string
  name: string | null
  email: string | null
  status: EmailStatus
  attempts: number
  sent_at: string | null
  /** Why it failed, or why the person was left out. */
  last_error: string | null
  created_at: string
  period_start: string | null
  period_end: string | null
  rows_sent: number | null
  revised: boolean
  subject: string
}

export const getDeliveryOptions = () => apiClient.get<DeliveryOptions>(`${BASE}/options`).then((r) => r.data)

export const getBriefingLists = () =>
  apiClient.get<{ lists: BriefingList[]; note: string }>(`${BASE}/briefing-recipients`).then((r) => r.data)

export const setBriefingList = (body: { site_id: string | null; user_ids: string[]; role_ids: number[] }) =>
  apiClient.put<{ lists: BriefingList[]; note: string }>(`${BASE}/briefing-recipients`, body).then((r) => r.data)

export const getBriefingEmails = (briefingId: string) =>
  apiClient.get<{ emails: OpsEmail[] }>(`${BASE}/briefings/${briefingId}/emails`).then((r) => r.data)

export const listSchedules = () =>
  apiClient.get<{ schedules: Schedule[]; note: string }>(`${BASE}/schedules`).then((r) => r.data)

export const makeSchedule = (body: ScheduleInput) =>
  apiClient.post<Schedule>(`${BASE}/schedules`, body).then((r) => r.data)

export const changeSchedule = (id: string, body: Partial<Omit<ScheduleInput, 'report_key' | 'site_id'>> & { is_active?: boolean }) =>
  apiClient.patch<Schedule>(`${BASE}/schedules/${id}`, body).then((r) => r.data)

export const removeSchedule = (id: string) =>
  apiClient.post<{ removed: boolean }>(`${BASE}/schedules/${id}/remove`).then((r) => r.data)

export const getScheduleEmails = (id: string) =>
  apiClient.get<{ emails: OpsEmail[] }>(`${BASE}/schedules/${id}/emails`).then((r) => r.data)

/**
 * The operations board and the daily briefing
 * (backend/app/routers/operations_board.py, daily_briefings.py).
 *
 * The board is counted when it is asked for: each section is a set of counts
 * of what is recorded, read under that section's own permission. A figure
 * ending `_now` is as things stand at the moment of asking; a figure ending
 * `_seconds` is a middle time and is null when there was nothing to time.
 *
 * A briefing is a day's counts in fixed sentences. The server drafts it; a
 * person leaves sections out, adds a note of their own and publishes it.
 */
import { apiClient } from './client'

const BOARD = '/api/v1/operations-board'
const BRIEFINGS = '/api/v1/daily-briefings'

export type SectionKey = 'INCIDENTS' | 'RESPONSE' | 'PATROLS' | 'GUARDS' | 'DEVICES' | 'VISITORS' | 'MAINTENANCE'
export type PatrolKind = 'tours' | 'virtual' | 'drone'
export type DeviceState = 'OK' | 'DEGRADED' | 'DOWN' | 'NOT_KNOWN' | 'OFF'

export interface PatrolFigures {
  scheduled: number; done: number; partial: number; missed: number; failed: number; open: number; cancelled: number
}

export interface Figures {
  INCIDENTS?: { opened: number; by_severity: Record<'critical' | 'high' | 'medium' | 'low', number>
                resolved: number; opened_still_open: number; open_now: number }
  RESPONSE?: { opened: number; acknowledged: number; acknowledge_seconds: number | null
               resolved: number; resolve_seconds: number | null
               sent: number; arrived: number; declined: number; arrive_seconds: number | null
               missed: { acknowledge: number; arrival: number; resolve: number } }
  /** A kind the caller may not read is null. */
  PATROLS?: Record<PatrolKind, PatrolFigures | null>
  GUARDS?: { on_shift_now: number; due_not_started_now: number; shifts: number; worked: number; late: number
             not_started: number }
  DEVICES?: { devices: number; by_state: Record<DeviceState, number> }
  VISITORS?: { on_site_now: number; arrived: number; departed: number; refused: number; waiting_now: number | null }
  MAINTENANCE?: { suggested_now: number; open_now: number; in_progress_now: number; overdue_now: number
                  raised: number; done: number }
}

export interface NotRead { key: string; title: string; needs: string; reason?: string }
export interface Period { days: number; from: string; to: string }
export interface Customer { id: string; name: string }

export interface Board {
  period: Period
  as_at: string
  scope: { site: { id: string; name: string } | null; client: Customer | null; sites: number; every_site: boolean }
  sections: { key: SectionKey; title: string; counted_from: string; figures: NonNullable<Figures[SectionKey]> }[]
  not_read: NotRead[]
  /** When the organisation switched the response clocks on, or null while they are off. */
  clocks_on_since: string | null
  /** How much advice stands for the same sites — null for somebody who may not read advice. */
  advice: { weeks: number; standing: number; by_level: Record<'HIGH' | 'MEDIUM' | 'LOW', number>; note: string } | null
  note: string
}

export interface SiteRow { id: string; name: string; is_active: boolean; client: Customer | null; figures: Figures }

export interface BoardSites {
  period: Period
  as_at: string
  client: Customer | null
  sections: { key: SectionKey; title: string }[]
  sites: SiteRow[]
  /** What is recorded at no site — only for somebody not held to particular sites. */
  no_site: Figures | null
  /** A customer's sites, summed. Its times are null: a middle time is not a sum. */
  clients: (Customer & { sites: number; figures: Figures })[]
  total: Figures
  not_read: NotRead[]
  clocks_on_since: string | null
  note: string
}

export type BriefingState = 'DRAFT' | 'PUBLISHED' | 'DISCARDED'
/** When a line is true of: the day, the moment of drafting, or the weeks before. */
export type AsAt = 'PERIOD' | 'DRAFTING' | 'WEEKS'

export interface BriefingSection {
  key: string
  title: string
  note?: string
  lines: { text: string; as_at: AsAt }[]
  left_out: boolean
}

export interface BriefingSummary {
  id: string
  site: { id: string; name: string } | null
  briefing_date: string
  revision: number
  state: BriefingState
  /** The later published revision for the same day, when there is one. */
  replaced_by: string | null
  period: { from: string; to: string; timezone: string; whole_day: boolean }
  drafted_at: string
  drafted_by_name: string | null
  published_at: string | null
  published_by_name: string | null
  note: string | null
  left_out: { key: string; title: string }[]
  may: { edit: boolean; recount: boolean; publish: boolean; discard: boolean; correct: boolean }
}

export interface Briefing extends BriefingSummary {
  sections: BriefingSection[]
  not_read: NotRead[]
  drafting_note: string
}

export const getBoard = (params: { site_id?: string; client_id?: string; days: number }) =>
  apiClient.get<Board>(BOARD, { params }).then((r) => r.data)

export const getBoardSites = (params: { client_id?: string; days: number }) =>
  apiClient.get<BoardSites>(`${BOARD}/sites`, { params }).then((r) => r.data)

export const listBriefings = (params: { site_id?: string; state?: BriefingState } = {}) =>
  apiClient.get<{ items: BriefingSummary[]; can_manage: boolean; max_days_back: number }>(BRIEFINGS, { params })
    .then((r) => r.data)

export const getBriefing = (id: string) => apiClient.get<Briefing>(`${BRIEFINGS}/${id}`).then((r) => r.data)

/** Counted by the server, now, from what the caller may read. */
export const draftBriefing = (body: { site_id?: string | null; briefing_date: string }) =>
  apiClient.post<Briefing>(BRIEFINGS, body).then((r) => r.data)

/** What the reviewer leaves out, and their own words. The counted lines are not sent: they are not edited. */
export const reviewBriefing = (id: string, body: { left_out?: string[]; note?: string | null }) =>
  apiClient.patch<Briefing>(`${BRIEFINGS}/${id}`, body).then((r) => r.data)

export const recountBriefing = (id: string) => apiClient.post<Briefing>(`${BRIEFINGS}/${id}/recount`).then((r) => r.data)
export const publishBriefing = (id: string) => apiClient.post<Briefing>(`${BRIEFINGS}/${id}/publish`).then((r) => r.data)
export const discardBriefing = (id: string) => apiClient.post<Briefing>(`${BRIEFINGS}/${id}/discard`).then((r) => r.data)

/** The server's reason, in its own words: a refusal here says what to do instead. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

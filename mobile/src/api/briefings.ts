/**
 * Daily briefings, read on the phone (backend/app/routers/daily_briefings.py).
 *
 * A briefing is one day put into fixed sentences with a count in each, which a
 * person reviewed and published. The phone reads published ones; drafting,
 * reviewing and publishing are done at a desk.
 */
import { apiClient } from './client'

const BASE = '/api/v1/daily-briefings'

/** When a line is true of: the day, the moment it was drafted, or the weeks before. */
export type AsAt = 'PERIOD' | 'DRAFTING' | 'WEEKS'

export interface BriefingSummary {
  id: string
  site: { id: string; name: string } | null
  briefing_date: string
  revision: number
  state: 'DRAFT' | 'PUBLISHED' | 'DISCARDED'
  /** The later published revision for the same day, when there is one. */
  replaced_by: string | null
  published_at: string | null
  published_by_name: string | null
  /** The reviewer's own words. */
  note: string | null
  left_out: { key: string; title: string }[]
}

export interface BriefingSection {
  key: string
  title: string
  note?: string
  lines: { text: string; as_at: AsAt }[]
  left_out: boolean
}

export interface Briefing extends BriefingSummary { sections: BriefingSection[] }

export const listPublishedBriefings = () =>
  apiClient.get<{ items: BriefingSummary[] }>(BASE, { params: { state: 'PUBLISHED' } }).then((r) => r.data.items)

export const getBriefing = (id: string) => apiClient.get<Briefing>(`${BASE}/${id}`).then((r) => r.data)

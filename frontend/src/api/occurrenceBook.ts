/**
 * Occurrence book API — the book searched, reviewed and corrected, the
 * instructions in force at a site, and a shift's summary
 * (backend/app/routers/occurrence_book.py).
 *
 * Writing an entry is not here: that is the endpoint that has always written
 * them (`createDobEntry` in ./guards). Nothing here edits or removes an entry.
 */
import { apiClient } from './client'

const BASE = '/api/v1/occurrence-book'

export type ReviewState = 'unreviewed' | 'noted' | 'follow_up' | 'closed'
export type ReviewOutcome = 'NOTED' | 'FOLLOW_UP' | 'CLOSED'
export type SummaryState = 'DRAFT' | 'CONFIRMED' | 'DISCARDED'

export interface Kinds {
  kinds: { key: string; label: string }[]
  /** Empty for somebody who reads the book and does not keep it. */
  review_states: ReviewState[]
  can_write: boolean
  can_review: boolean
  can_read_handovers: boolean
  can_manage_handovers: boolean
}

export interface Review {
  state: ReviewState
  note: string | null
  reviewed_at: string | null
  reviewed_by_name: string | null
}

export interface Entry {
  id: string
  entry_type: string
  body: string
  severity: string | null
  occurred_at: string
  site_id: string | null
  site_name: string | null
  shift_id: string | null
  author_user_id: string | null
  author_name: string | null
  /** The entry this one corrects, when it is a correction. */
  corrects_entry_id: string | null
  correction_reason: string | null
  /** The entry that corrects this one, when it has been corrected. */
  corrected_by_entry_id: string | null
  /** Null for a reader who does not keep the book: what a reviewer wrote is not theirs to read. */
  review: Review | null
  mine: boolean
}

export interface EntryDetail extends Entry {
  reviews: { id: string; outcome: ReviewOutcome; note: string | null; reviewed_at: string; reviewed_by_name: string | null }[]
  corrects: Entry | null
  corrected_by: Entry[]
}

export interface EntrySearch {
  q?: string
  entry_type?: string[]
  site_id?: string
  date_from?: string
  date_until?: string
  review?: ReviewState
  corrected?: boolean
  limit?: number
  offset?: number
}

export interface EntryPage { items: Entry[]; limit: number; offset: number; has_more: boolean; can_review: boolean; can_write: boolean }

export interface Instruction {
  id: string
  site_id: string
  site_name: string
  body: string
  issued_by_name: string | null
  issued_at: string
  expires_at: string | null
  closed_at: string | null
  closed_by_name: string | null
  close_note: string | null
  in_force: boolean
  /** How many people have said they read it. */
  reads: number
  read_by_me: boolean
}

export interface ShiftSummary {
  id: string
  shift_id: string
  site_name: string | null
  guard_name: string | null
  period_start: string
  period_end: string
  /** What the platform wrote, kept as written. */
  drafted_text: string
  /** What the person made of it. */
  final_text: string
  method: 'TEMPLATE'
  state: SummaryState
  drafted_by_name: string | null
  drafted_at: string
  confirmed_by_name: string | null
  confirmed_at: string | null
  handover_id: string | null
  edited: boolean
  note: string
  may: { edit: boolean; confirm: boolean; discard: boolean }
}

export const getKinds = () => apiClient.get<Kinds>(`${BASE}/kinds`).then((r) => r.data)

export const searchEntries = (params: EntrySearch = {}) =>
  apiClient.get<EntryPage>(`${BASE}/entries`, { params, paramsSerializer: { indexes: null } }).then((r) => r.data)

export const getEntry = (id: string) => apiClient.get<EntryDetail>(`${BASE}/entries/${id}`).then((r) => r.data)

export const reviewEntry = (id: string, outcome: ReviewOutcome, note?: string) =>
  apiClient.post<Entry>(`${BASE}/entries/${id}/review`, { outcome, note: note || null }).then((r) => r.data)

export const reviewEntries = (entryIds: string[]) =>
  apiClient.post<{ reviewed: string[]; left: { id: string; why: string }[] }>(
    `${BASE}/entries/review`, { entry_ids: entryIds }).then((r) => r.data)

export const correctEntry = (id: string, body: string, reason: string) =>
  apiClient.post<Entry>(`${BASE}/entries/${id}/correct`, { body, reason }).then((r) => r.data)

export const listInstructions = (params: { site_id?: string; state?: 'in_force' | 'ended' | 'all' } = {}) =>
  apiClient.get<{ items: Instruction[]; can_issue: boolean }>(`${BASE}/instructions`, { params }).then((r) => r.data)

export const issueInstruction = (body: { site_id: string; body: string; expires_at: string | null }) =>
  apiClient.post<Instruction>(`${BASE}/instructions`, body).then((r) => r.data)

export const readInstruction = (id: string) =>
  apiClient.post<Instruction>(`${BASE}/instructions/${id}/read`).then((r) => r.data)

export const closeInstruction = (id: string, note: string) =>
  apiClient.post<Instruction>(`${BASE}/instructions/${id}/close`, { note }).then((r) => r.data)

export const listSummaries = (params: { shift_id?: string; site_id?: string } = {}) =>
  apiClient.get<{ items: ShiftSummary[]; can_manage: boolean }>(`${BASE}/shift-summaries`, { params }).then((r) => r.data)

export const draftSummary = (shiftId: string) =>
  apiClient.post<ShiftSummary & { drafted_again: boolean }>(`${BASE}/shift-summaries`, { shift_id: shiftId })
    .then((r) => r.data)

export const editSummary = (id: string, finalText: string) =>
  apiClient.patch<ShiftSummary>(`${BASE}/shift-summaries/${id}`, { final_text: finalText }).then((r) => r.data)

export const confirmSummary = (id: string) =>
  apiClient.post<ShiftSummary>(`${BASE}/shift-summaries/${id}/confirm`).then((r) => r.data)

export const discardSummary = (id: string) =>
  apiClient.post<{ state: 'DISCARDED' }>(`${BASE}/shift-summaries/${id}/discard`).then((r) => r.data)

/** The server's reason, in its own words: a refusal here says what to do instead. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

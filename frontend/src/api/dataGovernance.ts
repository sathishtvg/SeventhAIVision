/**
 * How long records are kept, and where a person appears in them
 * (backend/app/routers/data_governance.py).
 *
 * The statement is a reading: it changes no period. A subject report says
 * where a person appears and how often, not what each record says — and that
 * it was asked for is written in the audit log.
 *
 * A name typed is sent in the body of a request, never in its address.
 */
import { apiClient } from './client'

const BASE = '/api/v1/data-governance'

export type Source = 'SITE_POLICY' | 'TENANT_SETTING' | 'INSTALLATION' | 'FIXED'
export type Part = 'ABOUT' | 'BY'
export type SubjectKind = 'STAFF' | 'VISITOR' | 'TEXT'

export interface PeriodInForce {
  key: string
  label: string
  tables: string[]
  names_people: boolean
  period: { amount: number; unit: 'days' | 'years'; source: Source; source_words: string; set_at: string | null; note: string | null }
  setting_key: string | null
  counted_from: string
  removed_by: string
  how: string
  hold_stops_it: boolean
  /** How many are under a hold now; null when no hold applies to this kind. */
  held_now: number | null
  kept_past: string | null
  per_organisation: boolean
}

export interface SitePeriod {
  site_id: string
  site_name: string
  in_use: boolean
  recording_days: number
  source: Source
  source_words: string
  keeps_none: boolean
  at_the_site_days: number | null
  set_at: string | null
}

export interface Kept {
  key: string
  label: string
  tables: { name: string; names_people: boolean }[]
  taken_away: Record<string, string>
}

/** A kind of record the organisation may give a period. Kept until one is set. */
export interface OptionalKind {
  key: string
  label: string
  setting_key: string
  tables: { name: string; names_people: boolean }[]
  /** The period in days, or null when none is set and the kind is kept. */
  days: number | null
  set_at: string | null
  counted_from: string
  removes: string
  kept_whatever: string | null
  removed_by: string
  taken_away: Record<string, string>
}

export interface Statement {
  read_at: string
  periods: PeriodInForce[]
  sites: SitePeriod[]
  optional: OptionalKind[]
  optional_note: string
  least_days: number
  /** Whether the reader may set a period: somebody who may change the organisation's settings. */
  may_set: boolean
  holds: { in_force: number; by_kind: Record<string, number>; words: string }
  kept: Kept[]
  everything_else: string
  erasure: string[]
  not_law: string
  sources: Record<Source, string>
}

export interface Line { column: string; part: Part; words: string; count: number; first: string | null; last: string | null }
export interface Match {
  text: string; count: number; first: string | null; last: string | null
  kind?: 'PERSON' | 'VEHICLE'; cases?: number; taken_off?: number
}
export interface Held {
  table: string
  label: string
  /** For a member of staff or a visitor: each way they are named, with how often. */
  lines?: Line[]
  /** For words as typed: how many rows hold them, and the text that matched. */
  count?: number
  distinct?: number
  matches?: Match[]
}
export interface Searches { count: number; by_people: number; first: string | null; last: string | null; words: string }

export interface SubjectReport {
  subject: {
    kind: SubjectKind; id?: string; name?: string | null; role?: string | null; in_use?: boolean
    company?: string | null; text?: string; as_plate?: string | null
  }
  held: Held[]
  nothing_in: string[]
  totals: { about?: number; by?: number; matches?: number; kinds_of_record: number }
  searches: Searches | null
  parts?: Record<Part, string>
  text_is_text?: string
  what_it_is: string
  not_read: string[]
  matches_shown?: number
}

export interface Found { id: string; name: string | null; detail: string | null; in_use: boolean }

export const getStatement = () => apiClient.get<Statement>(`${BASE}/retention`).then((r) => r.data)

/**
 * Set, change or take away (days: null) the period of one kind. A period that already has records older than
 * it is refused the first time with how many (409, `alreadyOlder`), and set when that number is said back.
 */
export const setRetentionPeriod = (kind: string, days: number | null, alreadyOlder?: number) =>
  apiClient.put<{ kind: string; label: string; days: number | null; set_at: string | null; already_older: number }>(
    `${BASE}/retention/periods/${kind}`, { days, ...(alreadyOlder == null ? {} : { already_older: alreadyOlder }) },
  ).then((r) => r.data)

/** What a refusal to set a period says is already older than it, when that is why it was refused. */
export function alreadyOlder(err: unknown): { message: string; already_older: number; days: number } | null {
  const d = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response
  const detail = d?.data?.detail as { message?: string; already_older?: number; days?: number } | undefined
  return d?.status === 409 && detail && typeof detail.already_older === 'number' && typeof detail.message === 'string'
    ? { message: detail.message, already_older: detail.already_older, days: detail.days ?? 0 } : null
}

export const findSubject = (kind: 'STAFF' | 'VISITOR', words: string) =>
  apiClient.post<{ kind: string; found: Found[]; more: boolean }>(`${BASE}/subjects/find`, { kind, words }).then((r) => r.data)

export const getStaffReport = (id: string) => apiClient.get<SubjectReport>(`${BASE}/subjects/staff/${id}`).then((r) => r.data)

export const getVisitorReport = (id: string) => apiClient.get<SubjectReport>(`${BASE}/subjects/visitor/${id}`).then((r) => r.data)

export const getWrittenReport = (text: string) =>
  apiClient.post<SubjectReport>(`${BASE}/subjects/written`, { text }).then((r) => r.data)

/** The server's reason, in its own words: a refusal here says what to do instead. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (d && typeof d === 'object' && typeof (d as { message?: unknown }).message === 'string') return (d as { message: string }).message
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

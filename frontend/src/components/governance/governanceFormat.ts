/**
 * The words of the retention statement and of a subject report.
 *
 * Nothing here adds to what the server says: a period is written as it was
 * read, a count beside what it is a count of.
 */
import type {
  Held, Line, Match, OptionalKind, PeriodInForce, Searches, SitePeriod, SubjectReport,
} from '@/api/dataGovernance'

/** A day, without the hour: a report says between which dates, not at what minute. */
export function day(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

const many = (n: number, one: string, more: string) => `${n} ${n === 1 ? one : more}`

/** "30 days", "7 years", "1 day". */
export const periodLine = (p: PeriodInForce['period']) =>
  `${p.amount} ${p.amount === 1 ? p.unit.slice(0, -1) : p.unit}`

/** A kind that may be given a period: how it stands. Kept, until a period is set. */
export const optionalLine = (k: OptionalKind) =>
  k.days == null ? 'Kept: no period is set' : `Removed ${many(k.days, 'day', 'days')} after ${k.counted_from}`

/** A site's recordings: its days, or that none is kept centrally. */
export const siteLine = (s: SitePeriod) =>
  s.keeps_none ? 'Nothing is kept centrally' : many(s.recording_days, 'day', 'days')

/** What a hold does to this kind of record, and how many are under one now. */
export function holdLine(p: PeriodInForce): string {
  if (!p.hold_stops_it) return 'No hold applies'
  return p.held_now ? `A hold stops it — ${many(p.held_now, 'is', 'are')} held now` : 'A hold stops it — none is held now'
}

/** "3, between 1 Sep 2026 and 4 Oct 2026" — or the one day, when it is one. */
export function between(x: { count: number; first: string | null; last: string | null }): string {
  if (!x.first || !x.last) return String(x.count)
  const a = day(x.first), b = day(x.last)
  return a === b ? `${x.count}, on ${a}` : `${x.count}, between ${a} and ${b}`
}

export const lineWords = (l: Line) => `${l.words}: ${between(l)}`

/** A piece of text that matched, with where: it is text, and is shown as it was written. */
export function matchLine(m: Match): string {
  const kind = m.kind === 'VEHICLE' ? 'a vehicle' : m.kind === 'PERSON' ? 'a person' : null
  const cases = m.cases ? ` in ${many(m.cases, 'case', 'cases')}` : ''
  const off = m.taken_off ? ` (${m.taken_off} taken off since)` : ''
  return `“${m.text}”${kind ? `, written in as ${kind}` : ''}${cases}: ${between(m)}${off}`
}

/** What a kind of record holds, in one line for its heading. */
export function heldCount(h: Held): string {
  if (h.lines) return many(h.lines.reduce((n, l) => n + l.count, 0), 'time', 'times')
  return many(h.count ?? 0, 'match', 'matches')
}

/** Who a report is about, as its heading. */
export function subjectLine(r: SubjectReport): string {
  const s = r.subject
  if (s.kind === 'TEXT') return `“${s.text}”, as it was typed`
  if (s.kind === 'VISITOR') return `${s.name ?? 'A visitor'}${s.company ? `, ${s.company}` : ''} — a visitor`
  return `${s.name ?? 'A member of staff'}${s.role ? `, ${s.role}` : ''}${s.in_use === false ? ' — no longer in use' : ''}`
}

/** The totals in words. */
export function totalsLine(r: SubjectReport): string {
  const t = r.totals
  if (!t.kinds_of_record) return 'Nothing in these records names them.'
  const kinds = many(t.kinds_of_record, 'kind of record', 'kinds of record')
  if (r.subject.kind === 'TEXT') return `${many(t.matches ?? 0, 'match', 'matches')} in ${kinds}.`
  return `${many(t.about ?? 0, 'record concerns', 'records concern')} them and ${many(t.by ?? 0, 'step was', 'steps were')} taken by them, in ${kinds}.`
}

export function searchesLine(s: Searches): string {
  if (!s.count) return 'No investigation search asked about them.'
  const a = day(s.first), b = day(s.last)
  const when = !s.first || !s.last ? '' : a === b ? `, on ${a}` : `, between ${a} and ${b}`
  return `${many(s.count, 'investigation search', 'investigation searches')} asked about them, by ${many(s.by_people, 'person', 'people')}${when}.`
}

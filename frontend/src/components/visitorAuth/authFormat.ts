/** The words the visitor authorisation screens share. */
import type { Authorisation, Movement, Standing } from '@/api/visitorAuth'

export const STANDING_LABEL: Record<Standing, string> = {
  NOT_ASKED: 'Not asked for', AWAITING_HOST: 'Waiting for an answer', LAPSED: 'Never answered', DECLINED: 'Declined',
  CANCELLED: 'Cancelled', NOT_YET_VALID: 'Approved, not yet valid', VALID: 'Valid', EXPIRED: 'Run out',
}

export const STANDING_COLOUR: Record<Standing, 'success' | 'default' | 'warning' | 'info' | 'error'> = {
  NOT_ASKED: 'default', AWAITING_HOST: 'info', LAPSED: 'warning', DECLINED: 'error', CANCELLED: 'default',
  NOT_YET_VALID: 'info', VALID: 'success', EXPIRED: 'warning',
}

/** The standings a list can be narrowed to, in the order they are offered. */
export const STANDINGS: Standing[] = ['AWAITING_HOST', 'VALID', 'NOT_YET_VALID', 'EXPIRED', 'LAPSED', 'DECLINED', 'CANCELLED']

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** Whose authorisation it is, in a line. */
export function who(a: Pick<Authorisation, 'subject'>): string {
  const s = a.subject
  if (s.kind === 'work_permit') return `${s.name ?? 'A contractor'} — work permit`
  return s.company ? `${s.name ?? 'A visitor'} (${s.company})` : s.name ?? 'A visitor'
}

/** What a work permit is for, or how a visitor is arriving. */
export function about(a: Pick<Authorisation, 'subject' | 'purpose'>): string {
  const s = a.subject
  if (s.kind === 'work_permit') {
    const people = s.workers_count ? ` · ${s.workers_count} ${s.workers_count === 1 ? 'worker' : 'workers'}` : ''
    return `${s.detail ?? ''}${people}`.trim()
  }
  return a.purpose ?? ''
}

export const period = (a: Pick<Authorisation, 'valid_from' | 'valid_until'>) => `${fmt(a.valid_from)} to ${fmt(a.valid_until)}`

/** A `datetime-local` value as the moment it names, or null when it is empty. */
export const iso = (local: string): string | null => (local ? new Date(local).toISOString() : null)

/** A moment as a `datetime-local` value, in this browser's own time. */
export function local(isoText: string | null | undefined): string {
  if (!isoText) return ''
  const d = new Date(isoText)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}T${two(d.getHours())}:${two(d.getMinutes())}`
}

/**
 * What can be said of a door event against what the visit is authorised for.
 * Something to look at is not a finding, and what is not known is said to be
 * not known — the server's reason, word for word.
 */
export function against(m: Pick<Movement, 'within' | 'in_period' | 'why_not_known'>): { text: string
                                                                                        tone: 'ok' | 'look' | 'unknown' } {
  const parts: string[] = []
  if (m.within === false) parts.push('outside the places it is authorised for')
  if (m.in_period === false) parts.push('outside the period it is authorised for')
  if (parts.length) return { text: `To look at: ${parts.join(', and ')}`, tone: 'look' }
  if (m.within === null) return { text: m.why_not_known ?? 'Cannot be said.', tone: 'unknown' }
  return { text: 'Within what the visit is authorised for', tone: 'ok' }
}

export const REVIEW_LABEL = { IN_ORDER: 'In order', FOLLOWED_UP: 'Followed up' } as const

/** Where a door is, as the site's map names it. */
export function door(m: Pick<Movement, 'door_name' | 'place_name' | 'part_of'>): string {
  if (!m.place_name) return m.door_name
  return m.part_of ? `${m.place_name}, ${m.part_of}` : m.place_name
}

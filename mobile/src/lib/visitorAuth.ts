/**
 * The rules of the phone's visitor authorisation cards, apart from the cards so
 * they can be tested with nothing mounted: the words, the colour of what
 * stands, and what the gate is offered at each point.
 */
import type { Authorisation, Standing } from '@/api/visitorAuth'

export const STANDING_LABEL: Record<Standing, string> = {
  NOT_ASKED: 'Not asked for', AWAITING_HOST: 'Waiting for an answer', LAPSED: 'Never answered', DECLINED: 'Declined',
  CANCELLED: 'Cancelled', NOT_YET_VALID: 'Approved, not yet valid', VALID: 'Valid', EXPIRED: 'Run out',
}

export type Tone = 'good' | 'wait' | 'stop' | 'plain'

/**
 * How what stands is coloured. It is the colour of a fact on the record — a
 * declined visit is red because the host said no, not because the visitor did
 * anything.
 */
export function tone(standing: Standing): Tone {
  if (standing === 'VALID') return 'good'
  if (standing === 'AWAITING_HOST' || standing === 'NOT_YET_VALID') return 'wait'
  if (standing === 'DECLINED' || standing === 'CANCELLED') return 'stop'
  return 'plain'
}

/**
 * Whether the gate is offered "Ask the host": when nothing stands that could
 * still be answered or used. While one waits or is valid there is nothing to
 * ask; the server refuses a second one anyway.
 */
export function canAsk(standing: Standing): boolean {
  return !['AWAITING_HOST', 'NOT_YET_VALID', 'VALID'].includes(standing)
}

/** The line at the top of the card on the first page. */
export function waitingHeading(items: Authorisation[]): string {
  return items.length === 1 ? '1 visit is waiting for your answer' : `${items.length} visits are waiting for your answer`
}

/** Who is waiting: the visitor and their company, or the contractor and the permit. */
export function whoWaits(a: Pick<Authorisation, 'subject'>): string {
  const s = a.subject
  if (s.kind === 'work_permit') return `${s.name ?? 'A contractor'} — work permit`
  return s.company ? `${s.name ?? 'A visitor'} (${s.company})` : s.name ?? 'A visitor'
}

/** Where and when, and that nobody in particular was asked when no host is named. */
export function waitingMeta(a: Pick<Authorisation, 'site_name' | 'valid_from' | 'valid_until' | 'purpose' | 'asked_of_me'
                                    | 'subject'>, format: (iso: string) => string): string {
  const about = a.subject.kind === 'work_permit' ? a.subject.detail : a.purpose
  const parts = [a.site_name, `${format(a.valid_from)} to ${format(a.valid_until)}`, about]
  if (!a.asked_of_me) parts.push('no host is named')
  return parts.filter(Boolean).join(' · ')
}

/** The kinds of document offered at the gate. The kind is recorded; a number never is. */
export const ID_KINDS = ['National identity card', 'Passport', 'Work pass', 'Driving licence', 'Company pass', 'Other']

/** Whether a reason is one: something other than spaces. */
export const isReason = (text: string) => text.trim().length > 0

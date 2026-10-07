/**
 * The rules of the phone's response card, apart from the card so they can be
 * tested with nothing mounted: which buttons a guard is offered and in what
 * order, the words for where a response stands, and how long is left to arrive.
 */
import type { Clock, May, MySending, ResponseState } from '@/api/responses'

export type StepKey = 'accept' | 'en_route' | 'arrived' | 'report' | 'decline'

export const STATE_LABEL: Record<ResponseState, string> = {
  SENT: 'You have been sent', ACCEPTED: 'You accepted', EN_ROUTE: 'You are on the way', ARRIVED: 'You are there',
  DECLINED: 'You said you cannot attend', STOOD_DOWN: 'You were stood down',
}

export interface Offered { key: StepKey; label: string; tone: 'go' | 'plain' | 'stop'; needsText: boolean }

/** In the order a guard reads them: the step forward first, the way out last. */
const STEPS: Offered[] = [
  { key: 'accept', label: 'Accept', tone: 'go', needsText: false },
  { key: 'en_route', label: 'On my way', tone: 'go', needsText: false },
  { key: 'arrived', label: 'I am there', tone: 'go', needsText: false },
  { key: 'report', label: 'Report what I found', tone: 'plain', needsText: true },
  { key: 'decline', label: 'I cannot attend', tone: 'stop', needsText: true },
]

/**
 * The buttons to show. The server says what may be done; this only orders
 * them. "Accept" stays beside "On my way": saying yes before moving is what
 * tells the desk to stop looking for somebody else.
 */
export function offered(may: May | undefined): Offered[] {
  if (!may) return []
  return STEPS.filter((s) => may[s.key])
}

/** A length of time in the words a person would use. */
export function span(seconds: number): string {
  const s = Math.abs(Math.round(seconds))
  if (s < 60) return `${s} s`
  const minutes = Math.round(s / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`
}

/** What the card says about the time to arrive — or nothing, when no time is set. */
export function arrivalText(arrival: Clock | undefined): { text: string; late: boolean } | null {
  if (!arrival || !arrival.due_at) return null
  if (!arrival.running) {
    return arrival.breached ? { text: 'You arrived after the time allowed', late: true }
      : { text: 'You arrived in time', late: false }
  }
  const left = arrival.seconds_left ?? 0
  return left < 0 ? { text: `Expected there ${span(left)} ago`, late: true }
    : { text: `Expected there in ${span(left)}`, late: false }
}

/** The one of my sendings that is this incident, if I am on it. */
export function sendingFor(items: MySending[] | undefined, incidentId: string): MySending | null {
  return (items ?? []).find((i) => i.id === incidentId) ?? null
}

/** The line under an incident's title on the home card: where, and where the response stands. */
export function summary(sending: MySending): string {
  const where = [sending.site_name, sending.camera_location ?? sending.camera_name].filter(Boolean).join(' · ')
  return [where, STATE_LABEL[sending.response.state]].filter(Boolean).join(' — ')
}

/** The server's reason for refusing a step, in its own words. */
export function refusal(err: unknown): string {
  const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  if (d && typeof d === 'object' && 'message' in d) return String((d as { message: unknown }).message)
  return 'That could not be recorded. Check your connection and try again.'
}

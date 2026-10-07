/**
 * The words the response screens use, in one place — so that the desk, the
 * dialogs and the settings say the same thing about the same state.
 */
import type { Clock, ClockName, Escalation, GuardResponse, Needs, Policy, ResponseState, StepKind, Trigger } from '@/api/incidentResponses'

export const STATE_LABEL: Record<ResponseState, string> = {
  SENT: 'Sent, not yet answered', ACCEPTED: 'Accepted', DECLINED: 'Cannot attend', EN_ROUTE: 'On the way',
  ARRIVED: 'There', STOOD_DOWN: 'Stood down',
}

export const STATE_COLOUR: Record<ResponseState, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  SENT: 'warning', ACCEPTED: 'info', DECLINED: 'error', EN_ROUTE: 'info', ARRIVED: 'success', STOOD_DOWN: 'default',
}

export const STEP_LABEL: Record<StepKind, string> = {
  SENT: 'Sent', ACCEPTED: 'Accepted', DECLINED: 'Said they cannot attend', EN_ROUTE: 'Set off', ARRIVED: 'Arrived',
  REPORTED: 'Reported', STOOD_DOWN: 'Stood down',
}

export const CLOCK_LABEL: Record<ClockName, string> = { ACKNOWLEDGE: 'Acknowledge', ARRIVAL: 'Arrive', RESOLVE: 'Resolve' }

export const NEEDS_LABEL: Record<Needs, string> = {
  DISPATCH: 'Nobody sent', ANSWER: 'Waiting for the guard to answer', ARRIVAL: 'Guard on the way',
  RESOLVE: 'Being dealt with',
}

/** What a policy watches for, as the choice a person makes when writing one. */
export const TRIGGER_LABEL: Record<Trigger, string> = {
  NOT_ACKNOWLEDGED: 'An incident is still not acknowledged', NOT_ARRIVED: 'A guard was sent and is still not there',
  NOT_RESOLVED: 'An incident is still not resolved',
}

/** A length of time in the words a person would use: 45 s, 3 min, 2 h 5 min, 3 days. */
export function span(seconds: number): string {
  const s = Math.abs(Math.round(seconds))
  if (s < 60) return `${s} s`
  const minutes = Math.round(s / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`
  return `${Math.round(hours / 24)} days`
}

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** How long ago something was, from the server's own "now". */
export function since(iso: string | null | undefined, asOf: string): string {
  if (!iso) return '—'
  return `${span((new Date(asOf).getTime() - new Date(iso).getTime()) / 1000)} ago`
}

export interface ClockReading { text: string; tone: 'none' | 'ok' | 'soon' | 'late' }

/** One clock, as the desk shows it. */
export function readClock(clock: Clock): ClockReading {
  if (!clock.due_at) return { text: '—', tone: 'none' }
  if (!clock.running) return clock.breached ? { text: 'Met late', tone: 'late' } : { text: 'In time', tone: 'ok' }
  const left = clock.seconds_left ?? 0
  if (left < 0) return { text: `Late by ${span(left)}`, tone: 'late' }
  return { text: `${span(left)} left`, tone: left <= 120 ? 'soon' : 'ok' }
}

export const TONE_COLOUR: Record<ClockReading['tone'], 'default' | 'success' | 'warning' | 'error'> = {
  none: 'default', ok: 'success', soon: 'warning', late: 'error',
}

/** Who the last response was, when it is over: "Tan cannot attend: holding a visitor". */
export function overBecause(last: GuardResponse | null): string | null {
  if (!last) return null
  const who = last.guard_name ?? 'The guard'
  if (last.state === 'DECLINED') return `${who} cannot attend: ${last.decline_reason ?? ''}`.trim()
  if (last.state === 'STOOD_DOWN') return `${who} was stood down: ${last.stand_down_reason ?? ''}`.trim()
  return null
}

/** Who a policy, or something that was told, is addressed to. */
export function addressedTo(x: Pick<Policy, 'notify_role_id' | 'notify_user_name'> & { notify_role_name?: string | null },
                            roles: { role_id: number; name: string }[] = []): string {
  if (x.notify_user_name) return x.notify_user_name
  if (x.notify_role_id !== null) {
    return x.notify_role_name ?? roles.find((r) => r.role_id === x.notify_role_id)?.name ?? `Role ${x.notify_role_id}`
  }
  return 'Nobody named'
}

/** A policy in a sentence. */
export function policySentence(p: Policy): string {
  const what = p.severity ? `a ${p.severity} incident` : 'an incident'
  const where = p.site_name ? ` at ${p.site_name}` : ''
  const after = `after ${span(p.after_seconds)}`
  if (p.trigger === 'NOT_ARRIVED') return `When a guard sent to ${what}${where} is still not there ${after}`
  return `When ${what}${where} is still not ${p.trigger === 'NOT_RESOLVED' ? 'resolved' : 'acknowledged'} ${after}`
}

/** What was told, in a sentence. */
export function toldSentence(e: Escalation): string {
  const what = e.kind === 'SLA_BREACH'
    ? { ACKNOWLEDGE: 'was not acknowledged in time', ARRIVAL: 'was not reached in time', RESOLVE: 'was not resolved in time' }[e.clock]
    : `— ${e.policy_name}`
  return `“${e.incident_title}” ${what}`
}

/** How many people something reached, in words that say when it reached nobody. */
export function reached(e: Escalation): string {
  const who = addressedTo(e)
  if (e.recipients === 0) {
    return who === 'Nobody named' ? 'Nobody is named to be told' : `${who}: nobody who may see this site`
  }
  return e.notify_user_name ? who : `${who} (${e.recipients} ${e.recipients === 1 ? 'person' : 'people'})`
}

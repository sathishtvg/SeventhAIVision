/**
 * The rules of the phone's situation screens, apart from the screens so they
 * can be tested with nothing mounted: which stage button comes next, which
 * decisions a phone offers, the words for each, and which live messages are
 * the layer's.
 */
import type {
  Authority, AuthorityAction, DecisionAction, DecisionStatus, MySituation,
} from '@/api/securityIntelligence'

export const DECISION_LABEL: Record<DecisionAction, string> = {
  MONITOR: 'Monitor', VERIFY: 'Verify', VIEW_CAMERA: 'View CCTV', VERIFY_WITH_DRONE: 'Verify with drone',
  DISPATCH_GUARD: 'Dispatch guard', ESCALATE: 'Escalate', INVESTIGATE: 'Investigate', CONTACT_SITE: 'Contact site',
  CREATE_INCIDENT: 'Create incident', ACKNOWLEDGE: 'Acknowledge', CONFIRM_INCIDENT: 'Confirm incident',
  REQUEST_ASSISTANCE: 'Request assistance', FALSE_POSITIVE: 'False positive', RESOLVE: 'Resolve',
}

export const STATUS_LABEL: Record<DecisionStatus, string> = {
  AWAITING: 'Awaiting a decision', ACKNOWLEDGED: 'Acknowledged', IN_HAND: 'In hand',
  PENDING_APPROVAL: 'Waiting for approval', ASSISTANCE_REQUESTED: 'Assistance requested', RESOLVED: 'Resolved',
  FALSE_POSITIVE: 'False positive',
}

export const SOURCE_LABEL: Record<string, string> = {
  CCTV_AI: 'CCTV', DRONE_PATROL: 'Drone', VIRTUAL_PATROL: 'Virtual patrol', LPR: 'Number plate',
  FACE_RECOGNITION: 'Face recognition', ACCESS_CONTROL: 'Access control', ALARM: 'Alarm', GUARD: 'Guard',
  SENSOR: 'Sensor', SYSTEM: 'System', OTHER: 'Other',
}

/**
 * The decisions a phone offers, in the order a person at the gate reads them.
 * Sending a guard, opening or confirming an incident, calling the site and
 * flying a drone are the command centre's, on the web: a phone that offered
 * them would be offering a guard the job of dispatching themselves.
 */
export const PHONE_DECISIONS: DecisionAction[] = [
  'ACKNOWLEDGE', 'INVESTIGATE', 'MONITOR', 'ESCALATE', 'REQUEST_ASSISTANCE', 'RESOLVE', 'FALSE_POSITIVE',
]

/** The decisions to show, each with whether it may be taken and why not. */
export function offered(authority: Authority | undefined): AuthorityAction[] {
  if (!authority) return []
  const by = new Map(authority.actions.map((a) => [a.action, a]))
  return PHONE_DECISIONS.map((a) => by.get(a)).filter((a): a is AuthorityAction => !!a)
}

/**
 * "Accept" is following what the layer put first — offered only when that
 * step is one a phone takes and this person may take it. It is still their
 * decision, recorded as theirs.
 */
export function acceptable(authority: Authority | undefined): AuthorityAction | null {
  const first = authority?.suggested_action
  if (!first || !PHONE_DECISIONS.includes(first)) return null
  const a = authority.actions.find((x) => x.action === first)
  return a && a.allowed && a.basis === 'FOLLOWED' ? a : null
}

/** What this person reports next on a situation they were sent to. */
export function nextStage(s: Pick<MySituation, 'assigned_to_me' | 'my_last'>): 'ACCEPTED' | 'ARRIVED' | null {
  if (s.my_last === 'ARRIVED') return null
  if (s.my_last === 'ACCEPTED') return 'ARRIVED'
  return s.assigned_to_me ? 'ACCEPTED' : 'ARRIVED'
}

export const STAGE_LABEL: Record<'ACCEPTED' | 'ARRIVED', string> = { ACCEPTED: 'Accept', ARRIVED: 'Arrived' }

/** Risk as words: the level and score, or that the layer has not assessed it. */
export function riskText(level: string | null | undefined, score: number | null | undefined): string {
  if (!level) return 'Not assessed yet'
  return score != null ? `Risk ${score}` : 'Risk —'
}

/** A confidence as a percentage, or that no source gave one — never 0%. */
export const pct = (v: number | null | undefined) => (v == null ? 'not given' : `${Math.round(v * 100)}%`)

/** The sources that reported, in words: "CCTV · Access control". */
export const sourcesText = (types: string[]) => types.map((t) => SOURCE_LABEL[t] ?? t).join(' · ')

/** Whether a live message is the layer's, so the list refreshes on it. */
export function isIntelRealtime(e: { event_type?: string }): boolean {
  return (e.event_type ?? '').startsWith('intel_')
}

/** One per press: sent again with a retry, so that one press is one decision. */
export function newClientRef(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = Math.floor(Math.random() * 16)
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

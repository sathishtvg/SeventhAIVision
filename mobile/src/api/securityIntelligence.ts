/**
 * AI Security Intelligence on the phone: the situations in front of this
 * person, what the layer suggests, what they may decide, and what they report
 * from the ground.
 *
 * The setup screens and the full decision history stay on the web. What belongs
 * in a pocket is the other half: something needs looking at, here is why, I
 * have it, I am there, this is what I see — and the handful of decisions a
 * person standing at the gate actually takes.
 *
 * Three things are kept apart here as they are on the server: what the layer
 * SUGGESTED, what a person DECIDED, and what a person REPORTED. A report is not
 * a decision and changes nothing else.
 *
 * Every path and field mirrors backend/app/routers/security_intelligence.py
 * and security_decisions.py. The repository test
 * backend/tests/test_intel_clients.py fails if a path in this file stops
 * existing on the server.
 */
import { apiClient } from './client'

const BASE = '/api/v1/security-intelligence'

export type RiskLevel = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export type DecisionStatus =
  | 'AWAITING' | 'ACKNOWLEDGED' | 'IN_HAND' | 'PENDING_APPROVAL' | 'ASSISTANCE_REQUESTED' | 'RESOLVED'
  | 'FALSE_POSITIVE'
export type DecisionAction =
  | 'MONITOR' | 'VERIFY' | 'VIEW_CAMERA' | 'VERIFY_WITH_DRONE' | 'DISPATCH_GUARD' | 'ESCALATE' | 'INVESTIGATE'
  | 'CONTACT_SITE' | 'CREATE_INCIDENT' | 'ACKNOWLEDGE' | 'CONFIRM_INCIDENT' | 'REQUEST_ASSISTANCE'
  | 'FALSE_POSITIVE' | 'RESOLVE'
export type DecisionBasis = 'FOLLOWED' | 'OVERRIDE' | 'CLOSING' | 'INDEPENDENT'
export type ReasonCode =
  | 'AUTHORISED_ACTIVITY' | 'ALREADY_HANDLED' | 'FALSE_DETECTION' | 'GUARD_RESPONDING' | 'MAINTENANCE'
  | 'EMERGENCY' | 'CAMERA_ISSUE' | 'OTHER'
export type ObservationKind = 'ACCEPTED' | 'ARRIVED' | 'OBSERVATION'

/** One open situation in front of this person. */
export interface MySituation {
  id: string
  situation_number: string
  title: string
  severity: string
  site_id: string | null
  site_name: string | null
  primary_camera_id: string | null
  primary_camera_name: string | null
  location_label: string | null
  latitude: number | null
  longitude: number | null
  started_at: string
  last_event_at: string
  event_count: number
  source_types: string[]
  /** The layer's assessment. Null until it has been assessed. */
  risk_score: number | null
  risk_level: RiskLevel | null
  /** What it appears to be, in the layer's words. */
  label: string | null
  kind: string | null
  /** Where it stands with people. */
  decision_status: DecisionStatus
  /** The command centre sent this person to it. */
  assigned_to_me: boolean
  dispatched_at: string | null
  dispatch_notes: string | null
  /** This person's own last report on it, so the next button is known. */
  my_last: 'ACCEPTED' | 'ARRIVED' | null
}

export interface SituationDetail {
  id: string
  situation_number: string
  title: string
  site_name: string | null
  primary_camera_id: string | null
  primary_camera_name: string | null
  location_label: string | null
  latitude: number | null
  longitude: number | null
  started_at: string
  event_count: number
  source_types: string[]
  risk_score: number | null
  risk_level: RiskLevel | null
  decision_status: DecisionStatus
  closed_at: string | null
  assessment: {
    id: string
    label: string
    summary: string
    risk_factors: { factor: string; points: number; detail: string }[]
    /** Three confidences, three fields. There is no single "confidence". */
    confidence: { detection: number | null; correlation: number | null; risk: number | null }
    unknowns: string[]
  } | null
  events: { id: string; source_type: string; title: string; occurred_at: string; camera_name: string | null
            is_duplicate: boolean }[]
  incident: { state: 'NONE' | 'PRELIMINARY' | 'CONFIRMED'; id: string | null }
}

/** What the layer suggests. Never a decision. */
export interface Recommendation {
  id: string
  rank: number
  action: DecisionAction
  reason: string
  recommendation_confidence: number
  available: boolean
  unavailable_reason: string | null
}

export interface Recommendations {
  /** Always false: these are suggestions. */
  is_decision: false
  assessment: { id: string } | null
  recommendations: Recommendation[]
}

export interface AuthorityAction {
  action: DecisionAction
  allowed: boolean
  how: 'ALONE' | 'WITH_APPROVAL' | null
  basis: DecisionBasis
  needs_reason: boolean
  needs: ('guard_user_id' | 'escalate_to_user_id')[]
  why_not: string | null
}

export interface Authority {
  closed: boolean
  in_reach: boolean
  suggested_action: DecisionAction | null
  reasons: { code: ReasonCode; label: string }[]
  actions: AuthorityAction[]
}

export interface Observation {
  id: string
  kind: ObservationKind
  note: string | null
  name: string | null
  role_id: number
  latitude: number | null
  longitude: number | null
  observed_at: string
}

export interface Position { latitude: number; longitude: number }

export const getMySituations = () =>
  apiClient.get<MySituation[]>(`${BASE}/my-situations`).then((r) => r.data ?? [])

export const getSituation = (id: string) =>
  apiClient.get<SituationDetail>(`${BASE}/situations/${id}`).then((r) => r.data)

export const getSituationRecommendations = (id: string) =>
  apiClient.get<Recommendations>(`${BASE}/situations/${id}/recommendations`).then((r) => r.data)

/** That this person looked at what was suggested. Decides nothing. */
export const recordSituationReview = (id: string) =>
  apiClient.post(`${BASE}/situations/${id}/reviews`, { via: 'mobile' }).then((r) => r.data)

export const getSituationAuthority = (id: string) =>
  apiClient.get<Authority>(`${BASE}/situations/${id}/authority`).then((r) => r.data)

/** The people an escalation can go to. Chosen by the person, not by the layer. */
export const getSituationResponders = (id: string) =>
  apiClient
    .get<{ escalation: { user_id: string; name: string; role_id: number }[] }>(`${BASE}/situations/${id}/responders`)
    .then((r) => r.data)

export interface DecisionInput {
  action: DecisionAction
  reason_code?: ReasonCode
  note?: string
  seen_assessment_id?: string
  escalate_to_user_id?: string
  /** One per press, sent again with a retry, so that one press is one decision. */
  client_ref: string
}

/**
 * Record this person's decision. Where the decision policy lets their role
 * decide here only with approval, the answer's `state` is PENDING_APPROVAL and
 * nothing has been carried out.
 */
export const decideSituation = (id: string, body: DecisionInput) =>
  apiClient
    .post<{ id: string; action: DecisionAction; state: string; actions: { action: string; result: string }[] }>(
      `${BASE}/situations/${id}/decisions`, { ...body, via: 'mobile' })
    .then((r) => r.data)

export const getSituationObservations = (id: string) =>
  apiClient.get<Observation[]>(`${BASE}/situations/${id}/observations`).then((r) => r.data ?? [])

/**
 * Report from the ground. An observation needs to say what was seen — the
 * server refuses one without (422), and so does this function before spending
 * the round trip. A report changes no alert, incident or dispatch.
 */
export const reportFromSituation = (id: string, kind: ObservationKind, opts: {
  note?: string; position?: Position; clientRef: string
}) => {
  const note = opts.note?.trim()
  if (kind === 'OBSERVATION' && !note) return Promise.reject(new Error('Say what you see.'))
  return apiClient
    .post<{ id: string; kind: ObservationKind }>(`${BASE}/situations/${id}/observations`, {
      kind, note: note || undefined, via: 'mobile', client_ref: opts.clientRef,
      latitude: opts.position?.latitude, longitude: opts.position?.longitude,
    })
    .then((r) => r.data)
}

/** A readable message from a failed call: the server's own reason when it gave one. */
export function intelApiError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string }
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

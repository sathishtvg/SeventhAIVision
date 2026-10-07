/**
 * Guard response API — every call the response screens make, typed to what the
 * backend returns (backend/app/routers/incident_responses.py).
 *
 * Nothing here dispatches a guard. A person does that through the dispatch
 * that has always done it (`dispatchGuard` in ./guards); `recommend` only
 * lists who could be sent and why.
 */
import { apiClient } from './client'

const BASE = '/api/v1/incident-responses'

export type ResponseState = 'SENT' | 'ACCEPTED' | 'DECLINED' | 'EN_ROUTE' | 'ARRIVED' | 'STOOD_DOWN'
export type StepKind = ResponseState | 'REPORTED'
export type ClockName = 'ACKNOWLEDGE' | 'ARRIVAL' | 'RESOLVE'
export type Trigger = 'NOT_ACKNOWLEDGED' | 'NOT_ARRIVED' | 'NOT_RESOLVED'
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'
/** What an open incident is waiting for. */
export type Needs = 'DISPATCH' | 'ANSWER' | 'ARRIVAL' | 'RESOLVE'
export type DeskView = 'active' | 'waiting' | 'sent' | 'late'

export interface Clock {
  started_at: string | null
  /** Null when there is no clock: no times set for the severity, or nobody sent. */
  due_at: string | null
  met_at: string | null
  breached: boolean
  running: boolean
  /** Negative once it has run out. Null when it is not running. */
  seconds_left: number | null
}
export type Clocks = Record<ClockName, Clock>

export interface IncidentSummary {
  id: string
  title: string
  description: string | null
  severity: Severity
  status: string
  created_at: string
  resolved_at: string | null
  site_id: string | null
  site_name: string | null
  camera_id: string | null
  camera_name: string | null
  camera_location: string | null
  dispatched_guard_id: string | null
  dispatched_guard_name: string | null
  dispatched_at: string | null
  dispatch_notes: string | null
  guard_arrived_at: string | null
  sla_deadline_at: string | null
  sla_breached: boolean
  escalated_at: string | null
  acknowledged_at: string | null
}

export interface GuardResponse {
  id: string
  incident_id: string
  guard_user_id: string | null
  guard_name: string | null
  dispatched_at: string
  state: ResponseState
  accepted_at: string | null
  declined_at: string | null
  decline_reason: string | null
  en_route_at: string | null
  arrived_at: string | null
  stood_down_at: string | null
  stood_down_by_name: string | null
  stand_down_reason: string | null
}

export interface May {
  accept: boolean; decline: boolean; en_route: boolean; arrived: boolean; report: boolean; stand_down: boolean
}

export interface DeskItem extends IncidentSummary {
  /** The response of the sending that stands now. Null when nobody is sent. */
  response: GuardResponse | null
  /** The most recent response, even one that is over: who declined, who was stood down. */
  last_response: GuardResponse | null
  clocks: Clocks
  late: ClockName[]
  /** False when the clocks are off, or the incident is older than when they were switched on. */
  judged: boolean
  needs: Needs
  /** How many times somebody was told about this incident. */
  escalations: number
  may: May
}

export interface Desk {
  items: DeskItem[]
  has_more: boolean
  view: DeskView
  hours: number
  as_of: string
  counts: { waiting: number; unanswered: number; on_the_way: number; late: number }
  sla_enabled: boolean
  sla_since: string | null
  can_dispatch: boolean
  can_manage: boolean
  note: string
}

export interface ScorePart { factor: string; points: number; detail: string }

export interface RankedGuard {
  user_id: string
  full_name: string | null
  site_name: string | null
  latitude: number | null
  longitude: number | null
  position_source: string | null
  position_at: string | null
  position_age_s: number | null
  stale: boolean
  available: boolean
  busy_incident_id: string | null
  emergency_id: string | null
  distance_m: number | null
  score: number
  parts: ScorePart[]
}

export interface Recommendation {
  incident_id: string
  incident: IncidentSummary
  guards: RankedGuard[]
  /** Always false: this is a reading, and a person decides. */
  is_decision: false
  located: boolean
  note: string
  why_nobody?: string
  site_requires?: string[]
}

export interface Step {
  id: string
  response_id: string
  step: StepKind
  actor_user_id: string | null
  actor_name: string | null
  note: string | null
  latitude: number | null
  longitude: number | null
  occurred_at: string
}

export interface Escalation {
  id: string
  incident_id: string
  incident_title: string
  severity: Severity
  site_name: string | null
  kind: 'SLA_BREACH' | 'POLICY_STEP'
  clock: ClockName
  policy_name: string | null
  due_at: string
  notify_role_id: number | null
  notify_role_name: string | null
  notify_user_id: string | null
  notify_user_name: string | null
  /** How many people it was addressed to. */
  recipients: number
  notification_sent: boolean
  created_at: string
}

export interface ResponseDetail {
  incident: IncidentSummary
  response: GuardResponse | null
  responses: GuardResponse[]
  steps: Step[]
  clocks: Clocks
  judged: boolean
  sla_enabled: boolean
  escalations: Escalation[]
  as_of: string
  may: May
}

export interface SeverityTimes {
  severity: Severity
  set: boolean
  ack_within_seconds?: number
  dispatch_within_seconds?: number
  resolve_within_seconds?: number
  escalation_user_id?: string | null
  escalation_user_name?: string | null
}

export interface ResponseSettings {
  sla_enabled: boolean
  sla_since: string | null
  times: SeverityTimes[]
  triggers: Trigger[]
  severities: Severity[]
  notify_roles: { role_id: number; name: string }[]
  can_manage: boolean
  note: string
}

export interface Policy {
  id: string
  name: string
  site_id: string | null
  site_name: string | null
  severity: Severity | null
  trigger: Trigger
  after_seconds: number
  notify_role_id: number | null
  notify_user_id: string | null
  notify_user_name: string | null
  is_active: boolean
}

export interface PolicyInput {
  name: string
  site_id: string | null
  severity: Severity | null
  trigger: Trigger
  after_seconds: number
  notify_role_id: number | null
  notify_user_id: string | null
}

export const getDesk = (params: { site_id?: string; view?: DeskView; hours?: number } = {}) =>
  apiClient.get<Desk>(`${BASE}/desk`, { params }).then((r) => r.data)

export const recommend = (incidentId: string) =>
  apiClient.get<Recommendation>(`${BASE}/recommend`, { params: { incident_id: incidentId } }).then((r) => r.data)

export const getResponse = (incidentId: string) =>
  apiClient.get<ResponseDetail>(`${BASE}/${incidentId}`).then((r) => r.data)

export const standDown = (incidentId: string, reason: string) =>
  apiClient.post<ResponseDetail>(`${BASE}/${incidentId}/stand-down`, { reason }).then((r) => r.data)

export const getResponseSettings = () => apiClient.get<ResponseSettings>(`${BASE}/settings`).then((r) => r.data)

export const switchClocks = (on: boolean) =>
  apiClient.put<{ sla_enabled: boolean; sla_since: string | null; changed: boolean }>(
    `${BASE}/settings`, { sla_enabled: on }).then((r) => r.data)

/** The times themselves are saved through the endpoint that has always held them. */
export const saveTimes = (severity: Severity, body: {
  ack_within_seconds: number; dispatch_within_seconds: number; resolve_within_seconds: number
  escalation_user_id: string | null
}) => apiClient.put(`/api/v1/sla/configs/${severity}`, body).then((r) => r.data)

export const listPolicies = (includeRetired = false) =>
  apiClient.get<{ items: Policy[]; can_manage: boolean }>(`${BASE}/policies`, {
    params: { include_retired: includeRetired } }).then((r) => r.data)

export const writePolicy = (body: PolicyInput) => apiClient.post<Policy>(`${BASE}/policies`, body).then((r) => r.data)

export const changePolicy = (id: string, body: Partial<Omit<PolicyInput, 'trigger'>>) =>
  apiClient.patch<Policy>(`${BASE}/policies/${id}`, body).then((r) => r.data)

export const retirePolicy = (id: string) => apiClient.post(`${BASE}/policies/${id}/retire`).then((r) => r.data)
export const restorePolicy = (id: string) => apiClient.post(`${BASE}/policies/${id}/restore`).then((r) => r.data)

export const listEscalations = (params: { incident_id?: string; hours?: number; limit?: number } = {}) =>
  apiClient.get<{ items: Escalation[] }>(`${BASE}/escalations`, { params }).then((r) => r.data)

/** The server's reason, in its own words: a refusal here says what to do instead. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  if (d && typeof d === 'object' && 'message' in d) return String((d as { message: unknown }).message)
  return e?.message ?? 'Something went wrong.'
}

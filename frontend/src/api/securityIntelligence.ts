/**
 * AI Security Intelligence API — every call the intelligence screens make,
 * typed to what the backend returns (backend/app/routers/security_intelligence.py
 * and security_decisions.py).
 *
 * Three things are kept apart here as they are on the server, and no type
 * merges them: what the layer SUGGESTED (Recommendation), what a person
 * DECIDED (Decision), and what the platform then DID (ActionRow).
 */
import { apiClient } from './client'

const BASE = '/api/v1/security-intelligence'

// ── Vocabularies ─────────────────────────────────────────────────────────────

export type RiskLevel = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical'
export type SourceType =
  | 'CCTV_AI' | 'DRONE_PATROL' | 'VIRTUAL_PATROL' | 'LPR' | 'FACE_RECOGNITION' | 'ACCESS_CONTROL'
  | 'ALARM' | 'GUARD' | 'SENSOR' | 'SYSTEM' | 'OTHER'
export type SituationKind =
  | 'GUARD_EMERGENCY' | 'WEAPON' | 'FIRE_SMOKE' | 'DOOR_FORCED' | 'ACCESS_REFUSED' | 'ALARM' | 'FALL'
  | 'BLOCK_LISTED' | 'RESTRICTED_ZONE' | 'PATROL_FINDING' | 'CAMERA_OFFLINE' | 'ACTIVITY'
/** The nine steps the layer can suggest. */
export type StepAction =
  | 'MONITOR' | 'VERIFY' | 'VIEW_CAMERA' | 'VERIFY_WITH_DRONE' | 'DISPATCH_GUARD' | 'ESCALATE'
  | 'INVESTIGATE' | 'CONTACT_SITE' | 'CREATE_INCIDENT'
/** Everything a person can decide: the nine steps, and five only a person can choose. */
export type DecisionAction =
  | StepAction | 'ACKNOWLEDGE' | 'CONFIRM_INCIDENT' | 'REQUEST_ASSISTANCE' | 'FALSE_POSITIVE' | 'RESOLVE'
export type DecisionBasis = 'FOLLOWED' | 'OVERRIDE' | 'CLOSING' | 'INDEPENDENT'
export type DecisionStatus =
  | 'AWAITING' | 'ACKNOWLEDGED' | 'IN_HAND' | 'PENDING_APPROVAL' | 'ASSISTANCE_REQUESTED' | 'RESOLVED'
  | 'FALSE_POSITIVE'
export type DecisionState = 'EFFECTIVE' | 'PENDING_APPROVAL' | 'APPROVED' | 'REJECTED'
export type ReasonCode =
  | 'AUTHORISED_ACTIVITY' | 'ALREADY_HANDLED' | 'FALSE_DETECTION' | 'GUARD_RESPONDING' | 'MAINTENANCE'
  | 'EMERGENCY' | 'CAMERA_ISSUE' | 'OTHER'
export type IncidentState = 'NONE' | 'PRELIMINARY' | 'CONFIRMED'
export type Priority = 'LOW' | 'MEDIUM' | 'HIGH' | 'URGENT'

export const RISK_LEVELS: RiskLevel[] = ['INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
export const DECISION_STATUSES: DecisionStatus[] = [
  'AWAITING', 'ACKNOWLEDGED', 'IN_HAND', 'PENDING_APPROVAL', 'ASSISTANCE_REQUESTED', 'RESOLVED', 'FALSE_POSITIVE',
]

export interface Paged<T> { items: T[]; total: number; limit: number; offset: number; has_more: boolean }

// ── Situations ───────────────────────────────────────────────────────────────

export interface Situation {
  id: string
  situation_number: string
  title: string
  /** Whether events are still joining it — not whether a person has dealt with it. */
  status: 'ACTIVE' | 'SETTLED'
  /** The most severe event's own severity. Not the risk. */
  severity: Severity
  site_id: string | null
  site_name: string | null
  started_at: string
  last_event_at: string
  event_count: number
  duplicate_count: number
  source_types: SourceType[]
  primary_camera_id: string | null
  primary_camera_name: string | null
  location_label: string | null
  correlation_confidence: number | null
  /** The layer's assessment in context. Null until it has been assessed. */
  risk_score: number | null
  risk_level: RiskLevel | null
  assessed_at: string | null
  /** Where it stands with the people responsible. Set only by a person's decision. */
  decision_status: DecisionStatus
  last_decided_at: string | null
  closed_at: string | null
  incident_id: string | null
  incident_confirmed_at: string | null
  /** The layer has assessed it again since the last decision, and it is still open. The decision stands. */
  reassessed_since_decision?: boolean
}

export interface Factor { factor: string; points: number; detail: string }
export interface Statement { kind: string; text: string; source: string }

/** Three confidences, three fields. There is no single "confidence". */
export interface AssessmentConfidence {
  detection: number | null
  correlation: number | null
  risk: number | null
}

export interface Assessment {
  id: string
  sequence: number
  assessed_at: string
  kind: SituationKind
  label: string
  summary: string
  risk_score: number
  risk_level: RiskLevel
  risk_factors: Factor[]
  normality_score: number | null
  anomaly_score: number | null
  normality_factors: Factor[]
  normality_basis: string | null
  confidence: AssessmentConfidence
  unknowns: string[]
  event_count: number
  engine_version: string
  statements?: Statement[]
  expected?: string[]
}

export interface SituationEvent {
  id: string
  source_type: SourceType
  event_type: string
  occurred_at: string
  severity: Severity
  title: string
  camera_id: string | null
  camera_name: string | null
  alert_id: string | null
  incident_id: string | null
  subject_kind: string
  subject_verdict: string | null
  confidence: number | null
  location_label: string | null
  method: string
  reason: string
  link_confidence: number | null
  is_duplicate: boolean
  /** As the source recorded them: a patrol's question and answer, how long a drone held and what more it saw. */
  attributes?: Record<string, unknown>
}

export interface SituationSource {
  source_type: SourceType
  camera_id: string | null
  label: string | null
  events: number
  first_at: string
  last_at: string
}

export interface SituationDetail extends Situation {
  sources: SituationSource[]
  events: SituationEvent[]
  assessment: Assessment | null
  incident: { state: IncidentState; id: string | null; opened_by_the_platform: boolean | null }
}

export interface SituationFilters {
  status?: string
  site_id?: string
  risk_level?: string
  decision_status?: string
  open?: boolean
  sort?: 'recent' | 'risk'
  limit?: number
  offset?: number
}

export const listSituations = (params: SituationFilters = {}) =>
  apiClient.get<Paged<Situation>>(`${BASE}/situations`, { params }).then((r) => r.data)

export const getSituation = (id: string) =>
  apiClient.get<SituationDetail>(`${BASE}/situations/${id}`).then((r) => r.data)

export const listAssessments = (id: string) =>
  apiClient.get<Assessment[]>(`${BASE}/situations/${id}/assessments`).then((r) => r.data)

// ── Recommendations: what the layer suggests. Never a decision. ──────────────

export interface RecommendedCamera {
  id: string
  name: string
  state: string
  /** It reported this, it is next to a camera that did, or it is the camera of a drone that saw this and is in the air. */
  relation: 'reported' | 'neighbour' | 'drone'
}

export interface Recommendation {
  id: string
  rank: number
  action: StepAction
  priority: Priority
  reason: string
  /** How sure the layer is of this suggestion — the fourth confidence. */
  recommendation_confidence: number
  limited_by: 'RULE' | 'DETECTION' | 'CORRELATION' | 'RISK'
  available: boolean
  unavailable_reason: string | null
  supporting: {
    risk?: { level: RiskLevel; score: number | null }
    rests_on?: string[]
    facts?: string[]
    cameras?: RecommendedCamera[]
    incident?: { id: string; status: string | null }
  }
  created_at: string
}

export interface Recommendations {
  situation_id: string
  /** Always false: these are suggestions. */
  is_decision: false
  current: boolean
  assessment: {
    id: string; sequence: number; assessed_at: string; kind: SituationKind; label: string
    risk_level: RiskLevel; risk_score: number; confidence: AssessmentConfidence
  } | null
  recommendations: Recommendation[]
}

export const getRecommendations = (id: string) =>
  apiClient.get<Recommendations>(`${BASE}/situations/${id}/recommendations`).then((r) => r.data)

/** That the signed-in person has looked at what was suggested. Decides nothing. */
export const recordReview = (id: string) =>
  apiClient.post<{ recorded: boolean; assessment_id: string | null }>(`${BASE}/situations/${id}/reviews`, { via: 'web' })
    .then((r) => r.data)

// ── Decisions: what a person chose. ──────────────────────────────────────────

export interface AuthorityAction {
  action: DecisionAction
  allowed: boolean
  how: 'ALONE' | 'WITH_APPROVAL' | null
  basis: DecisionBasis
  needs_reason: boolean
  needs: ('guard_user_id' | 'escalate_to_user_id')[]
  why_not: string | null
  /** What the platform would then do, in order. */
  carries_out: string[]
}

export interface Authority {
  situation_id: string
  decision_status: DecisionStatus
  closed: boolean
  risk_level: RiskLevel | null
  incident: { state: IncidentState; id: string | null }
  policy: { source: 'default' | 'tenant' | 'site'; rule: { alone?: RiskLevel | null; with_approval?: RiskLevel | null } }
  may_override: boolean
  may_approve: boolean
  suggested_action: StepAction | null
  reasons: { code: ReasonCode; label: string }[]
  actions: AuthorityAction[]
}

export const getAuthority = (id: string) =>
  apiClient.get<Authority>(`${BASE}/situations/${id}/authority`).then((r) => r.data)

/** Who a decision can name. Chosen by the officer — the layer does not choose. */
export interface Responders {
  guards: { user_id: string; name: string; on_shift_here: boolean; on_shift: boolean }[]
  escalation: { user_id: string; name: string; role_id: number }[]
}

export const getResponders = (id: string) =>
  apiClient.get<Responders>(`${BASE}/situations/${id}/responders`).then((r) => r.data)

/** What the platform did for a decision: one step, through one existing function. */
export interface ActionRow {
  sequence: number
  action: string
  through: string | null
  target_type: 'alert' | 'incident' | 'drone_event' | 'drone_look' | 'drone_mission' | 'drone_flight' | null
  target_id: string | null
  result: 'OK' | 'FAILED' | 'SKIPPED' | 'RECORDED'
  detail: string | null
  executed_at: string
  executed_by_user_id: string | null
}

export interface Person { user_id: string | null; name: string | null; role_id: number }

/** Who or what a decision named: the guard, the senior, or how a drone was to look. */
export interface DecisionParams {
  guard_user_id?: string
  escalate_to_user_id?: string
  drone_event_id?: string
  hold_seconds?: number
  drone_mission_id?: string
}

export interface Decision {
  id: string
  situation_id: string
  situation_number: string
  decided_at: string
  action: DecisionAction
  basis: DecisionBasis
  is_override: boolean
  reason_code: ReasonCode | null
  reason: string | null
  note: string | null
  decided_by: Person
  via: 'web' | 'mobile'
  /** What the layer had put first when this was decided. */
  suggested_action: StepAction | null
  recommendation_id: string | null
  assessment_id: string | null
  decided_on_an_earlier_assessment: boolean
  risk_level: RiskLevel | null
  risk_score: number | null
  authority: 'ALONE' | 'WITH_APPROVAL'
  state: DecisionState
  policy: { source?: string; said?: string }
  params: DecisionParams
  approval: { verdict: 'APPROVED' | 'REJECTED'; note: string | null; at: string; by: Person } | null
  actions: ActionRow[]
  replayed?: boolean
}

export interface DecisionInput {
  action: DecisionAction
  reason_code?: ReasonCode
  note?: string
  seen_assessment_id?: string
  guard_user_id?: string
  escalate_to_user_id?: string
  /** With VERIFY_WITH_DRONE, the officer's own choice of one: hold the flight that saw this sighting… */
  drone_event_id?: string
  hold_seconds?: number
  /** …or start this mission, which the site already has. With neither, the decision is a record. */
  drone_mission_id?: string
  /** One per press, sent again with a retry, so that one press is one decision. */
  client_ref: string
}

export const decide = (id: string, body: DecisionInput) =>
  apiClient.post<Decision>(`${BASE}/situations/${id}/decisions`, { ...body, via: 'web' }).then((r) => r.data)

export interface Trail {
  situation_id: string
  decision_status: DecisionStatus
  closed_at: string | null
  incident: { state: IncidentState; id: string | null; status: string | null }
  reviews: { user_id: string | null; name: string | null; role_id: number; assessment_id: string; via: string
             viewed_at: string }[]
  decisions: Decision[]
}

export const getTrail = (id: string) =>
  apiClient.get<Trail>(`${BASE}/situations/${id}/decisions`).then((r) => r.data)

export const listDecisions = (params: { state?: 'pending_approval'; site_id?: string; action?: string
                                        basis?: string; limit?: number; offset?: number } = {}) =>
  apiClient.get<Paged<Decision>>(`${BASE}/decisions`, { params }).then((r) => r.data)

export const approveDecision = (decisionId: string, note?: string) =>
  apiClient.post<Decision>(`${BASE}/decisions/${decisionId}/approve`, { note: note || undefined }).then((r) => r.data)

export const rejectDecision = (decisionId: string, note: string) =>
  apiClient.post<Decision>(`${BASE}/decisions/${decisionId}/reject`, { note }).then((r) => r.data)

// ── The timeline: one situation, in the order it happened ────────────────────

/** Whose an entry is. It follows from the record the entry was read from; it is never guessed. */
export type TimelineActor = 'SOURCE' | 'AI' | 'PERSON' | 'PLATFORM'
export type TimelineKind =
  | 'EVENT' | 'REPEATS' | 'ASSESSMENT' | 'RECOMMENDATION' | 'REVIEW' | 'DECISION' | 'APPROVAL' | 'ACTION'
  | 'OBSERVATION' | 'INCIDENT'

export interface TimelineEntry {
  at: string
  kind: TimelineKind
  actor: TimelineActor
  title: string
  detail: string | null
  /** The person, for an entry that is a person's. */
  who: Person | null
  /** The record this entry was read from. */
  ref: { type: 'event' | 'assessment' | 'recommendation' | 'decision' | 'observation' | 'incident'; id: string }
  source_type?: SourceType
  where?: string | null
  /** For repeats folded into one line: how many, and when the last was. */
  count?: number
  until?: string
  /** Always false, and only on what the layer suggested. */
  is_decision?: false
  action?: string
  result?: 'OK' | 'FAILED' | 'SKIPPED' | 'RECORDED'
  /** The existing function a step went through. */
  through?: string | null
  via?: string | null
  risk_level?: RiskLevel
  risk_score?: number
}

export interface Timeline {
  situation_id: string
  situation_number: string
  started_at: string
  closed_at: string | null
  decision_status: DecisionStatus
  /** False for a reader who may not see what the layer suggested: those entries are left out. */
  suggestions_shown: boolean
  counts: Record<TimelineActor, number>
  entries: TimelineEntry[]
}

export const getTimeline = (id: string) =>
  apiClient.get<Timeline>(`${BASE}/situations/${id}/timeline`).then((r) => r.data)

// ── The AI-assisted summary: fixed templates over the records ────────────────

export interface SituationSummary {
  /** Always true: the summary is the layer's, and says so wherever it goes. */
  is_ai_assisted: true
  label: string
  made_of: string
  /** The zone the times in the sentences are told in. */
  timezone: string
  situation_number: string
  suggestions_shown: boolean
  /** Each sentence with the records it was read from. */
  sentences: { text: string; refs: { type: string; id: string }[] }[]
  text: string
}

export const getSummary = (id: string) =>
  apiClient.get<SituationSummary>(`${BASE}/situations/${id}/summary`).then((r) => r.data)

// ── Evidence: references to what the platform kept ───────────────────────────

export type EvidenceKind = 'SNAPSHOT' | 'CLIP' | 'RECORDING' | 'DRONE_MEDIA' | 'PATROL_SNAPSHOT'

/** One thing the platform kept. The layer serves no media: `served_at` is the platform's own endpoint. */
export interface EvidenceItem {
  kind: EvidenceKind
  id: string
  what: string
  captured_at: string
  /** The event of this situation it belongs to; null for something kept with the incident. */
  event_id: string | null
  camera_name: string | null
  checksum_sha256: string | null
  kept: string | null
  media_type: 'image' | 'video'
  /** The existing permission its own endpoint asks for, and whether the caller holds it. */
  needs: string
  may_open: boolean
  /** Where the platform records that it was opened. */
  logged_in: 'evidence_access_log' | 'audit_log'
  served_at: { path: string; token_in_query: boolean }
  /** For a recording: how far into it the event is. */
  offset_seconds?: number | null
}

export interface SituationEvidence {
  situation_id: string
  summary: { total: number; may_open: number; by_kind: Record<EvidenceKind, number> }
  items: EvidenceItem[]
}

export const getSituationEvidence = (id: string) =>
  apiClient.get<SituationEvidence>(`${BASE}/situations/${id}/evidence`).then((r) => r.data)

/** What opening one item answered: recorded first, then where to fetch it. */
export interface EvidenceOpened {
  kind: EvidenceKind
  id: string
  what: string
  served_at: { path: string; token_in_query: boolean }
  media_type: 'image' | 'video'
  checksum_sha256: string | null
  custody_entry: string | null
  audited: true
}

/** Records that the caller is opening this piece of the situation's evidence. The file is fetched afterwards. */
export const openSituationEvidence = (id: string, item: { kind: EvidenceKind; id: string }) =>
  apiClient.post<EvidenceOpened>(`${BASE}/situations/${id}/evidence/open`, item).then((r) => r.data)

// ── Drones: what one could be asked, and what came back ──────────────────────

export interface DroneSighting {
  event_id: string
  drone_event_id: string
  title: string
  occurred_at: string
  location_label: string | null
  session_id: string | null
  session_number: string | null
  mission_name: string | null
  drone_name: string | null
  flight_status: string | null
  drone_risk_level: string | null
  detection_count: number | null
  /** A first answer: the drone module makes its own checks when it is asked. */
  can_hold: boolean
  why_not: string | null
}

export interface DroneMission {
  mission_id: string
  name: string
  drone_name: string | null
  drone_status: string | null
  route_name: string | null
  battery_level: number | null
  can_launch: boolean
  why_not: string | null
}

export interface DroneLook {
  id: string
  drone_event_id: string
  status: 'REQUESTED' | 'HOLDING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
  hold_seconds: number
  started_at: string | null
  ends_at: string | null
  completed_at: string | null
  result: { detections_added?: number; risk_before?: string; risk_after?: string; reason?: string }
}

/** One thing a decision on this situation asked of a drone, and what has come of it. */
export interface DroneAsked {
  action_id: string
  decision_id: string
  action: 'DRONE_HOLD' | 'DRONE_LAUNCH'
  result: 'OK' | 'FAILED' | 'SKIPPED'
  detail: string | null
  asked_at: string
  look: DroneLook | null
  flight: { id: string; session_number: string; mission_name: string | null; status: string; started_at: string | null
            ended_at: string | null; event_count: number | null; blocked_reason: string | null } | null
}

export interface DronePicture {
  situation_id: string
  closed: boolean
  licence: { ok: boolean; problem: string | null }
  may: { hold: boolean; launch: boolean; see_missions: boolean }
  hold_seconds: { min: number; max: number; default: number }
  notes: { hold: string; launch: string }
  sightings: DroneSighting[]
  missions: DroneMission[]
  asked: DroneAsked[]
  /** Looks at these sightings asked for from the drone screens, not by a decision here. */
  other_looks: (DroneLook & { asked_at: string })[]
}

export const getSituationDrone = (id: string) =>
  apiClient.get<DronePicture>(`${BASE}/situations/${id}/aerial`).then((r) => r.data)

// ── From the ground: what the person dealing with it reports ─────────────────

/** A statement by a person, not a decision: it changes no alert, incident or dispatch. */
export interface Observation {
  id: string
  kind: 'ACCEPTED' | 'ARRIVED' | 'OBSERVATION'
  note: string | null
  user_id: string | null
  name: string | null
  role_id: number
  latitude: number | null
  longitude: number | null
  via: 'web' | 'mobile'
  observed_at: string
}

export const getObservations = (id: string) =>
  apiClient.get<Observation[]>(`${BASE}/situations/${id}/observations`).then((r) => r.data)

// ── Who may decide ───────────────────────────────────────────────────────────

export type PolicyRoles = Record<string, { alone?: RiskLevel | null; with_approval?: RiskLevel | null }>

export interface StoredPolicy {
  roles: PolicyRoles
  effective: PolicyRoles
  note: string | null
  updated_at: string
}

export interface DecisionPolicy {
  roles: Record<string, string>
  levels: RiskLevel[]
  default: PolicyRoles
  always_allowed: DecisionAction[]
  tenant: StoredPolicy | null
  sites: (StoredPolicy & { site_id: string; site_name: string })[]
}

export const getDecisionPolicy = () =>
  apiClient.get<DecisionPolicy>(`${BASE}/decision-policy`).then((r) => r.data)

export const putDecisionPolicy = (roles: PolicyRoles, note?: string) =>
  apiClient.put<DecisionPolicy>(`${BASE}/decision-policy`, { roles, note: note || undefined }).then((r) => r.data)

export const putSiteDecisionPolicy = (siteId: string, roles: PolicyRoles, note?: string) =>
  apiClient.put<DecisionPolicy>(`${BASE}/decision-policy/sites/${siteId}`, { roles, note: note || undefined })
    .then((r) => r.data)

export const deleteSiteDecisionPolicy = (siteId: string) =>
  apiClient.delete<DecisionPolicy>(`${BASE}/decision-policy/sites/${siteId}`).then((r) => r.data)

// ── Whether it is running ────────────────────────────────────────────────────

export interface IntelStatus {
  /** Off until an administrator switches `intel.enabled` on. */
  enabled: boolean
  /** `unknown` when the runner could not be asked — never assumed to be running. */
  runner: { state: 'running' | 'degraded' | 'stopped' | 'unknown'; last_seen_at: string | null }
  last_24_hours: Record<string, number>
  sources: { source: string; read_from: string | null; last_run_at: string | null; last_count: number
             total_count: number; last_error: string | null }[]
}

export const getIntelStatus = () =>
  apiClient.get<IntelStatus>(`${BASE}/status`).then((r) => r.data)

// ── Site profiles: what an administrator says a site expects ─────────────────

export type BusinessHours = Record<string, [string, string][]>

export interface SiteProfile {
  site_id: string
  site_name: string
  has_profile: boolean
  timezone: string | null
  /** Null means not defined: the layer then never says "after hours" for the site. */
  business_hours: BusinessHours | null
  closed_on_public_holidays: boolean
  criticality: 'low' | 'medium' | 'high' | 'critical' | null
  notes: string | null
}

export const listSiteProfiles = () =>
  apiClient.get<SiteProfile[]>(`${BASE}/site-profiles`).then((r) => r.data)

export const putSiteProfile = (siteId: string, body: {
  timezone?: string | null; business_hours?: BusinessHours | null; closed_on_public_holidays?: boolean
  criticality?: string | null; notes?: string | null
}) => apiClient.put<SiteProfile>(`${BASE}/site-profiles/${siteId}`, body).then((r) => r.data)

/** The server's own sentence where it gave one; otherwise something readable. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

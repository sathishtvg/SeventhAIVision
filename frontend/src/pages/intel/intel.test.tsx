import type { ReactNode } from 'react'
import { Route, Routes, MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/securityIntelligence'
import type {
  Authority, AuthorityAction, Decision, DecisionAction, DecisionPolicy, DronePicture, EvidenceItem, FeedbackAnalytics,
  Insight as InsightData, Recommendations, SiteScores, SituationDetail, SituationEvidence, SituationFeedback,
  SituationSummary, Timeline, TimelineEntry, Trail,
} from '@/api/securityIntelligence'
import { upsertSetting } from '@/api/settings'
import { SituationsPanel } from '@/components/intel/SituationsPanel'
import { duration, rate } from '@/components/intel/intelFormat'
import Situations from './Situations'
import Situation from './Situation'
import Decisions from './Decisions'
import IntelSetup from './IntelSetup'
import Insight from './Insight'
import Feedback from './Feedback'

vi.mock('@/components/common/HlsPlayer', () => ({ HlsPlayer: () => <div data-testid="hls" /> }))
vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([{ id: 's1', name: 'Factory A' }, { id: 's2', name: 'Factory B' }]) }))
vi.mock('@/api/cameras', () => ({ getStreams: vi.fn().mockResolvedValue([{ id: 'st1' }]) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/securityIntelligence', async (orig) => {
  const real = await orig<typeof import('@/api/securityIntelligence')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

const ADMIN = 2, SUPERVISOR = 3, OPERATOR = 4, GUARD = 5, VIEWER = 6

function asRole(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'u1', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

function renderAt(path: string, pattern: string, el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <ThemeProvider theme={theme}><Routes><Route path={pattern} element={el} /></Routes></ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const ASSESSMENT = {
  id: 'a1', sequence: 1, assessed_at: '2026-10-05T02:18:02Z', kind: 'RESTRICTED_ZONE' as const,
  label: 'Suspicious activity in a restricted zone, out of hours',
  summary: 'Suspicious activity in a restricted zone, out of hours. Risk HIGH (65).',
  risk_score: 65, risk_level: 'HIGH' as const,
  risk_factors: [{ factor: 'SEVERITY', points: 45, detail: 'The most serious event is “Person at Gate 1” (high).' },
                 { factor: 'ZONE', points: 20, detail: 'Restricted zone “Fuel store” (high) was in force.' }],
  normality_score: null, anomaly_score: null, normality_factors: [],
  normality_basis: 'Insufficient history: 0 week(s) of alerts from this camera, 4 needed to say what is usual.',
  confidence: { detection: 0.94, correlation: null, risk: 0.76 },
  unknowns: ['Who the person is. Not identified is not the same as not authorised.'],
  event_count: 1, engine_version: 'rules-1',
  statements: [{ kind: 'time', text: 'Outside business hours — closed on Mondays', source: 'site profile' }],
  expected: [],
}

const SITUATION: SituationDetail = {
  id: 'sit1', situation_number: 'SIT-20261005-0001', title: 'Person at Gate 1', status: 'ACTIVE', severity: 'high',
  site_id: 's1', site_name: 'Factory A', started_at: '2026-10-05T02:17:04Z', last_event_at: '2026-10-05T02:17:04Z',
  event_count: 1, duplicate_count: 0, source_types: ['CCTV_AI'], primary_camera_id: 'c1',
  primary_camera_name: 'Gate 1', location_label: null, correlation_confidence: null, risk_score: 65,
  risk_level: 'HIGH', assessed_at: '2026-10-05T02:18:02Z', decision_status: 'AWAITING', last_decided_at: null,
  closed_at: null, incident_id: null, incident_confirmed_at: null,
  sources: [{ source_type: 'CCTV_AI', camera_id: 'c1', label: 'Gate 1', events: 1,
              first_at: '2026-10-05T02:17:04Z', last_at: '2026-10-05T02:17:04Z' }],
  events: [{ id: 'e1', source_type: 'CCTV_AI', event_type: 'intrusion.zone_breach', occurred_at: '2026-10-05T02:17:04Z',
             severity: 'high', title: 'Person at Gate 1', camera_id: 'c1', camera_name: 'Gate 1', alert_id: 'al1',
             incident_id: null, subject_kind: 'PERSON', subject_verdict: null, confidence: 0.94, location_label: null,
             method: 'FIRST_EVENT', reason: 'The first event of this situation.', link_confidence: null, is_duplicate: false }],
  assessment: ASSESSMENT,
  incident: { state: 'NONE', id: null, opened_by_the_platform: null },
}

const RECS: Recommendations = {
  situation_id: 'sit1', is_decision: false, current: true,
  assessment: { id: 'a1', sequence: 1, assessed_at: ASSESSMENT.assessed_at, kind: 'RESTRICTED_ZONE', label: ASSESSMENT.label,
                risk_level: 'HIGH', risk_score: 65, confidence: ASSESSMENT.confidence },
  recommendations: [
    { id: 'r1', rank: 1, action: 'VIEW_CAMERA', priority: 'HIGH', reason: 'Only one kind of source reported this: look before sending anyone.',
      recommendation_confidence: 0.85, limited_by: 'RULE', available: true, unavailable_reason: null, created_at: ASSESSMENT.assessed_at,
      supporting: { rests_on: [ASSESSMENT.risk_factors[0].detail],
                    cameras: [{ id: 'c1', name: 'Gate 1', state: 'online', relation: 'reported' }] } },
    { id: 'r2', rank: 2, action: 'DISPATCH_GUARD', priority: 'HIGH', reason: 'Send a guard to check.',
      recommendation_confidence: 0.7, limited_by: 'RULE', available: false,
      unavailable_reason: 'No guard is on shift at this site.', created_at: ASSESSMENT.assessed_at, supporting: {} },
  ],
}

const ALL: DecisionAction[] = ['MONITOR', 'VERIFY', 'VIEW_CAMERA', 'VERIFY_WITH_DRONE', 'DISPATCH_GUARD', 'ESCALATE',
  'INVESTIGATE', 'CONTACT_SITE', 'CREATE_INCIDENT', 'ACKNOWLEDGE', 'CONFIRM_INCIDENT', 'REQUEST_ASSISTANCE',
  'FALSE_POSITIVE', 'RESOLVE']

function authority(over: Partial<Record<DecisionAction, Partial<AuthorityAction>>> = {}, top: Partial<Authority> = {}): Authority {
  const basis = (a: DecisionAction) => (a === 'VIEW_CAMERA' ? 'FOLLOWED' : a === 'FALSE_POSITIVE' || a === 'RESOLVE' ? 'CLOSING'
    : ['ACKNOWLEDGE', 'CONFIRM_INCIDENT', 'REQUEST_ASSISTANCE'].includes(a) ? 'INDEPENDENT' : 'OVERRIDE')
  return {
    situation_id: 'sit1', decision_status: 'AWAITING', closed: false, risk_level: 'HIGH',
    incident: { state: 'NONE', id: null }, policy: { source: 'default', rule: { alone: 'CRITICAL' } },
    may_override: true, may_approve: false, suggested_action: 'VIEW_CAMERA',
    reasons: [{ code: 'GUARD_RESPONDING', label: 'Guard already responding' }, { code: 'OTHER', label: 'Other' }],
    actions: ALL.map((action) => ({
      action, allowed: true, how: 'ALONE', basis: basis(action), needs_reason: ['OVERRIDE', 'CLOSING'].includes(basis(action)),
      needs: action === 'DISPATCH_GUARD' ? ['guard_user_id'] : action === 'ESCALATE' ? ['escalate_to_user_id'] : [],
      why_not: null, carries_out: action === 'ACKNOWLEDGE' ? ['ALERT_ACKNOWLEDGE'] : [], ...over[action] } as AuthorityAction)),
    ...top,
  }
}

const DECISION: Decision = {
  id: 'd1', situation_id: 'sit1', situation_number: 'SIT-20261005-0001', decided_at: '2026-10-05T02:18:15Z',
  action: 'DISPATCH_GUARD', basis: 'OVERRIDE', is_override: true, reason_code: 'OTHER', reason: 'Other',
  note: 'Sending the patrol car.', decided_by: { user_id: 'u9', name: 'Sathish', role_id: OPERATOR }, via: 'web',
  suggested_action: 'VIEW_CAMERA', recommendation_id: 'r2', assessment_id: 'a1', decided_on_an_earlier_assessment: false,
  risk_level: 'HIGH', risk_score: 65, authority: 'ALONE', state: 'EFFECTIVE', policy: {}, params: { guard_user_id: 'g1' },
  approval: null,
  actions: [{ sequence: 1, action: 'INCIDENT_CREATE', through: 'app.routers.incidents.create_incident', target_type: 'incident',
              target_id: 'i1', result: 'OK', detail: null, executed_at: '2026-10-05T02:18:16Z', executed_by_user_id: 'u9' },
            { sequence: 2, action: 'INCIDENT_DISPATCH', through: 'app.routers.dispatch.dispatch_guard', target_type: 'incident',
              target_id: 'i1', result: 'FAILED', detail: '404: Incident not found', executed_at: '2026-10-05T02:18:17Z',
              executed_by_user_id: 'u9' }],
}

const EMPTY_TRAIL: Trail = { situation_id: 'sit1', decision_status: 'AWAITING', closed_at: null,
                             incident: { state: 'NONE', id: null, status: null }, reviews: [], decisions: [] }
const ON = { enabled: true, runner: { state: 'running' as const, last_seen_at: '2026-10-05T02:18:00Z' },
             last_24_hours: { CCTV_AI: 12 }, sources: [] }

const NO_TIMELINE: Timeline = {
  situation_id: 'sit1', situation_number: 'SIT-20261005-0001', started_at: '2026-10-05T02:17:04Z', closed_at: null,
  decision_status: 'AWAITING', suggestions_shown: true, counts: { SOURCE: 0, AI: 0, PERSON: 0, PLATFORM: 0 }, entries: [],
}
const line = (at: string, kind: TimelineEntry['kind'], actor: TimelineEntry['actor'], title: string,
              more: Partial<TimelineEntry> = {}): TimelineEntry =>
  ({ at: `2026-10-05T${at}Z`, kind, actor, title, detail: null, who: null, ref: { type: 'event', id: `${kind}-${at}` },
     ...more })
const TIMELINE: Timeline = {
  ...NO_TIMELINE, decision_status: 'IN_HAND', counts: { SOURCE: 2, AI: 2, PERSON: 3, PLATFORM: 2 },
  entries: [
    line('02:17:04', 'EVENT', 'SOURCE', 'Person at Gate 1', { source_type: 'CCTV_AI', where: 'Gate 1' }),
    line('02:17:08', 'EVENT', 'SOURCE', 'Access denied at the rear door',
         { source_type: 'ACCESS_CONTROL', where: 'Rear door', detail: 'An access event at the door this camera watches, 4 s apart.' }),
    line('02:18:02', 'ASSESSMENT', 'AI', 'AI-assisted assessment: Access refused, with activity seen nearby. Risk HIGH (65).',
         { detail: 'On 2 event(s).', risk_level: 'HIGH', risk_score: 65 }),
    line('02:18:06', 'RECOMMENDATION', 'AI', 'AI suggests: dispatch a guard',
         { detail: 'More than one kind of source reported this: send a guard.', is_decision: false, action: 'DISPATCH_GUARD' }),
    line('02:18:15', 'DECISION', 'PERSON', 'Decided: dispatch a guard',
         { detail: 'Followed what the layer suggested.', who: { user_id: 'u9', name: 'Priya', role_id: SUPERVISOR }, via: 'web' }),
    line('02:18:17', 'ACTION', 'PLATFORM', 'Guard dispatched',
         { result: 'OK', through: 'app.routers.dispatch.dispatch_guard', action: 'INCIDENT_DISPATCH' }),
    line('02:24:31', 'OBSERVATION', 'PERSON', 'Arrived',
         { who: { user_id: 'g1', name: 'Tan Wei Ming', role_id: GUARD }, via: 'mobile' }),
    line('02:27:10', 'OBSERVATION', 'PERSON', 'Reported from the ground: Authorised maintenance worker.',
         { who: { user_id: 'g1', name: 'Tan Wei Ming', role_id: GUARD }, via: 'mobile' }),
    line('02:28:00', 'INCIDENT', 'PLATFORM', 'Incident resolved'),
  ],
}
const REPEATS_AND_A_FAILURE: Timeline = {
  ...NO_TIMELINE, counts: { SOURCE: 1, AI: 0, PERSON: 0, PLATFORM: 1 },
  entries: [
    line('02:17:40', 'REPEATS', 'SOURCE', 'The same alert again, 6 time(s): Person at Gate 1',
         { source_type: 'CCTV_AI', where: 'Gate 1', count: 6, until: '2026-10-05T02:21:10Z',
           detail: 'Folded as repeats. Each is still an alert of its own.' }),
    line('02:19:46', 'ACTION', 'PLATFORM', 'Could not dispatch the guard',
         { result: 'FAILED', through: 'app.routers.dispatch.dispatch_guard',
           detail: '409: This guard is already dispatched to another incident.' }),
  ],
}

const SUMMARY: SituationSummary = {
  is_ai_assisted: true, label: 'AI-assisted summary', timezone: 'Asia/Singapore', situation_number: 'SIT-20261005-0001',
  made_of: 'Made only of what is recorded. Every sentence is read from the records listed with it.',
  suggestions_shown: true,
  sentences: [
    { text: 'At 10:17 on 5 Oct 2026, a camera reported “Person at Gate 1” at Gate 1, Factory A.', refs: [{ type: 'event', id: 'e1' }] },
    { text: 'The layer assessed it as HIGH risk (65): Access refused, with activity seen nearby.', refs: [{ type: 'assessment', id: 'a1' }] },
    { text: 'The situation is open: nobody has decided on it yet.', refs: [] }],
  text: '',
}
const NO_EVIDENCE: SituationEvidence = {
  situation_id: 'sit1', items: [],
  summary: { total: 0, may_open: 0, by_kind: { SNAPSHOT: 0, CLIP: 0, RECORDING: 0, DRONE_MEDIA: 0, PATROL_SNAPSHOT: 0 } },
}
const kept = (kind: EvidenceItem['kind'], id: string, what: string, more: Partial<EvidenceItem> = {}): EvidenceItem => ({
  kind, id, what, captured_at: '2026-10-05T02:17:04Z', event_id: 'e1', camera_name: 'Gate 1', checksum_sha256: null,
  kept: 'central', media_type: 'image', needs: 'evidence:read', may_open: true, logged_in: 'evidence_access_log',
  served_at: { path: `/api/v1/evidence/${id}/image`, token_in_query: true }, ...more })
const EVIDENCE: SituationEvidence = {
  ...NO_EVIDENCE,
  items: [
    kept('SNAPSHOT', 'ev1', 'Frame at the detection', { checksum_sha256: 'a'.repeat(64) }),
    kept('RECORDING', 'rec1', 'Recording of Gate 1', {
      media_type: 'video', needs: 'recording:read', logged_in: 'audit_log', offset_seconds: 600,
      served_at: { path: '/api/v1/recordings/rec1/play', token_in_query: true } }),
    kept('PATROL_SNAPSHOT', 'sc1', 'Snapshot taken at the virtual patrol’s check', {
      needs: 'vpatrol:read', may_open: false, logged_in: 'audit_log',
      served_at: { path: '/api/v1/virtual-patrol/sessions/vs1/cameras/sc1/snapshot', token_in_query: true } })],
}

const REVIEW_LISTS = {
  outcomes: [{ code: 'REAL_INCIDENT', label: 'A real security incident' }, { code: 'AUTHORISED_ACTIVITY', label: 'Authorised activity' },
             { code: 'UNDETERMINED', label: 'Could not be determined' }],
  assessment_verdicts: [{ code: 'ABOUT_RIGHT', label: 'About right' }, { code: 'TOO_HIGH', label: 'Assessed too high' }],
  recommendation_verdicts: [{ code: 'USEFUL', label: 'Useful' }, { code: 'NOT_USEFUL', label: 'Not useful' }],
}
const NO_FEEDBACK: SituationFeedback = { situation_id: 'sit1', closed: false, may_review: false, reviews: [], ...REVIEW_LISTS }

/** A site with no drone in the picture: nothing to ask, nothing asked. */
const NO_DRONE: DronePicture = {
  situation_id: 'sit1', closed: false, licence: { ok: true, problem: null },
  may: { hold: true, launch: true, see_missions: true }, hold_seconds: { min: 5, max: 120, default: 30 },
  notes: { hold: 'The drone module checks distance, battery and the provider when it is asked.',
           launch: 'Pre-flight runs when it is asked, and can still stop it.' },
  sightings: [], missions: [], asked: [], other_looks: [],
}
const SIGHTING = {
  event_id: 'e2', drone_event_id: 'de1', title: 'Possible unauthorised person', occurred_at: '2026-10-05T02:17:30Z',
  location_label: 'Loading Bay', session_id: 'f1', session_number: 'DPS-0007', mission_name: 'Night Watch',
  drone_name: 'Drone One', flight_status: 'ACTIVE', drone_risk_level: 'MEDIUM', detection_count: 3,
  can_hold: true, why_not: null,
}
const MISSION = { mission_id: 'm1', name: 'Night Watch', drone_name: 'Drone One', drone_status: 'READY',
                  route_name: 'Perimeter', battery_level: 90, can_launch: true, why_not: null }
const WITH_DRONE: DronePicture = { ...NO_DRONE, sightings: [SIGHTING],
  missions: [{ ...MISSION, can_launch: false, why_not: 'Its drone is already committed to another flight.' }] }
const DRONE_SUGGESTED = authority({ VERIFY_WITH_DRONE: { basis: 'FOLLOWED', needs_reason: false } })

function open(role: number, a: Authority = authority(), trail: Trail = EMPTY_TRAIL, drone: DronePicture = NO_DRONE,
              situation: SituationDetail = SITUATION, recs: Recommendations = RECS) {
  asRole(role)
  vi.mocked(api.getSituationDrone).mockResolvedValue(drone)
  vi.mocked(api.getTimeline).mockResolvedValue(NO_TIMELINE)
  vi.mocked(api.getSummary).mockResolvedValue(SUMMARY)
  vi.mocked(api.getSituationEvidence).mockResolvedValue(NO_EVIDENCE)
  vi.mocked(api.getSituationFeedback).mockResolvedValue(NO_FEEDBACK)
  vi.mocked(api.getIntelStatus).mockResolvedValue(ON)
  vi.mocked(api.getSituation).mockResolvedValue(situation)
  vi.mocked(api.getRecommendations).mockResolvedValue(recs)
  vi.mocked(api.getAuthority).mockResolvedValue(a)
  vi.mocked(api.getTrail).mockResolvedValue(trail)
  vi.mocked(api.recordReview).mockResolvedValue({ recorded: true, assessment_id: 'a1' })
  vi.mocked(api.getResponders).mockResolvedValue({
    guards: [{ user_id: 'g1', name: 'Tan Wei Ming', on_shift_here: true, on_shift: true }],
    escalation: [{ user_id: 'sv1', name: 'Priya', role_id: SUPERVISOR }] })
  vi.mocked(api.getObservations).mockResolvedValue([])
  return renderAt('/situations/sit1', '/situations/:id', <Situation />)
}

beforeEach(() => { vi.clearAllMocks() })

describe('the situation view', () => {
  it('never draws what the layer suggests as what a person decided', async () => {
    open(OPERATOR)
    const suggestions = await screen.findAllByTestId('ai-suggestion')
    expect(suggestions).toHaveLength(2)
    expect(within(suggestions[0]).getByText('AI suggests')).toBeInTheDocument()
    expect(within(suggestions[0]).getByText(/A suggestion, not a decision/)).toBeInTheDocument()
    expect(screen.getByText('Suggestions — not decisions')).toBeInTheDocument()
    // Nobody has decided: the human side says so, and holds no decision card.
    expect(screen.getByText('Pending — nobody has decided yet')).toBeInTheDocument()
    expect(screen.queryByTestId('human-decision')).toBeNull()
    expect(screen.getByText('Nobody has decided anything yet.')).toBeInTheDocument()
    expect(screen.getByText('Nothing here has been done. A person decides below.')).toBeInTheDocument()
  })

  it('says why a suggested step cannot be taken instead of hiding it', async () => {
    open(OPERATOR)
    const cards = await screen.findAllByTestId('ai-suggestion')
    expect(within(cards[1]).getByText('No guard is on shift at this site.')).toBeInTheDocument()
    expect(within(cards[1]).getByText('Not possible now')).toBeInTheDocument()
  })

  it('shows four confidences under four names and never one number', async () => {
    open(OPERATOR)
    const box = await screen.findByLabelText('Confidences')
    for (const [label, value] of [['Detection confidence', '94%'], ['Risk confidence', '76%'],
                                  ['Recommendation confidence', '85%']]) {
      const row = within(box).getByText(label).parentElement as HTMLElement
      expect(within(row).getByText(value)).toBeInTheDocument()
    }
    // A situation of one event: nothing was correlated, and it says so rather than showing 0% or 100%.
    const correlation = within(box).getByText('Correlation confidence').parentElement as HTMLElement
    expect(within(correlation).getByText('not given by any source')).toBeInTheDocument()
    expect(screen.queryByText(/overall confidence/i)).toBeNull()
    expect(screen.queryByText(/^confidence \d+%$/i)).toBeNull()
  })

  it('explains the risk factor by factor and lists what was not known', async () => {
    open(OPERATOR)
    expect(await screen.findByText('Restricted zone “Fuel store” (high) was in force.')).toBeInTheDocument()
    expect(screen.getByText('+20')).toBeInTheDocument()
    expect(screen.getByText('• Who the person is. Not identified is not the same as not authorised.')).toBeInTheDocument()
    expect(screen.getByText(/Not known \(1\) — each lowers the risk confidence, none adds to the risk/)).toBeInTheDocument()
    expect(screen.getByText('site profile')).toBeInTheDocument()
  })

  it('records once that the officer looked at what was suggested', async () => {
    open(OPERATOR)
    await screen.findAllByTestId('ai-suggestion')
    await waitFor(() => expect(api.recordReview).toHaveBeenCalledTimes(1))
    expect(api.recordReview).toHaveBeenCalledWith('sit1')
  })

  it('shows a viewer the situation and its assessment, not the suggestions and not the buttons', async () => {
    open(VIEWER)
    expect(await screen.findByText('Why this risk')).toBeInTheDocument()
    expect(screen.queryByText('AI recommendations')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Monitor' })).toBeNull()
    expect(screen.getByText(/Deciding on it needs the permission to decide/)).toBeInTheDocument()
    expect(api.getRecommendations).not.toHaveBeenCalled()
    expect(api.getAuthority).not.toHaveBeenCalled()
    expect(api.recordReview).not.toHaveBeenCalled()
  })

  it('following a suggestion needs no reason', async () => {
    vi.mocked(api.decide).mockResolvedValue(DECISION)
    open(OPERATOR)
    fireEvent.click(await screen.findByRole('button', { name: 'View CCTV' }))
    expect(await screen.findByText('This follows what the layer suggested.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Reason')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Record my decision' }))
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    const [id, body] = vi.mocked(api.decide).mock.calls[0]
    expect(id).toBe('sit1')
    expect(body).toMatchObject({ action: 'VIEW_CAMERA', seen_assessment_id: 'a1' })
    expect(body.reason_code).toBeUndefined()
    expect(body.client_ref).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  })

  it('going against the suggestion is allowed, and asks why', async () => {
    vi.mocked(api.decide).mockResolvedValue(DECISION)
    open(OPERATOR)
    fireEvent.click(await screen.findByRole('button', { name: 'Monitor' }))
    expect(await screen.findByText(/The layer suggested View CCTV first, not this\. You may still decide it: say why\./))
      .toBeInTheDocument()
    const record = screen.getByRole('button', { name: 'Record my decision' })
    expect(record).toBeDisabled()
    fireEvent.mouseDown(screen.getByLabelText('Reason'))
    fireEvent.click(await screen.findByRole('option', { name: 'Guard already responding' }))
    expect(record).toBeEnabled()
    fireEvent.click(record)
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.decide).mock.calls[0][1]).toMatchObject({ action: 'MONITOR', reason_code: 'GUARD_RESPONDING' })
  })

  it('"other" is not a reason until it says what', async () => {
    open(OPERATOR)
    fireEvent.click(await screen.findByRole('button', { name: 'Resolve' }))
    expect(await screen.findByText('This closes the situation. Say how it ended.')).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByLabelText('Reason'))
    fireEvent.click(await screen.findByRole('option', { name: 'Other' }))
    const record = screen.getByRole('button', { name: 'Record my decision' })
    expect(record).toBeDisabled()
    fireEvent.change(screen.getByLabelText('What was it? (required)'), { target: { value: 'Fox on the fence.' } })
    expect(record).toBeEnabled()
  })

  it('the officer chooses the guard: the layer lists, it does not pick', async () => {
    vi.mocked(api.decide).mockResolvedValue(DECISION)
    open(OPERATOR, authority({ DISPATCH_GUARD: { basis: 'FOLLOWED', needs_reason: false,
                                                 carries_out: ['INCIDENT_CREATE', 'INCIDENT_DISPATCH'] } }))
    fireEvent.click(await screen.findByRole('button', { name: 'Dispatch guard' }))
    expect(await screen.findByText('The platform will then: Incident opened → Guard dispatched.')).toBeInTheDocument()
    const record = screen.getByRole('button', { name: 'Record my decision' })
    expect(record).toBeDisabled()
    fireEvent.mouseDown(await screen.findByLabelText('Guard to send'))
    fireEvent.click(await screen.findByRole('option', { name: 'Tan Wei Ming — on shift here' }))
    fireEvent.click(record)
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.decide).mock.calls[0][1]).toMatchObject({ action: 'DISPATCH_GUARD', guard_user_id: 'g1' })
  })

  it('a decision that needs approval says so before and when it is made', async () => {
    const guard = authority(Object.fromEntries(ALL.map((a) => [a, { how: 'WITH_APPROVAL' as const }])),
                            { policy: { source: 'tenant', rule: { alone: 'MEDIUM', with_approval: 'CRITICAL' } }, may_override: false })
    open(GUARD, guard)
    fireEvent.click(await screen.findByRole('button', { name: 'View CCTV · needs approval' }))
    expect(await screen.findByText(/waits for a second person's approval\. Nothing is carried out until then\./))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Propose for approval' })).toBeInTheDocument()
  })

  it('what may not be decided is disabled, and says why', async () => {
    open(GUARD, authority(
      Object.fromEntries(ALL.filter((a) => a !== 'REQUEST_ASSISTANCE').map((a) => [a, {
        allowed: false, how: null, why_not: 'The decision policy does not let a guard decide here.' }])),
      { policy: { source: 'default', rule: {} }, may_override: false }))
    expect(await screen.findByRole('button', { name: 'Dispatch guard' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Request assistance' })).toBeEnabled()
    expect(screen.getByText(/The decision policy does not let your role decide here\. You can ask the command centre for help\./))
      .toBeInTheDocument()
    expect(screen.getAllByLabelText('The decision policy does not let a guard decide here.').length).toBeGreaterThan(5)
  })

  it('keeps what was decided apart from what the platform then did, failures included', async () => {
    open(OPERATOR, authority(), { ...EMPTY_TRAIL, decision_status: 'IN_HAND', decisions: [DECISION],
                                  reviews: [{ user_id: 'u9', name: 'Sathish', role_id: OPERATOR, assessment_id: 'a1',
                                              via: 'web', viewed_at: '2026-10-05T02:18:10Z' }] })
    const card = await screen.findByTestId('human-decision')
    expect(within(card).getByText('Decided')).toBeInTheDocument()
    expect(within(card).getByText('Sathish · Operator')).toBeInTheDocument()
    expect(within(card).getByText('Override')).toBeInTheDocument()
    expect(within(card).getByText('The layer had suggested View CCTV first.')).toBeInTheDocument()
    expect(within(card).getByText('“Sending the patrol car.”')).toBeInTheDocument()
    const done = within(card).getByTestId('actions-carried-out')
    expect(within(card).getByText('What the platform then did')).toBeInTheDocument()
    expect(within(done).getByText('Incident opened')).toBeInTheDocument()
    expect(within(done).getByText('Failed')).toBeInTheDocument()
    expect(within(done).getByText('404: Incident not found')).toBeInTheDocument()
    expect(screen.getByText(/Sathish \(Operator\)\s+looked at what was suggested/)).toBeInTheDocument()
    // The suggestion cards are still suggestions: deciding did not turn them into anything else.
    expect(screen.getAllByTestId('ai-suggestion')).toHaveLength(2)
  })

  it('shows what was reported from the ground, apart from what was decided', async () => {
    const view = open(OPERATOR)
    await screen.findAllByTestId('ai-suggestion')
    expect(screen.queryByText('From the ground')).toBeNull()        // nothing reported: no empty card
    view.unmount()
    open(OPERATOR)
    vi.mocked(api.getObservations).mockResolvedValue([
      { id: 'o1', kind: 'ARRIVED', note: null, user_id: 'g1', name: 'Tan Wei Ming', role_id: GUARD, latitude: 1.3,
        longitude: 103.8, via: 'mobile', observed_at: '2026-10-05T02:24:31Z' },
      { id: 'o2', kind: 'OBSERVATION', note: 'Night cleaner, badge checked.', user_id: 'g1', name: 'Tan Wei Ming',
        role_id: GUARD, latitude: null, longitude: null, via: 'mobile', observed_at: '2026-10-05T02:27:10Z' }])
    const reports = await screen.findAllByTestId('ground-report')
    expect(reports).toHaveLength(2)
    expect(within(reports[0]).getByText('Arrived')).toBeInTheDocument()
    expect(within(reports[0]).getByText(/Tan Wei Ming \(Guard\) · from the phone · with position/)).toBeInTheDocument()
    expect(within(reports[1]).getByText('Night cleaner, badge checked.')).toBeInTheDocument()
    expect(screen.getByText(/A report is not a decision and changes nothing else\./)).toBeInTheDocument()
    expect(screen.queryByTestId('human-decision')).toBeNull()       // reporting did not make a decision appear
  })

  it('a drone looks only as the officer says: which flight holds, and for how long, is their choice', async () => {
    vi.mocked(api.decide).mockResolvedValue(DECISION)
    open(OPERATOR, DRONE_SUGGESTED, EMPTY_TRAIL, WITH_DRONE)
    fireEvent.click(await screen.findByRole('button', { name: 'Verify with drone' }))
    const choice = await screen.findByTestId('drone-choice')
    expect(within(choice).getByText('Your choice. The layer chooses no flight and no mission, and steers nothing.'))
      .toBeInTheDocument()
    const hold = within(choice).getByRole('radio', { name: /Ask flight DPS-0007 to hold and look again/ })
    const launch = within(choice).getByRole('radio', { name: /Start mission “Night Watch”/ })
    const none = within(choice).getByRole('radio', { name: /Record only — I will fly it from the drone screens/ })
    expect(hold).toBeChecked()                       // the first thing that could be asked — still theirs to change
    expect(launch).toBeDisabled()                    // and what cannot be asked says why
    expect(within(choice).getByText('Its drone is already committed to another flight.')).toBeInTheDocument()
    expect(none).toBeEnabled()
    expect(screen.getByText(/through the drone module, under your own drone permission/)).toBeInTheDocument()
    expect(within(choice).getByText(NO_DRONE.notes.hold)).toBeInTheDocument()

    const record = screen.getByRole('button', { name: 'Record my decision' })
    fireEvent.change(within(choice).getByLabelText('Hold for (seconds)'), { target: { value: '200' } })
    expect(record).toBeDisabled()                    // outside the drone module's own range
    fireEvent.change(within(choice).getByLabelText('Hold for (seconds)'), { target: { value: '45' } })
    fireEvent.click(record)
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    const body = vi.mocked(api.decide).mock.calls[0][1]
    expect(body).toMatchObject({ action: 'VERIFY_WITH_DRONE', drone_event_id: 'de1', hold_seconds: 45 })
    expect(body.drone_mission_id).toBeUndefined()
  })

  it('starting a mission, or only recording it, sends that and nothing else about a drone', async () => {
    vi.mocked(api.decide).mockResolvedValue(DECISION)
    const view = open(OPERATOR, DRONE_SUGGESTED, EMPTY_TRAIL, { ...NO_DRONE, missions: [MISSION] })
    fireEvent.click(await screen.findByRole('button', { name: 'Verify with drone' }))
    const choice = await screen.findByTestId('drone-choice')
    expect(within(choice).getByRole('radio', { name: /Start mission “Night Watch” \(Drone One\)/ })).toBeChecked()
    expect(screen.getByText(/after its own pre-flight checks/)).toBeInTheDocument()
    expect(within(choice).queryByLabelText('Hold for (seconds)')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Record my decision' }))
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    let body = vi.mocked(api.decide).mock.calls[0][1]
    expect(body).toMatchObject({ action: 'VERIFY_WITH_DRONE', drone_mission_id: 'm1' })
    expect(body.drone_event_id).toBeUndefined()
    expect(body.hold_seconds).toBeUndefined()
    view.unmount()

    // Without the permission to start one, and with no flight to hold: a record, and it says so.
    vi.mocked(api.decide).mockClear()
    open(OPERATOR, DRONE_SUGGESTED, EMPTY_TRAIL,
         { ...NO_DRONE, missions: [MISSION], may: { hold: false, launch: false, see_missions: true } })
    fireEvent.click(await screen.findByRole('button', { name: 'Verify with drone' }))
    const again = await screen.findByTestId('drone-choice')
    expect(within(again).getByRole('radio', { name: /Record only/ })).toBeChecked()
    expect(within(again).getByText('Starting a mission needs the permission to start one.')).toBeInTheDocument()
    expect(screen.getByText('It is recorded as yours. Nothing is carried out by the platform.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Record my decision' }))
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    body = vi.mocked(api.decide).mock.calls[0][1]
    expect(body.drone_event_id).toBeUndefined()
    expect(body.drone_mission_id).toBeUndefined()
  })

  it('shows what was asked of a drone as a person’s act, what came back, and what the drone module refused', async () => {
    const view = open(OPERATOR)
    await screen.findAllByTestId('ai-suggestion')
    expect(screen.queryByTestId('drone-card')).toBeNull()            // no drone in the picture: no empty card
    view.unmount()
    open(OPERATOR, authority(), EMPTY_TRAIL, {
      ...WITH_DRONE,
      asked: [
        { action_id: 'x1', decision_id: 'd1', action: 'DRONE_HOLD', result: 'OK', detail: null,
          asked_at: '2026-10-05T02:18:20Z', flight: null,
          look: { id: 'v1', drone_event_id: 'de1', status: 'COMPLETED', hold_seconds: 30, started_at: null, ends_at: null,
                  completed_at: '2026-10-05T02:19:00Z',
                  result: { detections_added: 4, risk_before: 'MEDIUM', risk_after: 'HIGH' } } },
        { action_id: 'x2', decision_id: 'd2', action: 'DRONE_LAUNCH', result: 'FAILED',
          detail: 'Pre-flight stopped flight DPS-0008: Battery 12% is below 30%', asked_at: '2026-10-05T02:21:00Z',
          look: null, flight: { id: 'f2', session_number: 'DPS-0008', mission_name: 'Night Watch', status: 'BLOCKED',
                                started_at: null, ended_at: null, event_count: 0, blocked_reason: null } }],
      other_looks: [{ id: 'v2', drone_event_id: 'de1', status: 'HOLDING', hold_seconds: 20, started_at: null,
                      ends_at: null, completed_at: null, result: {}, asked_at: '2026-10-05T02:23:00Z' }],
    })
    const card = await screen.findByTestId('drone-card')
    expect(within(card).getByText(/A drone looks only when a person decides it should\./)).toBeInTheDocument()
    expect(within(card).getByText(/flight DPS-0007 \(Active\) · could be asked to hold and look again/)).toBeInTheDocument()
    const asked = within(card).getAllByTestId('drone-asked')
    expect(asked).toHaveLength(2)
    expect(within(asked[0]).getByText('Asked by a decision')).toBeInTheDocument()
    expect(within(asked[0]).getByText(
      'Looked for 30 s: 4 more detection(s). The drone module\'s own risk: Medium → High.')).toBeInTheDocument()
    expect(within(asked[1]).getByText('Not done')).toBeInTheDocument()
    expect(within(asked[1]).getByText('Pre-flight stopped flight DPS-0008: Battery 12% is below 30%')).toBeInTheDocument()
    const other = within(card).getByTestId('drone-other-look')
    expect(within(other).getByText(/Asked from the drone screens/)).toBeInTheDocument()
    expect(within(other).getByText('Holding and looking')).toBeInTheDocument()
    expect(within(card).queryByText(/AI/)).toBeNull()                // nothing here is the layer's doing
  })

  it('says when the layer has assessed it again since the last decision, and that the decision stands', async () => {
    const view = open(OPERATOR)
    await screen.findAllByTestId('ai-suggestion')
    expect(screen.queryByTestId('reassessed')).toBeNull()
    view.unmount()
    open(OPERATOR, authority(), EMPTY_TRAIL, NO_DRONE,
         { ...SITUATION, decision_status: 'IN_HAND', reassessed_since_decision: true })
    const banner = await screen.findByTestId('reassessed')
    expect(within(banner).getByText(/Assessed again since the last decision/)).toBeInTheDocument()
    expect(within(banner).getByText(/The decision stands until a person decides again\./)).toBeInTheDocument()
  })

  it('shows a patrol’s finding as the officer recorded it, and a drone’s second look as what it saw', async () => {
    open(OPERATOR, authority(), EMPTY_TRAIL, NO_DRONE, { ...SITUATION, events: [
      ...SITUATION.events,
      { ...SITUATION.events[0], id: 'e3', source_type: 'VIRTUAL_PATROL', event_type: 'vpatrol.exception',
        title: 'Virtual patrol exception: Is the rear gate closed?', method: 'PATROL_FINDING',
        reason: 'A virtual patrol reported an exception on this camera, 12 min apart.', confidence: null,
        attributes: { patrol_number: 'VP-0042', question: 'Is the rear gate closed?', answer: 'NO',
                      exception_reason: 'Gate left open', observation: 'Rear gate visibility abnormal',
                      has_snapshot: true } },
      { ...SITUATION.events[0], id: 'e4', source_type: 'DRONE_PATROL', event_type: 'drone.verification',
        title: 'Drone looked again and saw nothing more — Possible unauthorised person', method: 'DRONE_LOOK',
        reason: 'A person asked the drone to hold for 30 s and look again at this sighting: it saw nothing more.',
        confidence: null, attributes: { hold_seconds: 30, detections_added: 0, risk_before: 'HIGH',
                                        drone_risk_level: 'HIGH' } }] })
    const finding = await screen.findByTestId('patrol-finding')
    expect(within(finding).getByText('Virtual patrol VP-0042 — as the officer recorded it')).toBeInTheDocument()
    for (const line of ['Asked: Is the rear gate closed?', 'Answered: NO', 'Why it is an exception: Gate left open',
                        'Officer\'s note on the camera: Rear gate visibility abnormal',
                        'A snapshot was kept at the check.']) {
      expect(within(finding).getByText(line)).toBeInTheDocument()
    }
    const look = screen.getByTestId('drone-look-result')
    expect(within(look).getByText(/Held for 30 s\. Nothing more seen\. The drone module's own risk: High → High\./))
      .toBeInTheDocument()
  })

  it('tells the situation in order, and draws a source, the layer, a person and the platform each as itself', async () => {
    open(OPERATOR)
    vi.mocked(api.getTimeline).mockResolvedValue(TIMELINE)
    const card = await screen.findByTestId('timeline')
    const entries = await within(card).findAllByTestId('timeline-entry')
    expect(entries.map((e) => e.getAttribute('data-actor'))).toEqual(
      ['SOURCE', 'SOURCE', 'AI', 'AI', 'PERSON', 'PLATFORM', 'PERSON', 'PERSON', 'PLATFORM'])
    // A source says which; the layer says it is the layer; a person is named; the platform says it acted.
    expect(within(entries[0]).getByText('CCTV · Gate 1')).toBeInTheDocument()
    expect(within(entries[1]).getByText('An access event at the door this camera watches, 4 s apart.')).toBeInTheDocument()
    expect(within(entries[2]).getByText('AI-assisted')).toBeInTheDocument()
    expect(within(entries[3]).getByText('AI suggestion — not a decision')).toBeInTheDocument()
    expect(within(entries[4]).getByText('Priya · Supervisor')).toBeInTheDocument()
    expect(within(entries[4]).getByText('Decided: dispatch a guard')).toBeInTheDocument()
    expect(within(entries[5]).getByText('What the platform then did')).toBeInTheDocument()
    expect(within(entries[5]).getByText('through app.routers.dispatch.dispatch_guard')).toBeInTheDocument()
    expect(within(entries[6]).getByText('Tan Wei Ming · Guard · from the phone')).toBeInTheDocument()
    expect(within(entries[8]).getByText('The incident’s own record')).toBeInTheDocument()
    // The line of a suggestion carries nothing that marks a person; the line of a decision nothing that marks the layer.
    expect(within(entries[3]).queryByText(/Priya|Supervisor|Decided/)).toBeNull()
    expect(within(entries[4]).queryByText(/AI/)).toBeNull()
    expect(within(card).getByText('The layer assessed or suggested (2)')).toBeInTheDocument()
    expect(within(card).getByText('A person looked, decided or reported (3)')).toBeInTheDocument()
    expect(within(card).getByText(/Each line is read from the record it describes/)).toBeInTheDocument()
  })

  it('folds repeats into one line with when the last was, and does not draw a failed step as done', async () => {
    open(OPERATOR)
    vi.mocked(api.getTimeline).mockResolvedValue(REPEATS_AND_A_FAILURE)
    const entries = await within(await screen.findByTestId('timeline')).findAllByTestId('timeline-entry')
    expect(within(entries[0]).getByText('The same alert again, 6 time(s): Person at Gate 1')).toBeInTheDocument()
    expect(within(entries[0]).getByText(/^The last at .+\.$/)).toBeInTheDocument()
    const failed = within(entries[1]).getByText('Could not dispatch the guard')
    expect(failed).toHaveAttribute('data-result', 'FAILED')
    expect(within(entries[1]).getByText('409: This guard is already dispatched to another incident.')).toBeInTheDocument()
    expect(within(entries[1]).queryByText(/Guard dispatched/)).toBeNull()
  })

  it('an empty timeline says so, and one without suggestions says they are not shown', async () => {
    const view = open(OPERATOR)
    const card = await screen.findByTestId('timeline')
    expect(await within(card).findByText('Nothing is recorded yet.')).toBeInTheDocument()
    expect(within(card).queryByText(/not shown to your role/)).toBeNull()
    view.unmount()
    open(VIEWER)
    vi.mocked(api.getTimeline).mockResolvedValue({
      ...TIMELINE, suggestions_shown: false, entries: TIMELINE.entries.filter((e) => e.kind !== 'RECOMMENDATION') })
    const again = await screen.findByTestId('timeline')
    expect(await within(again).findByText('What the layer suggested is not shown to your role.')).toBeInTheDocument()
    expect(within(again).queryByText('AI suggestion — not a decision')).toBeNull()
  })

  it('marks the summary as AI-assisted, says it is templates and not the record, and keeps each sentence’s sources', async () => {
    open(OPERATOR)
    const card = await screen.findByTestId('ai-summary')
    expect(within(card).getByText('AI-assisted summary')).toBeInTheDocument()
    const sentences = await within(card).findAllByTestId('summary-sentence')
    expect(sentences.map((s) => s.textContent?.trim())).toEqual(SUMMARY.sentences.map((s) => s.text))
    expect(sentences.map((s) => s.getAttribute('data-refs'))).toEqual(['1', '1', '0'])
    expect(within(card).getByText(/Written by fixed templates, not by a language model\./)).toBeInTheDocument()
    expect(within(card).getByText(/Times are in Asia\/Singapore\./)).toBeInTheDocument()
    expect(within(card).getByText(/It is not the record: the timeline below is\./)).toBeInTheDocument()
    expect(within(card).queryByText(/not shown to your role/)).toBeNull()
    expect(card.querySelector('[data-testid="human-decision"]')).toBeNull()
  })

  it('lists the evidence as references, loads nothing until it is opened, and records the opening first', async () => {
    const view = open(OPERATOR)
    await screen.findByTestId('ai-summary')
    await waitFor(() => expect(api.getSituationEvidence).toHaveBeenCalledWith('sit1'))
    expect(screen.queryByTestId('evidence-card')).toBeNull()         // nothing kept: no empty card
    view.unmount()
    open(OPERATOR)
    vi.mocked(api.getSituationEvidence).mockResolvedValue(EVIDENCE)
    vi.mocked(api.openSituationEvidence).mockResolvedValue({
      kind: 'SNAPSHOT', id: 'ev1', what: 'Frame at the detection', media_type: 'image', checksum_sha256: 'a'.repeat(64),
      served_at: { path: '/api/v1/evidence/ev1/image', token_in_query: true }, custody_entry: 'c1', audited: true })
    const card = await screen.findByTestId('evidence-card')
    const items = await within(card).findAllByTestId('evidence-item')
    expect(items.map((i) => i.getAttribute('data-kind'))).toEqual(['SNAPSHOT', 'RECORDING', 'PATROL_SNAPSHOT'])
    expect(within(items[0]).getByText(/SHA-256 aaaaaaaaaaaa…/)).toBeInTheDocument()
    expect(within(items[1]).getByText(/the event is 600 s in/)).toBeInTheDocument()
    expect(within(items[2]).getByText(/no checksum recorded/)).toBeInTheDocument()
    // Not a single image or video is on the page before a person opens one.
    expect(document.querySelector('img[data-testid="evidence-image"], video')).toBeNull()
    expect(api.openSituationEvidence).not.toHaveBeenCalled()
    // What this person may not open is there, disabled, and says which permission it needs.
    expect(within(items[2]).getByRole('button', { name: 'Open' })).toBeDisabled()
    fireEvent.mouseOver(within(items[2]).getByRole('button', { name: 'Open' }).parentElement!)
    expect(await screen.findByText('Opening this needs the permission vpatrol:read.')).toBeInTheDocument()

    fireEvent.click(within(items[0]).getByRole('button', { name: 'Open' }))
    await waitFor(() => expect(api.openSituationEvidence).toHaveBeenCalledWith('sit1', { kind: 'SNAPSHOT', id: 'ev1' }))
    const image = await screen.findByTestId('evidence-image')
    expect(image.getAttribute('src')).toContain('/api/v1/evidence/ev1/image?token=tok')
    expect(screen.getByText(/Opened by you\. Recorded in the chain of custody and the audit log\./)).toBeInTheDocument()
  })

  it('lists the camera of a drone still in the air among the cameras to open, as the drone’s', async () => {
    open(OPERATOR, authority(), EMPTY_TRAIL, NO_DRONE, SITUATION, { ...RECS, recommendations: [
      { ...RECS.recommendations[0], supporting: { ...RECS.recommendations[0].supporting, cameras: [
        { id: 'c1', name: 'Gate 1', state: 'online', relation: 'reported' },
        { id: 'c9', name: 'Drone One camera', state: 'not_known', relation: 'drone' }] } }] })
    expect(await screen.findByText('Drone One camera')).toBeInTheDocument()
    expect(screen.getByText('the drone that saw this, in the air now · state not known')).toBeInTheDocument()
    expect(screen.getByText('reported this · online')).toBeInTheDocument()
  })

  it('offers a review of a closed situation to someone who may give one, and says it changes nothing', async () => {
    const closed = { ...SITUATION, decision_status: 'RESOLVED' as const, closed_at: '2026-10-05T02:30:00Z' }
    const view = open(SUPERVISOR)
    await screen.findByTestId('ai-summary')
    await waitFor(() => expect(api.getSituationFeedback).toHaveBeenCalledWith('sit1'))
    expect(screen.queryByTestId('review-card')).toBeNull()           // still open: what it turned out to be is not asked yet
    view.unmount()
    open(SUPERVISOR, authority(), EMPTY_TRAIL, NO_DRONE, closed)
    vi.mocked(api.getSituationFeedback).mockResolvedValue({ ...NO_FEEDBACK, closed: true, may_review: true })
    vi.mocked(api.reviewSituation).mockResolvedValue({})
    const card = await screen.findByTestId('review-card')
    expect(within(card).getByText(/nothing in the platform\s+learns from it by itself/)).toBeInTheDocument()
    const record = within(card).getByRole('button', { name: 'Record my review' })
    expect(record).toBeDisabled()
    fireEvent.mouseDown(within(card).getByLabelText('What it turned out to be'))
    fireEvent.click(await screen.findByRole('option', { name: 'Could not be determined' }))
    expect(record).toBeDisabled()                                    // undetermined has to say what is not known
    fireEvent.change(within(card).getByLabelText('What is still not known (required)'), { target: { value: 'Camera was down.' } })
    expect(record).toBeEnabled()
    fireEvent.click(record)
    await waitFor(() => expect(api.reviewSituation).toHaveBeenCalledWith('sit1', {
      outcome: 'UNDETERMINED', assessment_verdict: undefined, recommendation_verdict: undefined, note: 'Camera was down.' }))
  })

  it('shows what reviewers said as people’s statements, and no form to someone who may not review', async () => {
    const closed = { ...SITUATION, decision_status: 'RESOLVED' as const, closed_at: '2026-10-05T02:30:00Z' }
    open(OPERATOR, authority(), EMPTY_TRAIL, NO_DRONE, closed)
    vi.mocked(api.getSituationFeedback).mockResolvedValue({
      ...NO_FEEDBACK, closed: true, may_review: false,
      reviews: [{ id: 'f1', outcome: 'AUTHORISED_ACTIVITY', assessment_verdict: 'TOO_HIGH', recommendation_verdict: 'NOT_USEFUL',
                  note: 'Night cleaner on the rota.', reviewed_at: '2026-10-05T03:00:00Z', user_id: 'sv1', name: 'Priya',
                  role_id: SUPERVISOR }] })
    const review = await screen.findByTestId('review')
    expect(within(review).getByText('Priya · Supervisor')).toBeInTheDocument()
    expect(within(review).getByText('Authorised activity')).toBeInTheDocument()
    expect(within(review).getByText(/The assessment: Assessed too high · The suggestion: Not useful/)).toBeInTheDocument()
    expect(within(review).getByText('Night cleaner on the rota.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Record my review' })).toBeNull()
  })

  it('a closed situation offers nothing more to decide', async () => {
    asRole(OPERATOR)
    vi.mocked(api.getIntelStatus).mockResolvedValue(ON)
    vi.mocked(api.getSituation).mockResolvedValue({ ...SITUATION, decision_status: 'RESOLVED', closed_at: '2026-10-05T02:30:00Z' })
    vi.mocked(api.getRecommendations).mockResolvedValue(RECS)
    vi.mocked(api.getAuthority).mockResolvedValue(authority())
    vi.mocked(api.getSituationDrone).mockResolvedValue(NO_DRONE)
    vi.mocked(api.getTimeline).mockResolvedValue(NO_TIMELINE)
    vi.mocked(api.getSummary).mockResolvedValue(SUMMARY)
    vi.mocked(api.getSituationEvidence).mockResolvedValue(NO_EVIDENCE)
    vi.mocked(api.getSituationFeedback).mockResolvedValue({ ...NO_FEEDBACK, closed: true })
    vi.mocked(api.getTrail).mockResolvedValue({ ...EMPTY_TRAIL, decision_status: 'RESOLVED', closed_at: '2026-10-05T02:30:00Z' })
    vi.mocked(api.recordReview).mockResolvedValue({ recorded: false, assessment_id: 'a1' })
    vi.mocked(api.getObservations).mockResolvedValue([])
    renderAt('/situations/sit1', '/situations/:id', <Situation />)
    expect(await screen.findByText('This situation is closed. Nothing more can be decided on it.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Monitor' })).toBeNull()
  })

  it('opening a recommended camera is the officer’s act, and the screen says the layer moves none', async () => {
    open(OPERATOR)
    fireEvent.click(await screen.findByText('Gate 1', { selector: '.MuiListItemText-primary' }))
    expect(await screen.findByTestId('hls')).toBeInTheDocument()
    expect(screen.getByText(/The layer moves no camera and changes no view\./)).toBeInTheDocument()
  })
})

describe('the situations list', () => {
  it('asks for open situations, highest risk first, and shows where each stands', async () => {
    asRole(OPERATOR)
    vi.mocked(api.getIntelStatus).mockResolvedValue(ON)
    vi.mocked(api.listSituations).mockResolvedValue({ items: [SITUATION], total: 1, limit: 25, offset: 0, has_more: false })
    renderAt('/situations', '/situations', <Situations />)
    expect(await screen.findByText('Person at Gate 1')).toBeInTheDocument()
    expect(screen.getByText('HIGH · 65')).toBeInTheDocument()
    expect(screen.getByText('Awaiting a decision')).toBeInTheDocument()
    expect(screen.getByText('Risk (AI)')).toBeInTheDocument()
    expect(screen.getByText('Stands (people)')).toBeInTheDocument()
    expect(vi.mocked(api.listSituations).mock.calls[0][0]).toMatchObject({ open: true, sort: 'risk' })
  })

  it('says so when the layer is switched off, or its runner is not running', async () => {
    asRole(OPERATOR)
    vi.mocked(api.listSituations).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0, has_more: false })
    vi.mocked(api.getIntelStatus).mockResolvedValue({ ...ON, enabled: false })
    const first = renderAt('/situations', '/situations', <Situations />)
    expect(await screen.findByText(/Security intelligence is switched off for this organisation/)).toBeInTheDocument()
    first.unmount()
    vi.mocked(api.getIntelStatus).mockResolvedValue({ ...ON, runner: { state: 'unknown', last_seen_at: null } })
    renderAt('/situations', '/situations', <Situations />)
    expect(await screen.findByText(/could not be asked whether it is running/)).toBeInTheDocument()
  })
})

describe('the Command Centre panel', () => {
  const page = { items: [SITUATION], total: 1, limit: 8, offset: 0, has_more: false }

  it('adds nothing to the Command Centre when the layer is off or the user may not read it', async () => {
    asRole(OPERATOR)
    vi.mocked(api.getIntelStatus).mockResolvedValue({ ...ON, enabled: false })
    vi.mocked(api.listSituations).mockResolvedValue(page)
    const off = renderAt('/', '/', <SituationsPanel />)
    await waitFor(() => expect(api.getIntelStatus).toHaveBeenCalled())
    expect(off.container).toBeEmptyDOMElement()
    expect(api.listSituations).not.toHaveBeenCalled()
    off.unmount()
    vi.clearAllMocks()
    asRole(7)          // a client: no intel permission at all
    const client = renderAt('/', '/', <SituationsPanel />)
    expect(client.container).toBeEmptyDOMElement()
    expect(api.getIntelStatus).not.toHaveBeenCalled()
  })

  it('shows the open situations, by risk, when the layer is on', async () => {
    asRole(OPERATOR)
    vi.mocked(api.getIntelStatus).mockResolvedValue(ON)
    vi.mocked(api.listSituations).mockResolvedValue(page)
    renderAt('/', '/', <SituationsPanel />)
    const panel = await screen.findByTestId('situations-panel')
    expect(within(panel).getByText('Person at Gate 1')).toBeInTheDocument()
    expect(within(panel).getByText(/assessed by the layer, decided by people/)).toBeInTheDocument()
    expect(vi.mocked(api.listSituations).mock.calls[0][0]).toMatchObject({ open: true, sort: 'risk' })
  })
})

describe('the decisions page', () => {
  const waiting: Decision = { ...DECISION, id: 'd2', action: 'CREATE_INCIDENT', basis: 'FOLLOWED', is_override: false,
                              reason: null, reason_code: null, note: null, authority: 'WITH_APPROVAL',
                              state: 'PENDING_APPROVAL', actions: [], decided_by: { user_id: 'g1', name: 'Tan Wei Ming', role_id: GUARD } }
  const page = { items: [waiting], total: 1, limit: 25, offset: 0, has_more: false }

  it('lets a supervisor approve or reject a waiting decision, and a rejection must say why', async () => {
    asRole(SUPERVISOR)
    vi.mocked(api.getIntelStatus).mockResolvedValue(ON)
    vi.mocked(api.listDecisions).mockResolvedValue(page)
    vi.mocked(api.rejectDecision).mockResolvedValue({ ...waiting, state: 'REJECTED' })
    renderAt('/situation-decisions', '/situation-decisions', <Decisions />)
    expect(await screen.findByText('Tan Wei Ming')).toBeInTheDocument()
    expect(screen.getByText('Waiting for approval')).toBeInTheDocument()
    expect(screen.getByText('Nothing')).toBeInTheDocument()        // nothing has been carried out
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
    const dialog = await screen.findByRole('dialog')
    const reject = within(dialog).getByRole('button', { name: 'Reject' })
    expect(reject).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why is it rejected? (required)'), { target: { value: 'Seen on camera 2.' } })
    fireEvent.click(reject)
    await waitFor(() => expect(api.rejectDecision).toHaveBeenCalledWith('d2', 'Seen on camera 2.'))
  })

  it('offers no approve button to someone who may not approve', async () => {
    asRole(OPERATOR)
    vi.mocked(api.getIntelStatus).mockResolvedValue(ON)
    vi.mocked(api.listDecisions).mockResolvedValue(page)
    renderAt('/situation-decisions', '/situation-decisions', <Decisions />)
    expect(await screen.findByText('Tan Wei Ming')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
  })
})

describe('setup', () => {
  const policy: DecisionPolicy = {
    roles: { 2: 'Admin', 8: 'Manager', 3: 'Supervisor', 4: 'Operator', 5: 'Guard' },
    levels: ['INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'],
    default: { 2: { alone: 'CRITICAL' }, 8: { alone: 'CRITICAL' }, 3: { alone: 'CRITICAL' }, 4: { alone: 'CRITICAL' }, 5: {} },
    always_allowed: ['REQUEST_ASSISTANCE'], tenant: null, sites: [],
  }

  function setup(role: number) {
    asRole(role)
    vi.mocked(api.getIntelStatus).mockResolvedValue({ ...ON, enabled: false })
    vi.mocked(api.getDecisionPolicy).mockResolvedValue(policy)
    vi.mocked(api.putDecisionPolicy).mockResolvedValue(policy)
    vi.mocked(api.listSiteProfiles).mockResolvedValue([{
      site_id: 's1', site_name: 'Factory A', has_profile: false, timezone: null, business_hours: null,
      closed_on_public_holidays: true, criticality: null, notes: null }])
    return renderAt('/intelligence-setup', '/intelligence-setup', <IntelSetup />)
  }

  it('an administrator switches the layer on, and it says that it never acts', async () => {
    setup(ADMIN)
    const toggle = await screen.findByRole('switch', { name: 'Off for this organisation' })
    expect(screen.getByText(/It never acts: alerts, pushes, incidents and video are unchanged either way\./)).toBeInTheDocument()
    fireEvent.click(toggle)
    await waitFor(() => expect(upsertSetting).toHaveBeenCalledWith('intel.enabled', true))
  })

  it('each policy of the specification is one press, saved as a setting', async () => {
    setup(ADMIN)
    fireEvent.click(await screen.findByRole('button', { name: /^C: High risk needs/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Save the policy' }))
    await waitFor(() => expect(api.putDecisionPolicy).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.putDecisionPolicy).mock.calls[0][0]).toEqual({ 5: { alone: 'MEDIUM', with_approval: 'CRITICAL' } })
  })

  it('a site nobody has described is shown as not known, not as a default', async () => {
    setup(ADMIN)
    expect(await screen.findByText('Not defined')).toBeInTheDocument()
    expect(screen.getByText('Not set')).toBeInTheDocument()
    expect(screen.getByText(/Nothing has been set, so the default applies: the command centre decides, a guard does not\./))
      .toBeInTheDocument()
  })

  it('someone who may not manage it can read it and change nothing', async () => {
    setup(OPERATOR)
    expect(await screen.findByText('You can read this setup. Changing it needs an administrator.')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /^A: The command centre decides/ })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save the policy' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Describe' })).toBeNull()
    expect(await screen.findByRole('switch', { name: 'Off for this organisation' })).toBeDisabled()
  })
})


describe('the insight page', () => {
  const SCORES: SiteScores = {
    days: 7,
    rules: [{ factor: 'OPEN_INCIDENTS', points_each: 5, at_most: 20, weight: 1, counts: 'each unresolved incident' }],
    bands: [{ band: 'GOOD', from: 85 }, { band: 'FAIR', from: 65 }, { band: 'NEEDS_ATTENTION', from: 40 }, { band: 'POOR', from: 0 }],
    sites: [
      { site_id: 's1', site_name: 'Factory A', score: 54, out_of: 100, band: 'NEEDS_ATTENTION', note: null,
        deductions: [
          { factor: 'OPEN_INCIDENTS', count: 3, points: -15, detail: '3 unresolved incident(s).', capped: false },
          { factor: 'CAMERAS_OFFLINE', count: 5, points: -20, detail: '5 camera(s) not sending.', capped: true },
          { factor: 'PATROLS_MISSED', count: 2, points: -6, detail: '2 patrol(s) missed or failed in the period.', capped: false },
          { factor: 'HIGH_RISK_OPEN', count: 1, points: -5, detail: '1 open situation(s) assessed HIGH or CRITICAL.', capped: false }],
        went_well: ['5 drone patrol(s) completed.'],
        basis: { cameras: 12, situations: 9, incidents: 4, virtual_patrols: 3, drone_patrols: 5 } },
      { site_id: 's2', site_name: 'Factory B', score: 100, out_of: 100, band: 'NOTHING_RECORDED', deductions: [], went_well: [],
        basis: { cameras: 0, situations: 0, incidents: 0, virtual_patrols: 0, drone_patrols: 0 },
        note: 'Nothing was recorded for this site in the period — no cameras, situations, incidents or patrols. '
          + 'The score says that nothing was found wrong, not that nothing is.' }],
  }
  const COUNTS: InsightData['counts'] = {
    timezone: 'Asia/Singapore', situations: 10, events: 31, repeats_folded: 12, still_open: 3, closed: 7,
    false_positive: 4, resolved: 3, by_risk: { CRITICAL: 1, HIGH: 2, MEDIUM: 5, LOW: 2, NOT_ASSESSED: 0 },
    by_source: { CCTV_AI: 20, DRONE_PATROL: 6, ACCESS_CONTROL: 5 }, by_hour: { 1: 4, 2: 4, 14: 2 },
    locations: [{ name: 'Rear perimeter', camera_id: 'c7', situations: 7 }, { name: 'Gate 1', camera_id: 'c1', situations: 3 }],
    vehicles: [{ plate: 'SGX1234A', situations: 4 }], persons: [{ watchlist_entry_id: 'w1', situations: 2 }],
    decisions: 9, decided: 8, followed: 5, overrides: 3, median_seconds_to_decide: 1500, guard_arrivals: 0,
    median_seconds_to_arrive: null, offline_names: ['Dock 4'], cameras: 12, cameras_offline: 1, high_risk_open: 1,
    unattended: 1, patrols_missed: 2, virtual_patrols: 3, drone_patrols: 5,
  }
  const INSIGHT: InsightData = {
    period: { days: 7, from: '2026-09-28T00:00:00Z', to: '2026-10-05T00:00:00Z', timezone: 'Asia/Singapore' },
    site: null, counts: COUNTS, is_advisory: true, score: null,
    findings: [{ code: 'CONCENTRATED_PLACE', is_advisory: true, is_decision: false,
                 finding: 'Rear perimeter is where 70% of the situations of the last 7 day(s) began (7 of 10).',
                 consider: 'Review what that camera covers and the rule that raises its alerts, and whether the place needs more patrols.',
                 rests_on: { place: 'Rear perimeter', situations: 7, of: 10 } }],
  }

  function openInsight(insight: InsightData = INSIGHT, scores: SiteScores = SCORES) {
    asRole(SUPERVISOR)
    vi.mocked(api.getIntelStatus).mockResolvedValue(ON)
    vi.mocked(api.getSiteScores).mockResolvedValue(scores)
    vi.mocked(api.getInsight).mockResolvedValue(insight)
    return renderAt('/security-insight', '/security-insight', <Insight />)
  }

  it('shows each site’s score with every point taken off as a line, and the lines add up', async () => {
    openInsight()
    const cards = await screen.findAllByTestId('site-score')
    expect(cards).toHaveLength(2)
    expect(within(cards[0]).getByText('Factory A')).toBeInTheDocument()
    expect(within(cards[0]).getByText('Needs attention')).toBeInTheDocument()
    const lines = within(cards[0]).getAllByTestId('score-line')
    expect(lines).toHaveLength(4)
    expect(within(lines[1]).getByText('5 camera(s) not sending.')).toBeInTheDocument()
    expect(within(lines[1]).getByText('-20 (the most this can take)')).toBeInTheDocument()
    // What the screen shows is what the score is made of: 100 less the lines.
    const shown = SCORES.sites[0].deductions.reduce((sum, d) => sum + d.points, 100)
    expect(shown).toBe(SCORES.sites[0].score)
    expect(within(cards[0]).getByText('54')).toBeInTheDocument()
    expect(within(cards[0]).getByText('✓ 5 drone patrol(s) completed.')).toBeInTheDocument()
    expect(screen.getByText(/it is not a prediction, and not a grade of anybody/)).toBeInTheDocument()
  })

  it('does not let a hundred from no records read as secure', async () => {
    openInsight()
    const cards = await screen.findAllByTestId('site-score')
    expect(within(cards[1]).getByText('100')).toBeInTheDocument()
    expect(within(cards[1]).getByText('Nothing took points off.')).toBeInTheDocument()
    expect(within(cards[1]).getByText('Nothing recorded')).toBeInTheDocument()
    expect(within(cards[1]).queryByText('Good')).toBeNull()
    expect(within(cards[1]).getByText(/The score says that nothing was found wrong, not that nothing is\./)).toBeInTheDocument()
  })

  it('draws a finding as AI-assisted advice with what a person might consider, never as a decision', async () => {
    openInsight()
    const finding = await screen.findByTestId('finding')
    expect(within(finding).getByText('AI-assisted finding — advice, not a decision')).toBeInTheDocument()
    expect(within(finding).getByText(/Rear perimeter is where 70% of the situations/)).toBeInTheDocument()
    expect(within(finding).getByText(/^To consider: Review what that camera covers/)).toBeInTheDocument()
    expect(finding.querySelector('button')).toBeNull()               // nothing on it can be pressed
    expect(screen.queryByTestId('human-decision')).toBeNull()
    expect(screen.getByText(/A rule about a share stays silent on fewer than five\./)).toBeInTheDocument()
  })

  it('says so when nothing stands out, and when there is too little to give a time', async () => {
    openInsight({ ...INSIGHT, findings: [], counts: { ...COUNTS, median_seconds_to_decide: null, decided: 0 } })
    expect(await screen.findByText('Nothing in the period stands out by the rules this page applies.')).toBeInTheDocument()
    const tiles = await screen.findAllByTestId('insight-tile')
    const decide = tiles.find((t) => within(t).queryByText('To first decision'))!
    expect(within(decide).getByText('not enough to say')).toBeInTheDocument()
    expect(duration(45)).toBe('45 s')
    expect(duration(1500)).toBe('25 min')
    expect(duration(9000)).toBe('2.5 h')
    expect(duration(null)).toBe('not enough to say')
  })

  it('names a number plate and counts a watchlist entry, and names nobody', async () => {
    openInsight()
    expect(await screen.findByText('Plate SGX1234A')).toBeInTheDocument()
    expect(screen.getByText(/1 watchlist entry was part of more than one situation\./)).toBeInTheDocument()
    expect(screen.getByText(/A person nobody identified is not counted as anybody, and nobody is named here\./)).toBeInTheDocument()
    expect(screen.queryByText('w1')).toBeNull()
  })

  it('asks again for the site and the period a person chooses', async () => {
    openInsight()
    const cards = await screen.findAllByTestId('site-score')
    await waitFor(() => expect(api.getInsight).toHaveBeenCalledWith({ site_id: undefined, days: 7 }))
    fireEvent.click(cards[0])
    await waitFor(() => expect(api.getInsight).toHaveBeenCalledWith({ site_id: 's1', days: 7 }))
    fireEvent.mouseDown(screen.getByLabelText('Period'))
    fireEvent.click(await screen.findByRole('option', { name: 'Last 30 days' }))
    await waitFor(() => expect(api.getSiteScores).toHaveBeenCalledWith(30))
    await waitFor(() => expect(api.getInsight).toHaveBeenCalledWith({ site_id: 's1', days: 30 }))
  })
})


describe('the feedback page', () => {
  const ANALYTICS: FeedbackAnalytics = {
    from: '2026-09-05T00:00:00Z', to: '2026-10-05T00:00:00Z', situations: 20, with_a_suggestion: 18, decided: 16,
    followed: 12, overridden: 4, acceptance_rate: 0.75, closed_false: 5, false_positive_rate: 0.25,
    override_reasons: { GUARD_RESPONDING: 3, AUTHORISED_ACTIVITY: 1 },
    by_suggested_action: { DISPATCH_GUARD: { suggested: 9, followed: 5, overridden: 4, closed_false: 2 },
                           VIEW_CAMERA: { suggested: 9, followed: 7, overridden: 0, closed_false: 3 } },
    by_kind: {}, reviewed: 6, review_outcomes: { AUTHORISED_ACTIVITY: 4, REAL_INCIDENT: 2 },
    review_of_assessment: { TOO_HIGH: 3 }, review_of_recommendation: {},
    use: 'For people to read. Nothing in the platform is trained on this dataset or changes because of it; a rule or '
      + 'a weight is changed only by a person, as a setting or as released code.',
  }

  function openFeedback(role: number, data: FeedbackAnalytics = ANALYTICS) {
    asRole(role)
    vi.mocked(api.getIntelStatus).mockResolvedValue(ON)
    vi.mocked(api.getFeedbackAnalytics).mockResolvedValue(data)
    return renderAt('/security-feedback', '/security-feedback', <Feedback />)
  }

  it('says in the server’s own words that nothing learns from it, and counts how the suggestions fared', async () => {
    openFeedback(SUPERVISOR)
    const use = await screen.findByTestId('feedback-use')
    expect(use).toHaveTextContent('Nothing in the platform is trained on this dataset or changes because of it')
    const rows = screen.getAllByTestId('suggested-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('Dispatch guard')).toBeInTheDocument()
    expect(rows[0]).toHaveTextContent('Dispatch guard9542')
    expect(screen.getByText('75%')).toBeInTheDocument()
    expect(screen.getByText('Guard Responding')).toBeInTheDocument()
    expect(screen.getByText('No reviewer has said.')).toBeInTheDocument()
    expect(screen.getByText(/Going against a suggestion is a person using their judgement\./)).toBeInTheDocument()
  })

  it('gives no rate where there is nothing to divide by', async () => {
    openFeedback(SUPERVISOR, { ...ANALYTICS, situations: 0, with_a_suggestion: 0, decided: 0, followed: 0, overridden: 0,
                               acceptance_rate: null, closed_false: 0, false_positive_rate: null, override_reasons: {},
                               by_suggested_action: {}, reviewed: 0, review_outcomes: {}, review_of_assessment: {} })
    expect(await screen.findByText('No situation with a suggestion was closed in the period.')).toBeInTheDocument()
    expect(screen.getAllByText('not enough to say').length).toBeGreaterThanOrEqual(2)
    expect(rate(null)).toBe('not enough to say')
    expect(rate(0)).toBe('0%')
    expect(rate(0.746)).toBe('75%')
  })

  it('offers the export only to someone given it', async () => {
    const view = openFeedback(SUPERVISOR)
    await screen.findByTestId('feedback-use')
    expect(screen.queryByRole('button', { name: /Export the dataset/ })).toBeNull()
    view.unmount()
    openFeedback(ADMIN)
    vi.mocked(api.exportFeedbackCsv).mockResolvedValue(new Blob(['situation_number\n']))
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:x')
    globalThis.URL.revokeObjectURL = vi.fn()
    const saved: string[] = []
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download)
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Export the dataset (CSV)' }))
    await waitFor(() => expect(api.exportFeedbackCsv).toHaveBeenCalledWith(30))
    await waitFor(() => expect(saved).toEqual(['security-feedback-last-30-days.csv']))
    click.mockRestore()
    expect(screen.getByText(/No names and none of what anyone wrote\. Each export is in the audit log\./)).toBeInTheDocument()
  })
})

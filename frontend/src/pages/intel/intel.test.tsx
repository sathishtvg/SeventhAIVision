import type { ReactNode } from 'react'
import { Route, Routes, MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/securityIntelligence'
import type {
  Authority, AuthorityAction, Decision, DecisionAction, DecisionPolicy, Recommendations, SituationDetail, Trail,
} from '@/api/securityIntelligence'
import { upsertSetting } from '@/api/settings'
import { SituationsPanel } from '@/components/intel/SituationsPanel'
import Situations from './Situations'
import Situation from './Situation'
import Decisions from './Decisions'
import IntelSetup from './IntelSetup'

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

function open(role: number, a: Authority = authority(), trail: Trail = EMPTY_TRAIL) {
  asRole(role)
  vi.mocked(api.getIntelStatus).mockResolvedValue(ON)
  vi.mocked(api.getSituation).mockResolvedValue(SITUATION)
  vi.mocked(api.getRecommendations).mockResolvedValue(RECS)
  vi.mocked(api.getAuthority).mockResolvedValue(a)
  vi.mocked(api.getTrail).mockResolvedValue(trail)
  vi.mocked(api.recordReview).mockResolvedValue({ recorded: true, assessment_id: 'a1' })
  vi.mocked(api.getResponders).mockResolvedValue({
    guards: [{ user_id: 'g1', name: 'Tan Wei Ming', on_shift_here: true, on_shift: true }],
    escalation: [{ user_id: 'sv1', name: 'Priya', role_id: SUPERVISOR }] })
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

  it('a closed situation offers nothing more to decide', async () => {
    asRole(OPERATOR)
    vi.mocked(api.getIntelStatus).mockResolvedValue(ON)
    vi.mocked(api.getSituation).mockResolvedValue({ ...SITUATION, decision_status: 'RESOLVED', closed_at: '2026-10-05T02:30:00Z' })
    vi.mocked(api.getRecommendations).mockResolvedValue(RECS)
    vi.mocked(api.getAuthority).mockResolvedValue(authority())
    vi.mocked(api.getTrail).mockResolvedValue({ ...EMPTY_TRAIL, decision_status: 'RESOLVED', closed_at: '2026-10-05T02:30:00Z' })
    vi.mocked(api.recordReview).mockResolvedValue({ recorded: false, assessment_id: 'a1' })
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

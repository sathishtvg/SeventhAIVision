import type { ReactNode } from 'react'
import { Route, Routes, MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import { dispatchGuard } from '@/api/guards'
import * as api from '@/api/incidentResponses'
import type {
  Clock, Clocks, Desk, DeskItem, Escalation, GuardResponse, Policy, RankedGuard, Recommendation, ResponseDetail,
  ResponseSettings as Settings,
} from '@/api/incidentResponses'
import {
  addressedTo, overBecause, policySentence, reached, readClock, since, span, toldSentence,
} from '@/components/response/responseFormat'
import ResponseDesk from './ResponseDesk'
import ResponseSettings from './ResponseSettings'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([
  { id: 's1', name: 'Factory A', latitude: 1.3, longitude: 103.8 }, { id: 's2', name: 'Factory B', latitude: null, longitude: null }]) }))
vi.mock('@/api/users', () => ({ getUsers: vi.fn().mockResolvedValue([
  { id: 'u-mgr', full_name: 'Lim Mei Ling', email: 'lim@x.test', role_id: 8, is_active: true },
  { id: 'u-sup', full_name: null, email: 'sup@x.test', role_id: 3, is_active: true },
  { id: 'u-gone', full_name: 'Left Last Year', email: 'gone@x.test', role_id: 3, is_active: false },
  { id: 'u-client', full_name: 'A Client', email: 'client@x.test', role_id: 7, is_active: true }]) }))
// The dispatch the platform has always had. The desk calls it; it does not replace it.
vi.mock('@/api/guards', () => ({ dispatchGuard: vi.fn() }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/incidentResponses', async (orig) => {
  const real = await orig<typeof import('@/api/incidentResponses')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

function Where() {
  const { pathname } = useLocation()
  return <div data-testid="went-to">{pathname}</div>
}

function renderAt(path: string, el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <ThemeProvider theme={theme}>
          <Routes><Route path={path} element={el} /><Route path="*" element={<Where />} /></Routes>
        </ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const NOW = '2026-10-07T03:00:00Z'
const NO_CLOCK: Clock = { started_at: null, due_at: null, met_at: null, breached: false, running: false, seconds_left: null }
const clock = (over: Partial<Clock>): Clock => ({
  started_at: '2026-10-07T02:50:00Z', due_at: '2026-10-07T03:02:00Z', met_at: null, breached: false, running: true,
  seconds_left: 120, ...over })
const clocks = (over: Partial<Clocks> = {}): Clocks => ({
  ACKNOWLEDGE: clock({ running: false, met_at: '2026-10-07T02:51:00Z', seconds_left: null }), ARRIVAL: NO_CLOCK,
  RESOLVE: clock({ seconds_left: 1500 }), ...over })
const NO_MAY = { accept: false, decline: false, en_route: false, arrived: false, report: false, stand_down: false }
const response = (over: Partial<GuardResponse> = {}): GuardResponse => ({
  id: 'r1', incident_id: 'i2', guard_user_id: 'g1', guard_name: 'Tan Wei Ming', dispatched_at: '2026-10-07T02:55:00Z',
  state: 'SENT', accepted_at: null, declined_at: null, decline_reason: null, en_route_at: null, arrived_at: null,
  stood_down_at: null, stood_down_by_name: null, stand_down_reason: null, ...over })
const item = (over: Partial<DeskItem>): DeskItem => ({
  id: 'i1', title: 'Forced gate', description: null, severity: 'high', status: 'open', created_at: '2026-10-07T02:50:00Z',
  resolved_at: null, site_id: 's1', site_name: 'Factory A', camera_id: 'c1', camera_name: 'North Gate',
  camera_location: 'Gate', dispatched_guard_id: null, dispatched_guard_name: null, dispatched_at: null,
  dispatch_notes: null, guard_arrived_at: null, sla_deadline_at: null, sla_breached: false, escalated_at: null,
  acknowledged_at: null, response: null, last_response: null, clocks: clocks(), late: [], judged: true, needs: 'DISPATCH',
  escalations: 0, may: NO_MAY, ...over })

const WAITING = item({ clocks: clocks({ ACKNOWLEDGE: clock({ breached: true, seconds_left: -180 }) }), late: ['ACKNOWLEDGE'],
                       escalations: 2, sla_breached: true })
const SENT = item({ id: 'i2', title: 'Tailgating at the dock', severity: 'critical', status: 'in_progress', needs: 'ANSWER',
                    dispatched_guard_id: 'g1', dispatched_guard_name: 'Tan Wei Ming', response: response(),
                    last_response: response(), clocks: clocks({ ARRIVAL: clock({ seconds_left: 110 }) }),
                    may: { ...NO_MAY, stand_down: true } })
const DECLINED = item({ id: 'i3', title: 'Door held open', severity: 'medium',
                        last_response: response({ id: 'r3', incident_id: 'i3', state: 'DECLINED', guard_name: 'Raj Kumar',
                                                  decline_reason: 'Holding a detained person at Gate 2' }) })
const DESK: Desk = {
  items: [SENT, WAITING, DECLINED], has_more: false, view: 'active', hours: 24, as_of: NOW,
  counts: { waiting: 2, unanswered: 1, on_the_way: 0, late: 1 }, sla_enabled: true, sla_since: '2026-10-01T00:00:00Z',
  can_dispatch: true, can_manage: false,
  note: 'The clocks tell people. They do not reassign, re-dispatch, close or raise the severity of an incident: a person does that.',
}
const ranked = (over: Partial<RankedGuard>): RankedGuard => ({
  user_id: 'g1', full_name: 'Tan Wei Ming', site_name: 'Factory A', latitude: 1.3009, longitude: 103.8,
  position_source: 'checkpoint scan', position_at: '2026-10-07T02:50:00Z', position_age_s: 600, stale: false,
  available: true, busy_incident_id: null, emergency_id: null, distance_m: 100, score: 80,
  parts: [{ factor: 'AVAILABILITY', points: 40, detail: 'Free: not sent on anything that is still open.' },
          { factor: 'DISTANCE', points: 30, detail: '100 m away by last recorded position.' },
          { factor: 'WORKLOAD', points: 0, detail: 'Not sent on anything yet this shift.' }], ...over })
const SUGGESTION: Recommendation = {
  incident_id: 'i1', incident: WAITING, is_decision: false, located: true,
  note: 'A suggestion, made of the parts shown beside it. It sends nobody: a person chooses who to dispatch.',
  site_requires: ['First aid'],
  guards: [ranked({}), ranked({ user_id: 'g2', full_name: 'Raj Kumar', available: false, busy_incident_id: 'i9', score: 15,
                                stale: true, distance_m: 2001, position_source: 'shift check-in', position_age_s: 10800,
                                parts: [{ factor: 'AVAILABILITY', points: 0, detail: 'Already sent on an incident that is still open.' },
                                        { factor: 'WORKLOAD', points: -5, detail: 'Already sent on 1 incident this shift.' }] })],
}
const escalation = (over: Partial<Escalation>): Escalation => ({
  id: 'e1', incident_id: 'i1', incident_title: 'Forced gate', severity: 'high', site_name: 'Factory A', kind: 'SLA_BREACH',
  clock: 'ACKNOWLEDGE', policy_name: null, due_at: '2026-10-07T02:52:00Z', notify_role_id: null, notify_role_name: null,
  notify_user_id: 'u-mgr', notify_user_name: 'Lim Mei Ling', recipients: 1, notification_sent: true,
  created_at: '2026-10-07T02:53:00Z', ...over })
const STEP = escalation({ id: 'e2', kind: 'POLICY_STEP', policy_name: 'Unacknowledged after ten minutes', notify_role_id: 3,
                          notify_role_name: 'Supervisors', notify_user_id: null, notify_user_name: null, recipients: 2 })
const NOBODY = escalation({ id: 'e3', kind: 'POLICY_STEP', policy_name: 'Tell the site B supervisor', recipients: 0,
                            notify_user_name: 'Supervisor At B', notification_sent: true })
const DETAIL: ResponseDetail = {
  incident: SENT, response: response({ state: 'EN_ROUTE' }), judged: true, sla_enabled: true, as_of: NOW,
  responses: [response({ state: 'EN_ROUTE' }),
              response({ id: 'r0', state: 'STOOD_DOWN', guard_name: 'Raj Kumar', stand_down_reason: 'The incident was dispatched again.' })],
  steps: [{ id: 'st1', response_id: 'r1', step: 'SENT', actor_user_id: null, actor_name: null, note: 'East path', latitude: null,
            longitude: null, occurred_at: '2026-10-07T02:55:00Z' },
          { id: 'st2', response_id: 'r1', step: 'EN_ROUTE', actor_user_id: 'g1', actor_name: 'Tan Wei Ming', note: null,
            latitude: 1.3, longitude: 103.8, occurred_at: '2026-10-07T02:56:00Z' },
          { id: 'st0', response_id: 'r0', step: 'STOOD_DOWN', actor_user_id: null, actor_name: null,
            note: 'The incident was dispatched again.', latitude: null, longitude: null, occurred_at: '2026-10-07T02:55:00Z' }],
  clocks: clocks({ ARRIVAL: clock({ seconds_left: 110 }) }), escalations: [escalation({}), STEP], may: NO_MAY,
}
const policy = (over: Partial<Policy>): Policy => ({
  id: 'p1', name: 'Unacknowledged criticals', site_id: null, site_name: null, severity: 'critical',
  trigger: 'NOT_ACKNOWLEDGED', after_seconds: 600, notify_role_id: 3, notify_user_id: null, notify_user_name: null,
  is_active: true, ...over })
const SETTINGS: Settings = {
  sla_enabled: false, sla_since: null, can_manage: true, note: DESK.note,
  times: [{ severity: 'critical', set: true, ack_within_seconds: 120, dispatch_within_seconds: 300, resolve_within_seconds: 1800,
            escalation_user_id: 'u-mgr', escalation_user_name: 'Lim Mei Ling' },
          { severity: 'high', set: false }, { severity: 'medium', set: false }, { severity: 'low', set: false },
          { severity: 'info', set: false }],
  triggers: ['NOT_ACKNOWLEDGED', 'NOT_ARRIVED', 'NOT_RESOLVED'], severities: ['critical', 'high', 'medium', 'low', 'info'],
  notify_roles: [{ role_id: 2, name: 'Administrators' }, { role_id: 3, name: 'Supervisors' }, { role_id: 4, name: 'Operators' },
                 { role_id: 5, name: 'Guards' }, { role_id: 6, name: 'Viewers' }, { role_id: 8, name: 'Managers' }],
}
const refusal = (detail: unknown, status = 422) => Object.assign(new Error('x'), { response: { status, data: { detail } } })

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'u1', tenantId: 't1', roleId: 4 }, accessToken: 'tok', permissions: null })) as never)
  vi.mocked(api.getDesk).mockResolvedValue(DESK)
  vi.mocked(api.recommend).mockResolvedValue(SUGGESTION)
  vi.mocked(api.getResponse).mockResolvedValue(DETAIL)
  vi.mocked(api.standDown).mockResolvedValue(DETAIL)
  vi.mocked(dispatchGuard).mockResolvedValue({})
  vi.mocked(api.getResponseSettings).mockResolvedValue(SETTINGS)
  vi.mocked(api.listPolicies).mockResolvedValue({ items: [policy({})], can_manage: true })
  vi.mocked(api.listEscalations).mockResolvedValue({ items: [escalation({}), STEP, NOBODY] })
  vi.mocked(api.switchClocks).mockResolvedValue({ sla_enabled: true, sla_since: NOW, changed: true })
  vi.mocked(api.writePolicy).mockResolvedValue(policy({}))
  vi.mocked(api.changePolicy).mockResolvedValue(policy({}))
  for (const fn of [api.saveTimes, api.retirePolicy, api.restorePolicy]) vi.mocked(fn).mockResolvedValue({})
})

describe('The response desk', () => {
  const desk = () => renderAt('/response-desk', <ResponseDesk />)

  it('shows each open incident with who was sent, where the response stands, and what it is waiting for', async () => {
    desk()
    const rows = await screen.findAllByTestId('desk-row')
    expect(api.getDesk).toHaveBeenCalledWith({ site_id: undefined, view: 'active' })
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Tailgating at the dock'), expect.stringContaining('Forced gate'),
      expect.stringContaining('Door held open')])
    expect(rows[0]).toHaveTextContent('Sent, not yet answered')
    expect(rows[0]).toHaveTextContent('Tan Wei Ming · sent 5 min ago')
    expect(rows[1]).toHaveTextContent('Factory A · North Gate · opened 10 min ago')
    expect(rows[1]).toHaveTextContent('Nobody sent')
    expect(rows[1]).toHaveTextContent('2 times')
    for (const [label, n] of [['Sent, not yet answered', '1'], ['Guard on the way', '0'], ['Late', '1']]) {
      expect(screen.getAllByText(label).some((el) => el.parentElement?.textContent?.startsWith(n))).toBe(true)
    }
    expect(screen.getByText(/They do not reassign, re-dispatch, close or raise the severity/)).toBeInTheDocument()
    expect(screen.queryByText(/clocks are not switched on/)).not.toBeInTheDocument()
  })

  it('a clock says how long is left or how late it is, and one that is not running says how it ended', async () => {
    desk()
    const rows = await screen.findAllByTestId('desk-row')
    expect(within(rows[1]).getByTestId('clock-ACKNOWLEDGE')).toHaveTextContent('Acknowledge: Late by 3 min')
    expect(within(rows[1]).getByTestId('clock-RESOLVE')).toHaveTextContent('Resolve: 25 min left')
    expect(within(rows[1]).queryByTestId('clock-ARRIVAL')).not.toBeInTheDocument()   // nobody is sent
    expect(within(rows[0]).getByTestId('clock-ARRIVAL')).toHaveTextContent('Arrive: 2 min left')
    expect(within(rows[0]).getByTestId('clock-ACKNOWLEDGE')).toHaveTextContent('Acknowledge: In time')
  })

  it('says when the clocks are off, and offers the settings only to somebody who may change them', async () => {
    vi.mocked(api.getDesk).mockResolvedValue({ ...DESK, sla_enabled: false, items: [{ ...WAITING, judged: false }] })
    desk()
    expect(await screen.findByText(/The response clocks are not switched on/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Response settings' })).not.toBeInTheDocument()
    // Shown as it stands, and not in the colour of something that was judged late.
    expect(screen.getByTestId('clock-ACKNOWLEDGE').className).not.toMatch(/colorError/)
  })

  it('a manager is offered the settings from there', async () => {
    vi.mocked(api.getDesk).mockResolvedValue({ ...DESK, sla_enabled: false, can_manage: true })
    desk()
    fireEvent.click(await screen.findByRole('link', { name: 'Response settings' }))
    expect(await screen.findByTestId('went-to')).toHaveTextContent('/response-settings')
  })

  it('a guard who cannot attend is named with the reason, and the incident is offered for sending again', async () => {
    desk()
    const rows = await screen.findAllByTestId('desk-row')
    expect(rows[2]).toHaveTextContent('Raj Kumar cannot attend: Holding a detained person at Gate 2')
    expect(within(rows[2]).getByRole('button', { name: 'Who to send' })).toBeInTheDocument()
    expect(within(rows[0]).queryByRole('button', { name: 'Who to send' })).not.toBeInTheDocument()   // somebody is out on it
  })

  it('who to send is a ranked suggestion with its reasons, and nobody is sent until a person chooses', async () => {
    desk()
    const rows = await screen.findAllByTestId('desk-row')
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Who to send' }))
    const dialog = await screen.findByRole('dialog')
    const guards = await within(dialog).findAllByTestId('ranked-guard')
    expect(api.recommend).toHaveBeenCalledWith('i1')
    expect(within(dialog).getByText(/It sends nobody: a person chooses who to dispatch/)).toBeInTheDocument()
    expect(within(dialog).getByText('This site requires: First aid.')).toBeInTheDocument()
    expect(guards[0]).toHaveTextContent('1. Tan Wei Ming')
    expect(guards[0]).toHaveTextContent('Score 80')
    expect(guards[0]).toHaveTextContent('100 m away by checkpoint scan, 10 min ago')
    expect(guards[0]).toHaveTextContent('+40 · Free: not sent on anything that is still open.')
    expect(guards[1]).toHaveTextContent('Already sent somewhere')
    expect(guards[1]).toHaveTextContent('Old position')
    expect(guards[1]).toHaveTextContent('2.0 km away by shift check-in, 3 h ago')
    expect(guards[1]).toHaveTextContent('-5 · Already sent on 1 incident this shift.')
    const send = within(dialog).getByRole('button', { name: 'Choose who to send' })
    expect(send).toBeDisabled()
    expect(dispatchGuard).not.toHaveBeenCalled()

    // The person may choose the second: the ranking is advice.
    fireEvent.click(within(guards[1]).getByRole('button', { name: 'Choose' }))
    fireEvent.change(within(dialog).getByLabelText('Instructions for Raj Kumar (optional)'), { target: { value: ' East path ' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send Raj Kumar' }))
    await waitFor(() => expect(dispatchGuard).toHaveBeenCalledWith('i1', { guard_user_id: 'g2', dispatch_notes: 'East path' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(api.getDesk).toHaveBeenCalledTimes(2))
  })

  it('says why there is nobody to suggest', async () => {
    vi.mocked(api.recommend).mockResolvedValue({ ...SUGGESTION, guards: [], site_requires: undefined,
                                                 why_nobody: 'Nobody is clocked in at this site.' })
    desk()
    fireEvent.click(within((await screen.findAllByTestId('desk-row'))[1]).getByRole('button', { name: 'Who to send' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Nobody is clocked in at this site.')).toBeInTheDocument()
    expect(within(dialog).queryByTestId('ranked-guard')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Choose who to send' })).toBeDisabled()
  })

  it('standing a guard down says why, and says nobody else is sent by it', async () => {
    vi.mocked(api.standDown).mockRejectedValueOnce(refusal({ message: 'That response is already over.', state: 'DECLINED' }, 409))
    desk()
    const rows = await screen.findAllByTestId('desk-row')
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Stand down' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Nobody else is sent by this: you choose who, if anybody/)).toBeInTheDocument()
    const act = within(dialog).getByRole('button', { name: 'Stand down' })
    expect(act).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why'), { target: { value: '  False alarm  ' } })
    fireEvent.click(act)
    expect(await within(dialog).findByText('That response is already over.')).toBeInTheDocument()
    fireEvent.click(act)
    await waitFor(() => expect(api.standDown).toHaveBeenLastCalledWith('i2', 'False alarm'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('somebody who may not dispatch reads the desk and is offered nothing that sends or calls off', async () => {
    vi.mocked(api.getDesk).mockResolvedValue({ ...DESK, can_dispatch: false, items: [{ ...SENT, may: NO_MAY }, WAITING] })
    desk()
    await screen.findAllByTestId('desk-row')
    expect(screen.queryByRole('button', { name: 'Who to send' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stand down' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Open' })).toHaveLength(2)
  })

  it('opening an incident shows each response, its steps and who was told', async () => {
    desk()
    fireEvent.click(within((await screen.findAllByTestId('desk-row'))[0]).getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog')
    const blocks = await within(dialog).findAllByTestId('response-block')
    expect(api.getResponse).toHaveBeenCalledWith('i2')
    expect(blocks[0]).toHaveTextContent('Tan Wei Ming')
    expect(blocks[0]).toHaveTextContent('On the way')
    expect(blocks[1]).toHaveTextContent('Raj Kumar')
    expect(blocks[1]).toHaveTextContent('Stood down')
    const steps = within(dialog).getAllByTestId('response-step').map((s) => s.textContent ?? '')
    expect(steps[0]).toMatch(/· Sent — East path$/)
    expect(steps[1]).toMatch(/· Set off by Tan Wei Ming$/)
    expect(steps[2]).toMatch(/· Stood down by the platform — The incident was dispatched again\.$/)
    const told = within(dialog).getAllByTestId('told-line').map((s) => s.textContent ?? '')
    expect(told[0]).toMatch(/“Forced gate” was not acknowledged in time · Lim Mei Ling$/)
    expect(told[1]).toMatch(/“Forced gate” — Unacknowledged after ten minutes · Supervisors \(2 people\)$/)
  })

  it('choosing a site or a view asks again for that, and an empty view says what is empty', async () => {
    desk()
    await screen.findAllByTestId('desk-row')
    fireEvent.mouseDown(screen.getByLabelText('Site'))
    fireEvent.click(await screen.findByRole('option', { name: 'Factory A' }))
    await waitFor(() => expect(api.getDesk).toHaveBeenLastCalledWith({ site_id: 's1', view: 'active' }))
    vi.mocked(api.getDesk).mockResolvedValue({ ...DESK, items: [], view: 'late' })
    fireEvent.click(screen.getByRole('button', { name: 'Late' }))
    await waitFor(() => expect(api.getDesk).toHaveBeenLastCalledWith({ site_id: 's1', view: 'late' }))
    expect(await screen.findByText('Nothing is late.')).toBeInTheDocument()
  })
})

describe('Response settings', () => {
  const settings = () => renderAt('/response-settings', <ResponseSettings />)

  it('the clocks are switched on by a person, and say that nothing older is judged', async () => {
    settings()
    const flip = await screen.findByRole('switch', { name: 'Judge incidents against the times below' })
    expect(flip).not.toBeChecked()
    expect(screen.getByText(/nothing older is marked late/)).toBeInTheDocument()
    expect(screen.getByText(/They do not reassign, re-dispatch, close or raise the severity/)).toBeInTheDocument()
    fireEvent.click(flip)
    await waitFor(() => expect(api.switchClocks).toHaveBeenCalledWith(true))
    await waitFor(() => expect(api.getResponseSettings).toHaveBeenCalledTimes(2))
  })

  it('says since when they have been on, and a refusal to switch is shown in the server’s words', async () => {
    vi.mocked(api.getResponseSettings).mockResolvedValue({ ...SETTINGS, sla_enabled: true, sla_since: '2026-10-01T00:00:00Z' })
    vi.mocked(api.switchClocks).mockRejectedValueOnce(refusal('The clocks are switched on and off for the whole organisation.', 403))
    settings()
    const flip = await screen.findByRole('switch', { name: 'Judge incidents against the times below' })
    expect(flip).toBeChecked()
    expect(screen.getByText(/^Switched on .* Incidents opened since then are judged/)).toBeInTheDocument()
    fireEvent.click(flip)
    expect(await screen.findByText('The clocks are switched on and off for the whole organisation.')).toBeInTheDocument()
    expect(api.switchClocks).toHaveBeenCalledWith(false)
  })

  it('the times are kept in minutes and saved through the endpoint that has always held them', async () => {
    settings()
    const rows = await screen.findAllByTestId('times-row')
    expect(rows).toHaveLength(5)
    expect(within(rows[0]).getByLabelText('Acknowledge within for critical, in minutes')).toHaveValue(2)
    expect(within(rows[0]).getByLabelText('Arrive within for critical, in minutes')).toHaveValue(5)
    expect(within(rows[0]).getByLabelText('Resolve within for critical, in minutes')).toHaveValue(30)
    expect(rows[1]).toHaveTextContent('No times set: never late')
    const save = within(rows[0]).getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()                                       // nothing has changed
    fireEvent.change(within(rows[0]).getByLabelText('Acknowledge within for critical, in minutes'), { target: { value: '1.5' } })
    fireEvent.click(save)
    await waitFor(() => expect(api.saveTimes).toHaveBeenCalledWith('critical', {
      ack_within_seconds: 90, dispatch_within_seconds: 300, resolve_within_seconds: 1800, escalation_user_id: 'u-mgr' }))

    // A severity with no times needs all three before it can be saved.
    const high = within(rows[1])
    fireEvent.change(high.getByLabelText('Acknowledge within for high, in minutes'), { target: { value: '5' } })
    expect(high.getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(high.getByLabelText('Arrive within for high, in minutes'), { target: { value: '10' } })
    fireEvent.change(high.getByLabelText('Resolve within for high, in minutes'), { target: { value: '0' } })
    expect(high.getByRole('button', { name: 'Save' })).toBeDisabled()   // nought minutes is not a time
    fireEvent.change(high.getByLabelText('Resolve within for high, in minutes'), { target: { value: '60' } })
    fireEvent.click(high.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.saveTimes).toHaveBeenLastCalledWith('high', {
      ack_within_seconds: 300, dispatch_within_seconds: 600, resolve_within_seconds: 3600, escalation_user_id: null }))
  })

  it('a policy is listed as a sentence with who it tells, and is retired and restored, never removed', async () => {
    vi.mocked(api.listPolicies).mockResolvedValue({ can_manage: true, items: [
      policy({}), policy({ id: 'p2', name: 'Not there', trigger: 'NOT_ARRIVED', after_seconds: 420, severity: null,
                           site_id: 's1', site_name: 'Factory A', notify_role_id: null, notify_user_id: 'u-mgr',
                           notify_user_name: 'Lim Mei Ling', is_active: false })] })
    settings()
    const rows = await screen.findAllByTestId('policy-row')
    expect(rows[0]).toHaveTextContent('When a critical incident is still not acknowledged after 10 min')
    expect(rows[0]).toHaveTextContent('Supervisors')
    expect(rows[1]).toHaveTextContent('When a guard sent to an incident at Factory A is still not there after 7 min')
    expect(rows[1]).toHaveTextContent('Lim Mei Ling')
    expect(rows[1]).toHaveTextContent('Retired')
    expect(within(rows[1]).queryByRole('button', { name: 'Change' })).not.toBeInTheDocument()
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Retire' }))
    await waitFor(() => expect(api.retirePolicy).toHaveBeenCalledWith('p1'))
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Restore' }))
    await waitFor(() => expect(api.restorePolicy).toHaveBeenCalledWith('p2'))
    expect(screen.queryByRole('button', { name: /delete|remove/i })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('switch', { name: 'Show retired policies' }))
    await waitFor(() => expect(api.listPolicies).toHaveBeenLastCalledWith(true))
  })

  it('a policy is written to tell a role or one person, after so many minutes', async () => {
    vi.mocked(api.writePolicy).mockRejectedValueOnce(refusal('That person may not see this site, so they would never be told.'))
    settings()
    fireEvent.click(await screen.findByRole('button', { name: 'Write a policy' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/It does not send a guard, reassign the incident or change it/)).toBeInTheDocument()
    const write = within(dialog).getByRole('button', { name: 'Write it' })
    expect(write).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: ' Nobody there yet ' } })
    fireEvent.mouseDown(within(dialog).getByLabelText('When'))
    fireEvent.click(await screen.findByRole('option', { name: 'A guard was sent and is still not there' }))
    expect(within(dialog).getByText('From when the guard was sent')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('After (minutes)'), { target: { value: '0.2' } })
    expect(write).toBeDisabled()                                      // under half a minute
    fireEvent.change(within(dialog).getByLabelText('After (minutes)'), { target: { value: '7' } })
    fireEvent.mouseDown(within(dialog).getByLabelText('Tell'))
    // Somebody who has left, and a client, are not people to tell.
    expect(screen.queryByRole('option', { name: 'Left Last Year' })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'A Client' })).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('option', { name: 'Lim Mei Ling' }))
    fireEvent.mouseDown(within(dialog).getByLabelText('At'))
    fireEvent.click(await screen.findByRole('option', { name: 'Factory B' }))
    fireEvent.click(write)
    expect(await within(dialog).findByText('That person may not see this site, so they would never be told.')).toBeInTheDocument()
    expect(api.writePolicy).toHaveBeenCalledWith({
      name: 'Nobody there yet', site_id: 's2', severity: null, trigger: 'NOT_ARRIVED', after_seconds: 420,
      notify_role_id: null, notify_user_id: 'u-mgr' })
    fireEvent.mouseDown(within(dialog).getByLabelText('Tell'))
    fireEvent.click(await screen.findByRole('option', { name: 'Operators' }))
    fireEvent.click(write)
    await waitFor(() => expect(api.writePolicy).toHaveBeenLastCalledWith(expect.objectContaining({
      notify_role_id: 4, notify_user_id: null })))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('a policy is changed without changing what it watches', async () => {
    settings()
    fireEvent.click(within((await screen.findAllByTestId('policy-row'))[0]).getByRole('button', { name: 'Change' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText('When')).toHaveAttribute('aria-disabled', 'true')
    expect(within(dialog).getByText('What a policy watches does not change. Retire it and write another.')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('After (minutes)'), { target: { value: '15' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.changePolicy).toHaveBeenCalledWith('p1', {
      name: 'Unacknowledged criticals', site_id: null, severity: 'critical', after_seconds: 900, notify_role_id: 3,
      notify_user_id: null }))
  })

  it('what was told says who it reached, and says so when it reached nobody', async () => {
    settings()
    const rows = (await screen.findAllByTestId('told-row')).map((r) => r.textContent ?? '')
    expect(api.listEscalations).toHaveBeenCalledWith({ hours: 24, limit: 50 })
    expect(rows[0]).toMatch(/“Forced gate” was not acknowledged in time \(Factory A\) · Lim Mei Ling$/)
    expect(rows[1]).toMatch(/Unacknowledged after ten minutes \(Factory A\) · Supervisors \(2 people\)$/)
    expect(rows[2]).toMatch(/Supervisor At B: nobody who may see this site$/)
  })

  it('somebody who may only read sees it all and can change none of it', async () => {
    vi.mocked(api.getResponseSettings).mockResolvedValue({ ...SETTINGS, can_manage: false })
    vi.mocked(api.listPolicies).mockResolvedValue({ items: [policy({})], can_manage: false })
    settings()
    expect(await screen.findByRole('switch', { name: 'Judge incidents against the times below' })).toBeDisabled()
    const rows = await screen.findAllByTestId('times-row')
    expect(within(rows[0]).getByLabelText('Acknowledge within for critical, in minutes')).toBeDisabled()
    expect(within(rows[0]).getByText('Lim Mei Ling')).toBeInTheDocument()   // the person named is still shown by name
    for (const name of ['Save', 'Write a policy', 'Change', 'Retire']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    expect(screen.queryByRole('switch', { name: 'Show retired policies' })).not.toBeInTheDocument()
  })

  it('says what happens with no policy written', async () => {
    vi.mocked(api.listPolicies).mockResolvedValue({ items: [], can_manage: true })
    vi.mocked(api.listEscalations).mockResolvedValue({ items: [] })
    settings()
    expect(await screen.findByText(/No policy has been written\. With the clocks on, the person named for a severity is still told/))
      .toBeInTheDocument()
    expect(screen.getByText('Nobody has been told anything.')).toBeInTheDocument()
  })
})

describe('the words the response screens share', () => {
  it('a length of time, and how long ago', () => {
    expect([45, 60, 150, 3600, 7500, 172800, 400000].map(span)).toEqual(
      ['45 s', '1 min', '3 min', '1 h', '2 h 5 min', '2 days', '5 days'])
    expect(span(-180)).toBe('3 min')
    expect(since('2026-10-07T02:50:00Z', NOW)).toBe('10 min ago')
    expect(since(null, NOW)).toBe('—')
  })

  it('a clock is read as time left, lateness, or how it ended', () => {
    expect(readClock(NO_CLOCK)).toEqual({ text: '—', tone: 'none' })
    expect(readClock(clock({ seconds_left: 1500 }))).toEqual({ text: '25 min left', tone: 'ok' })
    expect(readClock(clock({ seconds_left: 90 }))).toEqual({ text: '2 min left', tone: 'soon' })
    expect(readClock(clock({ seconds_left: -45, breached: true }))).toEqual({ text: 'Late by 45 s', tone: 'late' })
    expect(readClock(clock({ running: false, seconds_left: null }))).toEqual({ text: 'In time', tone: 'ok' })
    expect(readClock(clock({ running: false, seconds_left: null, breached: true }))).toEqual({ text: 'Met late', tone: 'late' })
  })

  it('who is not coming and why, and nothing for a response that still stands', () => {
    expect(overBecause(null)).toBeNull()
    expect(overBecause(response())).toBeNull()
    expect(overBecause(response({ state: 'DECLINED', decline_reason: 'On another call' })))
      .toBe('Tan Wei Ming cannot attend: On another call')
    expect(overBecause(response({ state: 'STOOD_DOWN', guard_name: null, stand_down_reason: 'False alarm' })))
      .toBe('The guard was stood down: False alarm')
  })

  it('a policy and what was told, each in a sentence', () => {
    expect(policySentence(policy({ severity: null, trigger: 'NOT_RESOLVED', after_seconds: 7200 })))
      .toBe('When an incident is still not resolved after 2 h')
    expect(policySentence(policy({ trigger: 'NOT_ARRIVED', site_name: 'Factory A', after_seconds: 90 })))
      .toBe('When a guard sent to a critical incident at Factory A is still not there after 2 min')
    expect(toldSentence(escalation({ clock: 'ARRIVAL' }))).toBe('“Forced gate” was not reached in time')
    expect(toldSentence(STEP)).toBe('“Forced gate” — Unacknowledged after ten minutes')
    expect(addressedTo({ notify_role_id: 4, notify_user_name: null }, SETTINGS.notify_roles)).toBe('Operators')
    expect(addressedTo({ notify_role_id: 9, notify_user_name: null })).toBe('Role 9')
    expect(reached(escalation({ recipients: 0, notify_user_name: null, notify_user_id: null }))).toBe('Nobody is named to be told')
    expect(reached(escalation({ notify_user_name: null, notify_role_id: 4, notify_role_name: 'Operators', recipients: 1 })))
      .toBe('Operators (1 person)')
  })

  it('the server’s reason is given in its own words, whichever shape it comes in', () => {
    expect(api.apiError(refusal('Nobody is sent on this incident.', 409))).toBe('Nobody is sent on this incident.')
    expect(api.apiError(refusal({ message: 'That response is already over.', state: 'DECLINED' }, 409)))
      .toBe('That response is already over.')
    expect(api.apiError(refusal([{ msg: 'Field required' }, { msg: 'Too short' }]))).toBe('Field required; Too short')
    expect(api.apiError(refusal('x', 429))).toMatch(/Too many requests/)
  })
})

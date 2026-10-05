/**
 * The situation screen, mounted.
 *
 * The unit tests cover which decisions a phone offers; this covers the screen
 * putting them in front of the person and wiring each to its call — and the
 * three things it must never blur: what the layer suggests, what the person
 * reports, and what the person decides.
 */
import React from 'react'
import { Alert } from 'react-native'
import { fireEvent, render, waitFor } from '@testing-library/react-native'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

jest.mock('@/hooks/useWebSocket', () => ({ useWebSocket: jest.fn() }))
jest.mock('expo-location', () => ({
  requestForegroundPermissionsAsync: jest.fn(async () => ({ status: 'granted' })),
  getCurrentPositionAsync: jest.fn(async () => ({ coords: { latitude: 1.3001, longitude: 103.8001 } })),
  Accuracy: { Balanced: 3 },
}))
jest.mock('@/api/client', () => ({
  rows: (d: unknown) => d,
  apiClient: { defaults: { baseURL: 'http://test.local', headers: { common: { Authorization: 'Bearer t' } } } },
}))
jest.mock('@/api/cameras', () => ({ getStreams: jest.fn(async () => [{ id: 'st1' }]) }))

const mockNavigate = jest.fn()
jest.mock('@react-navigation/native', () => ({ useNavigation: () => ({ navigate: mockNavigate }) }))

let mockPermissions: string[] | null = []
let mockRole = 5
jest.mock('@/store/auth', () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ permissions: mockPermissions, user: { roleId: mockRole } }),
}))

const SITUATION = {
  id: 's1', situation_number: 'SIT-20261005-0001', title: 'Person at Gate 1', site_name: 'Factory A',
  primary_camera_id: 'c1', primary_camera_name: 'Gate 1', location_label: null, latitude: 1.3001, longitude: 103.8001,
  started_at: '2026-10-05T02:17:04Z', event_count: 2, source_types: ['CCTV_AI', 'ACCESS_CONTROL'], risk_score: 70,
  risk_level: 'HIGH', decision_status: 'IN_HAND', closed_at: null as string | null,
  assessment: {
    id: 'a1', label: 'Access refused, with activity seen nearby', summary: 'x',
    risk_factors: [{ factor: 'ACCESS', points: 15, detail: 'Access was refused nearby in time.' }],
    confidence: { detection: 0.94, correlation: 0.85, risk: 0.52 }, unknowns: ['one', 'two'],
  },
  events: [], incident: { state: 'CONFIRMED', id: 'i1' },
}
let mockMine: Record<string, unknown>[] = []
let mockAuthority: Record<string, unknown> = {}
const mockReport = jest.fn(async (..._a: unknown[]) => ({ id: 'o1', kind: 'ACCEPTED' }))
const mockDecide = jest.fn(async (..._a: unknown[]) => ({ id: 'd1', action: 'INVESTIGATE', state: 'EFFECTIVE', actions: [] }))
const mockReview = jest.fn(async (..._a: unknown[]) => ({}))
const mockRecs = jest.fn(async (..._a: unknown[]) => ({
  is_decision: false, assessment: { id: 'a1' },
  recommendations: [{ id: 'r1', rank: 1, action: 'INVESTIGATE', reason: 'Go and look at the rear door.',
                      recommendation_confidence: 0.75, available: true, unavailable_reason: null }],
}))
jest.mock('@/api/securityIntelligence', () => {
  const actual = jest.requireActual('@/api/securityIntelligence')
  return {
    ...actual,
    getSituation: jest.fn(async () => SITUATION),
    getMySituations: jest.fn(async () => mockMine),
    getSituationRecommendations: (...a: unknown[]) => mockRecs(...a),
    getSituationAuthority: jest.fn(async () => mockAuthority),
    getSituationObservations: jest.fn(async () => [
      { id: 'o0', kind: 'OBSERVATION', note: 'Rear door is ajar.', name: 'Tan Wei Ming', role_id: 5, latitude: null,
        longitude: null, observed_at: '2026-10-05T02:25:00Z' }]),
    getSituationResponders: jest.fn(async () => ({ escalation: [{ user_id: 'sv1', name: 'Priya', role_id: 3 }] })),
    recordSituationReview: (...a: unknown[]) => mockReview(...a),
    reportFromSituation: (...a: unknown[]) => mockReport(...a),
    decideSituation: (...a: unknown[]) => mockDecide(...a),
  }
})

import { SituationDetailScreen } from '@/screens/SituationDetailScreen'

const GUARD = ['intel:read', 'intel:recommendation:read', 'intel:decide', 'camera:read']
const ALL = ['MONITOR', 'VERIFY', 'VIEW_CAMERA', 'VERIFY_WITH_DRONE', 'DISPATCH_GUARD', 'ESCALATE', 'INVESTIGATE',
             'CONTACT_SITE', 'CREATE_INCIDENT', 'ACKNOWLEDGE', 'CONFIRM_INCIDENT', 'REQUEST_ASSISTANCE', 'FALSE_POSITIVE', 'RESOLVE']

function authority(over: Record<string, Record<string, unknown>> = {}, top: Record<string, unknown> = {}) {
  const basis = (a: string) => (a === 'INVESTIGATE' ? 'FOLLOWED' : a === 'RESOLVE' || a === 'FALSE_POSITIVE' ? 'CLOSING'
    : ['ACKNOWLEDGE', 'REQUEST_ASSISTANCE', 'CONFIRM_INCIDENT'].includes(a) ? 'INDEPENDENT' : 'OVERRIDE')
  return {
    closed: false, in_reach: true, suggested_action: 'INVESTIGATE',
    reasons: [{ code: 'AUTHORISED_ACTIVITY', label: 'Authorised activity' }, { code: 'OTHER', label: 'Other' }],
    actions: ALL.map((action) => ({
      action, allowed: true, how: 'ALONE', basis: basis(action), needs_reason: ['OVERRIDE', 'CLOSING'].includes(basis(action)),
      needs: action === 'ESCALATE' ? ['escalate_to_user_id'] : [], why_not: null, ...over[action] })),
    ...top,
  }
}

let qc: QueryClient
function mount() {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: 0 } } })
  return render(
    <QueryClientProvider client={qc}>
      <SituationDetailScreen route={{ params: { situationId: 's1' } }} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  mockPermissions = GUARD
  mockRole = 5
  SITUATION.closed_at = null
  mockAuthority = authority()
  mockMine = [{ id: 's1', assigned_to_me: true, my_last: null, dispatched_at: '2026-10-05T02:18:17Z',
                dispatch_notes: 'North stairs, the lift is out.' }]
  jest.spyOn(Alert, 'alert').mockImplementation(() => undefined)
})

afterEach(() => qc?.clear())

describe('situation screen', () => {
  jest.setTimeout(120_000)

  it('shows the guard what it is, why, and what they were told', async () => {
    const s = mount()
    expect(await s.findByText('Access refused, with activity seen nearby')).toBeTruthy()
    expect(s.getByText('Risk 70')).toBeTruthy()
    expect(s.getByText('CCTV · Access control · 2 events')).toBeTruthy()
    expect(await s.findByText('The command centre sent you to this')).toBeTruthy()
    expect(s.getByText('North stairs, the lift is out.')).toBeTruthy()
    expect(s.getByText('Access was refused nearby in time.')).toBeTruthy()
    // Two confidences, each under its own name.
    expect(s.getByText('Detection confidence 94% · Risk confidence 52%')).toBeTruthy()
    expect(s.getByText('Not known: 2 thing(s).')).toBeTruthy()
  })

  it('marks what the layer suggests as a suggestion, apart from what is the guard\'s to decide', async () => {
    const s = mount()
    expect(await s.findByText('AI SUGGESTS — NOT A DECISION')).toBeTruthy()
    expect(s.getByText('Go and look at the rear door.')).toBeTruthy()
    expect(s.getByText('Recommendation confidence 75%')).toBeTruthy()
    expect(s.getByText('YOUR DECISION')).toBeTruthy()
    await waitFor(() => expect(mockReview).toHaveBeenCalledWith('s1'))
    expect(mockDecide).not.toHaveBeenCalled()      // looking decided nothing
  })

  it('accepting the job is a report with the phone\'s position, not a decision', async () => {
    const s = mount()
    fireEvent.press(await s.findByText('Accept'))
    await waitFor(() => expect(mockReport).toHaveBeenCalledTimes(1))
    const [id, kind, opts] = mockReport.mock.calls[0] as [string, string, { position?: unknown; clientRef: string }]
    expect([id, kind]).toEqual(['s1', 'ACCEPTED'])
    expect(opts.position).toEqual({ latitude: 1.3001, longitude: 103.8001 })
    expect(opts.clientRef).toMatch(/^[0-9a-f-]{36}$/)
    expect(mockDecide).not.toHaveBeenCalled()
  })

  it('offers arrived once accepted, and nothing further once there', async () => {
    mockMine = [{ id: 's1', assigned_to_me: true, my_last: 'ACCEPTED' }]
    const s = mount()
    expect(await s.findByText('Arrived')).toBeTruthy()
    expect(s.queryByText('Accept')).toBeNull()
    s.unmount()
    mockMine = [{ id: 's1', assigned_to_me: true, my_last: 'ARRIVED' }]
    const there = mount()
    await there.findByText('YOUR DECISION')
    expect(there.queryByText('Arrived')).toBeNull()
  })

  it('accepting the suggestion is a decision, recorded as the guard\'s', async () => {
    const s = mount()
    fireEvent.press(await s.findByText('Accept: Investigate'))
    expect(await s.findByText('This follows what the layer suggested.')).toBeTruthy()
    fireEvent.press(s.getByText('Record my decision'))
    await waitFor(() => expect(mockDecide).toHaveBeenCalledTimes(1))
    const [id, body] = mockDecide.mock.calls[0] as [string, Record<string, unknown>]
    expect(id).toBe('s1')
    expect(body).toMatchObject({ action: 'INVESTIGATE', seen_assessment_id: 'a1' })
    expect(body.reason_code).toBeUndefined()
  })

  it('closing needs a reason, and "other" needs to say what', async () => {
    const s = mount()
    fireEvent.press(await s.findByText('Resolve'))
    expect(await s.findByText('This closes the situation. Say how it ended.')).toBeTruthy()
    fireEvent.press(s.getByText('Record my decision'))
    expect(mockDecide).not.toHaveBeenCalled()
    fireEvent.press(s.getByText('Other'))
    fireEvent.press(s.getByText('Record my decision'))
    expect(mockDecide).not.toHaveBeenCalled()
    fireEvent.changeText(s.getByPlaceholderText('What was it? (required)'), 'Fox on the fence.')
    fireEvent.press(s.getByText('Record my decision'))
    await waitFor(() => expect(mockDecide).toHaveBeenCalledTimes(1))
    expect((mockDecide.mock.calls[0] as [string, Record<string, unknown>])[1])
      .toMatchObject({ action: 'RESOLVE', reason_code: 'OTHER', note: 'Fox on the fence.' })
  })

  it('says why a decision is not the guard\'s to take, in the server\'s words', async () => {
    mockAuthority = authority({ MONITOR: { allowed: false, how: null,
                                           why_not: 'Choosing it is an override, which needs the permission intel:override.' } })
    const s = mount()
    fireEvent.press(await s.findByText('Monitor'))
    expect(Alert.alert).toHaveBeenCalledWith(
      'Not yours to decide here', 'Choosing it is an override, which needs the permission intel:override.')
    expect(s.queryByText('Decide: Monitor')).toBeNull()
  })

  it('says when a decision will wait for the command centre', async () => {
    mockAuthority = authority(Object.fromEntries(ALL.map((a) => [a, { how: 'WITH_APPROVAL' }])))
    mockDecide.mockResolvedValueOnce({ id: 'd1', action: 'INVESTIGATE', state: 'PENDING_APPROVAL', actions: [] })
    const s = mount()
    fireEvent.press(await s.findByText('Accept: Investigate · needs approval'))
    expect(await s.findByText(/It will wait for the command centre's approval; nothing is carried out until then\./)).toBeTruthy()
    fireEvent.press(s.getByText('Send for approval'))
    await waitFor(() => expect(Alert.alert).toHaveBeenCalledWith(
      'Sent for approval', 'Your decision waits for the command centre. Nothing has been carried out yet.'))
  })

  it('records what the guard sees only once it says something', async () => {
    const s = mount()
    expect(await s.findByText('Rear door is ajar.')).toBeTruthy()
    fireEvent.press(s.getByText('Record what I see'))
    expect(mockReport).not.toHaveBeenCalled()
    fireEvent.changeText(s.getByPlaceholderText('What do you see?'), 'Nobody here, door now shut.')
    fireEvent.press(s.getByText('Record what I see'))
    await waitFor(() => expect(mockReport).toHaveBeenCalledTimes(1))
    const [, kind, opts] = mockReport.mock.calls[0] as [string, string, { note: string; position?: unknown }]
    expect(kind).toBe('OBSERVATION')
    expect(opts.note).toBe('Nobody here, door now shut.')
    expect(s.getByText('A report is recorded as yours. It decides nothing and changes no incident.')).toBeTruthy()
  })

  it('opens the live view of the situation\'s camera', async () => {
    const s = mount()
    fireEvent.press(await s.findByText('View live'))
    expect(mockNavigate).toHaveBeenCalledWith('SituationCameraLive', { cameraId: 'c1', streamId: 'st1', cameraName: 'Gate 1' })
  })

  it('shows a viewer the situation, and neither the suggestion nor a single button', async () => {
    mockPermissions = ['intel:read']
    mockRole = 6
    const s = mount()
    expect(await s.findByText('Your role can view this situation but not decide on it.')).toBeTruthy()
    expect(s.queryByText('AI SUGGESTS — NOT A DECISION')).toBeNull()
    expect(s.queryByText('Record what I see')).toBeNull()
    expect(s.queryByText('Accept')).toBeNull()
    expect(mockRecs).not.toHaveBeenCalled()
    expect(mockReview).not.toHaveBeenCalled()
  })

  it('offers nothing more on a closed situation', async () => {
    SITUATION.closed_at = '2026-10-05T02:40:00Z'
    const s = mount()
    expect(await s.findByText('This situation is closed. Nothing more can be decided on it.')).toBeTruthy()
    expect(s.queryByText('Accept')).toBeNull()
    expect(s.queryByText('Record what I see')).toBeNull()
  })
})

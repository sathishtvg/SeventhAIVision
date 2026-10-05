/**
 * The drone event screen, mounted.
 *
 * The unit tests cover which actions a role is offered; this covers the screen
 * actually putting them in front of the officer and wiring each to its call —
 * the layer where a renamed prop or a dropped button leaves every unit test
 * green and the phone useless.
 */
import React from 'react'
import { Alert } from 'react-native'
import { fireEvent, render, waitFor } from '@testing-library/react-native'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

jest.mock('react-native-webview', () => ({ WebView: 'WebView' }))
jest.mock('@/hooks/useWebSocket', () => ({ useWebSocket: jest.fn() }))
jest.mock('@/api/client', () => ({
  rows: (d: unknown) => d,
  apiClient: { defaults: { baseURL: 'http://test.local', headers: { common: { Authorization: 'Bearer t' } } } },
}))

const mockNavigate = jest.fn()
jest.mock('@react-navigation/native', () => ({ useNavigation: () => ({ navigate: mockNavigate }) }))

let mockPermissions: string[] | null = []
jest.mock('@/store/auth', () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ permissions: mockPermissions, user: { roleId: 4 } }),
}))

const mockEvent = {
  id: 'e1', site_id: 's1', site_name: 'North Yard', session_id: 'p1', session_number: 'DP-1', mission_name: 'Night round',
  drone_name: 'Hawk 1', drone_code: 'H1', module_type: 'intrusion', label: 'person',
  detected_at: '2026-09-25T14:00:00Z', detection_count: 3, observed_seconds: 6, drone_latitude: 1.3,
  drone_longitude: 103.8, location_method: 'DRONE_POSITION', zone_name: 'Fuel store', zone_type: 'RESTRICTED',
  ai_confidence: 0.91, risk_score: 62, risk_level: 'HIGH',
  risk_factors: [{ factor: 'zone', points: 20, detail: 'Inside a restricted zone' }],
  verification_state: 'VERIFIED', status: 'NEW', alert_id: 'a1', incident_id: null as string | null,
  resolved_by_name: null, false_positive_reason: null, incident: null, alert: null,
  media: [{ id: 'm1', media_kind: 'SNAPSHOT', storage_location: 'local', sync_state: 'pending',
            captured_at: '2026-09-25T14:00:01Z', size_bytes: null }],
}
let mockCard: Record<string, unknown> = { event_id: 'e1', headline: 'Person in Fuel store', incident: null,
                                         detected_at_site_time: '25 Sep 2026 22:00' }
const mockDecide = jest.fn(async (..._a: unknown[]) => ({}))
jest.mock('@/api/drones', () => {
  const actual = jest.requireActual('@/api/drones')
  return {
    ...actual,
    getDroneEvent: jest.fn(async () => mockEvent),
    getDroneEventCard: jest.fn(async () => mockCard),
    getDroneEventGuards: jest.fn(async () => []),
    decideDroneEvent: (...a: unknown[]) => mockDecide(...a),
    openDroneIncident: jest.fn(), dispatchDroneGuard: jest.fn(),
  }
})

import { DroneEventDetailScreen } from '@/screens/DroneEventDetailScreen'

const OPERATOR = ['drone:event:read', 'drone:event:acknowledge', 'drone:event:investigate', 'incident:read',
                  'incident:create', 'incident:dispatch']

let qc: QueryClient
function mount() {
  // gcTime 0 on mutations too: a settled mutation is otherwise kept for five
  // minutes, and jest waits out that timer before it exits.
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: 0 } } })
  return render(
    <QueryClientProvider client={qc}>
      <DroneEventDetailScreen route={{ params: { eventId: 'e1' } }} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  mockPermissions = OPERATOR
  mockEvent.incident_id = null
  mockCard = { event_id: 'e1', headline: 'Person in Fuel store', incident: null,
               detected_at_site_time: '25 Sep 2026 22:00' }
  jest.spyOn(Alert, 'alert').mockImplementation(() => undefined)
})

// Leave nothing of the query client behind for the next test or for jest's exit.
afterEach(() => qc?.clear())

describe('drone event screen', () => {
  jest.setTimeout(120_000)

  it('shows an operator the event and the whole response', async () => {
    const s = mount()
    expect(await s.findByText('Person in Fuel store')).toBeTruthy()
    // Two numbers, labelled apart.
    expect(s.getByText('Risk 62')).toBeTruthy()
    expect(s.getByText('AI confidence 91%')).toBeTruthy()
    expect(s.getByText('Inside a restricted zone')).toBeTruthy()
    // Footage still on the site's gateway says so instead of a broken image.
    expect(s.getByText(/Held at the site/)).toBeTruthy()
    for (const label of ['Acknowledge', 'Escalate', 'Open Incident', 'Dispatch Guard', 'Resolve', 'False Positive']) {
      expect(s.getByText(label)).toBeTruthy()
    }
  })

  it('acknowledges in one tap', async () => {
    const s = mount()
    fireEvent.press(await s.findByText('Acknowledge'))
    await waitFor(() => expect(mockDecide).toHaveBeenCalledWith('e1', 'acknowledge'))
  })

  it('will not send a false positive until a reason is typed', async () => {
    const s = mount()
    fireEvent.press(await s.findByText('False Positive'))
    const input = s.getByPlaceholderText('Why is it a false positive? (required)')
    // The sheet's confirm button carries the same label as the action.
    const confirm = () => s.getAllByText('False Positive').slice(-1)[0]
    fireEvent.press(confirm())
    expect(mockDecide).not.toHaveBeenCalled()
    fireEvent.changeText(input, 'A guard on his rounds')
    fireEvent.press(confirm())
    await waitFor(() => expect(mockDecide).toHaveBeenCalledWith('e1', 'false-positive', 'A guard on his rounds'))
  })

  it('offers a guard only the incident', async () => {
    mockPermissions = ['drone:event:read', 'incident:read', 'incident:create']
    const s = mount()
    expect(await s.findByText('Open Incident')).toBeTruthy()
    expect(s.queryByText('Acknowledge')).toBeNull()
    expect(s.queryByText('Dispatch Guard')).toBeNull()
  })

  it('tells a viewer why there are no buttons', async () => {
    mockPermissions = ['drone:event:read']
    const s = mount()
    expect(await s.findByText('Your role can view this event but not act on it.')).toBeTruthy()
  })

  it('leads from the event to its incident, where it is updated', async () => {
    mockEvent.incident_id = 'i1'
    mockCard = { ...mockCard, incident: { id: 'i1', title: 'Intrusion at Fuel store', severity: 'high', status: 'open',
                                           incident_ref: 'DRN-0007', dispatched_guard_name: 'Ravi', dispatched_at: null,
                                           guard_arrived_at: null } }
    const s = mount()
    expect(await s.findByText('DRN-0007 · Intrusion at Fuel store')).toBeTruthy()
    expect(s.getByText(/Ravi dispatched/)).toBeTruthy()
    fireEvent.press(s.getByText('Open the incident to update it'))
    expect(mockNavigate).toHaveBeenCalledWith('Incidents', {
      screen: 'IncidentDetail', params: { incidentId: 'i1' }, initial: false, pop: true })
  })
})

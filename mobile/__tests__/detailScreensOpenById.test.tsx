/**
 * The alert and incident screens open the record they were asked for.
 *
 * WHY THIS MATTERS
 *   Each screen used to load the first page of the unfiltered list and look
 *   for its record there. The list is paginated, so only the newest fifty were
 *   ever looked through: on a tenant with 8,628 open alerts, tapping almost
 *   any row of the "Open" filter opened "Alert not found" (seen on the demo
 *   tenant, 2026-10-05). Incidents did the same.
 *
 *   These mount the real screens over the real API modules, with the network
 *   stood in for, and hold three things: the record is asked for by its id; a
 *   row the app already holds is shown at once, and still shown when the
 *   network does not answer; and something that is not there says so.
 */
import React from 'react'
import { Alert as RNAlert } from 'react-native'
import { fireEvent, render, waitFor } from '@testing-library/react-native'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const mockGet = jest.fn()
const mockPost = jest.fn(async (..._a: unknown[]) => ({ data: {} }))
jest.mock('@/api/client', () => ({
  rows: (d: { items?: unknown[] } | unknown[] | null | undefined) => (Array.isArray(d) ? d : d?.items ?? []),
  apiClient: {
    get: (...a: unknown[]) => mockGet(...a), post: (...a: unknown[]) => mockPost(...a),
    put: jest.fn(async () => ({ data: {} })), patch: jest.fn(async () => ({ data: {} })),
    defaults: { baseURL: 'http://test.local', headers: { common: { Authorization: 'Bearer t' } } },
  },
}))

let mockParams: Record<string, string> = {}
const mockNavigate = jest.fn()
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ navigate: mockNavigate }),
  useRoute: () => ({ params: mockParams }),
}))
jest.mock('@/store/auth', () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ permissions: ['alert:read', 'incident:read'], user: { roleId: 4 } }),
}))

import { AlertDetailScreen } from '@/screens/AlertDetailScreen'
import { IncidentDetailScreen } from '@/screens/IncidentDetailScreen'
import { listed } from '@/lib/listed'

const OLD_ALERT = {
  id: 'a-old', camera_id: 'c1', camera_name: 'Gate 1', site_name: 'North Yard', module_type: 'intrusion',
  severity: 'high', alert_code: 'intrusion.zone_breach', title: 'Person at Gate 1 in August',
  message: 'Two people at the north fence.', status: 'open', acknowledged_at: null, created_at: '2026-08-06T03:00:00Z',
}
const NEW_ALERT = { ...OLD_ALERT, id: 'a-new', title: 'The newest alert', created_at: '2026-09-21T03:00:00Z' }
const OLD_INCIDENT = {
  id: 'i-old', camera_id: 'c1', camera_name: 'Gate 1', site_name: 'North Yard', title: 'Forced gate in August',
  description: 'The north gate was found open.', severity: 'high', status: 'open', is_auto_created: false,
  resolved_at: null, created_at: '2026-08-06T03:00:00Z', updated_at: '2026-08-06T03:10:00Z',
}
const NEW_INCIDENT = { ...OLD_INCIDENT, id: 'i-new', title: 'The newest incident' }

/** The server: lists hold only the newest record; a record is there by its id. */
function server(over: Record<string, () => Promise<{ data: unknown }>> = {}) {
  mockGet.mockImplementation(async (url: string) => {
    if (over[url]) return over[url]()
    if (url === '/api/v1/alerts') return { data: { items: [NEW_ALERT], total: 8628 } }
    if (url === '/api/v1/alerts/a-old') return { data: OLD_ALERT }
    if (url === '/api/v1/incidents') return { data: { items: [NEW_INCIDENT], total: 1398 } }
    if (url === '/api/v1/incidents/i-old') return { data: OLD_INCIDENT }
    if (url.endsWith('/timeline') || url.endsWith('/streams')) return { data: [] }
    throw Object.assign(new Error('Request failed with status code 404'), { response: { status: 404 } })
  })
}
const asked = () => mockGet.mock.calls.map((c) => c[0] as string)
const never = () => new Promise<{ data: unknown }>(() => undefined)

let qc: QueryClient
function mount(screen: React.ReactElement) {
  return render(<QueryClientProvider client={qc}>{screen}</QueryClientProvider>)
}

beforeEach(() => {
  jest.clearAllMocks()
  // Kept for ever, as the app's persisted cache keeps a list: a row put there
  // is still there when a screen is opened a second time.
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: 0 } } })
  server()
  jest.spyOn(RNAlert, 'alert').mockImplementation(() => undefined)
})
// Let whatever is still being read come back before the next test, so that a
// late answer does not land on a screen that has gone.
afterEach(async () => {
  await waitFor(() => expect(qc.isFetching()).toBe(0))
  qc.clear()
})

describe('the alert screen', () => {
  jest.setTimeout(120_000)

  it('opens an alert that is not on the first page of the list, by asking for it by id', async () => {
    mockParams = { alertId: 'a-old' }
    const s = mount(<AlertDetailScreen />)
    expect(await s.findByText('Person at Gate 1 in August')).toBeTruthy()
    expect(s.getByText('Two people at the north fence.')).toBeTruthy()
    expect(asked()).toContain('/api/v1/alerts/a-old')
    expect(asked()).not.toContain('/api/v1/alerts')          // the list is not what it looks in any more
    expect(s.queryByText('Alert not found')).toBeNull()
  })

  it('shows the row a list already holds at once, and keeps it when the network does not answer', async () => {
    mockParams = { alertId: 'a-old' }
    qc.setQueryData(['alerts', 'open', undefined, undefined], [{ ...OLD_ALERT, message: null }])
    server({ '/api/v1/alerts/a-old': never })
    const s = mount(<AlertDetailScreen />)
    expect(s.getByText('Person at Gate 1 in August')).toBeTruthy()   // no waiting: it was already here
    s.unmount()
    qc.removeQueries({ queryKey: ['alerts', 'one'] })                // the request that never came back

    server({ '/api/v1/alerts/a-old': async () => { throw new Error('Network Error') } })
    const offline = mount(<AlertDetailScreen />)
    await waitFor(() => expect(asked().filter((u) => u === '/api/v1/alerts/a-old').length).toBe(2))
    expect(offline.getByText('Person at Gate 1 in August')).toBeTruthy()
    expect(offline.queryByText('Alert not found')).toBeNull()
  })

  it('says so when the alert is not there', async () => {
    mockParams = { alertId: 'a-gone' }
    const s = mount(<AlertDetailScreen />)
    expect(await s.findByText('Alert not found')).toBeTruthy()
  })

  it('acknowledging it reads it again, so the screen shows what the server now says', async () => {
    mockParams = { alertId: 'a-old' }
    const s = mount(<AlertDetailScreen />)
    fireEvent.press(await s.findByText('Acknowledge Alert'))
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith('/api/v1/alerts/a-old/acknowledge', { via: 'mobile' }))
    await waitFor(() => expect(asked().filter((u) => u === '/api/v1/alerts/a-old').length).toBe(2))
  })
})

describe('the incident screen', () => {
  jest.setTimeout(120_000)

  it('opens an incident that is not on the first page of the list, by asking for it by id', async () => {
    mockParams = { incidentId: 'i-old' }
    const s = mount(<IncidentDetailScreen />)
    expect(await s.findByText('Forced gate in August')).toBeTruthy()
    expect(s.getByText('The north gate was found open.')).toBeTruthy()
    expect(asked()).toContain('/api/v1/incidents/i-old')
    expect(asked()).not.toContain('/api/v1/incidents')
    // What hangs off the incident is asked for once it is known.
    await waitFor(() => expect(asked()).toContain('/api/v1/incidents/i-old/timeline'))
  })

  it('shows the row a list already holds when the network does not answer', async () => {
    mockParams = { incidentId: 'i-old' }
    qc.setQueryData(['incidents', 'open'], [OLD_INCIDENT])
    server({ '/api/v1/incidents/i-old': async () => { throw new Error('Network Error') } })
    const s = mount(<IncidentDetailScreen />)
    await waitFor(() => expect(asked()).toContain('/api/v1/incidents/i-old'))
    expect(s.getByText('Forced gate in August')).toBeTruthy()
    expect(s.queryByText('Incident not found')).toBeNull()
  })

  it('says so when the incident is not there', async () => {
    mockParams = { incidentId: 'i-gone' }
    const s = mount(<IncidentDetailScreen />)
    expect(await s.findByText('Incident not found')).toBeTruthy()
  })
})

describe('a record the app already holds', () => {
  it('is found in any list under the key, and a single record there is not mistaken for a list', () => {
    const cached: [unknown, unknown][] = [
      [['alerts', 'one', 'a-new'], NEW_ALERT],            // a record, not a list
      [['alerts', 'open'], undefined],                    // a list still on its way
      [['alerts', 'all'], [NEW_ALERT]],
      [['alerts', 'acknowledged'], [OLD_ALERT]],
    ]
    expect(listed(cached, 'a-old')).toBe(OLD_ALERT)
    expect(listed(cached, 'a-new')).toBe(NEW_ALERT)
    expect(listed(cached, 'nowhere')).toBeUndefined()
    expect(listed([], 'a-old')).toBeUndefined()
  })
})

import type { ReactNode } from 'react'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/drones'
import type { AnalyticsOverview, Recommendations, RiskMap } from '@/api/drones'
import { BarList, ColumnChart } from '@/components/drones/charts'
import DroneAnalytics from './DroneAnalytics'
import { DroneNav } from './DroneNav'

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: ReactNode }) => <div data-testid="map">{children}</div>,
  TileLayer: () => null, Marker: () => null, Polyline: () => null,
  CircleMarker: ({ children }: { children?: ReactNode }) => <div data-testid="hot-spot">{children}</div>,
  Polygon: ({ children }: { children?: ReactNode }) => <div data-testid="zone">{children}</div>,
  Circle: ({ children }: { children?: ReactNode }) => <div data-testid="zone">{children}</div>,
  Tooltip: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  useMap: () => ({ setView: () => {}, fitBounds: () => {}, getZoom: () => 16 }),
  useMapEvents: () => null,
}))
vi.mock('leaflet', () => ({ default: { divIcon: () => ({}), latLngBounds: () => ({}) } }))
vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([{ id: 's1', name: 'North Yard' }]) }))
vi.mock('@/api/drones', async (orig) => {
  const real = await orig<typeof import('@/api/drones')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

function asRole(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'u1', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

const OVERVIEW: AnalyticsOverview = {
  scope: 'all sites', from: '2026-09-28', to: '2026-10-04', days: 7, timezone: 'Asia/Singapore',
  flights: { total: 20, completed: 15, due: 18, success_rate: 0.8333, by_status: { COMPLETED: 15, BLOCKED: 3 },
             flight_seconds: 7200, distance_m: 9000,
             did_not_complete: [{ status: 'BLOCKED', reason: 'Battery too low', flights: 3 }] },
  events: { total: 40, suspicious: 12, verified: 30, open: 4, unreviewed_over_a_day: 2,
            by_risk: { CRITICAL: 1, HIGH: 5, MEDIUM: 6, LOW: 20, INFO: 8 }, false_positives: 10,
            false_positive_rate: 0.25, incidents: 3, with_incident: 4, incident_conversion_rate: 0.1, per_flight: 2 },
  detection_types: [
    { module_type: 'intrusion', name: 'Intrusion', events: 25, suspicious: 10, false_positives: 2,
      false_positive_rate: 0.08, incident_conversion_rate: 0.12 },
    { module_type: 'lpr', name: 'Licence plate', events: 15, suspicious: 2, false_positives: 8,
      false_positive_rate: 0.5333, incident_conversion_rate: 0.07 },
  ],
  missions: [{ name: 'Night Perimeter', flights: 20, completed: 15, due: 18, success_rate: 0.8333,
               common_reason: 'Battery too low', events: 40, suspicious: 12, with_incident: 4 }],
  drones: [{ name: 'Hawk One', flights: 20, completed: 15, due: 18, success_rate: 0.8333, common_reason: null,
             events: 40, suspicious: 12, with_incident: 4 }],
  suspicious_by_hour: Array.from({ length: 24 }, (_, h) => ({ hour: h, events: h === 23 ? 7 : 0, night: h >= 19 || h < 7 })),
  suspicious_by_weekday: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((name, i) => ({ weekday: i + 1, name, events: i })),
  daily: ['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']
    .map((day, i) => ({ day, flights: 3, completed: 2, events: i === 5 ? 12 : 4, suspicious: i, false_positives: 1, incidents: 0 })),
}
const RISK: RiskMap = {
  scope: 'all sites', from: '2026-09-28', to: '2026-10-04', days: 7, timezone: 'Asia/Singapore',
  disclaimer: 'An analytical score from recorded drone events in the period. It is not a prediction of where something will happen.',
  method: { description: 'Each event adds its weight; the total per week, times 5, is the score.',
            weights: { CRITICAL: 10, HIGH: 6, MEDIUM: 3, LOW: 1, INFO: 0 }, levels: { HIGH: 60, MEDIUM: 25, LOW: 1 },
            cell_metres: 28 },
  areas: [
    { site_id: 's1', site_name: 'North Yard', area: 'Rear Perimeter', zone_type: 'RESTRICTED', zone_id: 'z1', score: 78,
      level: 'HIGH', weighted_events: 16, events: 9, suspicious: 7, night_suspicious: 6, false_positives: 0,
      incidents: 2, suspicious_without_cctv: 0, last_event_at: '2026-10-03T15:00:00Z' },
    { site_id: 's1', site_name: 'North Yard', area: 'Main Gate', zone_type: 'NORMAL', zone_id: null, score: 5,
      level: 'LOW', weighted_events: 1, events: 1, suspicious: 0, night_suspicious: 0, false_positives: 0,
      incidents: 0, suspicious_without_cctv: 0, last_event_at: null },
  ],
  hot_spots: [{ latitude: 1.3, longitude: 103.8, events: 5, suspicious: 4, weighted_events: 20 }],
  repeated_intrusion_locations: [{ latitude: 1.3, longitude: 103.8, zone_name: 'Rear Perimeter', site_name: 'North Yard',
                                   events: 4, days: 3, at_night: 4, first_seen_at: '2026-09-29T15:00:00Z',
                                   last_seen_at: '2026-10-03T15:00:00Z' }],
}
const RECS: Recommendations = {
  scope: 'all sites', rules: {},
  disclaimer: 'System-generated suggestions from recorded events and flights. Each is a prompt for review, not a conclusion.',
  recommendations: [{ code: 'NIGHT_ACTIVITY_IN_AREA', subject: 'Rear Perimeter', system_generated: true, basis: {},
                      observation: 'Rear Perimeter at North Yard generated 6 suspicious events during night patrols.',
                      suggestion: 'Review perimeter CCTV coverage there, or increase the approved patrol frequency.' }],
}

beforeEach(() => {
  asRole(2)
  vi.mocked(api.getEntitlement).mockResolvedValue({ licensed: true, reason: null, expires_at: null,
                                                     limits: { max_drones: null, max_missions: null, max_sites: null }, usage: {} })
  vi.mocked(api.getAnalyticsOverview).mockResolvedValue(OVERVIEW)
  vi.mocked(api.getRiskMap).mockResolvedValue(RISK)
  vi.mocked(api.getRecommendations).mockResolvedValue(RECS)
  vi.mocked(api.listZones).mockResolvedValue([])
})

describe('the charts', () => {
  it('draws equal values as equal bars, and labels only the largest', () => {
    render(<ColumnChart title="Events per day" unit="events"
                        data={[{ key: 'a', label: 'Mon', value: 6 }, { key: 'b', label: 'Tue', value: 3 },
                               { key: 'c', label: 'Wed', value: 6 }, { key: 'd', label: 'Thu', value: 0 }]} />)
    const bars = Array.from(document.querySelectorAll<HTMLElement>('.bar'))
    // The label must not take height from the bar it sits on: both sixes are full height.
    expect(bars.map((b) => getComputedStyle(b).height)).toEqual(['100%', '50%', '100%', '0%'])
    expect(screen.getAllByText('6')).toHaveLength(1)
    expect(screen.queryByText('3')).not.toBeInTheDocument()
    // Screen readers get the summary a sighted reader takes from the shape.
    expect(screen.getByRole('img', { name: 'Events per day: 15 events in total; highest 6 (Mon)' })).toBeInTheDocument()
  })

  it('ranks named things with the value at each tip', () => {
    render(<BarList unit="events" data={[{ key: 'i', name: 'Intrusion', value: 25, note: '8% false positive' },
                                         { key: 'l', name: 'Licence plate', value: 15 }]} />)
    const rows = screen.getAllByRole('listitem')
    expect(within(rows[0]).getByText('Intrusion')).toBeInTheDocument()
    expect(within(rows[0]).getByText('25')).toBeInTheDocument()
    expect(within(rows[0]).getByText('8% false positive')).toBeInTheDocument()
    expect(within(rows[1]).getByText('15')).toBeInTheDocument()
  })
})

describe('DroneAnalytics', () => {
  it('leads with the headline numbers, each saying what it is a share of', async () => {
    render(<DroneAnalytics />)
    expect(await screen.findByText('Mission success')).toBeInTheDocument()
    // The same rate also appears per mission and per drone below; this is the tile.
    expect(within(screen.getByText('Mission success').parentElement!).getByText('83%')).toBeInTheDocument()
    expect(screen.getByText('15 of 18 flights completed')).toBeInTheDocument()
    expect(screen.getByText('25%')).toBeInTheDocument()
    expect(screen.getByText('10 of 40 events')).toBeInTheDocument()
    expect(screen.getByText('10%')).toBeInTheDocument()
    expect(screen.getByText('4 of 40 events · 3 incident(s)')).toBeInTheDocument()
    expect(screen.getByText('3× blocked — Battery too low')).toBeInTheDocument()
    expect(screen.getByText('53% false positive')).toBeInTheDocument()
  })

  it('shows trends as separate charts rather than one with two scales', async () => {
    render(<DroneAnalytics />)
    for (const name of [/^Flights per day:/, /^Events per day: 36 events in total; highest 12/, /^Suspicious events per day:/,
                        /^Suspicious events by hour: 7 suspicious events in total; highest 7 \(23:00–23:59\)/,
                        /^Suspicious events by day of the week:/]) {
      expect(await screen.findByRole('img', { name })).toBeInTheDocument()
    }
  })

  it('labels the risk map as an analytical score, not a prediction', async () => {
    render(<DroneAnalytics />)
    expect(await screen.findByText(/It is not a prediction of where something will happen/)).toBeInTheDocument()
    const row = screen.getByText('Rear Perimeter', { selector: 'td' }).closest('tr')!
    // The level is written, not only coloured.
    expect(within(row).getByText('HIGH · 78')).toBeInTheDocument()
    expect(screen.getByText('LOW · 5')).toBeInTheDocument()
    expect(screen.getByText(/How the score is made: Each event adds its weight/)).toBeInTheDocument()
    expect(screen.getByText(/4 times on 3 day\(s\) near Rear Perimeter, North Yard — 4 at night/)).toBeInTheDocument()
    // No site chosen: the table still ranks every area; the map waits for one.
    expect(screen.getByText(/Choose a site above/)).toBeInTheDocument()
    expect(screen.queryByTestId('map')).not.toBeInTheDocument()
  })

  it('marks every recommendation system-generated, with what it saw and what it suggests', async () => {
    render(<DroneAnalytics />)
    expect(await screen.findByText(/Each is a prompt for review, not a conclusion/)).toBeInTheDocument()
    expect(screen.getByText('System-generated')).toBeInTheDocument()
    expect(screen.getByText('Rear Perimeter at North Yard generated 6 suspicious events during night patrols.')).toBeInTheDocument()
    expect(screen.getByText(/^Suggested: Review perimeter CCTV coverage there/)).toBeInTheDocument()
  })

  it('asks again when the period changes', async () => {
    render(<DroneAnalytics />)
    await screen.findByText('Mission success')
    const first = vi.mocked(api.getAnalyticsOverview).mock.calls[0][0]
    fireEvent.click(screen.getByRole('button', { name: 'Last 7 days' }))
    await waitFor(() => expect(vi.mocked(api.getAnalyticsOverview).mock.calls.length).toBeGreaterThan(1))
    const last = vi.mocked(api.getAnalyticsOverview).mock.calls.at(-1)![0]
    const span = (q: { from: string; to: string }) => Math.round((Date.parse(q.to) - Date.parse(q.from)) / 86_400_000) + 1
    expect(span(first)).toBe(30)
    expect(span(last)).toBe(7)
    expect(vi.mocked(api.getRiskMap)).toHaveBeenLastCalledWith(last)
  })

  it('says so when there is nothing to recommend', async () => {
    vi.mocked(api.getRecommendations).mockResolvedValue({ ...RECS, recommendations: [] })
    render(<DroneAnalytics />)
    expect(await screen.findByText('Nothing in this period crosses the thresholds for a suggestion.')).toBeInTheDocument()
  })
})

describe('the Analytics tab', () => {
  it('is offered to those who may read reports, and not to a guard', () => {
    asRole(6)   // viewer: drone:report:read
    const view = render(<DroneNav />)
    expect(screen.getByRole('tab', { name: 'Analytics' })).toBeInTheDocument()
    view.unmount()
    asRole(5)   // guard: drone:event:read only
    render(<DroneNav />)
    expect(screen.queryByRole('tab', { name: 'Analytics' })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Events' })).toBeInTheDocument()
  })
})

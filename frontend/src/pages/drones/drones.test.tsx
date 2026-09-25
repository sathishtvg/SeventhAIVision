import type { ReactNode } from 'react'
import { Route, Routes, MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/drones'
import type { DroneEvent as DroneEventRow, EventCard, EventDetail, Session } from '@/api/drones'
import { draftComplete, emptyDraft, routeLengthM } from '@/components/drones/geo'
import DroneEvents from './DroneEvents'
import DroneEvent from './DroneEvent'
import DronePatrols from './DronePatrols'
import DronePatrol from './DronePatrol'
import MissionDesigner from './MissionDesigner'

// Leaflet needs a real layout engine; the screens' map layers are not what is
// under test here, so the map renders its children into a plain div.
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: ReactNode }) => <div data-testid="map">{children}</div>,
  TileLayer: () => null, Marker: () => null, CircleMarker: () => null, Polyline: () => null,
  Polygon: () => null, Circle: () => null, Tooltip: () => null,
  useMap: () => ({ setView: () => {}, fitBounds: () => {}, getZoom: () => 16 }),
  useMapEvents: () => null,
}))
vi.mock('leaflet', () => ({ default: { divIcon: () => ({}), latLngBounds: () => ({}) } }))
vi.mock('@/components/common/HlsPlayer', () => ({ HlsPlayer: () => <div data-testid="hls" /> }))
vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([]) }))
vi.mock('@/api/cameras', () => ({ getStreams: vi.fn().mockResolvedValue([]) }))
vi.mock('@/api/drones', async (orig) => {
  const real = await orig<typeof import('@/api/drones')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError, blobApiError: real.blobApiError, mediaUrl: real.mediaUrl }
})

function asRole(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'u1', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

/** Render at a path, so useParams sees the id. */
function renderAt(path: string, pattern: string, el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <ThemeProvider theme={theme}><Routes><Route path={pattern} element={el} /></Routes></ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const EVENT: DroneEventRow = {
  id: 'e1', site_id: 's1', site_name: 'North Yard', session_id: 'p1', session_number: 'DP-20260925-AAAA',
  mission_name: 'Night round', drone_id: 'd1', drone_name: 'Hawk 1', drone_code: 'H1', module_type: 'intrusion',
  label: 'person', detected_at: '2026-09-25T14:00:00Z', last_detected_at: null, detection_count: 3,
  observed_seconds: 6, drone_latitude: 1.3, drone_longitude: 103.8, location_method: 'DRONE_POSITION',
  zone_name: 'Fuel store', zone_type: 'RESTRICTED', ai_confidence: 0.91, risk_score: 62, risk_level: 'HIGH',
  risk_factors: [{ factor: 'zone', points: 20, detail: 'Inside a restricted zone' }],
  verification_state: 'VERIFIED', status: 'NEW', source: 'CENTRAL', alert_id: null, incident_id: null,
  acknowledged_by_name: null, resolved_by_name: null, false_positive_reason: null, attributes: {},
}
const DETAIL: EventDetail = {
  ...EVENT, observations: [], cameras: [], incident: null, alert: null,
  media: [{ id: 'm1', media_kind: 'SNAPSHOT', storage_location: 'local', sync_state: 'pending',
            captured_at: '2026-09-25T14:00:01Z', size_bytes: null }],
}
const CARD = { event_id: 'e1', headline: 'Person in Fuel store', incident: null, actions: [] } as unknown as EventCard

const SESSION: Session = {
  id: 'p1', session_number: 'DP-20260925-AAAA', mission_id: 'mi1', mission_name: 'Night round', drone_id: 'd1',
  drone_name: 'Hawk 1', route_name: 'Perimeter', profile_name: null, site_id: 's1', site_name: 'North Yard',
  status: 'BLOCKED', triggered_by: 'MANUAL', scheduled_for: null, started_at: '2026-09-25T13:00:00Z',
  launched_at: null, ended_at: '2026-09-25T13:00:00Z', blocked_reason: 'Battery too low', failure_reason: null,
  abort_reason: null, distance_m: null, event_count: 0, incident_count: 0, last_waypoint_sequence: null,
  edge_gateway_id: null, created_at: '2026-09-25T13:00:00Z', waypoints: [],
}

beforeEach(() => {
  asRole(2)
  vi.mocked(api.getEntitlement).mockResolvedValue({ licensed: true, reason: null, expires_at: null,
                                                     limits: { max_drones: null, max_missions: null, max_sites: null }, usage: {} })
  vi.mocked(api.listEvents).mockResolvedValue({ items: [EVENT], total: 1, limit: 25, offset: 0, has_more: false })
  vi.mocked(api.getEvent).mockResolvedValue(DETAIL)
  vi.mocked(api.getEventCard).mockResolvedValue(CARD)
  vi.mocked(api.getEventCctv).mockResolvedValue({ event_id: 'e1', cameras: [], corroborating: [], settled: true,
    location: { latitude: 1.3, longitude: 103.8, method: 'DRONE_POSITION', note: 'No camera near.' },
    window: { start: '', end: '', pre_seconds: 30, post_seconds: 30 } })
  vi.mocked(api.listVerifications).mockResolvedValue([])
  vi.mocked(api.listSessions).mockResolvedValue({ items: [SESSION], total: 1, limit: 25, offset: 0, has_more: false })
  vi.mocked(api.listDrones).mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0, has_more: false })
  vi.mocked(api.getSession).mockResolvedValue(SESSION)
  vi.mocked(api.getTrack).mockResolvedValue({ total_samples: 0, points: [] })
  vi.mocked(api.listZones).mockResolvedValue([])
  vi.mocked(api.listProfiles).mockResolvedValue([])
})

describe('drone map helpers', () => {
  it('measures a route out and back to the launch point', () => {
    const base = { lat: 1.3, lng: 103.8 }
    const wp = { name: null, latitude: 1.301, longitude: 103.8, altitude_m: null, hover_seconds: 0,
                 observe_seconds: 0, snapshot_required: false }
    const oneWay = routeLengthM(base, [wp], false)
    expect(oneWay).toBeGreaterThan(100)
    expect(oneWay).toBeLessThan(120)
    expect(routeLengthM(base, [wp], true)).toBeCloseTo(oneWay * 2, 5)
  })

  it('knows when a drawn zone is complete', () => {
    expect(draftComplete(emptyDraft('POLYGON'))).toBe(false)
    expect(draftComplete({ ...emptyDraft('RECTANGLE'), points: [{ lat: 1, lng: 2 }, { lat: 1.1, lng: 2.1 }] })).toBe(true)
    expect(draftComplete({ ...emptyDraft('CIRCLE'), center: { lat: 1, lng: 2 }, radius_m: 0 })).toBe(false)
    expect(draftComplete({ ...emptyDraft('CIRCLE'), center: { lat: 1, lng: 2 }, radius_m: 40 })).toBe(true)
  })
})

describe('DroneEvents', () => {
  it('lists events with risk and AI confidence as two separate numbers', async () => {
    render(<DroneEvents />)
    expect(await screen.findByText('Intrusion')).toBeInTheDocument()
    expect(screen.getByText('HIGH · 62')).toBeInTheDocument()
    expect(screen.getByText('AI confidence 91%')).toBeInTheDocument()
    expect(screen.getByText('Fuel store (Restricted)')).toBeInTheDocument()
    expect(vi.mocked(api.listEvents)).toHaveBeenCalledWith(expect.objectContaining({ open_only: true }))
  })

  it('says so when nothing is open', async () => {
    vi.mocked(api.listEvents).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0, has_more: false })
    render(<DroneEvents />)
    expect(await screen.findByText('No open drone events.')).toBeInTheDocument()
  })
})

describe('DroneEvent', () => {
  it('offers the response actions an admin may take', async () => {
    renderAt('/drone-events/e1', '/drone-events/:id', <DroneEvent />)
    expect(await screen.findByText('Person in Fuel store')).toBeInTheDocument()
    for (const name of ['Acknowledge', 'Escalate', 'Open incident', 'Dispatch guard', 'Verify with drone', 'Resolve',
                        'False positive']) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
    }
    expect(screen.getByText('Inside a restricted zone')).toBeInTheDocument()
    expect(screen.getByText(/Held at the site/)).toBeInTheDocument()
  })

  it('offers a viewer no actions', async () => {
    asRole(6)
    renderAt('/drone-events/e1', '/drone-events/:id', <DroneEvent />)
    expect(await screen.findByText('Person in Fuel store')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Dispatch guard' })).not.toBeInTheDocument()
  })

  it('will not mark a false positive without a reason', async () => {
    vi.mocked(api.decideEvent).mockResolvedValue({})
    renderAt('/drone-events/e1', '/drone-events/:id', <DroneEvent />)
    fireEvent.click(await screen.findByRole('button', { name: 'False positive' }))
    const confirm = screen.getByRole('button', { name: 'Mark as a false positive' })
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Why is it a false positive/), { target: { value: 'A guard on rounds' } })
    expect(confirm).toBeEnabled()
    fireEvent.click(confirm)
    await waitFor(() => expect(vi.mocked(api.decideEvent)).toHaveBeenCalledWith('e1', 'false-positive',
                                                                                  { reason: 'A guard on rounds' }))
  })
})

describe('DronePatrols', () => {
  it('lists flights, with the export for those allowed it', async () => {
    render(<DronePatrols />)
    expect(await screen.findByText('DP-20260925-AAAA')).toBeInTheDocument()
    expect(screen.getByText('Battery too low')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeInTheDocument()
  })

  it('hides the export from a guard', async () => {
    asRole(5)
    render(<DronePatrols />)
    expect(await screen.findByText('DP-20260925-AAAA')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Export CSV' })).not.toBeInTheDocument()
  })
})

describe('DronePatrol', () => {
  it('shows a flight that never launched as a replay, with why', async () => {
    renderAt('/drone-patrols/p1', '/drone-patrols/:id', <DronePatrol />)
    expect(await screen.findByText('Blocked: Battery too low')).toBeInTheDocument()
    expect(screen.getByText('Replay')).toBeInTheDocument()
    expect(screen.getByText(/recorded no track — it never launched/)).toBeInTheDocument()
    expect(screen.queryByText('LIVE')).not.toBeInTheDocument()
  })

  it('shows live controls while it flies', async () => {
    vi.mocked(api.getSession).mockResolvedValue({ ...SESSION, status: 'ACTIVE', blocked_reason: null, ended_at: null })
    vi.mocked(api.listCommands).mockResolvedValue([])
    vi.mocked(api.getDrone).mockResolvedValue({ camera_id: null } as never)
    renderAt('/drone-patrols/p1', '/drone-patrols/:id', <DronePatrol />)
    expect(await screen.findByText('LIVE')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Abort' })).toBeInTheDocument()
    expect(await screen.findByText(/no camera linked/)).toBeInTheDocument()
  })
})

describe('MissionDesigner', () => {
  it('asks for a site before the route can be drawn, and cannot save without one', async () => {
    renderAt('/drone-missions/new', '/drone-missions/:id', <MissionDesigner />)
    expect(await screen.findByText('Choose a site to draw the route on its map.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})

/**
 * Privacy areas for drones (4 November 2026; Phase 5 of the known limits): the tab they are drawn on, a picture
 * withheld at one, and a route that crosses one.
 */
import type { ReactNode } from 'react'
import { Route, Routes, MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/drones'
import * as privacy from '@/api/dronePrivacy'
import type { PrivacyArea, PrivacyAreaList } from '@/api/dronePrivacy'
import type { EventDetail, Mission, Route as DroneRoute } from '@/api/drones'
import { PrivacyAreasTab } from '@/components/drones/PrivacyAreas'
import DroneEvent from './DroneEvent'
import MissionDesigner from './MissionDesigner'

// Leaflet needs a real layout engine; the map renders its children into a plain div.
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
vi.mock('@/api/sites', () => ({
  getSites: vi.fn().mockResolvedValue([
    { id: 's1', name: 'North Yard', latitude: 1.3, longitude: 103.8 },
    { id: 's2', name: 'Annexe', latitude: 1.35, longitude: 103.85 },
  ]),
}))
vi.mock('@/api/cameras', () => ({ getStreams: vi.fn().mockResolvedValue([]) }))
vi.mock('@/api/dronePrivacy', () => ({
  listPrivacyAreas: vi.fn(), drawPrivacyArea: vi.fn(), removePrivacyArea: vi.fn(),
}))
vi.mock('@/api/drones', async (orig) => {
  const real = await orig<typeof import('@/api/drones')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError, blobApiError: real.blobApiError, mediaUrl: real.mediaUrl }
})

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

const AREA: PrivacyArea = {
  id: 'a1', site_id: 's1', site_name: 'North Yard', name: "Neighbour's yard", shape: 'CIRCLE', polygon: null,
  center_latitude: 1.3005, center_longitude: 103.8005, radius_m: 30, margin_m: 25,
  created_at: '2026-11-04T02:00:00Z', created_by_name: 'Admin',
}
const OTHER_SITE: PrivacyArea = { ...AREA, id: 'a2', site_id: 's2', site_name: 'Annexe', name: 'School field' }
const LIST: PrivacyAreaList = {
  areas: [AREA, OTHER_SITE], margin_default_m: 25, margin_most_m: 200, applies_within_seconds: 2,
  note: 'While a drone is in an area or within its margin its whole picture is withheld.',
}

async function chooseSite(name: string) {
  fireEvent.mouseDown(await screen.findByRole('combobox', { name: 'Site' }))
  fireEvent.click(await screen.findByRole('option', { name }))
}

beforeEach(() => {
  asRole(2)
  vi.mocked(privacy.listPrivacyAreas).mockResolvedValue(LIST)
  vi.mocked(privacy.removePrivacyArea).mockResolvedValue({ removed: true })
})

describe('Privacy areas tab', () => {
  it("lists the chosen site's areas only, and takes one away", async () => {
    renderAt('/drone-zones', '/drone-zones', <PrivacyAreasTab />)
    expect(await screen.findByText('Choose a site to see its privacy areas.')).toBeInTheDocument()
    expect(screen.getByText(LIST.note)).toBeInTheDocument()
    await chooseSite('North Yard')
    const rows = await screen.findAllByTestId('privacy-area-row')
    expect(rows).toHaveLength(1)
    expect(within(rows[0]).getByText("Neighbour's yard")).toBeInTheDocument()
    expect(within(rows[0]).getByText('circle, and 25 m around it')).toBeInTheDocument()
    expect(screen.queryByText('School field')).not.toBeInTheDocument()
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Take away' }))
    await waitFor(() => expect(privacy.removePrivacyArea).toHaveBeenCalledWith('a1'))
  })

  it('will not draw an area until it has a name and a whole shape', async () => {
    renderAt('/drone-zones', '/drone-zones', <PrivacyAreasTab />)
    await chooseSite('North Yard')
    fireEvent.click(await screen.findByRole('button', { name: 'Draw an area' }))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Pool' } })
    expect(screen.getByRole('button', { name: 'Draw it' })).toBeDisabled()
    expect(privacy.drawPrivacyArea).not.toHaveBeenCalled()
  })

  it('offers somebody who may not manage privacy neither drawing nor taking away', async () => {
    asRole(6)
    renderAt('/drone-zones', '/drone-zones', <PrivacyAreasTab />)
    await chooseSite('North Yard')
    expect(await screen.findAllByTestId('privacy-area-row')).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Take away' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Draw an area' })).not.toBeInTheDocument()
    expect(screen.getByText("Whoever may draw a camera's privacy zones draws these.")).toBeInTheDocument()
  })

  it('says so when the areas cannot be loaded, and can try again', async () => {
    vi.mocked(privacy.listPrivacyAreas).mockRejectedValue(new Error('offline'))
    renderAt('/drone-zones', '/drone-zones', <PrivacyAreasTab />)
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.queryByTestId('privacy-areas')).not.toBeInTheDocument()
  })
})

describe('A picture withheld at a privacy area', () => {
  it('says why a snapshot is blank and that a clip was not kept', async () => {
    const detail = {
      id: 'e1', site_id: 's1', site_name: 'North Yard', session_id: 'p1', session_number: 'DP-1', mission_name: 'Night',
      drone_id: 'd1', drone_name: 'Hawk 1', drone_code: 'H1', module_type: 'intrusion', label: 'person',
      detected_at: '2026-11-04T14:00:00Z', last_detected_at: null, detection_count: 1, observed_seconds: 2,
      drone_latitude: 1.3, drone_longitude: 103.8, location_method: 'DRONE_POSITION', zone_name: null, zone_type: null,
      ai_confidence: 0.8, risk_score: 40, risk_level: 'MEDIUM', risk_factors: [], verification_state: 'UNVERIFIED',
      status: 'NEW', source: 'CENTRAL', alert_id: null, incident_id: null, acknowledged_by_name: null,
      resolved_by_name: null, false_positive_reason: null, attributes: {}, observations: [], cameras: [],
      incident: null, alert: null,
      media: [{ id: 'm1', media_kind: 'SNAPSHOT', storage_location: 'local', sync_state: 'pending',
                captured_at: '2026-11-04T14:00:01Z', size_bytes: null,
                withheld_reason: "Taken over the privacy area Neighbour's yard: stored blank." }],
      withheld: [{ id: 'w1', media_kind: 'CLIP', action: 'NOT_KEPT', captured_at: null,
                   reason: "Recorded over the privacy area Neighbour's yard: not kept." }],
    } as unknown as EventDetail
    vi.mocked(api.getEvent).mockResolvedValue(detail)
    vi.mocked(api.getEventCard).mockResolvedValue({ event_id: 'e1', headline: 'Person seen', incident: null,
                                                    actions: [] } as never)
    vi.mocked(api.getEventCctv).mockResolvedValue({ event_id: 'e1', cameras: [], corroborating: [], settled: true,
      location: { latitude: 1.3, longitude: 103.8, method: 'DRONE_POSITION', note: '' },
      window: { start: '', end: '', pre_seconds: 30, post_seconds: 30 } })
    vi.mocked(api.listVerifications).mockResolvedValue([])
    renderAt('/drone-events/e1', '/drone-events/:id', <DroneEvent />)
    expect(await screen.findByTestId('media-withheld')).toHaveTextContent('stored blank')
    expect(screen.getByTestId('clip-withheld')).toHaveTextContent('Clip')
    expect(screen.getByTestId('clip-withheld')).toHaveTextContent('not kept')
  })
})

describe('A route that crosses a privacy area', () => {
  it('is told which, and can still be saved', async () => {
    const mission = {
      id: 'mi1', site_id: 's1', site_name: 'North Yard', name: 'Night round', description: null, drone_id: null,
      drone_name: null, drone_status: null, route_id: 'r1', route_name: 'Perimeter', security_profile_id: null,
      profile_name: null, recording_sync_mode: null, priority: 3, min_battery_pct: 30, max_duration_minutes: null,
      enabled: true, schedule_count: 0, last_session_status: null, last_session_at: null, in_flight: false,
      next_run: null, schedules: [], recent_sessions: [],
    } as Mission
    const route = {
      id: 'r1', site_id: 's1', name: 'Perimeter', description: null, base_latitude: 1.3, base_longitude: 103.8,
      default_altitude_m: 40, default_speed_mps: 5, return_to_base: true, is_active: true, waypoints: [],
      summary: { waypoint_count: 0, length_m: 0, outside_site_geofence: [],
                 privacy_crossings: [{ area_id: 'a1', name: "Neighbour's yard", metres: 60, seconds: 12 }],
                 privacy_warning: "Crosses the privacy area Neighbour's yard: the picture is blank for about 12 s." },
    } as unknown as DroneRoute
    vi.mocked(api.getMission).mockResolvedValue(mission)
    vi.mocked(api.getRoute).mockResolvedValue(route)
    vi.mocked(api.getPreflight).mockResolvedValue({ passed: false, checks: [] } as never)
    vi.mocked(api.listDrones).mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0, has_more: false })
    vi.mocked(api.listZones).mockResolvedValue([])
    vi.mocked(api.listProfiles).mockResolvedValue([])
    renderAt('/drone-missions/mi1', '/drone-missions/:id', <MissionDesigner />)
    expect(await screen.findByTestId('privacy-warning')).toHaveTextContent('Crosses 1 privacy area')
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
  })
})

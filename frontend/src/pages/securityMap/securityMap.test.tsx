import type { ReactNode } from 'react'
import { Route, Routes, MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/siteMap'
import type { Around, Feature, Features, Layers, NearGuard } from '@/api/siteMap'
import { about, ago, colourOf, distance, gathered, sizeOf } from '@/components/securityMap/mapFormat'
import SecurityMap from './SecurityMap'
import SitePlaces from './SitePlaces'

// The map itself is Leaflet's; what is put on it is what is tested here.
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: ReactNode }) => <div data-testid="map">{children}</div>,
  TileLayer: () => null,
  CircleMarker: ({ children, eventHandlers, pathOptions }: {
    children?: ReactNode; eventHandlers?: { click?: () => void }; pathOptions?: { dashArray?: string } }) => (
    <button type="button" data-testid="mark" data-stale={pathOptions?.dashArray ? 'yes' : 'no'}
            onClick={() => eventHandlers?.click?.()}>{children}</button>),
  Polygon: ({ children }: { children?: ReactNode }) => <div data-testid="area">{children}</div>,
  Polyline: () => <div data-testid="route" />,
  Tooltip: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  useMap: () => ({ setView: () => {}, fitBounds: () => {} }),
}))
vi.mock('leaflet', () => ({ default: { latLngBounds: () => ({}) } }))
vi.mock('leaflet/dist/leaflet.css', () => ({}))
vi.mock('@/components/common/LocationPickerMap', () => ({
  LocationPickerMap: ({ onChange, onPolygonChange }: {
    onChange: (lat: number, lng: number) => void; onPolygonChange?: (p: { lat: number; lng: number }[]) => void }) => (
    <div data-testid="picker">
      <button type="button" onClick={() => onChange(1.3002, 103.8002)}>put it here</button>
      <button type="button" onClick={() => onPolygonChange?.([{ lat: 1.3, lng: 103.8 }, { lat: 1.3, lng: 103.801 },
                                                              { lat: 1.301, lng: 103.801 }])}>outline it</button>
    </div>),
}))
vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([
  { id: 's1', name: 'Factory A', latitude: 1.3, longitude: 103.8 }, { id: 's2', name: 'Factory B', latitude: null, longitude: null }]) }))
vi.mock('@/api/access', () => ({ listDoors: vi.fn().mockResolvedValue([{ id: 'd1', name: 'Server room' }]) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/siteMap', async (orig) => {
  const real = await orig<typeof import('@/api/siteMap')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

const ADMIN = 2, OPERATOR = 4

function asRole(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'u1', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

function Where() {
  const { pathname } = useLocation()
  return <div data-testid="went-to">{pathname}</div>
}

function renderAt(path: string, pattern: string, el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <ThemeProvider theme={theme}>
          <Routes><Route path={pattern} element={el} /><Route path="*" element={<Where />} /></Routes>
        </ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const feature = (over: Partial<Feature>): Feature => ({
  layer: 'CAMERA', id: 'c1', label: 'North Gate', latitude: 1.3, longitude: 103.8, state: 'online', at: null,
  site_id: 's1', site_name: 'Factory A', outline: null, detail: {}, ...over,
})
const SITE = feature({ layer: 'SITE', id: 's1', label: 'Factory A', state: 'attention',
                       detail: { cameras: 3, cameras_online: 2, cameras_offline: 1 } })
const CAMERA = feature({})
const OFFLINE = feature({ id: 'c2', label: 'Yard', latitude: 1.3018, state: 'offline' })
const GUARD = feature({ layer: 'GUARD', id: 'g1', label: 'Tan Wei Ming', latitude: 1.3009, state: 'available',
                        at: '2026-10-07T01:00:00Z',
                        detail: { position_source: 'checkpoint scan', position_age_s: 600, stale: false } })
const STALE = feature({ layer: 'GUARD', id: 'g2', label: 'Raj Kumar', latitude: 1.318, state: 'busy',
                        detail: { position_source: 'shift check-in', position_age_s: 10800, stale: true } })
const INCIDENT = feature({ layer: 'INCIDENT', id: 'i1', label: 'Forced gate', state: 'high', at: '2026-10-07T00:40:00Z',
                           detail: { status: 'open', position_from: 'its camera' } })
const SECOND = feature({ ...INCIDENT, id: 'i2', label: 'Tailgating at the gate', state: 'medium' })
const SITUATION = feature({ layer: 'SITUATION', id: 'sit1', label: 'Activity at the gate', state: 'high',
                            detail: { risk_level: 'HIGH', stands: 'AWAITING' } })
const BUILDING = feature({ layer: 'PLACE', id: 'pl1', label: 'Warehouse 1', state: 'building', latitude: 1.3005,
                           outline: [[1.3, 103.8], [1.3, 103.801], [1.301, 103.801]],
                           detail: { kind: 'BUILDING', has_point: false, is_active: true, parent_id: null, level: null,
                                     door_id: null, description: 'Cold store.' } })
const GATE = feature({ layer: 'PLACE', id: 'pl2', label: 'North Gate door', state: 'alert', at: '2026-10-07T01:07:00Z',
                       detail: { kind: 'ACCESS_POINT', has_point: true, is_active: true, parent_id: 'pl1',
                                 parent_name: 'Warehouse 1', level: 0, door_id: 'd1', last_door_event: 'forced',
                                 description: null } })
const CHECKPOINTS = [
  feature({ layer: 'CHECKPOINT', id: 'cp2', label: 'Back fence', latitude: 1.3009, state: 'scanned',
            detail: { route_id: 'r1', route_name: 'Perimeter', sequence: 2 } }),
  feature({ layer: 'CHECKPOINT', id: 'cp1', label: 'Front', state: 'never scanned',
            detail: { route_id: 'r1', route_name: 'Perimeter', sequence: 1 } }),
]

const layer = (key: api.LayerKey, label: string, may = true): Layers['layers'][number] =>
  ({ key, label, permission: 'x', may_see: may, live: ['INCIDENT', 'ALERT', 'SITUATION'].includes(key) })
const LAYERS: Layers = {
  layers: [layer('SITE', 'Sites'), layer('CAMERA', 'Cameras'), layer('GUARD', 'Guards on shift'),
           layer('INCIDENT', 'Open incidents'), layer('ALERT', 'Live alerts'), layer('SITUATION', 'Open situations'),
           layer('DRONE', 'Drones', false), layer('CHECKPOINT', 'Patrol checkpoints'), layer('PLACE', 'Places'),
           layer('DRONE_ZONE', 'Drone zones', false)],
  place_kinds: ['BUILDING', 'FLOOR', 'GATE', 'ACCESS_POINT', 'EMERGENCY_POINT', 'ASSEMBLY_POINT', 'ZONE', 'PARKING', 'OTHER'],
  can_manage: false, default_hours: 24, max_hours: 168, default_radius_m: 300, max_radius_m: 5000, stale_after_s: 3600,
  note: 'A guard’s position is where they last clocked in, scanned a checkpoint or reported from — not where they are now.',
}
const FEATURES: Features = {
  as_of: '2026-10-07T01:10:00Z', hours: 24,
  layers: { SITE: [SITE], CAMERA: [CAMERA, OFFLINE], GUARD: [GUARD, STALE], INCIDENT: [INCIDENT, SECOND], ALERT: [],
            SITUATION: [SITUATION], CHECKPOINT: CHECKPOINTS, PLACE: [BUILDING, GATE] },
  without_position: { CAMERA: 1, INCIDENT: 2, ALERT: 4 }, more: {},
  not_shown: [{ layer: 'DRONE', label: 'Drones', reason: 'You do not hold the permission drone:read.' }],
  note: LAYERS.note,
}
const guard = (over: Partial<NearGuard>): NearGuard => ({
  user_id: 'g1', full_name: 'Tan Wei Ming', site_name: 'Factory A', latitude: 1.3009, longitude: 103.8,
  position_source: 'checkpoint scan', position_at: '2026-10-07T01:00:00Z', position_age_s: 600, stale: false,
  available: true, busy_incident_id: null, emergency_id: null, distance_m: 100, ...over,
})
const AROUND: Around = {
  subject: INCIDENT, located: true, radius_m: 300,
  nearby: { CAMERA: [{ ...CAMERA, distance_m: 0 }, { ...OFFLINE, distance_m: 200 }], PLACE: [{ ...GATE, distance_m: 33 }],
            GUARD: [guard({})] },
  nearest_guards: [guard({}), guard({ user_id: 'g2', full_name: 'Raj Kumar', position_source: 'shift check-in',
                                      position_age_s: 10800, stale: true, available: false, busy_incident_id: 'i9',
                                      distance_m: 2001 })],
  not_shown: [{ layer: 'DRONE', label: 'Drones', reason: 'You do not hold the permission drone:read.' }],
  note: LAYERS.note,
}

beforeEach(() => {
  vi.clearAllMocks()
  asRole(OPERATOR)
  vi.mocked(api.getLayers).mockResolvedValue(LAYERS)
  vi.mocked(api.getFeatures).mockResolvedValue(FEATURES)
  vi.mocked(api.getAround).mockResolvedValue(AROUND)
  vi.mocked(api.listPlaces).mockResolvedValue({ items: [BUILDING, GATE], kinds: LAYERS.place_kinds, can_manage: true })
  vi.mocked(api.drawPlace).mockResolvedValue(GATE)
  vi.mocked(api.changePlace).mockResolvedValue(GATE)
  for (const fn of [api.retirePlace, api.restorePlace]) vi.mocked(fn).mockResolvedValue({})
})

const map = () => renderAt('/security-map', '/security-map', <SecurityMap />)
const marks = () => screen.getAllByTestId('mark').map((m) => m.textContent ?? '')

describe('The security map', () => {
  it('draws the layers that are on, and a layer is turned on and off by the person', async () => {
    map()
    await screen.findAllByTestId('mark')
    expect(api.getFeatures).toHaveBeenCalledWith({ site_id: undefined, hours: 24 })
    const toggles = screen.getByTestId('layer-toggles')
    expect(within(toggles).getByText('Cameras 2')).toBeInTheDocument()
    expect(within(toggles).getByText('Guards on shift 2')).toBeInTheDocument()
    expect(within(toggles).queryByText(/Drones/)).not.toBeInTheDocument()
    expect(marks().some((m) => m.includes('North Gate'))).toBe(true)
    expect(marks().some((m) => m.includes('Back fence'))).toBe(false)
    expect(screen.queryByTestId('route')).not.toBeInTheDocument()
    fireEvent.click(within(toggles).getByText('Patrol checkpoints 2'))
    expect(marks().some((m) => m.includes('Back fence'))).toBe(true)
    expect(screen.getByTestId('route')).toBeInTheDocument()
    fireEvent.click(within(toggles).getByText('Cameras 2'))
    expect(marks().some((m) => m.includes('Stream offline'))).toBe(false)
  })

  it('a guard is drawn where they last recorded being, and says how long ago', async () => {
    map()
    const all = await screen.findAllByTestId('mark')
    const fresh = all.find((m) => m.textContent?.includes('Tan Wei Ming'))!
    const old = all.find((m) => m.textContent?.includes('Raj Kumar'))!
    expect(fresh).toHaveTextContent('Tan Wei Ming · 10 min ago')
    expect(fresh).toHaveAttribute('data-stale', 'no')
    expect(old).toHaveTextContent('Raj Kumar · 3 h ago')
    expect(old).toHaveAttribute('data-stale', 'yes')
    expect(screen.getByTestId('legend')).toHaveTextContent('a guard’s position over an hour old')
  })

  it('things at one spot are one mark with a count, and an area is drawn as its outline', async () => {
    map()
    await screen.findAllByTestId('mark')
    expect(marks().filter((m) => m.includes('Forced gate') || m.includes('Tailgating'))).toEqual(['2 · Forced gate'])
    const areas = screen.getAllByTestId('area')
    expect(areas).toHaveLength(1)
    expect(areas[0]).toHaveTextContent('Warehouse 1 — Building')
    // The building is its outline and has no mark of its own; the door in it does.
    expect(marks().some((m) => m.startsWith('Warehouse 1'))).toBe(false)
    expect(marks().some((m) => m.includes('North Gate door') && m.includes('door last forced'))).toBe(true)
    expect(marks()).toContain('Factory A')
  })

  it('says what is not on the map and why, so that an empty patch is not read as a quiet one', async () => {
    map()
    const notes = await screen.findByTestId('map-notes')
    expect(notes).toHaveTextContent('Not on the map for want of a position: 1 cameras, 2 open incidents.')
    expect(notes).not.toHaveTextContent('4 live alerts')
    expect(notes).toHaveTextContent('An empty patch of map is not necessarily a quiet one.')
    expect(notes).toHaveTextContent('Drones are not shown: You do not hold the permission drone:read.')
    expect(notes).toHaveTextContent('not where they are now')
    fireEvent.click(within(screen.getByTestId('layer-toggles')).getByText('Live alerts 0'))
    expect(screen.getByTestId('map-notes')).toHaveTextContent('4 live alerts')
  })

  it('choosing a site or a period asks again for that', async () => {
    map()
    await screen.findAllByTestId('mark')
    fireEvent.mouseDown(screen.getByLabelText('Site'))
    fireEvent.click(await screen.findByRole('option', { name: 'Factory A' }))
    await waitFor(() => expect(api.getFeatures).toHaveBeenLastCalledWith({ site_id: 's1', hours: 24 }))
    fireEvent.mouseDown(screen.getByLabelText('Incidents, alerts and situations from'))
    fireEvent.click(await screen.findByRole('option', { name: 'The last 7 days' }))
    await waitFor(() => expect(api.getFeatures).toHaveBeenLastCalledWith({ site_id: 's1', hours: 168 }))
  })

  it('selecting an incident lists what is near it, nearest guard first, and sends nobody', async () => {
    map()
    const all = await screen.findAllByTestId('mark')
    expect(screen.queryByTestId('around')).not.toBeInTheDocument()
    fireEvent.click(all.find((m) => m.textContent?.includes('Forced gate'))!)
    const panel = await screen.findByTestId('around')
    await waitFor(() => expect(api.getAround).toHaveBeenCalledWith('INCIDENT', 'i1'))
    const guards = await within(panel).findAllByTestId('near-guard')
    expect(guards).toHaveLength(2)
    expect(guards[0]).toHaveTextContent('Tan Wei Ming')
    expect(guards[0]).toHaveTextContent('Free')
    expect(guards[0]).toHaveTextContent('100 m')
    expect(guards[0]).toHaveTextContent('Last recorded at a checkpoint scan, 10 min ago')
    expect(guards[1]).toHaveTextContent('Already sent somewhere')
    expect(guards[1]).toHaveTextContent('2.0 km')
    expect(guards[1]).toHaveTextContent('too long ago to say where they are now')
    const things = within(panel).getAllByTestId('near-thing')
    expect(things.map((t) => t.textContent)).toEqual([
      expect.stringContaining('North Gate'), expect.stringContaining('Yard'), expect.stringContaining('North Gate door')])
    expect(things[1]).toHaveTextContent('200 m')
    expect(panel).toHaveTextContent('Cameras within 300 m')
    expect(panel).toHaveTextContent('Drones are not shown: You do not hold the permission drone:read.')
    expect(panel).toHaveTextContent('This list sends nobody anywhere.')
    expect(within(panel).queryByRole('button', { name: /dispatch|send/i })).not.toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('button', { name: 'Close' }))
    expect(screen.queryByTestId('around')).not.toBeInTheDocument()
  })

  it('something with no position says nothing can be said to be near it', async () => {
    vi.mocked(api.getAround).mockResolvedValue({
      ...AROUND, located: false, nearby: {}, nearest_guards: [guard({ distance_m: null })] })
    map()
    fireEvent.click((await screen.findAllByTestId('mark')).find((m) => m.textContent?.includes('Activity at the gate'))!)
    const panel = await screen.findByTestId('around')
    await waitFor(() => expect(api.getAround).toHaveBeenCalledWith('SITUATION', 'sit1'))
    expect(await within(panel).findByText(/has no position recorded, so nothing can be said to be near it/)).toBeInTheDocument()
    expect(within(panel).getByTestId('near-guard')).toHaveTextContent('distance not known')
    expect(within(panel).queryByTestId('near-thing')).not.toBeInTheDocument()
  })

  it('a camera or a guard is not something to select', async () => {
    map()
    fireEvent.click((await screen.findAllByTestId('mark')).find((m) => m.textContent?.includes('Tan Wei Ming'))!)
    expect(screen.queryByTestId('around')).not.toBeInTheDocument()
    expect(api.getAround).not.toHaveBeenCalled()
  })

  it('only somebody who may draw places is offered to', async () => {
    const first = map()
    await screen.findAllByTestId('mark')
    expect(screen.queryByRole('button', { name: 'Draw places' })).not.toBeInTheDocument()
    first.unmount()
    vi.mocked(api.getLayers).mockResolvedValue({ ...LAYERS, can_manage: true })
    map()
    fireEvent.click(await screen.findByRole('button', { name: 'Draw places' }))
    expect(await screen.findByTestId('went-to')).toHaveTextContent('/site-places')
  })
})

describe('The places of a site', () => {
  const places = () => renderAt('/site-places', '/site-places', <SitePlaces />)

  async function choose() {
    fireEvent.mouseDown(screen.getByLabelText('Site'))
    fireEvent.click(await screen.findByRole('option', { name: 'Factory A' }))
    return screen.findAllByTestId('place-row')
  }

  it('lists a site’s places once a site is chosen, and says how each is drawn', async () => {
    asRole(ADMIN)
    places()
    expect(screen.getByText('Choose a site to see or draw its places.')).toBeInTheDocument()
    expect(api.listPlaces).not.toHaveBeenCalled()
    const rows = await choose()
    expect(api.listPlaces).toHaveBeenCalledWith({ site_id: 's1', include_retired: false })
    expect(rows[0]).toHaveTextContent('Warehouse 1')
    expect(rows[0]).toHaveTextContent('An outline of 3 points')
    expect(rows[0]).toHaveTextContent('Cold store.')
    expect(rows[1]).toHaveTextContent('Access point · in Warehouse 1 · level 0 · door last forced')
    expect(rows[1]).toHaveTextContent('A point')
  })

  it('a place is drawn as a point or an outline, and names a door when it is one', async () => {
    asRole(ADMIN)
    places()
    await choose()
    fireEvent.click(screen.getByRole('button', { name: 'Draw a place' }))
    const dialog = await screen.findByRole('dialog')
    const draw = within(dialog).getByRole('button', { name: 'Draw it' })
    expect(draw).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: ' South Gate ' } })
    expect(draw).toBeEnabled()
    fireEvent.mouseDown(within(dialog).getByLabelText('The door it is (optional)'))
    fireEvent.click(await screen.findByRole('option', { name: 'Server room' }))
    fireEvent.click(within(dialog).getByText('put it here'))
    fireEvent.click(draw)
    await waitFor(() => expect(api.drawPlace).toHaveBeenCalledWith({
      site_id: 's1', kind: 'GATE', name: 'South Gate', parent_id: null, level: null, latitude: 1.3002,
      longitude: 103.8002, polygon: null, door_id: 'd1', description: null }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('a floor is a level of a building and needs no position of its own', async () => {
    asRole(ADMIN)
    places()
    await choose()
    fireEvent.click(screen.getByRole('button', { name: 'Draw a place' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.mouseDown(within(dialog).getByLabelText('What it is'))
    fireEvent.click(await screen.findByRole('option', { name: 'Floor' }))
    expect(within(dialog).queryByLabelText('The door it is (optional)')).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Level 2' } })
    fireEvent.change(within(dialog).getByLabelText('Level'), { target: { value: '2' } })
    fireEvent.mouseDown(within(dialog).getByLabelText('Part of (a building)'))
    fireEvent.click(await screen.findByRole('option', { name: 'Warehouse 1' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Draw it' }))
    await waitFor(() => expect(api.drawPlace).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'FLOOR', name: 'Level 2', parent_id: 'pl1', level: 2, latitude: null, longitude: null, polygon: null,
      door_id: null })))
  })

  it('a place is changed without changing what it is, and the server’s refusal is shown', async () => {
    asRole(ADMIN)
    vi.mocked(api.changePlace).mockRejectedValueOnce({ response: { status: 409, data: {
      detail: 'This site already has an active place of that kind with that name.' } } })
    places()
    const rows = await choose()
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Change' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText('What it is')).toHaveAttribute('aria-disabled', 'true')
    expect(within(dialog).getByLabelText('Name')).toHaveValue('Warehouse 1')
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Warehouse One' } })
    fireEvent.click(within(dialog).getByText('outline it'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(await within(dialog).findByText(/already has an active place of that kind/)).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.changePlace).toHaveBeenLastCalledWith('pl1', {
      name: 'Warehouse One', parent_id: null, level: null, latitude: null, longitude: null,
      polygon: [[1.3, 103.8], [1.3, 103.801], [1.301, 103.801]], door_id: null, description: 'Cold store.' }))
  })

  it('a place is retired and restored, never removed', async () => {
    asRole(ADMIN)
    vi.mocked(api.listPlaces).mockResolvedValue({
      items: [BUILDING, { ...GATE, detail: { ...GATE.detail, is_active: false } }], kinds: LAYERS.place_kinds, can_manage: true })
    places()
    const rows = await choose()
    expect(rows[1]).toHaveTextContent('Retired')
    expect(within(rows[1]).queryByRole('button', { name: 'Change' })).not.toBeInTheDocument()
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Retire' }))
    await waitFor(() => expect(api.retirePlace).toHaveBeenCalledWith('pl1'))
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Restore' }))
    await waitFor(() => expect(api.restorePlace).toHaveBeenCalledWith('pl2'))
    expect(screen.queryByRole('button', { name: /delete|remove/i })).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Show retired places'))
    await waitFor(() => expect(api.listPlaces).toHaveBeenLastCalledWith({ site_id: 's1', include_retired: true }))
  })

  it('somebody who may only read sees the places and nothing that changes them', async () => {
    vi.mocked(api.listPlaces).mockResolvedValue({ items: [BUILDING], kinds: LAYERS.place_kinds, can_manage: false })
    places()
    await choose()
    for (const name of ['Draw a place', 'Change', 'Retire']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    expect(screen.queryByLabelText('Show retired places')).not.toBeInTheDocument()
  })

  it('says a site with no places works all the same', async () => {
    vi.mocked(api.listPlaces).mockResolvedValue({ items: [], kinds: LAYERS.place_kinds, can_manage: true })
    places()
    fireEvent.mouseDown(screen.getByLabelText('Site'))
    fireEvent.click(await screen.findByRole('option', { name: 'Factory A' }))
    expect(await screen.findByText(/No places have been drawn for this site. The security map shows its cameras/))
      .toBeInTheDocument()
  })
})

describe('the words and colours the map screens share', () => {
  it('how long ago, and how far', () => {
    expect([ago(null), ago(30), ago(600), ago(5400), ago(10800), ago(3 * 86400)]).toEqual(
      ['no position recorded', 'just now', '10 min ago', '2 h ago', '3 h ago', '3 days ago'])
    expect([distance(null), distance(0), distance(250), distance(2001)]).toEqual(
      ['distance not known', '0 m', '250 m', '2.0 km'])
  })

  it('a thing is coloured by the state it is in, else by what it is', () => {
    expect(colourOf(OFFLINE)).toBe(colourOf(feature({ layer: 'INCIDENT', state: 'critical' })))
    expect(colourOf(GUARD)).toBe(colourOf(CAMERA))
    expect(colourOf(feature({ layer: 'PLACE', state: 'building' }))).not.toBe(colourOf(CAMERA))
    expect(colourOf(feature({ layer: 'CAMERA', state: null }))).toBe(colourOf(feature({ layer: 'CAMERA', state: 'unknown' })))
    expect(sizeOf('INCIDENT')).toBeGreaterThan(sizeOf('CAMERA'))
  })

  it('what is said under a thing’s name', () => {
    expect(about(SITE)).toBe('2 of 3 cameras online')
    expect(about(INCIDENT)).toBe('High · Open · at its camera')
    expect(about(SITUATION)).toBe('Risk High · Awaiting')
    expect(about(CHECKPOINTS[1])).toBe('Perimeter · never scanned')
    expect(about(feature({ layer: 'DRONE', state: 'ready', detail: { battery_level: 87 } }))).toBe('Ready · battery 87%')
    expect(about(feature({ layer: 'GUARD', state: 'available', detail: { position_source: null, position_age_s: null } })))
      .toBe('Available · no position, no position recorded')
  })

  it('things at one spot are gathered, and a thing with no position is not drawn', () => {
    const groups = gathered([INCIDENT, SECOND, CAMERA, feature({ id: 'x', latitude: null, longitude: null })])
    expect(groups.map((g) => g.map((f) => f.id))).toEqual([['i1', 'i2'], ['c1']])
  })
})

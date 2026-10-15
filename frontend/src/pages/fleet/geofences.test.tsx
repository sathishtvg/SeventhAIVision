import type { ReactNode } from 'react'
import { act } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import * as api from '@/api/fleet'
import type { Geofence, GeofenceList, GeofenceOptions } from '@/api/fleet'
import { draftProblem, eventWhen, ruleDrafts, ruleProblem, rulesInput, selfCrossing } from '@/components/fleet/geofenceFormat'
import FleetGeofences from './FleetGeofences'
import FleetProviders from './FleetProviders'

const clicks: { click?: (e: { latlng: { lat: number; lng: number } }) => void } = {}
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: ReactNode }) => <div data-testid="map">{children}</div>,
  TileLayer: () => null,
  Circle: ({ children }: { children?: ReactNode }) => <div data-testid="circle">{children}</div>,
  CircleMarker: ({ children }: { children?: ReactNode }) => <div data-testid="mark">{children}</div>,
  Polygon: ({ children }: { children?: ReactNode }) => <div data-testid="polygon">{children}</div>,
  Tooltip: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  useMap: () => ({ setView: () => {}, fitBounds: () => {} }),
  useMapEvents: (handlers: typeof clicks) => { clicks.click = handlers.click; return null },
}))
vi.mock('leaflet', () => ({ default: { latLngBounds: () => ({}) } }))
vi.mock('leaflet/dist/leaflet.css', () => ({}))
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/fleet', async (orig) => {
  const real = await orig<typeof import('@/api/fleet')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, vehicleLabel: real.vehicleLabel, fleetError: real.fleetError }
})
const mocked = vi.mocked(api)

const fence = (over: Partial<Geofence> = {}): Geofence => ({
  id: 'g1', name: 'Main gate', description: null, site_id: 's1', site_name: 'Factory A', shape: 'circle',
  center: { lat: 1.3, lon: 103.8 }, radius_m: 200, polygon: [], is_active: true, archived: false, archived_at: null,
  archive_reason: null, groups: [], inside_now: 2, may_change: true, created_at: '', updated_at: '',
  rules: [{ kind: 'entry', minutes: null, speed_kmh: null, severity: 'medium', user_ids: [], role_ids: [], is_active: true },
          { kind: 'dwell_inside', minutes: 30, speed_kmh: null, severity: 'high', user_ids: [], role_ids: [], is_active: true },
          { kind: 'exit', minutes: null, speed_kmh: null, severity: 'low', user_ids: [], role_ids: [], is_active: false }],
  ...over,
})
const OPTIONS: GeofenceOptions = {
  sites: [{ id: 's1', name: 'Factory A', latitude: 1.3, longitude: 103.8, has_boundary: true }],
  groups: [], people: [{ id: 'u1', full_name: 'K. Tan', role_id: 3, role_name: 'Supervisor' }], roles: [{ id: 3, name: 'Supervisor' }],
  kinds: [], severities: ['low', 'medium', 'high', 'critical'],
  limits: { least_radius_m: 25, most_radius_m: 50000, least_points: 3, most_points: 200 },
}
const list = (items: Geofence[], manage = true, organisationWide = false): GeofenceList =>
  ({ items, may: { manage, manage_alerts: manage, organisation_wide: organisationWide } })

function show(element: React.ReactElement, at: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[at]}>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}>
        <Routes><Route path={at} element={element} /></Routes>
      </ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

beforeEach(() => {
  vi.clearAllMocks()
  mocked.getGeofenceOptions.mockResolvedValue(OPTIONS)
  mocked.listFleetGeofenceEvents.mockResolvedValue({ items: [] })
})

describe('what is drawn is checked before it is sent', () => {
  it('asks for a centre and a radius of 25 m to 50 km, or three corners whose edges do not cross', () => {
    expect(draftProblem({ kind: 'circle', center: null, radius: '200' })).toMatch(/centre/)
    expect(draftProblem({ kind: 'circle', center: { lat: 1, lon: 1 }, radius: '10' })).toMatch(/25 m to 50 km/)
    expect(draftProblem({ kind: 'circle', center: { lat: 1, lon: 1 }, radius: '200' })).toBeNull()
    const square = [{ lat: 0, lon: 0 }, { lat: 0, lon: 1 }, { lat: 1, lon: 1 }, { lat: 1, lon: 0 }]
    expect(draftProblem({ kind: 'polygon', points: square.slice(0, 2) })).toMatch(/at least 3/)
    expect(draftProblem({ kind: 'polygon', points: square })).toBeNull()
    expect(selfCrossing([square[0], square[2], square[1], square[3]])).toBe(true)
    expect(draftProblem({ kind: 'polygon', points: [square[0], square[2], square[1], square[3]] })).toMatch(/cross/)
  })

  it('sends only the rules switched on, each with what it needs', () => {
    const drafts = ruleDrafts(null)
    drafts.entry.on = true
    drafts.overspeed = { ...drafts.overspeed, on: true, speed: '2' }
    expect(ruleProblem('overspeed', drafts.overspeed)).toMatch(/5 to 300/)
    drafts.overspeed.speed = '70'
    drafts.dwell_outside = { ...drafts.dwell_outside, on: true, minutes: '45' }
    expect(rulesInput(drafts).map((r) => [r.kind, r.minutes, r.speed_kmh])).toEqual([
      ['entry', null, null], ['dwell_outside', 45, null], ['overspeed', null, 70]])
  })

  it('says "between" two times when nothing was heard while the vehicle crossed', () => {
    const at = '2026-10-15T08:00:00Z'
    expect(eventWhen({ occurred_at: at, between_start: null, between_end: null })).toBe(new Date(at).toLocaleString())
    expect(eventWhen({ occurred_at: at, between_start: '2026-10-15T07:00:00Z', between_end: at })).toMatch(/^between /)
  })
})

describe('the geofences page', () => {
  it('shows each geofence with its alerts and how many are inside, and the last day’s events', async () => {
    mocked.listGeofences.mockResolvedValue(list([fence()], false))
    mocked.listFleetGeofenceEvents.mockResolvedValue({ items: [
      { id: 'e1', event_type: 'exit', occurred_at: '2026-10-15T08:00:00Z', lat: null, lon: null, speed: null, accuracy: null,
        between_start: '2026-10-15T07:00:00Z', between_end: '2026-10-15T08:00:00Z', alert_id: 'a1', alert_status: 'open',
        alert_severity: 'high', vehicle_id: 'v1', vehicle_name: 'Patrol 3', plate_number: 'SBA 1234 K', geofence_id: 'g1', geofence_name: 'Main gate' },
      { id: 'e2', event_type: 'entry', occurred_at: '2026-10-15T09:00:00Z', lat: null, lon: null, speed: null, accuracy: null,
        between_start: null, between_end: null, alert_id: null, alert_status: null, alert_severity: null,
        vehicle_id: 'v1', vehicle_name: 'Patrol 3', plate_number: 'SBA 1234 K', geofence_id: 'g1', geofence_name: 'Main gate' },
    ] })
    show(<FleetGeofences />, '/gps/geofences')
    const table = await screen.findByRole('table', { name: 'Geofences' })
    expect(await within(table).findByText('Main gate')).toBeInTheDocument()
    expect(within(table).getByText('Enters')).toBeInTheDocument()
    expect(within(table).getByText('Inside longer than 30 min')).toBeInTheDocument()
    expect(within(table).queryByText('Leaves')).not.toBeInTheDocument()
    expect(within(table).getByText('Circle, 200 m radius')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Draw a geofence' })).not.toBeInTheDocument()
    const events = await screen.findByRole('table', { name: 'Geofence events' })
    expect(await within(events).findByText(/^between /)).toBeInTheDocument()
    expect(within(events).getByText('high · open')).toBeInTheDocument()
    expect(within(events).getByText('Recorded, no alert')).toBeInTheDocument()
  })

  it('draws a circle where the map is clicked and sends its rules', async () => {
    mocked.listGeofences.mockResolvedValue(list([]))
    mocked.createGeofence.mockResolvedValue(fence())
    show(<FleetGeofences />, '/gps/geofences')
    const draw = await screen.findByRole('button', { name: 'Draw a geofence' })
    await waitFor(() => expect(draw).toBeEnabled())
    fireEvent.click(draw)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Click the map where the centre is.')).toBeInTheDocument()
    act(() => clicks.click?.({ latlng: { lat: 1.301, lng: 103.801 } }))
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: 'Yard' } })
    fireEvent.click(within(dialog).getByLabelText('Enters'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Make the geofence' }))
    await waitFor(() => expect(mocked.createGeofence).toHaveBeenCalled())
    const body = mocked.createGeofence.mock.calls[0][0]
    expect(body).toMatchObject({ name: 'Yard', site_id: 's1', group_ids: [],
                                 shape: { kind: 'circle', center: { lat: 1.301, lon: 103.801 }, radius_m: 200 } })
    expect(body.rules?.map((r) => r.kind)).toEqual(['entry'])
  })

  it('sends a changed name without the shape, so no vehicle’s side is forgotten', async () => {
    mocked.listGeofences.mockResolvedValue(list([fence()]))
    mocked.changeGeofence.mockResolvedValue(fence())
    show(<FleetGeofences />, '/gps/geofences')
    fireEvent.click(await screen.findByRole('button', { name: 'Change Main gate' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: 'North gate' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mocked.changeGeofence).toHaveBeenCalled())
    const [id, body] = mocked.changeGeofence.mock.calls[0]
    expect(id).toBe('g1')
    expect(body.name).toBe('North gate')
    expect(body).not.toHaveProperty('shape')
    expect(body).not.toHaveProperty('group_ids')
    expect(body.rules?.map((r) => r.kind)).toEqual(['entry', 'dwell_inside'])
  })

  it('archives with a reason only', async () => {
    mocked.listGeofences.mockResolvedValue(list([fence()]))
    mocked.archiveGeofence.mockResolvedValue(fence({ archived: true, is_active: false }))
    show(<FleetGeofences />, '/gps/geofences')
    fireEvent.click(await screen.findByRole('button', { name: 'Archive Main gate' }))
    const dialog = await screen.findByRole('dialog')
    const archive = within(dialog).getByRole('button', { name: 'Archive' })
    expect(archive).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText(/^Why/), { target: { value: 'Gate moved' } })
    fireEvent.click(archive)
    await waitFor(() => expect(mocked.archiveGeofence).toHaveBeenCalledWith('g1', 'Gate moved'))
  })

  it('says when the geofences could not be loaded', async () => {
    mocked.listGeofences.mockRejectedValue({ message: 'Network Error', code: 'ERR_NETWORK' })
    show(<FleetGeofences />, '/gps/geofences')
    expect(await screen.findByText('The geofences could not be loaded')).toBeInTheDocument()
  })
})

describe('the alerts when a vehicle stops reporting', () => {
  it('are offline on and stale off until changed, by whoever may', async () => {
    const may = { edit: true, archive: true, manage_fields: true, manage_devices: false, manage_limits: true, read_history: true, export_history: true }
    mocked.getFleetOptions.mockResolvedValue({ vehicle_types: [], service_statuses: [], field_types: [], sites: [], drivers: [], fields: [],
                                              site_required: false, may })
    mocked.getFleetLimits.mockResolvedValue({ online_minutes: 5, offline_minutes: 30 })
    mocked.getFleetRetention.mockResolvedValue({ kinds: [], least_days: 30, most_days: 3650, may_set: false })
    mocked.getStopAlerts.mockResolvedValue({ offline: { on: true, severity: 'medium' }, stale: { on: false, severity: 'low' } })
    mocked.setStopAlerts.mockImplementation(async (b) => b)
    show(<FleetProviders />, '/gps/providers')
    const stale = await screen.findByLabelText(/has not reported for a while/)
    expect(stale).not.toBeChecked()
    expect(screen.getByLabelText(/stops reporting \(offline\)/)).toBeChecked()
    fireEvent.click(stale)
    const card = screen.getByText('Alerts when a vehicle in service stops reporting').closest('div') as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mocked.setStopAlerts).toHaveBeenCalledWith({
      offline: { on: true, severity: 'medium' }, stale: { on: true, severity: 'low' } }))
  })
})

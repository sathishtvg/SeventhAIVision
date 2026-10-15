import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import * as api from '@/api/fleet'
import type { JourneyHistory, RoutePoint } from '@/api/fleet'
import { lastAt, localInput, minutesText, step } from '@/components/fleet/journeyFormat'
import FleetHistory from './FleetHistory'

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: ReactNode }) => <div data-testid="map">{children}</div>,
  TileLayer: () => null,
  CircleMarker: ({ children }: { children?: ReactNode }) => <div data-testid="mark">{children}</div>,
  Polyline: () => <div data-testid="route" />,
  Tooltip: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  useMap: () => ({ setView: () => {}, fitBounds: () => {} }),
}))
vi.mock('leaflet', () => ({ default: { latLngBounds: () => ({}) } }))
vi.mock('leaflet/dist/leaflet.css', () => ({}))
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/fleet', async (orig) => {
  const real = await orig<typeof import('@/api/fleet')>()
  return { ...real, getJourneyHistory: vi.fn(), getFleetMap: vi.fn(), getFleetOptions: vi.fn(), downloadJourneys: vi.fn() }
})
const mocked = vi.mocked(api)

const point = (at: string, lat: number, speed: number | null = 40): RoutePoint => ({ at, lat, lon: 103.9, speed })
const ROUTE = [point('2026-10-15T08:00:00Z', 1.30), point('2026-10-15T08:00:30Z', 1.31), point('2026-10-15T08:01:00Z', 1.32)]

describe('playing a journey back', () => {
  it('is at the last real position at or before the moment, and nowhere before the first', () => {
    expect(lastAt(ROUTE, Date.parse('2026-10-15T07:59:59Z'))).toBeNull()
    expect(lastAt(ROUTE, Date.parse('2026-10-15T08:00:00Z'))?.lat).toBe(1.30)
    expect(lastAt(ROUTE, Date.parse('2026-10-15T08:00:29Z'))?.lat).toBe(1.30)
    expect(lastAt(ROUTE, Date.parse('2026-10-15T08:00:45Z'))?.lat).toBe(1.31)
    expect(lastAt(ROUTE, Date.parse('2026-10-15T09:00:00Z'))?.lat).toBe(1.32)
    expect(lastAt([], 0)).toBeNull()
  })

  it('moves the slider by the speed chosen and holds at the end; says minutes in words', () => {
    expect(step(0, 10, 10_000)).toBe(2_000)
    expect(step(9_000, 60, 10_000)).toBe(10_000)
    expect(minutesText(42.4)).toBe('42 min')
    expect(minutesText(135)).toBe('2 h 15 min')
    expect(localInput(new Date(2026, 9, 15, 8, 5))).toBe('2026-10-15T08:05')
  })
})

describe('the journey history page', () => {
  const history = (over: Partial<JourneyHistory> = {}): JourneyHistory => ({
    vehicle: { id: 'v1', name: 'Patrol 3', plate_number: 'SBA 1234 K' }, start: '', end: '', enough: true, positions: 40,
    left_out: { inaccurate: 1, impossible_jump: 0 }, route: ROUTE, route_thinned: false,
    trips: [{ start: '2026-10-15T08:00:00Z', end: '2026-10-15T08:01:00Z', minutes: 1, distance_km: 2.2, max_speed: 40, avg_kmh: 132,
              from: { lat: 1.3, lon: 103.9 }, to: { lat: 1.32, lon: 103.9 } }],
    stops: [{ start: '2026-10-15T08:05:00Z', end: '2026-10-15T08:15:00Z', minutes: 10, lat: 1.32, lon: 103.9, ignition_off: true }],
    gaps: [{ start: '2026-10-15T08:15:00Z', end: '2026-10-15T08:36:00Z' }],
    summary: { distance_km: 2.2, driving_minutes: 1, stopped_minutes: 10 }, ...over,
  })
  const may = (exportHistory: boolean) => ({
    vehicle_types: [], service_statuses: [], field_types: [], sites: [], drivers: [], fields: [], site_required: false,
    may: { edit: false, archive: false, manage_fields: false, manage_devices: false, manage_limits: false, read_history: true,
           export_history: exportHistory },
  })
  function show(at: string) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    return render(
      <MemoryRouter initialEntries={[at]}>
        <QueryClientProvider client={qc}><ThemeProvider theme={theme}>
          <Routes><Route path="/gps/history" element={<FleetHistory />} /></Routes>
        </ThemeProvider></QueryClientProvider>
      </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
  }
  beforeEach(() => {
    vi.clearAllMocks()
    mocked.getFleetMap.mockResolvedValue({ items: [], limits: { online_minutes: 5, offline_minutes: 30 }, at: '' })
  })
  const AT = '/gps/history?vehicle=v1&start=2026-10-15T00:00&end=2026-10-15T23:59'

  it('shows the distance, trips and stops, and says what was left out and when nothing came', async () => {
    mocked.getFleetOptions.mockResolvedValue(may(false))
    mocked.getJourneyHistory.mockResolvedValue(history())
    show(AT)
    expect(await screen.findByText('2.2 km')).toBeInTheDocument()
    expect(screen.getByText(/1 position was left out \(1 reported as inaccurate/)).toBeInTheDocument()
    expect(screen.getByText(/No position came for one stretch of more than 15 minutes/)).toBeInTheDocument()
    expect(screen.getAllByText('10 min').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /Export/ })).not.toBeInTheDocument()
    expect(mocked.getJourneyHistory).toHaveBeenCalledWith('v1', new Date('2026-10-15T00:00').toISOString(),
                                                         new Date('2026-10-15T23:59').toISOString())
  })

  it('says when there are too few positions for a journey, and asks for no range longer than a week', async () => {
    mocked.getFleetOptions.mockResolvedValue(may(false))
    mocked.getJourneyHistory.mockResolvedValue(history({ enough: false, positions: 1, trips: [], stops: [], route: [] }))
    show(AT)
    expect(await screen.findByText('Not enough positions for a journey')).toBeInTheDocument()
    mocked.getJourneyHistory.mockClear()
    show('/gps/history?vehicle=v1&start=2026-10-01T00:00&end=2026-10-15T00:00')
    expect(await screen.findByText('A range is a week at most.')).toBeInTheDocument()
    expect(mocked.getJourneyHistory).not.toHaveBeenCalled()
  })

  it('exports for whoever may, with the range shown', async () => {
    mocked.getFleetOptions.mockResolvedValue(may(true))
    mocked.getJourneyHistory.mockResolvedValue(history())
    mocked.downloadJourneys.mockResolvedValue()
    show(AT)
    fireEvent.click(await screen.findByRole('button', { name: 'Export trips (CSV)' }))
    await waitFor(() => expect(mocked.downloadJourneys).toHaveBeenCalledWith('v1', new Date('2026-10-15T00:00').toISOString(),
                                                                            new Date('2026-10-15T23:59').toISOString(), 'trips', 'SBA1234K'))
  })

  it('says when the journeys could not be loaded', async () => {
    mocked.getFleetOptions.mockResolvedValue(may(false))
    mocked.getJourneyHistory.mockRejectedValue({ message: 'Network Error', code: 'ERR_NETWORK' })
    show(AT)
    expect(await screen.findByText('The journeys could not be loaded')).toBeInTheDocument()
  })
})

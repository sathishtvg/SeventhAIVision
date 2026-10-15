import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { act, render, screen, fireEvent, waitFor } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import * as api from '@/api/fleet'
import type { MapVehicle } from '@/api/fleet'
import { STATUS_STYLE, drawn, headingEnd, matches, merge, statusNow } from '@/components/fleet/fleetMapFormat'
import { idsIn } from '@/components/fleet/useFleetLive'
import { useWsStore } from '@/store/websocket'
import FleetLiveMap from './FleetLiveMap'

// The map itself is Leaflet's; what is put on it is what is tested here.
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: ReactNode }) => <div data-testid="map">{children}</div>,
  TileLayer: () => null,
  CircleMarker: ({ children, eventHandlers, pathOptions }: {
    children?: ReactNode; eventHandlers?: { click?: () => void }; pathOptions?: { dashArray?: string; fillOpacity?: number } }) => (
    <button type="button" data-testid="mark" data-hollow={pathOptions?.dashArray ? 'yes' : 'no'}
            onClick={() => eventHandlers?.click?.()}>{children}</button>),
  Polyline: () => <div data-testid="heading" />,
  Tooltip: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  useMap: () => ({ setView: () => {}, fitBounds: () => {}, getZoom: () => 12 }),
}))
vi.mock('leaflet', () => ({ default: { latLngBounds: () => ({}) } }))
vi.mock('leaflet/dist/leaflet.css', () => ({}))
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/fleet', async (orig) => {
  const real = await orig<typeof import('@/api/fleet')>()
  return { ...real, getFleetMap: vi.fn() }
})
const getFleetMap = vi.mocked(api.getFleetMap)

const NOW = Date.parse('2026-10-15T05:00:00Z')
const LIMITS = { online_minutes: 5, offline_minutes: 30 }
const minutesAgo = (m: number) => new Date(NOW - m * 60_000).toISOString()
const car = (over: Partial<MapVehicle> = {}): MapVehicle => ({
  id: 'v1', name: 'Patrol 3', plate_number: 'SBA 1234 K', vehicle_type: 'patrol_car', type_label: 'Patrol car', site_id: 's1',
  site_name: 'Depot', driver_name: 'K. Tan', service_status: 'in_service', tracking_status: 'online', status_label: 'Online',
  motion: 'moving', last_lat: 1.3644, last_lon: 103.9915, last_fix_at: minutesAgo(1), last_speed: '37.04', last_heading: 45,
  last_accuracy: '6.00', ...over,
})

describe('the map’s rules', () => {
  it('works the status out against the clock from the last fix, and never calls a vehicle without one online', () => {
    expect(statusNow(car({ last_fix_at: minutesAgo(4.9) }), LIMITS, NOW)).toBe('online')
    expect(statusNow(car({ last_fix_at: minutesAgo(5) }), LIMITS, NOW)).toBe('stale')
    expect(statusNow(car({ last_fix_at: minutesAgo(30) }), LIMITS, NOW)).toBe('offline')
    expect(statusNow(car({ last_fix_at: null }), LIMITS, NOW)).toBe('offline')
    expect(statusNow(car({ tracking_status: 'not_configured' }), LIMITS, NOW)).toBe('not_configured')
  })

  it('draws only a configured vehicle with a fix, an offline one hollow, a stale one faint', () => {
    expect(drawn(car())).toBe(true)
    expect(drawn(car({ last_fix_at: null, last_lat: null, last_lon: null }))).toBe(false)
    expect(drawn(car({ tracking_status: 'not_configured' }))).toBe(false)
    expect(STATUS_STYLE.offline.fillOpacity).toBe(0)
    expect(STATUS_STYLE.offline.dashArray).toBeTruthy()
    expect(STATUS_STYLE.stale.opacity).toBeLessThan(STATUS_STYLE.online.opacity)
  })

  it('points the heading line the way the vehicle goes, about 45 m ahead', () => {
    const [lat, lon] = headingEnd(1.3, 103.8, 90)
    expect(lat).toBeCloseTo(1.3, 6)
    expect((lon - 103.8) * 111_320 * Math.cos((1.3 * Math.PI) / 180)).toBeCloseTo(45, 0)
  })

  it('puts the vehicles a refetch brought in place, and searches without spaces, dashes or case', () => {
    const merged = merge([car(), car({ id: 'v2', plate_number: 'SKZ 9' })], [car({ id: 'v2', plate_number: 'SKZ 9', last_lat: 1.4 }), car({ id: 'v3' })])
    expect(merged.map((v) => [v.id, v.last_lat])).toEqual([['v1', 1.3644], ['v2', 1.4], ['v3', 1.3644]])
    const f = { q: 'sba1234-k', site: '', type: '', statuses: [] }
    expect(matches(car(), f, 'online')).toBe(true)
    expect(matches(car(), { ...f, statuses: ['stale'] }, 'online')).toBe(false)
  })

  it('takes from the live connection only which vehicles moved', () => {
    expect(idsIn(JSON.stringify({ event_type: 'fleet_moved', payload: { vehicle_ids: ['v1', 'v2'] } }))).toEqual(['v1', 'v2'])
    expect(idsIn(JSON.stringify({ event_type: 'fleet_status_changed', payload: { vehicle_ids: ['v3'] } }))).toEqual(['v3'])
    expect(idsIn(JSON.stringify({ event_type: 'alert_created', payload: { vehicle_ids: ['v1'] } }))).toEqual([])
    expect(idsIn('not json')).toEqual([])
  })
})

describe('the live fleet map', () => {
  function show(at = '/gps/map') {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    return render(
      <MemoryRouter initialEntries={[at]}>
        <QueryClientProvider client={qc}><ThemeProvider theme={theme}>
          <Routes><Route path="/gps/map" element={<FleetLiveMap />} /></Routes>
        </ThemeProvider></QueryClientProvider>
      </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
  }
  beforeEach(() => { vi.clearAllMocks() })

  it('lists every vehicle, draws only those that have reported, and says why the others are not there', async () => {
    getFleetMap.mockResolvedValue({ limits: LIMITS, at: '', items: [
      car({ last_fix_at: new Date(Date.now() - 60_000).toISOString() }),
      car({ id: 'v2', plate_number: 'SKZ 9', last_fix_at: new Date(Date.now() - 60 * 60_000).toISOString() }),
      car({ id: 'v3', plate_number: 'FBA 7', tracking_status: 'not_configured', last_lat: null, last_lon: null, last_fix_at: null, motion: null }),
    ] })
    show()
    expect(await screen.findByText('SKZ 9')).toBeInTheDocument()
    const marks = screen.getAllByTestId('mark')
    expect(marks).toHaveLength(2)
    expect(marks.map((m) => m.getAttribute('data-hollow'))).toEqual(['no', 'yes'])
    expect(screen.getByText(/no device/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('SKZ 9'))
    expect(await screen.findByText(/Last known at 1.36440, 103.99150/)).toBeInTheDocument()
  })

  it('says when no vehicle has reported yet, rather than showing an empty map as a quiet fleet', async () => {
    getFleetMap.mockResolvedValue({ limits: LIMITS, at: '', items: [
      car({ tracking_status: 'offline', last_lat: null, last_lon: null, last_fix_at: null, motion: null })] })
    show()
    expect(await screen.findByText('No vehicle has reported a position yet')).toBeInTheDocument()
    expect(screen.queryAllByTestId('mark')).toHaveLength(0)
  })

  it('asks for just the vehicles the live connection names, and puts them in place', async () => {
    getFleetMap.mockResolvedValueOnce({ limits: LIMITS, at: '', items: [car({ last_fix_at: new Date().toISOString() })] })
    show('/gps/map?focus=v1')
    expect(await screen.findByText(/At 1.36440, 103.99150/)).toBeInTheDocument()
    getFleetMap.mockResolvedValueOnce({ limits: LIMITS, at: '', items: [car({ last_lat: 1.4, last_fix_at: new Date().toISOString() })] })
    act(() => { useWsStore.setState({ lastMessage: JSON.stringify({ event_type: 'fleet_moved', payload: { vehicle_ids: ['v1'] } }) }) })
    await waitFor(() => expect(getFleetMap).toHaveBeenLastCalledWith(['v1']), { timeout: 4000 })
    expect(await screen.findByText(/At 1.40000, 103.99150/)).toBeInTheDocument()
  })

  it('says when the fleet could not be loaded', async () => {
    getFleetMap.mockRejectedValue({ message: 'Network Error', code: 'ERR_NETWORK' })
    show()
    expect(await screen.findByText('The fleet could not be loaded')).toBeInTheDocument()
  })
})

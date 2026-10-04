import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/drones'
import type { ReportDelivery, ReportRecipient, Session } from '@/api/drones'
import DronePatrols from './DronePatrols'
import DronePatrol from './DronePatrol'
import { DeliveriesPanel, RecipientsPanel, SummaryPanel } from './DroneReportPanels'

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
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([{ id: 's1', name: 'North Yard' }]) }))
vi.mock('@/api/cameras', () => ({ getStreams: vi.fn().mockResolvedValue([]) }))
vi.mock('@/api/drones', async (orig) => {
  const real = await orig<typeof import('@/api/drones')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError, blobApiError: real.blobApiError }
})

const ADMIN = 2, OPERATOR = 4, GUARD = 5, VIEWER = 6

function asRole(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'u1', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

const RECIPIENTS: ReportRecipient[] = [
  { id: 'r1', site_id: null, mission_id: null, email: 'director@agency.test', frequency: 'WEEKLY', is_active: true,
    scope: 'organisation', site_name: null, mission_name: null, created_by_name: 'Mei Lin', created_at: '' },
  { id: 'r2', site_id: 's1', mission_id: null, email: 'site-lead@agency.test', frequency: 'IMMEDIATE', is_active: false,
    scope: 'site', site_name: 'North Yard', mission_name: null, created_by_name: null, created_at: '' },
]
const FAILED: ReportDelivery = {
  id: 'd1', session_id: 'p1', session_number: 'DP-1', site_name: 'North Yard', scope_label: null,
  frequency: 'IMMEDIATE', period_start: null, period_end: null, recipients: ['site-lead@agency.test'],
  subject: 'Drone patrol report: Night round at North Yard — completed (DP-1)', status: 'FAILED', attempts: 5,
  attempts_left: 0, last_error: 'Connection refused by mail server', scheduled_at: '2026-10-04T02:00:00Z',
  sent_at: null, created_at: '2026-10-03T14:30:00Z',
}
const ENDED: Session = {
  id: 'p1', session_number: 'DP-1', mission_id: 'mi1', mission_name: 'Night round', drone_id: 'd1',
  drone_name: 'Hawk 1', route_name: 'Perimeter', profile_name: null, site_id: 's1', site_name: 'North Yard',
  status: 'COMPLETED', triggered_by: 'SCHEDULE', scheduled_for: null, started_at: '2026-10-03T14:00:00Z',
  launched_at: '2026-10-03T14:00:20Z', ended_at: '2026-10-03T14:09:00Z', blocked_reason: null, failure_reason: null,
  abort_reason: null, distance_m: 742, event_count: 0, incident_count: 0, last_waypoint_sequence: null,
  edge_gateway_id: null, created_at: '2026-10-03T14:00:00Z', waypoints: [],
}

beforeEach(() => {
  asRole(ADMIN)
  vi.mocked(api.getEntitlement).mockResolvedValue({ licensed: true, reason: null, expires_at: null,
                                                     limits: { max_drones: null, max_missions: null, max_sites: null }, usage: {} })
  vi.mocked(api.listSessions).mockResolvedValue({ items: [ENDED], total: 1, limit: 25, offset: 0, has_more: false })
  vi.mocked(api.listDrones).mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0, has_more: false })
  vi.mocked(api.getSession).mockResolvedValue(ENDED)
  vi.mocked(api.getTrack).mockResolvedValue({ total_samples: 0, points: [] })
  vi.mocked(api.listEvents).mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0, has_more: false })
  vi.mocked(api.listRecipients).mockResolvedValue(RECIPIENTS)
  vi.mocked(api.listDeliveries).mockResolvedValue({ items: [FAILED], total: 1, limit: 50, offset: 0, has_more: false })
  vi.mocked(api.updateRecipient).mockResolvedValue(RECIPIENTS[0])
  vi.mocked(api.createRecipient).mockResolvedValue(RECIPIENTS[0])
  vi.mocked(api.retryDelivery).mockResolvedValue({})
  vi.mocked(api.downloadSessionReport).mockResolvedValue(undefined)
  vi.mocked(api.getPeriodSummary).mockResolvedValue({
    scope: 'all sites', from: '2026-09-28', to: '2026-10-04', timezone: 'Asia/Singapore',
    totals: { flights: 12, completed: 10, did_not_complete: 2, by_status: { COMPLETED: 10, BLOCKED: 2 },
              flight_seconds: 7200, distance_m: 8400, events: 5, suspicious: 2, false_positives: 1, incidents: 1,
              by_risk: { INFO: 0, LOW: 3, MEDIUM: 1, HIGH: 1, CRITICAL: 0 } },
  })
})

function renderAt(path: string, pattern: string, el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <ThemeProvider theme={theme}><Routes><Route path={pattern} element={el} /></Routes></ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

describe('Patrols & Reports tabs', () => {
  it('offers the report tabs to someone who may read reports', async () => {
    asRole(VIEWER)
    render(<DronePatrols />)
    expect(await screen.findByRole('tab', { name: 'Report recipients' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Email deliveries' }))
    expect(await screen.findByText('Connection refused by mail server')).toBeInTheDocument()
  })

  it('shows a guard the flights and nothing of the reporting', async () => {
    asRole(GUARD)
    render(<DronePatrols />)
    expect(await screen.findByText('DP-1')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Report recipients' })).not.toBeInTheDocument()
  })
})

describe('RecipientsPanel', () => {
  it('says who gets what, how often, and lets it be paused', async () => {
    render(<RecipientsPanel />)
    const row = (await screen.findByText('director@agency.test')).closest('tr')!
    expect(within(row).getByText('All sites')).toBeInTheDocument()
    expect(within(row).getByText('Weekly')).toBeInTheDocument()
    expect(screen.getByText('Site: North Yard')).toBeInTheDocument()
    expect(screen.getByText('After each flight')).toBeInTheDocument()
    fireEvent.click(within(row).getByRole('switch', { name: 'Send to director@agency.test' }))
    await waitFor(() => expect(vi.mocked(api.updateRecipient)).toHaveBeenCalledWith('r1', { is_active: false }))
  })

  it('adds a recipient only once the address is one', async () => {
    render(<RecipientsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Add recipient' }))
    const add = screen.getByRole('button', { name: 'Add' })
    expect(add).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'not-an-address' } })
    expect(add).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: ' ops@agency.test ' } })
    expect(add).toBeEnabled()
    fireEvent.click(add)
    await waitFor(() => expect(vi.mocked(api.createRecipient)).toHaveBeenCalledWith(
      { email: 'ops@agency.test', frequency: 'IMMEDIATE', site_id: null, mission_id: null }))
  })

  it('is read-only for someone who may read reports but not export them', async () => {
    asRole(VIEWER)
    render(<RecipientsPanel />)
    expect(await screen.findByText('director@agency.test')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add recipient' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove director@agency.test' })).not.toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Send to director@agency.test' })).toBeDisabled()
  })
})

describe('DeliveriesPanel', () => {
  it("shows a failed email with the mail server's reason, and sends it again", async () => {
    render(<DeliveriesPanel />)
    expect(await screen.findByText('Connection refused by mail server')).toBeInTheDocument()
    expect(screen.getByText('Failed 5 times and will not be tried again by itself')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Send again' }))
    await waitFor(() => expect(vi.mocked(api.retryDelivery)).toHaveBeenCalledWith('d1'))
  })

  it('does not offer to send again to someone who cannot export', async () => {
    asRole(OPERATOR)
    render(<DeliveriesPanel />)
    expect(await screen.findByText('Connection refused by mail server')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send again' })).not.toBeInTheDocument()
  })
})

describe('SummaryPanel', () => {
  it('shows the period in numbers and keeps the workbook for those who may export', async () => {
    const view = render(<SummaryPanel />)
    expect(await screen.findByText('Did not complete')).toBeInTheDocument()
    expect(screen.getByText('2.0 h')).toBeInTheDocument()
    expect(screen.getByText('Blocked · 2')).toBeInTheDocument()
    expect(screen.getByText('HIGH · 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download workbook' })).toBeEnabled()
    view.unmount()
    asRole(VIEWER)
    render(<SummaryPanel />)
    expect(await screen.findByText('Did not complete')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download workbook' })).not.toBeInTheDocument()
  })
})

describe("a flight's report", () => {
  it('offers the PDF and the workbook once the flight has ended', async () => {
    renderAt('/drone-patrols/p1', '/drone-patrols/:id', <DronePatrol />)
    fireEvent.click(await screen.findByRole('button', { name: 'Report PDF' }))
    await waitFor(() => expect(vi.mocked(api.downloadSessionReport)).toHaveBeenCalledWith('p1', 'pdf'))
    fireEvent.click(screen.getByRole('button', { name: 'Excel' }))
    await waitFor(() => expect(vi.mocked(api.downloadSessionReport)).toHaveBeenCalledWith('p1', 'excel'))
  })

  it('gives an operator the PDF but not the export', async () => {
    asRole(OPERATOR)
    renderAt('/drone-patrols/p1', '/drone-patrols/:id', <DronePatrol />)
    expect(await screen.findByRole('button', { name: 'Report PDF' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Excel' })).not.toBeInTheDocument()
  })

  it('offers no report while the flight is still in the air', async () => {
    vi.mocked(api.getSession).mockResolvedValue({ ...ENDED, status: 'ACTIVE', ended_at: null })
    vi.mocked(api.listCommands).mockResolvedValue([])
    vi.mocked(api.getDrone).mockResolvedValue({ camera_id: null } as never)
    renderAt('/drone-patrols/p1', '/drone-patrols/:id', <DronePatrol />)
    expect(await screen.findByText('LIVE')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Report PDF' })).not.toBeInTheDocument()
  })
})

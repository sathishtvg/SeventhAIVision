import { render, screen, fireEvent, waitFor } from '@/test/utils'
import Alerts from '@/pages/Alerts'
import { getAlerts, acknowledgeAlert } from '@/api/alerts'
import { useAuthStore } from '@/store/auth'
import type { Alert } from '@/types/api'

vi.mock('@/api/alerts', () => ({
  getAlerts: vi.fn(),
  acknowledgeAlert: vi.fn(),
}))

vi.mock('@/store/auth', () => ({
  useAuthStore: vi.fn(),
}))

const mockOpenAlert: Alert = {
  id: 'a1', title: 'Blocklist Hit', severity: 'critical', status: 'open',
  camera_id: 'c1', module_type: 'lpr', created_at: new Date().toISOString(),
  detection_id: null, alert_code: 'lpr.blocklist_hit', message: 'Plate SGB1234X matched blocklist',
  acknowledged_by_user_id: null, acknowledged_at: null,
}

function mockUser(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({ user: { id: '1', tenantId: 'a', roleId }, accessToken: 'tok' })
  )
}

beforeEach(() => {
  mockUser(2) // admin by default
  vi.mocked(getAlerts).mockResolvedValue({ items: [mockOpenAlert], has_more: false })
  vi.mocked(acknowledgeAlert).mockResolvedValue({} as Alert)
})

describe('Alerts', () => {
  it('renders all filter chips', () => {
    render(<Alerts />)
    // Status, site, and module filter rows each have their own "All" chip
    expect(screen.getAllByText('All').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Open')).toBeInTheDocument()
    expect(screen.getByText('Acknowledged')).toBeInTheDocument()
    expect(screen.getByText('Resolved')).toBeInTheDocument()
    expect(screen.getByText('Dismissed')).toBeInTheDocument()
  })

  it('renders the table column headers', () => {
    render(<Alerts />)
    expect(screen.getByText('Severity')).toBeInTheDocument()
    expect(screen.getByText('Title')).toBeInTheDocument()
    expect(screen.getByText('Status')).toBeInTheDocument()
    expect(screen.getByText('Module')).toBeInTheDocument()
  })

  it('renders alert rows with title and message after data loads', async () => {
    render(<Alerts />)
    expect(await screen.findByText('Blocklist Hit')).toBeInTheDocument()
    expect(screen.getByText('Plate SGB1234X matched blocklist')).toBeInTheDocument()
  })

  it('shows "No alerts found" when query returns empty list', async () => {
    vi.mocked(getAlerts).mockResolvedValue({ items: [], has_more: false })
    render(<Alerts />)
    expect(await screen.findByText('No alerts found')).toBeInTheDocument()
  })

  it('shows Ack button for a user with alert:acknowledge on open alerts', async () => {
    mockUser(2) // admin has alert:acknowledge
    render(<Alerts />)
    expect(await screen.findByRole('button', { name: /^ack$/i })).toBeInTheDocument()
  })

  it('hides Ack button for viewer who lacks alert:acknowledge', async () => {
    mockUser(6) // viewer has no alert:acknowledge
    render(<Alerts />)
    await screen.findByText('Blocklist Hit') // wait for data
    expect(screen.queryByRole('button', { name: /^ack$/i })).not.toBeInTheDocument()
  })

  it('calls acknowledgeAlert with the alert id when Ack is clicked', async () => {
    mockUser(2)
    render(<Alerts />)
    const ackBtn = await screen.findByRole('button', { name: /^ack$/i })
    fireEvent.click(ackBtn)
    await waitFor(() => {
      // TanStack Query v5 passes a mutationFnContext as second arg; only the first matters.
      expect(vi.mocked(acknowledgeAlert)).toHaveBeenCalledWith('a1', expect.anything())
    })
  })

  it('switches filter by clicking a status chip', async () => {
    render(<Alerts />)
    fireEvent.click(screen.getByText('Acknowledged'))
    await waitFor(() => {
      // Page passes (status, siteId, moduleType) since the Phase 10 site/module filters
      expect(vi.mocked(getAlerts)).toHaveBeenCalledWith('acknowledged', undefined, undefined)
    })
  })

  it('does not show Ack button for acknowledged alerts (status !== open)', async () => {
    vi.mocked(getAlerts).mockResolvedValue({ items: [{ ...mockOpenAlert, status: 'acknowledged' }], has_more: false })
    mockUser(2) // has the permission, but status is wrong
    render(<Alerts />)
    await screen.findByText('Blocklist Hit')
    expect(screen.queryByRole('button', { name: /^ack$/i })).not.toBeInTheDocument()
  })
})

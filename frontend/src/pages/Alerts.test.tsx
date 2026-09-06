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
  // Filters now live in the collapsing FilterRail rather than as chip rows on
  // the page, so they are deliberately absent until the rail is opened. These
  // two tests drive that interaction instead of asserting the chips are
  // permanently on screen — the chips being gone at rest IS the feature.
  it('keeps filter chips out of the page until the rail is opened', () => {
    render(<Alerts />)
    expect(screen.queryByText('Acknowledged')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^filters/i })).toBeInTheDocument()
  })

  it('renders all status filter chips once the rail is opened', () => {
    render(<Alerts />)
    fireEvent.click(screen.getByRole('button', { name: /^filters/i }))
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

  it('shows Acknowledge button for a user with alert:acknowledge on open alerts', async () => {
    mockUser(2) // admin has alert:acknowledge
    render(<Alerts />)
    expect(await screen.findByRole('button', { name: /^acknowledge$/i })).toBeInTheDocument()
  })

  it('hides Acknowledge button for viewer who lacks alert:acknowledge', async () => {
    mockUser(6) // viewer has no alert:acknowledge
    render(<Alerts />)
    await screen.findByText('Blocklist Hit') // wait for data
    expect(screen.queryByRole('button', { name: /^acknowledge$/i })).not.toBeInTheDocument()
  })

  it('calls acknowledgeAlert with the alert id when Acknowledge is clicked', async () => {
    mockUser(2)
    render(<Alerts />)
    const ackBtn = await screen.findByRole('button', { name: /^acknowledge$/i })
    fireEvent.click(ackBtn)
    await waitFor(() => {
      // TanStack Query v5 passes a mutationFnContext as second arg; only the first matters.
      expect(vi.mocked(acknowledgeAlert)).toHaveBeenCalledWith('a1', expect.anything())
    })
  })

  it('switches filter by clicking a status chip in the rail', async () => {
    render(<Alerts />)
    fireEvent.click(screen.getByRole('button', { name: /^filters/i }))
    fireEvent.click(screen.getByText('Acknowledged'))
    await waitFor(() => {
      // Page passes (status, siteId, moduleType) since the Phase 10 site/module filters
      expect(vi.mocked(getAlerts)).toHaveBeenCalledWith('acknowledged', undefined, undefined)
    })
    // Picking auto-hides the rail — the behaviour that keeps the page clean.
    expect(screen.queryByText('Dismissed')).not.toBeInTheDocument()
  })

  it('does not show Ack button for acknowledged alerts (status !== open)', async () => {
    vi.mocked(getAlerts).mockResolvedValue({ items: [{ ...mockOpenAlert, status: 'acknowledged' }], has_more: false })
    mockUser(2) // has the permission, but status is wrong
    render(<Alerts />)
    await screen.findByText('Blocklist Hit')
    expect(screen.queryByRole('button', { name: /^acknowledge$/i })).not.toBeInTheDocument()
  })
})

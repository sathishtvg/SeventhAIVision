import { render, screen } from '@/test/utils'
import { Sidebar } from '@/components/layout/Sidebar'
import { useAuthStore } from '@/store/auth'

vi.mock('@/store/auth', () => ({
  useAuthStore: vi.fn(),
}))

function mockUser(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({ user: { id: '1', tenantId: 'a', roleId }, accessToken: 'tok' })
  )
}

describe('Sidebar', () => {
  it('renders the app branding', () => {
    mockUser(2)
    render(<Sidebar />)
    expect(screen.getByText('7th AI Vision')).toBeInTheDocument()
  })

  it('admin sees Users and Settings in the admin section', () => {
    mockUser(2) // admin has user:read and settings:read
    render(<Sidebar />)
    expect(screen.getByText('Users')).toBeInTheDocument()
    expect(screen.getByText('Settings')).toBeInTheDocument()
  })

  it('viewer cannot see Users or Settings', () => {
    mockUser(6) // viewer has no user:read or settings:read
    render(<Sidebar />)
    expect(screen.queryByText('Users')).not.toBeInTheDocument()
    expect(screen.queryByText('Settings')).not.toBeInTheDocument()
  })

  it('viewer sees standard read-only nav items', () => {
    mockUser(6) // viewer has alert:read, camera:read, incident:read, evidence:read, audit:read, detection:read
    render(<Sidebar />)
    expect(screen.getByText('Alerts')).toBeInTheDocument()
    expect(screen.getByText('Cameras')).toBeInTheDocument()
    expect(screen.getByText('Audit Logs')).toBeInTheDocument()
    expect(screen.getByText('Evidence')).toBeInTheDocument()
  })

  it('viewer cannot see Watchlists or Zones', () => {
    mockUser(6) // viewer has no watchlist:manage or zone:manage
    render(<Sidebar />)
    expect(screen.queryByText('Watchlists')).not.toBeInTheDocument()
    expect(screen.queryByText('Zones')).not.toBeInTheDocument()
  })

  it('security_guard (roleId=5) sees Detections but not Audit Logs', () => {
    mockUser(5) // security_guard has detection:read but no audit:read
    render(<Sidebar />)
    expect(screen.getByText('Detections')).toBeInTheDocument()
    expect(screen.queryByText('Audit Logs')).not.toBeInTheDocument()
  })

  it('security_guard (roleId=5) sees Alerts and Evidence', () => {
    mockUser(5) // security_guard has alert:read and evidence:read
    render(<Sidebar />)
    expect(screen.getByText('Alerts')).toBeInTheDocument()
    expect(screen.getByText('Evidence')).toBeInTheDocument()
  })

  it('Dashboard is always visible regardless of role', () => {
    mockUser(6)
    render(<Sidebar />)
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
  })
})

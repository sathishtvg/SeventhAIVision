import { render, screen, fireEvent } from '@/test/utils'
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

/**
 * The sidebar folds every group by default and only auto-opens the one holding
 * the current route, so an item can be absent from the DOM for two completely
 * different reasons: the role may not have the permission, or its group may
 * simply be shut. A permission assertion has to open everything first,
 * otherwise "forbidden" and "folded" look identical — and the negative
 * assertions below would pass without proving anything at all.
 */
function expandAllSections() {
  // Looped rather than single-pass: expanding is what puts a group's items in
  // the DOM, so anything nested only becomes clickable on a later pass. The
  // bound stops a mis-wired toggle from spinning forever.
  for (let pass = 0; pass < 5; pass++) {
    const shut = screen
      .getAllByRole('button')
      .filter((b) => b.getAttribute('aria-expanded') === 'false')
    if (shut.length === 0) return
    shut.forEach((b) => fireEvent.click(b))
  }
}

function sectionHeaders() {
  return screen.getAllByRole('button').filter((b) => b.hasAttribute('aria-expanded'))
}

describe('Sidebar', () => {
  beforeEach(() => {
    // Open/closed state persists to localStorage, so without this each test
    // would inherit whatever the previous one expanded.
    localStorage.clear()
  })

  it('renders the app branding', () => {
    mockUser(2)
    render(<Sidebar />)
    expect(screen.getByText('7th AI Vision')).toBeInTheDocument()
  })

  it('admin sees Users and Settings in the admin section', () => {
    mockUser(2) // admin has user:read and settings:read
    render(<Sidebar />)
    expandAllSections()
    expect(screen.getByText('Users')).toBeInTheDocument()
    expect(screen.getByText('Settings')).toBeInTheDocument()
  })

  it('viewer cannot see Users or Settings', () => {
    mockUser(6) // viewer has no user:read or settings:read
    render(<Sidebar />)
    expandAllSections()
    expect(screen.queryByText('Users')).not.toBeInTheDocument()
    expect(screen.queryByText('Settings')).not.toBeInTheDocument()
  })

  it('viewer sees standard read-only nav items', () => {
    mockUser(6) // viewer has alert:read, camera:read, incident:read, evidence:read, audit:read, detection:read
    render(<Sidebar />)
    expandAllSections()
    expect(screen.getByText('Alerts')).toBeInTheDocument()
    expect(screen.getByText('Cameras')).toBeInTheDocument()
    expect(screen.getByText('Audit Logs')).toBeInTheDocument()
    expect(screen.getByText('Evidence')).toBeInTheDocument()
  })

  it('viewer cannot see Watchlists or Zones', () => {
    mockUser(6) // viewer has no watchlist:manage or zone:manage
    render(<Sidebar />)
    expandAllSections()
    expect(screen.queryByText('Watchlists')).not.toBeInTheDocument()
    expect(screen.queryByText('Zones')).not.toBeInTheDocument()
  })

  it('security_guard (roleId=5) sees Detections but not Audit Logs', () => {
    mockUser(5) // security_guard has detection:read but no audit:read
    render(<Sidebar />)
    expandAllSections()
    expect(screen.getByText('Detections')).toBeInTheDocument()
    expect(screen.queryByText('Audit Logs')).not.toBeInTheDocument()
  })

  it('security_guard (roleId=5) sees Alerts and Evidence', () => {
    mockUser(5) // security_guard has alert:read and evidence:read
    render(<Sidebar />)
    expandAllSections()
    expect(screen.getByText('Alerts')).toBeInTheDocument()
    expect(screen.getByText('Evidence')).toBeInTheDocument()
  })

  it('Dashboard is always visible regardless of role', () => {
    mockUser(6)
    render(<Sidebar />)
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
  })

  // The grouping itself, pinned. These two are what the permission tests above
  // silently depended on and never stated — when the flat menu became folding
  // groups, four of them broke and the rest started passing for the wrong
  // reason.
  it('folds every group except the one holding the current route', () => {
    mockUser(2)
    render(<Sidebar />)

    const headers = sectionHeaders()
    expect(headers.length).toBeGreaterThan(1)

    // MemoryRouter starts at "/", which is Dashboard, which lives in Monitoring.
    const open = headers.filter((b) => b.getAttribute('aria-expanded') === 'true')
    expect(open).toHaveLength(1)
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
  })

  it('a folded group hides its items until it is expanded', () => {
    mockUser(2)
    render(<Sidebar />)

    // Users lives in an admin group, which is shut on arrival.
    expect(screen.queryByText('Users')).not.toBeInTheDocument()
    expandAllSections()
    expect(screen.getByText('Users')).toBeInTheDocument()
    expect(sectionHeaders().every((b) => b.getAttribute('aria-expanded') === 'true')).toBe(true)
  })
})

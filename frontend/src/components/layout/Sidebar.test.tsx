import { render, screen, fireEvent } from '@/test/utils'
import { Sidebar } from '@/components/layout/Sidebar'
import { useAuthStore } from '@/store/auth'
import { PRODUCT_NAME } from '@/lib/brand'

vi.mock('@/store/auth', () => ({
  useAuthStore: vi.fn(),
}))

function mockUser(roleId: number) {
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({ user: { id: '1', tenantId: 'a', roleId }, accessToken: 'tok' })
  )
}

/** Super Admin, with the permission set migrations 0102-0103 actually give it.
 *  The permissions are supplied rather than left to the fallback matrix
 *  because the sidebar prefers the backend-fetched set, and that is what a
 *  real session carries. */
function mockPlatformOwner() {
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({
      user: { id: '1', tenantId: 'a', roleId: 1 },
      accessToken: 'tok',
      permissions: ['tenant:manage', 'license:manage', 'audit:read',
                    'support:manage', 'billing:read', 'billing:manage',
                    'platform:read'],
    })
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
    expect(screen.getByText(PRODUCT_NAME)).toBeInTheDocument()
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

  it('every tenant role sees a Dashboard', () => {
    // Was "always visible regardless of role", which stopped being true when
    // the platform owner got a sidebar of its own. Every role still lands on a
    // dashboard; the platform owner's is a different page answering a
    // different question, and the test below pins that.
    mockUser(6)
    render(<Sidebar />)
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
  })

  // ── The platform owner gets a different application (§25, §26) ────────────
  //
  // Not the tenant's sidebar with items removed. Filtering one to make the
  // other leaves headings like "Monitoring" over a single item, which reads as
  // an application that has lost most of itself — which is what a Super Admin
  // saw before the split.

  it('the platform owner sees the business nav, not the operational one', () => {
    mockPlatformOwner()
    render(<Sidebar />)
    expandAllSections()
    expect(screen.getByText('Tenant Usage')).toBeInTheDocument()
    expect(screen.getByText('Error Centre')).toBeInTheDocument()
    expect(screen.getByText('Support Sessions')).toBeInTheDocument()
  })

  it('the platform owner gets the billing section', () => {
    // Billing moved off Admin, Supervisor and Manager in migration 0103: the
    // Stripe tables describe what each CUSTOMER owes, which is the vendor's
    // side of the relationship.
    mockPlatformOwner()
    render(<Sidebar />)
    expandAllSections()
    expect(screen.getByText('Plans & Pricing')).toBeInTheDocument()
    expect(screen.getByText('Invoices')).toBeInTheDocument()
  })

  it('the platform owner is not given the customer operational pages', () => {
    // A camera going offline at a guarded warehouse is that company's
    // emergency and none of the vendor's.
    mockPlatformOwner()
    render(<Sidebar />)
    expandAllSections()
    for (const label of ['Action Center', 'Live Wall', 'Alerts', 'Incidents',
                         'Roster', 'Payroll', 'Key Register']) {
      expect(screen.queryByText(label), label).not.toBeInTheDocument()
    }
  })

  it('a tenant admin is not given the platform nav', () => {
    mockUser(2)
    render(<Sidebar />)
    expandAllSections()
    expect(screen.queryByText('Tenant Usage')).not.toBeInTheDocument()
    expect(screen.queryByText('Error Centre')).not.toBeInTheDocument()
    expect(screen.queryByText('Plans & Pricing')).not.toBeInTheDocument()
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

import { render, screen } from '@/test/utils'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { useAuthStore } from '@/store/auth'

vi.mock('@/store/auth', () => ({
  useAuthStore: vi.fn(),
}))

function mockUser(roleId: number | null) {
  vi.mocked(useAuthStore).mockImplementation((sel: any) =>
    sel({
      user: roleId !== null ? { id: '1', tenantId: 'a', roleId } : null,
      accessToken: roleId !== null ? 'tok' : null,
    })
  )
}

beforeEach(() => {
  mockUser(2) // default: admin
})

describe('PermissionGuard', () => {
  it('renders children when user has the required permission', () => {
    mockUser(2) // admin has user:create
    render(
      <PermissionGuard permission="user:create">
        <span>admin content</span>
      </PermissionGuard>
    )
    expect(screen.getByText('admin content')).toBeInTheDocument()
  })

  it('renders nothing when user lacks the required permission', () => {
    mockUser(6) // viewer has no user:create
    render(
      <PermissionGuard permission="user:create">
        <span>admin content</span>
      </PermissionGuard>
    )
    expect(screen.queryByText('admin content')).not.toBeInTheDocument()
  })

  it('renders fallback when user lacks the required permission', () => {
    mockUser(6)
    render(
      <PermissionGuard permission="user:create" fallback={<span>no access</span>}>
        <span>admin content</span>
      </PermissionGuard>
    )
    expect(screen.queryByText('admin content')).not.toBeInTheDocument()
    expect(screen.getByText('no access')).toBeInTheDocument()
  })

  it('renders nothing when not logged in', () => {
    mockUser(null)
    render(
      <PermissionGuard permission="alert:read">
        <span>protected content</span>
      </PermissionGuard>
    )
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('viewer can read alerts', () => {
    mockUser(6) // viewer has alert:read
    render(
      <PermissionGuard permission="alert:read">
        <span>alert list</span>
      </PermissionGuard>
    )
    expect(screen.getByText('alert list')).toBeInTheDocument()
  })

  it('viewer cannot acknowledge alerts', () => {
    mockUser(6) // viewer has no alert:acknowledge
    render(
      <PermissionGuard permission="alert:acknowledge">
        <button>Ack</button>
      </PermissionGuard>
    )
    expect(screen.queryByRole('button', { name: /ack/i })).not.toBeInTheDocument()
  })
})

import { renderHook } from '@testing-library/react'
import { usePermission } from '@/hooks/usePermission'
import { useAuthStore } from '@/store/auth'

function setUser(roleId: number | null) {
  // permissions: null forces usePermission down its hardcoded-matrix fallback,
  // which is what these built-in-role tests exercise (Gap 91).
  useAuthStore.setState(
    roleId !== null
      ? { user: { id: '1', tenantId: 'a', roleId }, accessToken: 'tok', permissions: null }
      : { user: null, accessToken: null, permissions: null }
  )
}

beforeEach(() => {
  setUser(null)
})

describe('usePermission', () => {
  it('returns false when not logged in', () => {
    const { result } = renderHook(() => usePermission('alert:read'))
    expect(result.current).toBe(false)
  })

  // Super Admin used to hold all 142 permissions against Admin's 140, which
  // made the platform operator a tenant administrator with two extra switches.
  // Migration 0102 cut it to the four that are actually the platform's job;
  // reading a customer's data is a support session now, not a standing grant.
  it('super_admin (roleId=1) runs the platform', () => {
    setUser(1)
    for (const code of ['tenant:manage', 'license:manage', 'audit:read', 'support:manage',
                        'billing:read', 'billing:manage', 'platform:read']) {
      const { result } = renderHook(() => usePermission(code))
      expect(result.current, code).toBe(true)
    }
  })

  it("super_admin (roleId=1) is not the customer's administrator", () => {
    setUser(1)
    // Rosters, payroll and HR records belong to the tenant that employs those
    // guards. role:manage goes with them: custom roles are per-tenant.
    for (const code of ['role:manage', 'user:read', 'payroll:read', 'shift:read']) {
      const { result } = renderHook(() => usePermission(code))
      expect(result.current, code).toBe(false)
    }
  })

  it('only super_admin may open a support session', () => {
    setUser(2)
    const { result } = renderHook(() => usePermission('support:manage'))
    expect(result.current).toBe(false)
  })

  it('billing belongs to the vendor, not the customer', () => {
    // Migration 0103. billing:read and billing:manage were on Admin,
    // Supervisor and Manager — so a security company could change its own
    // subscription to Seventh AI, and Seventh AI could see none of it.
    setUser(2)
    for (const code of ['billing:read', 'billing:manage']) {
      const { result } = renderHook(() => usePermission(code))
      expect(result.current, code).toBe(false)
    }
    setUser(1)
    for (const code of ['billing:read', 'billing:manage']) {
      const { result } = renderHook(() => usePermission(code))
      expect(result.current, code).toBe(true)
    }
  })

  it('admin (roleId=2) can manage roles (custom roles — Gap 91)', () => {
    setUser(2)
    const { result } = renderHook(() => usePermission('role:manage'))
    expect(result.current).toBe(true)
  })

  it('admin (roleId=2) still cannot manage tenants (super-admin only)', () => {
    setUser(2)
    const { result } = renderHook(() => usePermission('tenant:manage'))
    expect(result.current).toBe(false)
  })

  it('admin (roleId=2) can create users', () => {
    setUser(2)
    const { result } = renderHook(() => usePermission('user:create'))
    expect(result.current).toBe(true)
  })

  it('viewer (roleId=6) can read alerts', () => {
    setUser(6)
    const { result } = renderHook(() => usePermission('alert:read'))
    expect(result.current).toBe(true)
  })

  it('viewer (roleId=6) cannot acknowledge alerts', () => {
    setUser(6)
    const { result } = renderHook(() => usePermission('alert:acknowledge'))
    expect(result.current).toBe(false)
  })

  it('operator (roleId=4) can acknowledge alerts', () => {
    setUser(4)
    const { result } = renderHook(() => usePermission('alert:acknowledge'))
    expect(result.current).toBe(true)
  })

  it('operator (roleId=4) cannot manage watchlists', () => {
    setUser(4)
    const { result } = renderHook(() => usePermission('watchlist:manage'))
    expect(result.current).toBe(false)
  })

  it('supervisor (roleId=3) can manage watchlists and zones', () => {
    setUser(3)
    const watchlistHook = renderHook(() => usePermission('watchlist:manage'))
    const zoneHook = renderHook(() => usePermission('zone:manage'))
    expect(watchlistHook.result.current).toBe(true)
    expect(zoneHook.result.current).toBe(true)
  })

  it('security_guard (roleId=5) can read cameras but not manage watchlists', () => {
    setUser(5)
    const cameraHook = renderHook(() => usePermission('camera:read'))
    const watchlistHook = renderHook(() => usePermission('watchlist:manage'))
    expect(cameraHook.result.current).toBe(true)
    expect(watchlistHook.result.current).toBe(false)
  })

  it('returns false for an unknown permission code even for super_admin', () => {
    setUser(1)
    const { result } = renderHook(() => usePermission('nonexistent:permission'))
    expect(result.current).toBe(false)
  })
})

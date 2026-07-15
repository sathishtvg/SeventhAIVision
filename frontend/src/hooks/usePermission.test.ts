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

  it('super_admin (roleId=1) has all permissions including role:manage', () => {
    setUser(1)
    const { result } = renderHook(() => usePermission('role:manage'))
    expect(result.current).toBe(true)
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

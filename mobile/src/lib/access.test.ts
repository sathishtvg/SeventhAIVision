/**
 * Who sees which screen.
 *
 * The real permission sets below are copied from the Demo database, because the
 * case that started this only appears with real data: a guard HOLDS
 * attendance:read, so a test using an invented "guard has nothing" set would
 * pass against a permission-only gate that still shows Live Attendance on every
 * guard's phone.
 */
import { canSee, isFieldRole, visible, FIELD_ROLES } from './access'

// Security Guard (role 5), abridged — every code used by a gated menu row.
const GUARD = [
  'access:read', 'alarm:read', 'alert:read', 'attendance:read', 'broadcast:read',
  'bwc:read', 'camera:read', 'compliance:read', 'contractor:read', 'defect:read',
  'detection:read', 'equipment:read', 'evidence:read', 'gps:read', 'handover:read',
  'incident:read', 'iot:read', 'keyreg:read', 'lostfound:read', 'parking:read',
  'patrol:read', 'shift:read', 'training:read', 'violation:read', 'visitor:read',
  'vpatrol:execute',
]
// Admin (role 2) holds all of the above plus the manage/write codes.
const ADMIN = [
  ...GUARD, 'attendance:manage', 'notification:manage', 'settings:read',
  'settings:write', 'watchlist:manage', 'report:schedule', 'gps:write',
  'alarm:arm', 'bwc:write', 'access:write', 'contractor:write', 'parking:manage',
  'compliance:manage', 'iot:write',
]

const GUARD_ROLE = 5
const ADMIN_ROLE = 2

const LIVE_ATTENDANCE = { permission: 'attendance:read', audience: 'ops' as const }
const MY_SCHEDULE = { permission: 'shift:read' }
const SETTINGS = { permission: 'settings:read', audience: 'ops' as const }

describe('field roles', () => {
  it('matches the backend, which treats Operator and Security Guard as field', () => {
    // attendance.py and leave.py both use {4, 5}. If that changes, this should.
    expect([...FIELD_ROLES].sort()).toEqual([4, 5])
  })

  it('does not count a supervisor or admin as field', () => {
    expect(isFieldRole(3)).toBe(false)
    expect(isFieldRole(ADMIN_ROLE)).toBe(false)
    expect(isFieldRole(GUARD_ROLE)).toBe(true)
  })
})

describe('the case this was built for', () => {
  it('hides Live Attendance from a guard who holds the permission for it', () => {
    // THE WHOLE POINT. The guard can read attendance — the server would answer.
    // A permission-only gate leaves this row on a guard's phone, which is the
    // bug that was reported. Only the audience mark removes it.
    expect(GUARD).toContain('attendance:read')
    expect(canSee(LIVE_ATTENDANCE, GUARD, GUARD_ROLE)).toBe(false)
  })

  it('still shows Live Attendance to an admin', () => {
    expect(canSee(LIVE_ATTENDANCE, ADMIN, ADMIN_ROLE)).toBe(true)
  })
})

describe('permission gate', () => {
  it('shows a screen the user holds the permission for', () => {
    expect(canSee(MY_SCHEDULE, GUARD, GUARD_ROLE)).toBe(true)
  })

  it('hides a screen the server would refuse', () => {
    expect(GUARD).not.toContain('settings:read')
    expect(canSee(SETTINGS, GUARD, GUARD_ROLE)).toBe(false)
  })

  it('shows an ungated row to everyone', () => {
    expect(canSee({}, GUARD, GUARD_ROLE)).toBe(true)
    expect(canSee({}, null, GUARD_ROLE)).toBe(true)
  })
})

describe('before permissions have loaded', () => {
  it('passes permission checks, so a guard keeps their own tools offline', () => {
    // Fail-open on the network-dependent gate: this is what the app did before
    // the gate existed, so a failed fetch is no worse than the old behaviour.
    expect(canSee(MY_SCHEDULE, null, GUARD_ROLE)).toBe(true)
    expect(canSee(SETTINGS, null, ADMIN_ROLE)).toBe(true)
  })

  it('still hides ops screens, because the role is in the token already', () => {
    // The audience gate needs no network, so the guard's menu is right from the
    // first frame rather than flickering a roll-call screen into view.
    expect(canSee(LIVE_ATTENDANCE, null, GUARD_ROLE)).toBe(false)
    expect(canSee(SETTINGS, null, GUARD_ROLE)).toBe(false)
  })

  it('shows ops screens to a non-field role while loading', () => {
    expect(canSee(LIVE_ATTENDANCE, null, ADMIN_ROLE)).toBe(true)
  })
})

describe('visible()', () => {
  const MENU = [
    { key: 'my-record', permission: 'violation:read' },
    { key: 'live-attendance', ...LIVE_ATTENDANCE },
    { key: 'my-schedule', ...MY_SCHEDULE },
    { key: 'settings', ...SETTINGS },
    { key: 'action-center' },
  ]

  it('keeps a guard to their own work, in the original order', () => {
    expect(visible(MENU, GUARD, GUARD_ROLE).map((i) => i.key))
      .toEqual(['my-record', 'my-schedule', 'action-center'])
  })

  it('hides nothing from an admin', () => {
    expect(visible(MENU, ADMIN, ADMIN_ROLE)).toHaveLength(MENU.length)
  })

  it('hides nothing from a signed-out state it cannot judge', () => {
    // roleId null (no token yet) must not blank the menu.
    expect(visible(MENU, null, null)).toHaveLength(MENU.length)
  })
})

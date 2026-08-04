import { useAuthStore } from '@/store/auth'

// Mirrors backend DB role_permissions seed data (migrations 0001-0012)
const ALL_PERMISSIONS = [
  'camera:create', 'camera:read', 'camera:update', 'camera:delete',
  'user:create', 'user:read', 'user:update', 'user:delete',
  'role:manage',
  'tenant:manage',
  'settings:read', 'settings:write',
  'notification:manage',
  'watchlist:manage',
  'zone:manage',
  'site:read', 'site:manage',
  'recording:create', 'recording:read',
  'license:manage',
  'alert:read', 'alert:acknowledge',
  'incident:read', 'incident:create', 'incident:update', 'incident:resolve', 'incident:assign',
  'incident:dispatch',
  'evidence:read', 'evidence:custody:read',
  'audit:read',
  'detection:read',
  // Guard operations (migration 0008)
  'shift:read', 'shift:manage',
  'patrol:read', 'patrol:manage', 'patrol:scan',
  'dob:read', 'dob:write',
  'guard:sos',
  // Attendance hardening (migration 0061 — ShiftSecure Phase 2A)
  'attendance:read', 'attendance:request', 'attendance:manage',
  // Roster rebuild — AI auto-scheduler (migration 0062 — ShiftSecure Phase 2B)
  'roster:autoschedule',
  // Violations (migration 0065 — ShiftSecure Phase 3)
  'violation:read', 'violation:manage',
  // Leave Management (migration 0066 — ShiftSecure Phase 4)
  'leave:read', 'leave:request', 'leave:manage',
  // Payroll / CPF / IR8A (migration 0067 — ShiftSecure Phase 5)
  'payroll:read', 'payroll:manage',
  // Client Billing / Invoicing (migration 0069 — ShiftSecure final phase)
  'invoicing:read', 'invoicing:manage',
  // Guard Training (migration 0031) — pre-existing in the DB but missing
  // here until now, which made the "Guard Training" sidebar link
  // invisible to every role since usePermission() always returned false.
  'training:read', 'training:write', 'training:manage',
  // Dispatch / SLA (migration 0009)
  'sla:manage',
  // Visitor management (migration 0010)
  'visitor:read', 'visitor:manage', 'visitor:checkin',
  // Privacy / PDPA (migration 0010)
  'privacy:manage', 'pdpa:admin', 'pdpa:read',
  // Advanced AI (migration 0011)
  'tampering:read', 'abandoned:read', 'fall:read',
  // Tier 2 (migrations 0014-0019)
  'apikey:manage',
  'iplist:manage',
  'audit:verify',
  '2fa:policy',
  'alert:dedup:manage',
  'alert:create',
  // Scheduled reports (migration 0020)
  'report:schedule',
  // 2FA management + push notification registration
  '2fa:manage',
  'push:register',
  // Client portal
  'portal:view',
  // Barrier / gate actuation (migration 0076). read is broad — a guard should
  // be able to see whether the gate is open; operate is the command-centre
  // action; manage covers device credentials and auto-open policy.
  'barrier:read',
  'barrier:operate',
  'barrier:manage',
]

const ROLE_PERMISSIONS: Record<number, string[]> = {
  1: ALL_PERMISSIONS, // super_admin — everything
  // admin — everything except cross-tenant super-admin powers (role:manage is
  // granted to admin as of migration 0057 so they can manage custom roles)
  2: ALL_PERMISSIONS.filter((p) => !['tenant:manage', 'license:manage'].includes(p)),
  // manager (migration 0063) — same grant set as admin; mirrors the DB seed
  // that clones role 2's role_permissions rows for role 8.
  8: ALL_PERMISSIONS.filter((p) => !['tenant:manage', 'license:manage'].includes(p)),
  3: [ // supervisor
    'camera:read', 'camera:update',
    'user:read',
    'settings:read',
    'watchlist:manage', 'zone:manage',
    'site:read', 'site:manage',
    'recording:create', 'recording:read',
    'alert:read', 'alert:acknowledge', 'alert:create',
    'incident:read', 'incident:create', 'incident:update', 'incident:resolve', 'incident:assign', 'incident:dispatch',
    'evidence:read', 'evidence:custody:read', 'audit:read', 'detection:read',
    'shift:read', 'shift:manage',
    'patrol:read', 'patrol:manage', 'patrol:scan',
    'dob:read', 'dob:write',
    'guard:sos',
    'attendance:read', 'attendance:manage', 'attendance:request',
    'roster:autoschedule',
    'violation:read', 'violation:manage',
    'leave:read', 'leave:manage', 'leave:request',
    'payroll:read',
    'invoicing:read',
    'training:read', 'training:write',
    'sla:manage',
    'barrier:read', 'barrier:operate',
    'visitor:read', 'visitor:manage', 'visitor:checkin',
    'privacy:manage', 'pdpa:admin', 'pdpa:read',
    'tampering:read', 'abandoned:read', 'fall:read',
  ],
  4: [ // operator
    'camera:read',
    'site:read',
    'recording:create', 'recording:read',
    'alert:read', 'alert:acknowledge',
    'incident:read', 'incident:create', 'incident:update', 'incident:dispatch',
    'evidence:read', 'detection:read',
    'shift:read', 'shift:manage',
    'patrol:read', 'patrol:manage', 'patrol:scan',
    'dob:read', 'dob:write',
    'guard:sos',
    'attendance:read', 'attendance:request',
    'violation:read', 'violation:manage',
    'leave:read', 'leave:request',
    'payroll:read',
    'training:read',
    'visitor:read', 'visitor:manage', 'visitor:checkin',
    'pdpa:read',
    'tampering:read', 'abandoned:read', 'fall:read',
    'barrier:read', 'barrier:operate',
  ],
  5: [ // security_guard
    'camera:read',
    'site:read',
    'recording:read',
    'alert:read', 'alert:acknowledge',
    'incident:read', 'incident:create',
    'evidence:read', 'detection:read',
    'shift:read',
    'patrol:read', 'patrol:scan',
    'dob:read', 'dob:write',
    'guard:sos',
    'attendance:read', 'attendance:request',
    'violation:read',
    'leave:read', 'leave:request',
    'payroll:read',
    'training:read',
    'visitor:checkin',
    'barrier:read',
  ],
  6: [ // viewer
    'camera:read',
    'site:read',
    'recording:read',
    'alert:read',
    'incident:read',
    'evidence:read',
    'audit:read',
    'detection:read',
    'shift:read',
    'patrol:read',
    'dob:read',
    'attendance:read',
    'violation:read',
    'leave:read',
    'payroll:read',
    'training:read',
    'visitor:read',
    'pdpa:read',
    'barrier:read',
  ],
  7: [ // client — building owner / third-party, read-only portal
    'alert:read',
    'incident:read',
    'camera:read',
    'site:read',
    'recording:read',
    'evidence:read',
    'detection:read',
    'dob:read',
    'pdpa:read',
    'tampering:read', 'abandoned:read', 'fall:read',
    'invoicing:read',
    'portal:view',
  ],
}

export function usePermission(code: string): boolean {
  // Prefer the backend-fetched permission set (authoritative, and the only way
  // to know a custom role's permissions — Gap 91). Fall back to the hardcoded
  // built-in matrix while the fetch is pending or unavailable.
  const fetched = useAuthStore((s) => s.permissions)
  const roleId = useAuthStore((s) => s.user?.roleId)
  if (fetched != null) return fetched.includes(code)
  if (roleId == null) return false
  return ROLE_PERMISSIONS[roleId]?.includes(code) ?? false
}

export function getPermissionsForRole(roleId: number): string[] {
  return ROLE_PERMISSIONS[roleId] ?? []
}

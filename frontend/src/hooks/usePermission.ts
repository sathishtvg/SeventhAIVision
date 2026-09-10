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
  'recording_policy:read', 'recording_policy:manage',
  // Configurable alert rules (migration 0080) and hardware protocol config (0081)
  'alert_rule:read', 'alert_rule:manage',
  'device_config:read', 'device_config:manage',
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

  // Operational modules whose codes existed in the database but were never
  // mirrored here. The matrix is the fallback when the backend permission
  // fetch hasn't landed, so drift silently hid these features.
  'access:ingest',
  'access:read',
  'access:write',
  'alarm:arm',
  'alarm:manage',
  'alarm:read',
  'billing:manage',
  'billing:read',
  'broadcast:acknowledge',
  'broadcast:read',
  'broadcast:send',
  'bwc:manage',
  'bwc:read',
  'bwc:write',
  'compliance:manage',
  'compliance:read',
  'contractor:approve',
  'contractor:read',
  'contractor:write',
  'gps:ingest',
  'gps:read',
  'gps:write',
  'handover:create',
  'handover:read',
  'i18n:manage',
  'iot:ingest',
  'iot:read',
  'iot:write',
  'parking:manage',
  'parking:read',
  'parking:write',
  'scan:create',
  'scim:manage',
  'session:manage',
  'sso:manage',
  'webhook:manage',
  'webhook:read',
  'support:manage',
  'platform:read',
]

/** The platform operator's whole job (migration 0102). Super Admin used to hold
 *  all 142 permissions against Admin's 140, which made it a tenant
 *  administrator with two extra switches rather than a role of its own — and
 *  put a customer's rosters, payroll and employment records in front of
 *  somebody whose work never needs them. Reading a tenant's data is now a
 *  support session: deliberate, time-boxed and written into that customer's
 *  own audit log. */
const PLATFORM_PERMISSIONS = [
  'tenant:manage',
  'license:manage',
  'audit:read',
  'support:manage',
  // Moved off Admin/Supervisor/Manager in 0103. The Stripe tables are keyed by
  // tenant_id — they describe what each customer owes the vendor, which is the
  // vendor's business, not the customer's.
  'billing:read',
  'billing:manage',
  // Cross-tenant platform figures: usage, adoption, errors.
  'platform:read',
  // Not platform powers but the way out of one: the MFA requirement withholds
  // everything above until the owner enrols, and enrolling needs these.
  '2fa:manage',
  '2fa:policy',
]

const ROLE_PERMISSIONS: Record<number, string[]> = {
  // super_admin — the platform, and nothing of the customer's. Mirrors the DB;
  // this matrix is only the fallback while /me/permissions is in flight, and a
  // fallback that disagreed would flash the whole operational nav on every
  // page load before removing it again.
  1: PLATFORM_PERMISSIONS,
  // admin — everything except cross-tenant super-admin powers (role:manage is
  // granted to admin as of migration 0057 so they can manage custom roles)
  2: ALL_PERMISSIONS.filter((p) => !['tenant:manage', 'license:manage', 'support:manage',
     'platform:read', 'billing:read', 'billing:manage'].includes(p)),
  // manager (migration 0063) — same grant set as admin; mirrors the DB seed
  // that clones role 2's role_permissions rows for role 8.
  8: ALL_PERMISSIONS.filter((p) => !['tenant:manage', 'license:manage', 'support:manage',
     'platform:read', 'billing:read', 'billing:manage'].includes(p)),
  3: [ // supervisor
    'alert_rule:read',
    'camera:read', 'camera:update',
    'user:read',
    'settings:read',
    'watchlist:manage', 'zone:manage',
    'site:read', 'site:manage',
    'recording:create', 'recording:read',
    // Read but not manage — a supervisor should know what retention their site
    // is under without being able to shorten it. Mirrors migration 0079's grant.
    'recording_policy:read',
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
    'access:ingest',
    'access:read',
    'access:write',
    'alarm:arm',
    'alarm:manage',
    'alarm:read',
    'billing:read',
    'broadcast:acknowledge',
    'broadcast:read',
    'broadcast:send',
    'bwc:manage',
    'bwc:read',
    'bwc:write',
    'compliance:manage',
    'compliance:read',
    'contractor:approve',
    'contractor:read',
    'contractor:write',
    'gps:ingest',
    'gps:read',
    'gps:write',
    'handover:create',
    'handover:read',
    'iot:ingest',
    'iot:read',
    'iot:write',
    'parking:manage',
    'parking:read',
    'parking:write',
    'scan:create',
    'webhook:manage',
    'webhook:read',
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
    'access:ingest',
    'access:read',
    'alarm:arm',
    'alarm:read',
    'broadcast:acknowledge',
    'broadcast:read',
    'bwc:read',
    'bwc:write',
    'compliance:read',
    'contractor:read',
    'contractor:write',
    'gps:ingest',
    'gps:read',
    'handover:read',
    'iot:ingest',
    'iot:read',
    'parking:read',
    'parking:write',
    'scan:create',
    'webhook:read',
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
    'access:read',
    'alarm:read',
    'broadcast:acknowledge',
    'broadcast:read',
    'bwc:read',
    'compliance:read',
    'contractor:read',
    'gps:read',
    'iot:read',
    'parking:read',
    'scan:create',
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
    'access:read',
    'alarm:read',
    'broadcast:acknowledge',
    'broadcast:read',
    'bwc:read',
    'compliance:read',
    'contractor:read',
    'gps:read',
    'iot:read',
    'parking:read',
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
    'broadcast:acknowledge',
    'broadcast:read',
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

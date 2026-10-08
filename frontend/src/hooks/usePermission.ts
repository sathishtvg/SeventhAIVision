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
  // Drone Patrol (migration 0123)
  'drone:read', 'drone:create', 'drone:update', 'drone:delete', 'drone:operate',
  'drone:mission:create', 'drone:mission:update', 'drone:mission:execute', 'drone:mission:abort',
  'drone:event:read', 'drone:event:acknowledge', 'drone:event:investigate',
  'drone:maintenance:read', 'drone:maintenance:manage', 'drone:report:read', 'drone:report:export',
  // AI Security Intelligence (migration 0132). The platform owner and the
  // client role hold none of these.
  'intel:read', 'intel:recommendation:read', 'intel:decide', 'intel:override', 'intel:approve', 'intel:manage',
  'intel:feedback:export',
  // Smart Investigation (migration 0143). The platform owner, the client role
  // and guards hold neither.
  'investigation:read', 'investigation:manage',
  // Evidence packages (migration 0144). The platform owner, the client role
  // and guards hold none.
  'evidence:package:read', 'evidence:package:manage', 'evidence:package:export', 'evidence:hold:manage',
  // The security map (migration 0145). The platform owner, the client role
  // and guards hold neither.
  'sitemap:read', 'sitemap:manage',
  // Guard response (migration 0146). The platform owner and the client role
  // hold neither; a guard answers for a dispatch and does not read the desk.
  'response:read', 'response:act',
  // Reviewing the occurrence book (migration 0147): admin, manager and supervisor.
  'dob:review',
  // The SOP library (migration 0148). The platform owner and the client role hold none.
  'sop:read', 'sop:write', 'sop:approve',
  // Visitor and contractor authorisation (migration 0149). The platform owner and the client role hold none.
  'visitorauth:read', 'visitorauth:write', 'visitorauth:manage',
  // Device health, the asset register and maintenance (migration 0150). The platform owner, a guard and the
  // client role hold none.
  'asset:read', 'asset:manage', 'maintenance:read', 'maintenance:manage',
  // Risk patterns and advice (migration 0151). The platform owner, a guard and the client role hold neither.
  'advice:read', 'advice:answer',
  // The operations board and the daily briefing (migration 0152). The platform owner, a guard and the client role hold none.
  'board:read', 'briefing:read', 'briefing:manage',
  // Taking an operations report out as a file (migration 0153). Admin, manager and supervisor only.
  'opsreport:export',
  // Workforce readings and recommendations (migration 0154). The platform owner, a viewer and the client role hold none.
  'workforce:read', 'workforce:answer', 'workforce:own',
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
    // Drone Patrol (migration 0123)
    'drone:read', 'drone:operate', 'drone:mission:create', 'drone:mission:update', 'drone:mission:execute',
    'drone:mission:abort', 'drone:event:read', 'drone:event:acknowledge', 'drone:event:investigate',
    'drone:maintenance:read', 'drone:report:read', 'drone:report:export',
    // AI Security Intelligence (migration 0132)
    'intel:read', 'intel:recommendation:read', 'intel:decide', 'intel:override', 'intel:approve',
    'investigation:read', 'investigation:manage', // Smart Investigation (0143)
    // Evidence packages (0144): puts together, seals and exports; does not lift a hold
    'evidence:package:read', 'evidence:package:manage', 'evidence:package:export',
    'sitemap:read', // The security map (0145): reads; does not draw places
    'response:read', 'response:act', // Guard response (0146)
    'dob:review', // The occurrence book (0147): reviews entries, issues and closes instructions
    'sop:read', 'sop:write', // The SOP library (0148): writes procedures; does not approve them
    'visitorauth:read', 'visitorauth:write', 'visitorauth:manage', // Visitor authorisation (0149)
    'asset:read', 'asset:manage', 'maintenance:read', 'maintenance:manage', // Assets and maintenance (0150)
    'advice:read', 'advice:answer', // Risk and advice (0151): reads it, and answers it
    'board:read', 'briefing:read', 'briefing:manage', // Operations board and briefing (0152): reads; drafts and publishes
    'opsreport:export', // Operations reports (0153): takes a report out as a file
    'workforce:read', 'workforce:answer', 'workforce:own', // Workforce readings (0154): reads, answers; and their own
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
    // Drone Patrol (migration 0123)
    'drone:read', 'drone:operate', 'drone:mission:execute', 'drone:mission:abort', 'drone:event:read',
    'drone:event:acknowledge', 'drone:event:investigate', 'drone:report:read',
    'intel:read', 'intel:recommendation:read', 'intel:decide', 'intel:override', // AI Security Intelligence (0132)
    'investigation:read', 'investigation:manage', // Smart Investigation (0143)
    'evidence:package:read', 'evidence:package:manage', // Evidence packages (0144): puts together and seals
    'sitemap:read', // The security map (0145)
    'response:read', 'response:act', // Guard response (0146)
    'sop:read', // The SOP library (0148)
    'visitorauth:read', 'visitorauth:write', // Visitor authorisation (0149): asks; answers as a host
    'asset:read', 'maintenance:read', // Assets and maintenance (0150): reads; works the orders they are given
    'advice:read', // Risk and advice (0151): reads
    'board:read', 'briefing:read', // Operations board and briefing (0152): reads
    'workforce:own', // Workforce readings (0154): their own reading, nobody else's
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
    'drone:event:read', // Drone Patrol (migration 0123)
    // AI Security Intelligence (0132). Deciding still needs the decision
    // policy to let a guard decide; by default it does not.
    'intel:read', 'intel:recommendation:read', 'intel:decide',
    'response:act', // Guard response (0146): answers for a dispatch they were sent on
    'sop:read', // The SOP library (0148)
    'visitorauth:read', 'visitorauth:write', // Visitor authorisation (0149): asks at the gate; records the ID seen
    'workforce:own', // Workforce readings (0154): their own reading, nobody else's
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
    'drone:read', 'drone:event:read', 'drone:report:read', // Drone Patrol (migration 0123)
    'intel:read', // AI Security Intelligence (0132): situations and assessments, not the suggestions
    'investigation:read', // Smart Investigation (0143): searches and reads, files nothing
    'evidence:package:read', // Evidence packages (0144): reads
    'sitemap:read', // The security map (0145)
    'response:read', // Guard response (0146): reads the desk
    'sop:read', // The SOP library (0148)
    'visitorauth:read', // Visitor authorisation (0149): reads; answers only as a host
    'asset:read', 'maintenance:read', // Assets and maintenance (0150): reads
    'advice:read', // Risk and advice (0151): reads
    'board:read', 'briefing:read', // Operations board and briefing (0152): reads
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

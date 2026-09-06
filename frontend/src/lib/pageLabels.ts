/**
 * Every page's name and purpose, in one place.
 *
 * Two reasons this is a registry rather than props scattered across 60 files:
 * a reader can see the whole product's vocabulary at once and keep it
 * consistent, and a tenant can override any entry without us shipping a
 * build. Overrides live in tenant_settings under "ui.page_labels" as
 * {pageKey: {title?, subtitle?}} — only what was actually changed is stored,
 * so a tenant who renames one page still receives improved wording on every
 * other page.
 *
 * Subtitles say what the page is FOR in the reader's terms. "Detections —
 * everything the AI recognised, before any alert rule was applied" tells a
 * security manager something; "Detections — view detections" does not.
 */
export interface PageLabel {
  title: string
  subtitle: string
}

export const PAGE_LABELS = {
  // ── Monitoring ────────────────────────────────────────────────────────
  dashboard: { title: 'Dashboard', subtitle: 'Today at a glance — activity, open work and system health' },
  'command-centre': { title: 'Command Centre', subtitle: 'Live operational picture across every site you cover' },
  'action-center': { title: 'Action Center', subtitle: 'What needs your attention right now, ranked by urgency' },
  alerts: { title: 'Alerts', subtitle: 'AI detections that crossed a rule and need a person to confirm or dismiss' },
  incidents: { title: 'Incidents', subtitle: 'Confirmed events being worked through to resolution, with notes and ownership' },
  cameras: { title: 'Cameras', subtitle: 'Every camera, its streams, health and which AI modules run on it' },
  'live-wall': { title: 'Live Wall', subtitle: 'Multi-camera video wall for continuous monitoring' },
  alarms: { title: 'Alarm Panel Integration', subtitle: 'Third-party intruder and fire panels feeding events into the platform' },

  // ── Investigate ───────────────────────────────────────────────────────
  detections: { title: 'Detections', subtitle: 'Everything the AI recognised, before any alert rule was applied' },
  evidence: { title: 'Evidence', subtitle: 'Snapshots and clips saved against detections and incidents, each with an integrity checksum' },
  recordings: { title: 'Recordings', subtitle: 'Continuous footage held per your retention policy' },
  playback: { title: 'Playback', subtitle: 'Scrub a day of recorded footage with alert markers on the timeline' },
  analytics: { title: 'Analytics', subtitle: 'Detection and alert volume over time — by module, by camera and by period' },
  heatmap: { title: 'Activity Heatmap', subtitle: 'Where activity and alerts are concentrated across your sites' },
  map: { title: 'Site Map', subtitle: 'Your sites and cameras on a map, with live status' },

  // ── Guard operations ──────────────────────────────────────────────────
  'guard-ops': { title: 'Guard Operations', subtitle: 'Shift, patrol and occurrence-book activity for guards currently on duty' },
  attendance: { title: 'Attendance', subtitle: 'Who is on shift right now, who is late, and who needs chasing' },
  roster: { title: 'Roster', subtitle: 'Who is scheduled where, and auto-scheduling that respects rest and leave' },
  violations: { title: 'Violations', subtitle: 'Attendance and conduct issues logged against guards, with points' },
  leave: { title: 'Leave', subtitle: 'Requests, approvals and remaining balances per guard' },
  training: { title: 'Guard Training & Certifications', subtitle: 'Courses, quizzes and certification expiry for your officers' },
  compliance: { title: 'Guard Tour Compliance', subtitle: 'Whether patrols are actually being walked, per route and per guard' },
  'post-orders': { title: 'Post Orders', subtitle: 'Site-specific standing instructions guards can read on duty' },

  // ── People & money ────────────────────────────────────────────────────
  users: { title: 'Users', subtitle: 'Staff accounts, roles, employment details, pay rates and site access' },
  roles: { title: 'Roles', subtitle: 'What each role is allowed to see and do' },
  payroll: { title: 'Payroll', subtitle: 'Pay runs with CPF and overtime calculated from real worked hours' },
  invoicing: { title: 'Client Invoicing', subtitle: 'Bill your clients for guard hours delivered at their sites' },
  sites: { title: 'Sites', subtitle: 'The premises you protect — geofence, billing and recording policy per site' },
  contractors: { title: 'Contractors', subtitle: 'Third-party workers on site, and their document expiry' },

  // ── Access & vehicles ─────────────────────────────────────────────────
  'access-control': { title: 'Access Control', subtitle: 'Doors and readers — who may enter which area, and when' },
  barriers: { title: 'Barriers', subtitle: 'Vehicle barriers and the plate rules that raise them' },
  parking: { title: 'Smart Parking Management', subtitle: 'Bay occupancy and overstay across your car parks' },
  'vms-onsite': { title: 'VMS', subtitle: 'Gatehouse board of everyone currently on site — walk-ins, deliveries and vehicles' },
  'visitor-prereg': { title: 'Visitor Pre-Registration', subtitle: 'Expected visitors, so the gate knows them before they arrive' },
  watchlists: { title: 'Watchlists', subtitle: 'Plates and faces that should raise an alert on sight — and the ones that never should' },
  zones: { title: 'Zones', subtitle: 'Areas drawn on a camera view that the AI treats as restricted or monitored' },

  // ── Devices & integrations ────────────────────────────────────────────
  bwc: { title: 'Body Worn Camera Management', subtitle: 'Assignment, docking and footage from officers’ body cameras' },
  iot: { title: 'Smart Facilities', subtitle: 'Environmental and door sensors reporting alongside your cameras' },
  gps: { title: 'GPS Fleet Tracking', subtitle: 'Vehicle positions, journeys and geofence entry/exit alerts' },
  'device-protocols': { title: 'Device Protocols', subtitle: 'How the platform talks to each make and model of hardware' },

  // ── Admin ─────────────────────────────────────────────────────────────
  settings: { title: 'Settings', subtitle: 'Tenant configuration — AI thresholds, retention periods, branding and appearance' },
  'alert-rules': { title: 'Alert Rules', subtitle: 'What the AI must see before it raises an alert, per module' },
  'alert-dedup': { title: 'Alert De-duplication', subtitle: 'Collapse repeat alerts from one event so operators are not flooded' },
  notifications: { title: 'Notifications', subtitle: 'Where alerts get sent — email, SMS and webhook channels, and their delivery history' },
  reports: { title: 'Reports & Compliance', subtitle: 'On-demand PDF reports and PDPA data-subject requests' },
  'scheduled-reports': { title: 'Scheduled Reports', subtitle: 'Reports that generate and send themselves on a recurring schedule' },
  export: { title: 'Export', subtitle: 'Download your data as CSV or JSON for reporting and hand-off' },
  audit: { title: 'Audit Log', subtitle: 'Every action taken in the system, by whom and when — your record for compliance and investigations' },
  tenants: { title: 'Tenants', subtitle: 'Customer organisations on the platform, their module licensing and account status' },
  'api-keys': { title: 'API Keys', subtitle: 'Credentials that let other systems read your data — revoke any key that leaves your control' },
  'ip-allowlist': { title: 'IP Allowlist', subtitle: 'Restrict sign-in to networks you trust' },
  developer: { title: 'Developer', subtitle: 'API reference and request tester for integrating other systems' },
  'emergency-broadcast': { title: 'Emergency Broadcasts', subtitle: 'Push an urgent message to every guard on duty at once' },
  'client-portal': { title: 'Client Portal', subtitle: 'What your client sees for the sites they are paying you to protect' },
} as const satisfies Record<string, PageLabel>

export type PageKey = keyof typeof PAGE_LABELS

/** Tenant overrides, as stored under the ui.page_labels tenant setting. */
export type PageLabelOverrides = Partial<Record<string, Partial<PageLabel>>>

/**
 * Product default merged with the tenant's override, field by field — a
 * tenant that renamed only the title still gets our subtitle, and later
 * wording improvements reach them.
 */
export function resolvePageLabel(key: PageKey, overrides?: PageLabelOverrides): PageLabel {
  const base = PAGE_LABELS[key]
  const over = overrides?.[key]
  if (!over) return base
  return {
    title: over.title?.trim() || base.title,
    subtitle: over.subtitle?.trim() ?? base.subtitle,
  }
}

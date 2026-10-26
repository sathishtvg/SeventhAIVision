/**
 * Every page's own scene: what is drawn as the page is entered, and what goes
 * on moving while the page's first answers are fetched.
 *
 *   entrance  the page's emblem draws itself over the page area with the
 *             page's name - a wall of tiles lighting up for the camera wall,
 *             a sweep for a map, rotors for a drone screen - and clears.
 *   loading   if the page is still fetching when the entrance is over, the
 *             same emblem keeps going, small, beside the words "Loading …",
 *             until the first answers are in.
 *
 * The scene is drawn OVER the page, never instead of it: the page is there
 * and answers a click from its first frame, a click or a key ends the scene at
 * once, and nothing waits for it. A page where seconds matter (`urgent`) is
 * never covered at all - its emblem plays in the corner while the page is
 * already in view.
 *
 * `scenes.test.ts` reads the route table and holds that every page has a
 * scene of its own here.
 */
export type SceneMotif =
  | 'wall' | 'console' | 'dashboard' | 'bell' | 'warning' | 'radar' | 'broadcast' | 'pulse' | 'search' | 'casefile'
  | 'film' | 'chart' | 'clock' | 'route' | 'drone' | 'shield' | 'camera' | 'gate' | 'badge' | 'ledger' | 'receipt'
  | 'report' | 'sliders' | 'building'

export type SceneTone = 'brand' | 'danger' | 'warn'

export interface PageScene {
  /** Which drawing. */
  motif: SceneMotif
  /** The page's name, shown under the emblem. */
  name: string
  /** What is being fetched, said while it is: "Loading alerts". */
  loading: string
  /** The emblem's colours: the brand's, or red or amber for a page about something wrong. */
  tone?: SceneTone
  /** Seconds matter here: the page is never covered, and the emblem plays in the corner. */
  urgent?: boolean
}

const scene = (motif: SceneMotif, name: string, loading: string, more: Partial<PageScene> = {}): PageScene =>
  ({ motif, name, loading, ...more })

/** Every page with a fixed address. */
export const PAGE_SCENES: Record<string, PageScene> = {
  // Monitoring
  '/': scene('dashboard', 'Dashboard', 'Loading today’s figures'),
  '/command-centre': scene('console', 'Command Centre', 'Loading the command centre'),
  '/action-center': scene('ledger', 'Action Center', 'Loading what needs doing', { urgent: true }),
  '/operations-board': scene('console', 'Operations Board', 'Loading the board'),
  '/alerts': scene('bell', 'Alerts', 'Loading alerts'),
  '/incidents': scene('warning', 'Incidents', 'Loading incidents', { tone: 'warn' }),
  '/response-desk': scene('badge', 'Response Desk', 'Loading the response desk', { urgent: true }),
  '/live': scene('wall', 'Live Wall', 'Connecting the cameras'),
  '/map': scene('radar', 'Site Map', 'Loading the site map'),
  '/security-map': scene('radar', 'Security Map', 'Loading the security map'),
  '/emergency': scene('broadcast', 'Emergency Alert', 'Loading emergency broadcasts', { tone: 'danger', urgent: true }),
  '/man-down': scene('pulse', 'Man Down', 'Loading man-down events', { tone: 'danger', urgent: true }),
  '/alarms': scene('bell', 'Alarm Panels', 'Loading the alarm panels', { tone: 'danger', urgent: true }),

  // Investigate
  '/investigate': scene('search', 'Search Records', 'Opening the records'),
  '/investigations': scene('casefile', 'Investigations', 'Loading investigations'),
  '/evidence-packages': scene('casefile', 'Evidence Packages', 'Loading evidence packages'),
  '/evidence-holds': scene('casefile', 'Evidence Holds', 'Loading evidence holds'),
  '/footage': scene('film', 'Footage Review', 'Loading the videos'),
  '/footage/search': scene('search', 'Footage Search', 'Getting the search ready'),
  '/cases': scene('casefile', 'Cases', 'Loading cases'),
  '/detections': scene('search', 'Detections', 'Loading detections'),
  '/evidence': scene('film', 'Evidence', 'Loading evidence'),
  '/recordings': scene('film', 'Recordings', 'Loading recordings'),
  '/playback': scene('film', 'Playback', 'Loading the cameras'),
  '/analytics': scene('chart', 'Analytics', 'Working out the figures'),
  '/heatmap': scene('radar', 'Heatmap', 'Loading the heatmap'),

  // Guard operations
  '/attendance': scene('clock', 'Attendance', 'Loading attendance'),
  '/shifts': scene('clock', 'Shifts', 'Loading shifts'),
  '/roster': scene('clock', 'Roster', 'Loading the roster'),
  '/duty-assignments': scene('badge', 'Duty Teams', 'Loading duty teams'),
  '/guard-ops': scene('dashboard', 'Guard Ops', 'Loading guard operations'),
  '/my-patrols': scene('route', 'My Patrols', 'Loading your patrols'),
  '/virtual-patrol': scene('route', 'Virtual Patrol', 'Loading patrol routes'),
  '/certification-compliance': scene('badge', 'Certifications', 'Loading certifications'),
  '/compliance': scene('route', 'Tour Compliance', 'Loading tour compliance'),
  '/violations': scene('warning', 'Violations', 'Loading violations', { tone: 'warn' }),
  '/leave': scene('clock', 'Leave', 'Loading leave'),
  '/payroll': scene('receipt', 'Payroll', 'Loading payroll'),
  '/training': scene('badge', 'Guard Training', 'Loading training'),
  '/workforce-readings': scene('chart', 'Workforce Readings', 'Loading workforce readings'),
  '/my-reading': scene('chart', 'My Reading', 'Loading your reading'),
  '/post-orders': scene('ledger', 'SOP', 'Loading post orders'),
  '/sop-library': scene('ledger', 'SOP Library', 'Loading the SOP library'),
  '/key-register': scene('ledger', 'Key Register', 'Loading the key register'),
  '/lost-found': scene('ledger', 'Lost & Found', 'Loading lost and found'),
  '/defects': scene('warning', 'Defect Log', 'Loading the defect log', { tone: 'warn' }),
  '/equipment': scene('ledger', 'Equipment', 'Loading equipment'),
  '/occurrence-book': scene('ledger', 'Occurrence Book', 'Opening the occurrence book'),
  '/handovers': scene('clock', 'Handovers', 'Loading handovers'),

  // Security intelligence
  '/situations': scene('shield', 'Situations', 'Loading situations'),
  '/situation-decisions': scene('shield', 'Decisions', 'Loading decisions'),
  '/security-insight': scene('chart', 'Insight', 'Loading insight'),
  '/security-feedback': scene('shield', 'Feedback', 'Loading feedback'),
  '/risk-advice': scene('chart', 'Risk & Advice', 'Loading risk and advice'),
  '/intelligence-setup': scene('sliders', 'Intelligence Setup', 'Loading the setup'),

  // The AI module
  '/ai-settings': scene('sliders', 'AI Settings', 'Loading the AI settings'),

  // Drones
  '/drones': scene('drone', 'Drone Fleet', 'Loading the fleet'),
  '/drone-missions': scene('route', 'Drone Missions', 'Loading missions'),
  '/drone-patrols': scene('drone', 'Drone Patrols', 'Loading patrols'),
  '/drone-events': scene('warning', 'Drone Events', 'Loading drone events', { tone: 'warn' }),
  '/drone-zones': scene('radar', 'Drone Zones', 'Loading drone zones'),
  '/drone-analytics': scene('chart', 'Drone Analytics', 'Working out the figures'),

  // Sites and devices
  '/sites': scene('building', 'Sites', 'Loading sites'),
  '/site-places': scene('radar', 'Site Places', 'Loading site places'),
  '/cameras': scene('camera', 'Cameras', 'Loading cameras'),
  '/zones': scene('radar', 'Zones', 'Loading zones'),
  '/barriers': scene('gate', 'Barriers', 'Loading barriers'),
  '/access': scene('gate', 'Access Control', 'Loading access control'),
  '/parking': scene('gate', 'Smart Parking', 'Loading parking'),
  '/iot': scene('broadcast', 'Smart Facilities', 'Loading sensors'),
  '/bwc': scene('camera', 'Body Cameras', 'Loading body cameras'),
  '/gps': scene('radar', 'GPS Fleet', 'Finding the vehicles'),
  '/gps/map': scene('radar', 'Live Fleet Map', 'Finding where each vehicle is'),
  '/gps/history': scene('route', 'Journey History', 'Working out the journeys'),
  '/gps/geofences': scene('radar', 'Geofences', 'Drawing the geofences'),
  '/gps/vehicles': scene('ledger', 'Fleet Vehicles', 'Loading the vehicles'),
  '/gps/groups': scene('ledger', 'Vehicle Groups', 'Loading the groups'),
  '/gps/fields': scene('sliders', 'Vehicle Fields', 'Loading the fields'),
  '/gps/providers': scene('broadcast', 'GPS Providers', 'Loading the providers'),
  '/assets-maintenance': scene('ledger', 'Assets & Maintenance', 'Loading assets'),

  // Visitors
  '/visitor-prereg': scene('gate', 'Visitor Pre-Reg', 'Loading visitors'),
  '/visitor-authorisations': scene('badge', 'Visitor Authorisations', 'Loading authorisations'),
  '/vms-onsite': scene('gate', 'Visitors On Site', 'Loading who is on site'),
  '/watchlists': scene('search', 'Watchlists', 'Loading watchlists'),
  '/contractors': scene('badge', 'Contractors', 'Loading contractors'),

  // Reports
  '/reports': scene('report', 'Reports', 'Loading reports'),
  '/scheduled-reports': scene('report', 'Scheduled Reports', 'Loading scheduled reports'),
  '/export': scene('report', 'Export', 'Loading the export'),
  '/invoicing': scene('receipt', 'Client Invoicing', 'Loading invoices'),
  '/audit': scene('ledger', 'Audit Logs', 'Loading the audit log'),
  '/data-retention': scene('casefile', 'Data Retention', 'Loading retention'),

  // Settings
  '/settings': scene('sliders', 'Settings', 'Loading settings'),
  '/alert-rules': scene('bell', 'Alert Rules', 'Loading alert rules'),
  '/alert-dedup': scene('bell', 'Alert Dedup', 'Loading the dedup rules'),
  '/device-protocols': scene('camera', 'Device Protocols', 'Loading device protocols'),
  '/notifications': scene('bell', 'Notifications', 'Loading notifications'),
  '/response-settings': scene('sliders', 'Response Settings', 'Loading response settings'),
  '/users': scene('badge', 'Users', 'Loading users'),
  '/roles': scene('badge', 'Roles', 'Loading roles'),
  '/api-keys': scene('sliders', 'API Keys', 'Loading API keys'),
  '/ip-allowlist': scene('shield', 'IP Allowlist', 'Loading the allowlist'),
  '/developer': scene('sliders', 'Developer', 'Loading the developer tools'),

  // Platform owner
  '/platform': scene('dashboard', 'Platform Dashboard', 'Loading the platform'),
  '/tenants': scene('building', 'Tenants', 'Loading tenants'),
  '/platform/tenants': scene('building', 'Tenant Usage', 'Loading tenant usage'),
  '/platform/analytics': scene('chart', 'Growth', 'Working out the figures'),
  '/platform/billing': scene('receipt', 'Plans & Pricing', 'Loading plans'),
  '/platform/invoices': scene('receipt', 'Platform Invoices', 'Loading invoices'),
  '/platform/errors': scene('warning', 'Error Centre', 'Loading errors', { tone: 'warn' }),
  '/support-sessions': scene('badge', 'Support Sessions', 'Loading support sessions'),
}

/** One record, by its id. */
const RECORD_SCENES: [RegExp, PageScene][] = [
  [/^\/situations\/[^/]+$/, scene('shield', 'Situation', 'Loading the situation')],
  [/^\/gps\/vehicles\/[^/]+$/, scene('ledger', 'Vehicle', 'Loading the vehicle')],
  [/^\/investigations\/[^/]+$/, scene('casefile', 'Investigation', 'Opening the investigation')],
  [/^\/evidence-packages\/[^/]+$/, scene('casefile', 'Evidence Package', 'Opening the package')],
  [/^\/drone-missions\/[^/]+$/, scene('route', 'Mission', 'Loading the mission')],
  [/^\/drone-patrols\/[^/]+$/, scene('drone', 'Drone Patrol', 'Reaching the drone', { urgent: true })],
  [/^\/drone-events\/[^/]+$/, scene('warning', 'Drone Event', 'Loading the event', { tone: 'warn' })],
  [/^\/platform\/tenants\/[^/]+$/, scene('building', 'Tenant', 'Loading the tenant')],
  [/^\/footage\/[^/]+$/, scene('film', 'Footage', 'Opening the video')],
]

/** What a page that is not listed gets: the product's own mark, so a page added later still arrives as one of these. */
export const DEFAULT_SCENE: PageScene = scene('shield', '', 'Loading')

export function sceneOf(pathname: string): PageScene {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  return PAGE_SCENES[path] ?? RECORD_SCENES.find(([shape]) => shape.test(path))?.[1] ?? DEFAULT_SCENE
}

/** How long the entrance is on screen before it clears, and how long clearing takes. `motion.css` has the same two. */
export const SCENE_MS = 520
export const SCENE_EXIT_MS = 190

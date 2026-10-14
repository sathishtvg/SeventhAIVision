/**
 * Every screen's own scene: what is drawn as the screen is opened, and what
 * goes on moving while the screen's first answers are fetched. The phone's
 * counterpart of `frontend/src/motion/scenes.ts`.
 *
 *   entrance  the screen's emblem and name over the screen, for about half a
 *             second, and the screen arrives as it clears.
 *   loading   if the screen is still fetching then, the same emblem goes on,
 *             small, at the top beside the screen's own words, until the
 *             first answers are in.
 *
 * A scene is drawn OVER the screen and takes no touch: the first touch ends
 * it. A screen where seconds matter, or one whose whole point is a camera's
 * picture, is never covered at all (`open`): its emblem plays at the top
 * while the screen is already in view.
 *
 * The name of a scene is the name its route has in `navigation/index.tsx`.
 * `__tests__/screenScenes.test.tsx` reads that file and holds that every
 * screen has a scene of its own here.
 */
import type { MaterialCommunityIcons } from '@expo/vector-icons'

export type SceneIcon = keyof typeof MaterialCommunityIcons.glyphMap

/** How the emblem goes on moving. */
export type SceneMove = 'ring' | 'sweep' | 'swing' | 'bob' | 'pulse'

export type SceneTone = 'brand' | 'danger' | 'warn'

export interface ScreenSceneDef {
  icon: SceneIcon
  /** The screen's name, shown under the emblem. */
  name: string
  /** What is being fetched, said while it is: "Loading alerts". */
  loading: string
  move: SceneMove
  tone?: SceneTone
  /** Never covered: seconds matter here, or the screen is a camera's picture. */
  open?: boolean
}

const scene = (
  icon: SceneIcon, name: string, loading: string, move: SceneMove = 'pulse', more: Partial<ScreenSceneDef> = {},
): ScreenSceneDef => ({ icon, name, loading, move, ...more })

const OPEN = { open: true } as const
const LIVE_VIEW = scene('cctv', 'Live View', 'Connecting the camera', 'ring', OPEN)
const DRONE_EVENT = scene('quadcopter', 'Drone Event', 'Loading the event', 'bob', { tone: 'warn' })

export const SCREEN_SCENES: Record<string, ScreenSceneDef> = {
  // The tabs that are a screen themselves
  Dashboard: scene('view-dashboard-outline', 'Dashboard', 'Loading today’s figures'),
  Watchlists: scene('car-search-outline', 'Watchlists', 'Loading watchlists'),
  Evidence: scene('image-multiple-outline', 'Evidence', 'Loading evidence'),

  // Alerts
  AlertsList: scene('bell-ring-outline', 'Alerts', 'Loading alerts', 'swing'),
  AlertDetail: scene('bell-alert-outline', 'Alert', 'Loading the alert', 'ring'),
  AlertDroneEvent: DRONE_EVENT,

  // Incidents
  IncidentsList: scene('alert-outline', 'Incidents', 'Loading incidents', 'ring', { tone: 'warn' }),
  IncidentDetail: scene('alert-circle-outline', 'Incident', 'Loading the incident', 'ring', { tone: 'warn' }),
  IncidentCameraLive: LIVE_VIEW,

  // Cameras
  CamerasList: scene('cctv', 'Cameras', 'Loading cameras', 'ring'),
  Sites: scene('office-building-outline', 'Sites', 'Loading sites'),
  CameraLive: LIVE_VIEW,
  LiveWall: scene('view-grid-outline', 'Live Wall', 'Connecting the cameras', 'pulse', OPEN),
  ZoneDraw: scene('vector-polygon', 'Draw Zone', 'Connecting the camera', 'pulse', OPEN),
  CameraPrivacyZones: scene('shield-lock-outline', 'Privacy Zones', 'Loading privacy zones'),

  // Patrol
  Shifts: scene('calendar-clock', 'My Shifts', 'Loading your shifts', 'sweep'),
  PatrolSelect: scene('map-marker-path', 'Select Route', 'Loading routes'),
  PatrolScan: scene('qrcode-scan', 'Patrol', 'Loading the patrol', 'pulse', OPEN),
  OccurrenceBook: scene('book-open-page-variant-outline', 'Occurrence Book', 'Opening the occurrence book'),
  VirtualPatrols: scene('video-check-outline', 'Virtual Patrols', 'Loading your patrols'),
  VirtualPatrolRun: scene('video-check', 'Virtual Patrol', 'Loading the patrol', 'pulse', OPEN),

  // More
  MoreMenu: scene('view-grid-plus-outline', 'More', 'Loading'),
  ActionCenter: scene('clipboard-check-outline', 'Action Center', 'Loading what needs doing', 'pulse', OPEN),
  Attendance: scene('account-clock-outline', 'Live Attendance', 'Loading attendance', 'sweep'),
  MySchedule: scene('calendar-account-outline', 'My Schedule', 'Loading your schedule'),
  Training: scene('school-outline', 'My Training', 'Loading your training'),
  PostOrders: scene('file-document-outline', 'SOP', 'Loading post orders'),
  KeyRegister: scene('key-chain-variant', 'Key Register', 'Loading the key register', 'swing'),
  LostFound: scene('archive-search-outline', 'Lost & Found', 'Loading lost and found'),
  DefectLog: scene('wrench-outline', 'Defect Log', 'Loading the defect log', 'swing', { tone: 'warn' }),
  MyKit: scene('bag-personal-outline', 'My Kit', 'Loading your kit'),
  Handover: scene('swap-horizontal', 'Handovers', 'Loading handovers'),
  Visitors: scene('account-arrow-right-outline', 'Visitor Management', 'Loading visitors'),
  Dispatch: scene('account-alert-outline', 'Dispatch', 'Loading dispatch', 'ring', OPEN),
  Detections: scene('image-search-outline', 'AI Detections', 'Loading detections'),
  Notifications: scene('bell-check-outline', 'Notification Logs', 'Loading notifications', 'swing'),
  Reports: scene('file-chart-outline', 'Reports', 'Loading reports'),
  Analytics: scene('chart-bar', 'Analytics', 'Working out the figures'),
  Settings: scene('tune-variant', 'Settings', 'Loading settings'),
  GPS: scene('radar', 'GPS Tracking', 'Finding the vehicles', 'sweep'),
  Alarms: scene('alarm-light-outline', 'Alarm Panels', 'Loading the alarm panels', 'ring', { tone: 'danger', open: true }),
  BWC: scene('camera-account', 'Body Worn Cameras', 'Loading body cameras', 'ring'),
  AccessControl: scene('door-open', 'Access Control', 'Loading access control'),
  Contractors: scene('account-hard-hat-outline', 'Contractors', 'Loading contractors'),
  Parking: scene('boom-gate-outline', 'Parking', 'Loading parking'),
  Compliance: scene('clipboard-list-outline', 'Compliance', 'Loading compliance'),
  IoT: scene('access-point', 'IoT Devices', 'Loading sensors', 'ring'),
  Emergency: scene('bullhorn-outline', 'Emergency Broadcast', 'Loading emergency broadcasts', 'ring', { tone: 'danger', open: true }),
  MyRecord: scene('card-account-details-outline', 'My Record', 'Loading your record'),
  Leave: scene('calendar-remove-outline', 'Leave', 'Loading leave'),
  DroneEvents: scene('quadcopter', 'Drone Events', 'Loading drone events', 'bob', { tone: 'warn' }),
  DroneEventDetail: DRONE_EVENT,
  Situations: scene('shield-search', 'Security Situations', 'Loading situations', 'sweep'),
  SituationDetail: scene('shield-alert-outline', 'Situation', 'Loading the situation', 'ring'),
  MyReading: scene('chart-line', 'My Reading', 'Loading your reading'),
  Briefings: scene('newspaper-variant-outline', 'Daily Briefings', 'Loading briefings'),
  MyWorkOrders: scene('hammer-wrench', 'My Work Orders', 'Loading your work orders', 'swing'),
  MyCaseTasks: scene('briefcase-check-outline', 'My Case Tasks', 'Loading your tasks'),
  SituationCameraLive: LIVE_VIEW,
}

/** The scene of a route, or none: a tab that only holds a stack has no scene of its own - its screens have. */
export function sceneOf(routeName: string): ScreenSceneDef | null {
  return SCREEN_SCENES[routeName] ?? null
}

/** The emblem arrives, stays a moment, and clears. Half a second in all: the screen has already slid in. */
export const SCENE_ENTER_MS = 200
export const SCENE_HOLD_MS = 160
export const SCENE_CLEAR_MS = 140
/** A "Loading" that would be gone again at once is not shown. */
export const SCENE_WAIT_MS = 150

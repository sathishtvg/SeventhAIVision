/**
 * What kind of page a path is, which decides how it arrives.
 *
 *   operational  a screen somebody watches or works a live task on: the camera
 *                wall, playback, a patrol in progress, the command centre, the
 *                response desk, a live map. It appears - a short fade, no
 *                movement. Video must not slide, and an operator who has just
 *                switched screens must not wait for one to settle.
 *   dashboard    figures and cards. It rises a little, and its cards may take
 *                their turns.
 *   detail       one record: a mission, a situation, an investigation.
 *   table        everything else - lists, registers, forms, settings. A fade.
 *
 * A path that is not listed is a table, which is the calmest of the four that
 * move at all, so a page added later is safe without being added here.
 * The entrance is a CSS class on the page area (`motion.css`): the page is
 * drawn and answers a click from its first frame, whatever the class.
 */
export type PageKind = 'operational' | 'dashboard' | 'detail' | 'table'

const OPERATIONAL = [
  '/live', '/playback', '/recordings', '/command-centre', '/action-center', '/my-patrols', '/response-desk',
  '/operations-board', '/map', '/security-map', '/gps', '/vms-onsite', '/man-down', '/emergency', '/bwc', '/alarms',
]

const DASHBOARDS = [
  '/', '/platform', '/platform/analytics', '/platform/billing', '/analytics', '/drones', '/drone-analytics',
  '/security-insight', '/guard-ops', '/heatmap', '/workforce-readings', '/risk-advice',
]

/** One record, by its id: `/situations/42`, `/platform/tenants/abc`. */
const DETAIL = /^\/(situations|investigations|evidence-packages|drone-missions|drone-patrols|drone-events|platform\/tenants)\/[^/]+$/

export function pageKindOf(pathname: string): PageKind {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  if (OPERATIONAL.includes(path)) return 'operational'
  if (DASHBOARDS.includes(path)) return 'dashboard'
  if (DETAIL.test(path)) return 'detail'
  return 'table'
}

/** The classes for the page area. `page-enter` is the name it has always had; the second says which entrance. */
export function pageEnterClass(pathname: string): string {
  return `page-enter page-enter--${pageKindOf(pathname)}`
}

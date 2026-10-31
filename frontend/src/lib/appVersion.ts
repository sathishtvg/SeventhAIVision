/**
 * Which build of the console this is, said where the person can see it (the foot of the sidebar), so they can tell
 * an updated console from an old one at a glance.
 *
 * The web build is stamped when it is built (vite.config.ts: the commit and the date). The Windows app is that same
 * web build inside Electron, which names the app and its version in every page's user agent
 * ("seventh-ai-vision-desktop/1.0.13"), so the desktop's own version is read from there: no change to the shell.
 */

declare const __APP_BUILD__: { commit: string; date: string } | undefined

export const WEB_BUILD: { commit: string; date: string } =
  typeof __APP_BUILD__ !== 'undefined' && __APP_BUILD__ ? __APP_BUILD__ : { commit: '', date: '' }

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2026-10-31" as "31 Oct 2026", the same in every locale. */
export function buildDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return ''
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1] ?? ''} ${m[1]}`
}

/** The Windows app's version, or null in a browser. */
export function desktopVersion(userAgent: string = typeof navigator === 'undefined' ? '' : navigator.userAgent): string | null {
  const m = /seventh-ai-vision-desktop\/(\d+\.\d+\.\d+)/.exec(userAgent)
  return m ? m[1] : null
}

/** What the sidebar says: "Windows 1.0.13 · build 0219501 · 31 Oct 2026", or "Web build 0219501 · 31 Oct 2026". */
export function versionLine(build = WEB_BUILD, userAgent?: string): string {
  const desktop = desktopVersion(userAgent)
  const parts = [build.commit ? `build ${build.commit}` : 'build not stamped', buildDay(build.date)].filter(Boolean)
  return desktop ? `Windows ${desktop} · ${parts.join(' · ')}` : `Web ${parts.join(' · ')}`
}

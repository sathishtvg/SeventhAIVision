/**
 * Which build of the phone app this is, said where the person can see it (the foot of the More menu and of the
 * sign-in screen), so they can tell an updated app from an old one.
 *
 * The app's version number stays 1.0.0 from build to build, so the build is told apart by what app.config.js stamps
 * into it when it is made: the commit Expo's build server was given, and the day.
 */
import Constants from 'expo-constants'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2026-10-31" as "31 Oct 2026", the same in every locale. */
export function buildDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return ''
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1] ?? ''} ${m[1]}`
}

interface Config { version?: string; extra?: { build?: { commit?: string; date?: string } } | Record<string, unknown> }

/** "Version 1.0.0 · build 0219501 · 31 Oct 2026". */
export function versionLine(config: Config | null | undefined = Constants.expoConfig as Config | null): string {
  const build = (config?.extra as { build?: { commit?: string; date?: string } } | undefined)?.build
  return [
    `Version ${config?.version ?? '?'}`,
    build?.commit ? `build ${build.commit}` : 'build not stamped',
    build?.date ? buildDay(build.date) : '',
  ].filter(Boolean).join(' · ')
}

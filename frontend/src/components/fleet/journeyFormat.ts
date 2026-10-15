/**
 * Playing a journey back.
 *
 * The marker is at the last position the vehicle really reported at or before
 * the moment on the slider - it jumps from fix to fix and never glides between
 * them, because a place between two fixes is a place nobody recorded.
 */
import type { RoutePoint } from '@/api/fleet'

/** How many times faster than real time the journey can be played. */
export const PLAY_SPEEDS = [1, 10, 60] as const
/** How often the slider moves while playing. */
export const PLAY_STEP_MS = 200

export function lastAt(route: RoutePoint[], t: number): RoutePoint | null {
  if (!route.length || t < Date.parse(route[0].at)) return null
  let lo = 0
  let hi = route.length - 1
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2)
    if (Date.parse(route[mid].at) <= t) lo = mid
    else hi = mid - 1
  }
  return route[lo]
}

/** The next moment on the slider while playing, held at the end. */
export function step(t: number, speed: number, end: number): number {
  return Math.min(end, t + PLAY_STEP_MS * speed)
}

export function minutesText(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)} min`
  const h = Math.floor(minutes / 60)
  return `${h} h ${Math.round(minutes - h * 60)} min`
}

/** A datetime-local input's value for a moment, in the browser's own time. */
export function localInput(at: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${at.getFullYear()}-${p(at.getMonth() + 1)}-${p(at.getDate())}T${p(at.getHours())}:${p(at.getMinutes())}`
}

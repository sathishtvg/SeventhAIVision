import { useSyncExternalStore } from 'react'

/**
 * How much the app moves, for this person.
 *
 *   system   what the operating system says (the default)
 *   reduced  no movement; a state changes at once or with the briefest fade
 *   full     the app's ordinary motion, even where the system asks for less -
 *            somebody who chose it here chose it knowingly
 *
 * The choice is written on the page's root as `data-motion`, where the one
 * rule in `motion.css` reads it; code that moves things itself (a number
 * counting up) asks `reducedMotion()`. Nothing is ever told by movement
 * alone, so with less of it nothing is lost.
 */
export type MotionPreference = 'system' | 'reduced' | 'full'

export const MOTION_PREFERENCES: MotionPreference[] = ['system', 'reduced', 'full']
export const MOTION_ATTRIBUTE = 'data-motion'
const CHANGED = 'sav:motion-preference'
const QUERY = '(prefers-reduced-motion: reduce)'

export function isMotionPreference(value: unknown): value is MotionPreference {
  return typeof value === 'string' && (MOTION_PREFERENCES as string[]).includes(value)
}

/** Puts the choice where the style sheet and the code read it. */
export function applyMotionPreference(preference: MotionPreference, root: HTMLElement = document.documentElement): void {
  if (preference === 'system') root.removeAttribute(MOTION_ATTRIBUTE)
  else root.setAttribute(MOTION_ATTRIBUTE, preference)
  window.dispatchEvent(new Event(CHANGED))
}

export function systemPrefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.(QUERY).matches
}

/** Whether to move less: the app's own setting first, the system's when the app has none. */
export function reducedMotion(root: HTMLElement | undefined = typeof document === 'undefined' ? undefined : document.documentElement): boolean {
  const chosen = root?.getAttribute(MOTION_ATTRIBUTE)
  if (chosen === 'reduced') return true
  if (chosen === 'full') return false
  return systemPrefersReducedMotion()
}

function subscribe(changed: () => void): () => void {
  const system = window.matchMedia?.(QUERY)
  system?.addEventListener?.('change', changed)
  window.addEventListener(CHANGED, changed)
  return () => {
    system?.removeEventListener?.('change', changed)
    window.removeEventListener(CHANGED, changed)
  }
}

/** `reducedMotion()`, kept up to date: a component drawn with it is redrawn when the setting changes. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, reducedMotion, () => false)
}

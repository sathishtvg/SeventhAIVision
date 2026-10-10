/**
 * How long things take, and how they move. The one place that says.
 *
 * These are the values the app was already using, written once: `motion.css`
 * carries them as variables and the MUI theme as its transition durations, and
 * `tokens.test.ts` holds all three equal - before this they had drifted (the
 * same "standard" transition was 220 ms in one and 250 ms in the other).
 *
 * WHAT EACH IS FOR
 *   fast      a control answering a hand: hover, press, a chip changing colour
 *   standard  one thing replacing another in place: a tab, a row's status
 *   panel     a drawer, a dialog, a section opening
 *   exit      the same going away: about two thirds of arriving, so that
 *             closing something never feels like waiting for it
 *   page      a page arriving
 *   slow      the longest anything takes; a chart drawing itself
 *
 * Nothing here is a reason to make somebody wait. An entrance plays on
 * something that is already there and already answers a click; no code holds
 * content back for one.
 */
export const duration = {
  fast: 150,
  standard: 220,
  panel: 280,
  exit: 190,
  page: 320,
  slow: 350,
} as const

export const easing = {
  /** Arriving: quick at first, settling. */
  out: 'cubic-bezier(0.0, 0.0, 0.2, 1)',
  /** Moving between two places it can rest. */
  inOut: 'cubic-bezier(0.4, 0.0, 0.2, 1)',
  /** Leaving: slow at first, then gone. */
  in: 'cubic-bezier(0.4, 0, 1, 1)',
  /** Snapping to a state. */
  sharp: 'cubic-bezier(0.4, 0.0, 0.6, 1)',
} as const

/** One after another: the gap between items, and the item after which they stop waiting. */
export const stagger = { step: 40, most: 10 } as const

/**
 * A placeholder is in the page from the first frame, so nothing moves when it
 * is replaced, and is seen only after this long. An answer that comes sooner
 * is not preceded by a flash. The content itself is never delayed.
 */
export const appear = { delay: 150, fade: duration.standard } as const

/** How long something that has just arrived stays marked as new. */
export const NEW_ITEM_MS = 6000

export const ms = (value: number) => `${value}ms`

/** A CSS transition for the properties named, and no others. */
export function transitionOf(properties: string | string[], time: number = duration.standard, ease: string = easing.inOut): string {
  return (Array.isArray(properties) ? properties : [properties]).map((p) => `${p} ${time}ms ${ease}`).join(', ')
}

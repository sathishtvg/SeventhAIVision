/**
 * Motion: how the app moves, and how long it takes. See `tokens.ts` for the
 * timings, `preference.ts` for the person's own setting, `pageKind.ts` for how
 * a page arrives, `useNewItems.ts` for marking what has just come in.
 * The states a page shows while it loads, fails or is empty are components:
 * `@/components/states`.
 */
export { duration, easing, stagger, appear, NEW_ITEM_MS, ms, transitionOf } from './tokens'
export {
  MOTION_ATTRIBUTE, MOTION_PREFERENCES, applyMotionPreference, isMotionPreference, reducedMotion,
  systemPrefersReducedMotion, useReducedMotion,
} from './preference'
export type { MotionPreference } from './preference'
export { enterSx, fadeUpSx, staggerDelay } from './entrance'
export { useCountUp } from './countUp'
export { pageEnterClass, pageKindOf } from './pageKind'
export type { PageKind } from './pageKind'
export { announce, countOf, useAnnouncer } from './announce'
export { newItemProps, toneOf, useNewItems } from './useNewItems'
export type { NewItemTone, NewItemsOptions } from './useNewItems'

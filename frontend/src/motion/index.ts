/**
 * Motion: how the app moves, and how long it takes. See `tokens.ts` for the
 * timings, `preference.ts` for the person's own setting, `pageKind.ts` for how
 * a page arrives, `scenes.ts` for each page's own entrance and loading,
 * `useNewItems.ts` for marking what has just come in.
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
export { DEFAULT_SCENE, PAGE_SCENES, SCENE_EXIT_MS, SCENE_MS, sceneOf } from './scenes'
export type { PageScene, SceneMotif, SceneTone } from './scenes'
export { setPageScenes, usePageScenes } from './scenePreference'
export type { PageKind } from './pageKind'
export { announce, countOf, useAnnouncer } from './announce'
export { newItemProps, toneOf, useNewItems } from './useNewItems'
export type { NewItemTone, NewItemsOptions } from './useNewItems'

/**
 * What a phone screen shows when it is not simply showing its content: loading,
 * failed, being refreshed, something just arrived, something just done - the
 * phone's copies of the web's `frontend/src/components/states`, with the same
 * words and the same rules:
 *
 *   1. loading   a placeholder shaped like the content (`Skeleton`)
 *   2. failed    `ErrorState`, with why and "Try again"; never "nothing here"
 *   3. empty     only when the server answered with nothing
 *   4. a changed filter keeps the last rows, behind `RefreshingLine`
 *   5. what arrives is marked (`NewMark` with `useNewItems`), a camera says what
 *      it is doing (`VideoLoadingState`), and nothing says "done" before the
 *      server has (`SuccessTick`)
 *
 * Everything that moves here is opacity or position, and is still on a phone
 * set to remove animations.
 */
export { errorText, isNotFound } from './errorText'
export { ErrorState } from './ErrorState'
export type { ErrorStateProps } from './ErrorState'
export { Bone, DetailSkeleton, ListSkeleton, StatsSkeleton } from './Skeleton'
export { NewMark, RefreshingLine, SuccessTick, VideoLoadingState, useBriefWord } from './Live'
export type { VideoState } from './Live'
export { AnimatedNumber, FadeInView } from './Animated'
export { PICTURE_WATCH_SCRIPT, TILE_STATE_HTML, TILE_WATCH_SCRIPT, pictureNews, tileStateCss } from './liveHtml'
export type { PictureNews } from './liveHtml'

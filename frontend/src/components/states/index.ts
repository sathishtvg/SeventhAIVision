/**
 * What a page shows when it is not simply showing its content: loading,
 * failed, empty, refreshing, saving, done - and what it says when something
 * changes. One of each, used everywhere, so the same thing looks and reads
 * the same wherever it happens.
 *
 * The rules every page keeps (the design note has the reasons):
 *   1. loading   - a placeholder shaped like the content (`Skeletons`)
 *   2. failed    - `ErrorState`, with why and "Try again"; never "nothing here"
 *   3. empty     - `EmptyState`, only when the server answered with nothing
 *   4. a changed filter keeps the last rows, behind `RefreshingLine`
 *   5. saving, exporting and buffering are their own states (`SaveStatus`,
 *      `ProgressState`, `VideoLoadingState`), and none claims progress or
 *      success the server has not reported
 *
 * How things move is `@/motion`.
 */
export { errorText } from './errorText'
export { ErrorState, TableErrorRow } from './ErrorState'
export type { ErrorStateProps } from './ErrorState'
export { EmptyState, TableEmptyRow } from './EmptyState'
export type { EmptyStateProps } from './EmptyState'
export { RefreshingLine, TableRefreshingRow } from './Refreshing'
export {
  CardGridSkeleton, ChartSkeleton, DashboardCardSkeleton, DetailSkeleton, KpiSkeleton, ListSkeleton, MapSkeleton,
  TableSkeleton,
} from './Skeletons'
export { LoadState } from './LoadState'
export type { LoadStateProps } from './LoadState'
export { stateOf } from './stateOf'
export type { QueryLike } from './stateOf'
export { ProgressState, SaveStatus, SuccessTick } from './Progress'
export type { ProgressStateProps, SaveStatusProps } from './Progress'
export { AnimatedCounter, AnimatedList } from './Animated'
export type { AnimatedCounterProps, AnimatedListProps } from './Animated'
export { LiveAnnouncer, StaleBadge, VideoLoadingState } from './Live'
export type { StaleBadgeProps, VideoLoadingStateProps } from './Live'
export { ageText, isFresh } from './freshness'
export { MotionPreferences } from './MotionPreferences'
export { LoadFailureNotice } from './LoadFailureNotice'

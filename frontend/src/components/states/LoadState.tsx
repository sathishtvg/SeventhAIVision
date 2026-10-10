import type { ReactNode } from 'react'
import { EmptyState } from './EmptyState'
import { ErrorState } from './ErrorState'

export interface LoadStateProps {
  /** The first answer has not come yet. */
  loading: boolean
  /** What the request failed with, when it failed and there is nothing to show instead. */
  error?: unknown
  onRetry?: () => unknown
  /** The server answered with nothing. */
  empty?: boolean
  /** What stands in for the content while it loads - shaped like it (`Skeletons`). */
  skeleton: ReactNode
  emptyState?: ReactNode
  errorTitle?: string
  /** Read by nothing here: `stateOf` gives them, and they are taken so its result can be spread. */
  refreshing?: boolean
  refreshFailed?: boolean
  children: ReactNode
}

/**
 * The four things a part of a page can be - loading, failed, empty, there -
 * in the order that keeps each honest: it is not "failed" while it is still
 * loading, and not "empty" unless the server said so.
 *
 * A request that fails while the last answer is still on screen is not this
 * component's business: the content stays, and `stateOf` reports the failure
 * separately (`refreshFailed`) for the page to mention if it matters.
 */
export function LoadState({ loading, error, onRetry, empty = false, skeleton, emptyState, errorTitle, children }: LoadStateProps) {
  if (loading) return <>{skeleton}</>
  if (error) return <ErrorState error={error} onRetry={onRetry} title={errorTitle} />
  if (empty) return <>{emptyState ?? <EmptyState />}</>
  return <>{children}</>
}

/** The parts of a query result that the states read. TanStack Query's result has all of them. */
export interface QueryLike<T> {
  data: T | undefined
  error: unknown
  isLoading: boolean
  isError: boolean
  isFetching: boolean
  isPlaceholderData?: boolean
  refetch: () => unknown
}

/**
 * A query's result as the props the states take.
 *
 *   <LoadState {...stateOf(query, (d) => d.items.length === 0)} skeleton={…}>
 *
 * `error` is given only when there is nothing to show: a refresh that fails
 * over rows that are already there leaves them there, and sets
 * `refreshFailed`. `refreshing` is true while the rows on screen belong to the
 * last filter and this one's are on their way.
 */
export function stateOf<T>(query: QueryLike<T>, isEmpty?: (data: T) => boolean) {
  const has = query.data !== undefined
  return {
    loading: query.isLoading,
    error: query.isError && !has ? (query.error ?? new Error('The request failed.')) : undefined,
    onRetry: query.refetch,
    empty: has && !query.isPlaceholderData && !!isEmpty?.(query.data as T),
    refreshing: !!query.isPlaceholderData && query.isFetching,
    refreshFailed: query.isError && has,
  }
}

/**
 * Where the conversion of every page stands. `conversion.test.ts` reads the
 * pages themselves and holds this file to them, so it cannot say more than is
 * true and the lists can only get shorter.
 *
 * RULE 2 - a failed request says it failed. A page that fetches is converted
 * when its own content has the shared failed state (`ErrorState`,
 * `TableErrorRow` or `LoadState`) beside its placeholder. Before, a page that
 * was not loading and had no rows said "No … found" - which is also what it
 * said when the server had not answered - and a page that waited with
 * `loading || !data` showed its placeholder for ever.
 *
 * The failed state is bound to the request's `isLoadingError`: failed with
 * nothing to show. A refresh that fails over rows already on screen leaves
 * them there (the notice at the foot of the screen says they may be out of
 * date); it never blanks a list an operator is reading.
 */

/** Pages that fetch and do not yet have the shared failed state, and the phase that gives it to them. */
export const NOT_YET_CONVERTED: Record<string, string> = {
  'Analytics.tsx': 'seven charts, each fetched by itself and none reading its loading flag - phase 6',
  'ClientPortal.tsx': 'six lists with no loading or failed state of any kind - phase 6',
  'LiveWall.tsx': 'the wall: its tiles need the video states, not a list state - phase 4',
  'Playback.tsx': 'the timeline and player need the video states - phase 4',
  'PostOrders.tsx': 'lists with no loading or failed state - phase 6',
  'Roles.tsx': 'lists with no loading or failed state - phase 6',
  'investigations/InvestigationSearch.tsx': 'a search that is run, not a list that is fetched - phase 5',
}

/** Files under pages/ that are not a page: what they fetch is a detail of something else. */
export const NOT_A_PAGE: Record<string, string> = {
  'intel/IntelNav.tsx': 'a row of links with a count; when the count cannot be fetched the links are still there, without it',
}

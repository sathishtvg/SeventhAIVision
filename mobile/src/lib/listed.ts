/**
 * A record the app is already holding in one of its lists.
 *
 * A detail screen is opened from a list, and that list — whichever filter it
 * was showing — has the row. Looking there first means the screen opens at
 * once and opens offline; the record itself is then asked for by its id, so
 * that what is shown does not depend on which page of which list it was on.
 *
 * `cached` is what `queryClient.getQueriesData({ queryKey: ['alerts'] })`
 * returns: every query under that key, lists and single records alike. Only
 * the lists are looked through.
 */
export function listed<T extends { id: string }>(
  cached: ReadonlyArray<readonly [unknown, unknown]>, id: string,
): T | undefined {
  for (const [, data] of cached) {
    if (!Array.isArray(data)) continue
    const row = (data as T[]).find((r) => r?.id === id)
    if (row) return row
  }
  return undefined
}

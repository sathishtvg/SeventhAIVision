import { useEffect } from 'react'
import { Box, LinearProgress, TableCell, TableRow } from '@mui/material'
import { announce } from '@/motion'

/** A refresh that is over sooner than this is not worth a sentence. */
const SAY_AFTER_MS = 600

/**
 * Tells a screen reader, once, that a list is being refreshed - if it is still
 * being refreshed after a moment. Said through the app's one announcer, not a
 * region of this component's own: every list having its own live region would
 * make them compete (the design skill's rule for live updates).
 */
function useSayRefreshing(label: string) {
  useEffect(() => {
    const timer = setTimeout(() => announce(label), SAY_AFTER_MS)
    return () => clearTimeout(timer)
  }, [label])
}

/**
 * Put before content that is the last list's, kept on screen while this
 * list's is fetched (`placeholderData: keepPreviousData` and
 * `isPlaceholderData`). It draws a thin line and dims everything after it in
 * the same container (`.sav-refreshing ~ *`, `motion.css`).
 *
 * Like every placeholder it waits a moment before it is seen, and so does the
 * dimming: a filter that answers at once changes the rows and nothing else.
 *
 * Not for the routine refresh a page makes every few seconds - the rows then
 * are current, and a line that came and went on each poll would say nothing.
 */
export function RefreshingLine({ label = 'Refreshing the list' }: { label?: string }) {
  useSayRefreshing(label)
  return (
    <Box className="sav-refreshing" aria-busy="true" sx={{ position: 'relative', height: 2, width: '100%', flexBasis: '100%', gridColumn: '1 / -1' }}>
      <LinearProgress aria-label={label} sx={{ height: 2, borderRadius: 1 }} />
    </Box>
  )
}

/** The same, as the first row of a table body: the rows after it are dimmed. */
export function TableRefreshingRow({ cols = 999, label = 'Refreshing the list' }: { cols?: number; label?: string }) {
  useSayRefreshing(label)
  return (
    <TableRow className="sav-refreshing" aria-busy="true">
      <TableCell colSpan={cols} sx={{ p: 0, border: 0, height: 2, lineHeight: 0, position: 'relative' }}>
        <LinearProgress aria-label={label} sx={{ height: 2 }} />
      </TableCell>
    </TableRow>
  )
}

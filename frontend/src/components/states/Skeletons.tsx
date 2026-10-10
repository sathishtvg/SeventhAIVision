import { Box, Skeleton } from '@mui/material'
import type { SxProps, Theme } from '@mui/material'
import { SkeletonRows } from '@/components/common/SkeletonRows'

/**
 * Placeholders shaped like what they stand in for.
 *
 * Each takes the room the content will take, so nothing moves when the content
 * replaces it, and - like every skeleton in the app - is seen only after a
 * moment (`motion.css`), so an answer that comes at once is not preceded by a
 * flash. None of them holds the content back.
 *
 * They say nothing a screen reader needs; the region they are in is marked
 * busy by `aria-busy` here, and what replaces them speaks for itself.
 */

/** Rows of a table: put it inside the `<TableBody>`. `cols` must be the header's column count. */
export function TableSkeleton({ cols, rows = 6 }: { cols: number; rows?: number }) {
  return <SkeletonRows cols={cols} rows={rows} />
}

/**
 * A table, where the placeholder cannot be rows of the table itself because
 * the table is not drawn until there is something to put in it: a header
 * strip and rows, the width of whatever it is in. Where the `<TableBody>` is
 * there to put rows into, `TableSkeleton` is the truer shape.
 */
export function TableBlockSkeleton({ rows = 6, rowHeight = 40 }: { rows?: number; rowHeight?: number }) {
  return (
    <Box aria-busy="true" sx={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 0.75, gridColumn: '1 / -1' }}>
      <Skeleton variant="rounded" height={36} />
      {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} variant="rounded" height={rowHeight} />)}
    </Box>
  )
}

/** A grid of cards: sites, cameras, drones, packages. */
export function CardGridSkeleton({ cards = 6, height = 132, minWidth = 260, sx }: {
  cards?: number; height?: number; minWidth?: number; sx?: SxProps<Theme>
}) {
  return (
    <Box
      aria-busy="true"
      sx={[
        { display: 'grid', gap: 2, gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))`, width: '100%', gridColumn: '1 / -1' },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      {Array.from({ length: cards }).map((_, i) => <Skeleton key={i} variant="rounded" height={height} />)}
    </Box>
  )
}

/** A strip of figures across the top of a dashboard. */
export function KpiSkeleton({ count = 4, height = 96 }: { count?: number; height?: number }) {
  return (
    <Box aria-busy="true" sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', width: '100%' }}>
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} variant="rounded" height={height} sx={{ flex: '1 1 180px', minWidth: 0 }} />
      ))}
    </Box>
  )
}

/** One dashboard card, for a card that loads by itself. */
export function DashboardCardSkeleton({ height = 132 }: { height?: number }) {
  return <Skeleton aria-busy="true" variant="rounded" height={height} width="100%" />
}

/** A chart: the plot area, and a line where the legend or axis will be. */
export function ChartSkeleton({ height = 240 }: { height?: number }) {
  return (
    <Box aria-busy="true" sx={{ width: '100%' }}>
      <Skeleton variant="rounded" height={Math.max(height - 28, 40)} />
      <Skeleton variant="text" width="38%" sx={{ mt: 1 }} />
    </Box>
  )
}

/** One record: a title, a few lines, a block. */
export function DetailSkeleton({ lines = 5, block = 160 }: { lines?: number; block?: number }) {
  return (
    <Box aria-busy="true" sx={{ width: '100%' }}>
      <Skeleton variant="text" width="42%" height={36} />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} variant="text" width={`${92 - ((i * 13) % 34)}%`} />
      ))}
      {block > 0 && <Skeleton variant="rounded" height={block} sx={{ mt: 2 }} />}
    </Box>
  )
}

/** A plain list: a line each, with something round at its start. */
export function ListSkeleton({ rows = 5, height = 44 }: { rows?: number; height?: number }) {
  return (
    <Box aria-busy="true" sx={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 1 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <Box key={i} sx={{ display: 'flex', alignItems: 'center', gap: 1.5, height }}>
          <Skeleton variant="circular" width={28} height={28} sx={{ flexShrink: 0 }} />
          <Skeleton variant="text" sx={{ flex: 1 }} />
        </Box>
      ))}
    </Box>
  )
}

/** A map before its tiles and its markers: the area it will fill, and nothing drawn on it. */
export function MapSkeleton({ height = 420 }: { height?: number | string }) {
  return <Skeleton aria-busy="true" variant="rounded" width="100%" height={height} />
}

import { Skeleton, TableCell, TableRow } from '@mui/material'

/**
 * Placeholder rows for a table that is still loading.
 *
 * The height is deliberate rather than left to the default. A `variant="text"`
 * Skeleton is scaled down from the font's line box, so a placeholder row came
 * out visibly shorter than a real one — and real rows here hold chips and
 * two-line cells. The table therefore rendered short, then grew the moment the
 * data arrived, shifting everything below it. Pinning the placeholder to the
 * height real content settles at keeps the layout still across that swap.
 *
 * `cols` must match the header's column count, otherwise the placeholder rows
 * are narrower than the header and the table visibly re-flows on load.
 */
export function SkeletonRows({ cols, rows = 4 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, i) => (
        <TableRow key={i}>
          {Array.from({ length: cols }).map((__, j) => (
            <TableCell key={j}>
              <Skeleton variant="rounded" height={24} />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  )
}

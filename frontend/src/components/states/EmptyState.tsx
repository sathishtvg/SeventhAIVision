import type { ReactNode } from 'react'
import { Box, TableCell, TableRow, Typography } from '@mui/material'
import type { SxProps, Theme } from '@mui/material'
import InboxOutlinedIcon from '@mui/icons-material/InboxOutlined'

export interface EmptyStateProps {
  /** What there is none of: "No alerts", "No cameras at this site". */
  title?: ReactNode
  /** What that means, or what to do: "New alerts appear here as they are raised." */
  hint?: ReactNode
  icon?: ReactNode
  /** The one thing to do about it, if there is one: an "Add camera" button. */
  action?: ReactNode
  compact?: boolean
  sx?: SxProps<Theme>
}

/**
 * The server answered, and there is nothing.
 *
 * Only for that. While a request is on its way the page shows a placeholder,
 * and when it failed it shows `ErrorState`; "nothing here" said in either of
 * those cases would be untrue.
 */
export function EmptyState({ title = 'Nothing here yet', hint, icon, action, compact = false, sx }: EmptyStateProps) {
  return (
    <Box
      sx={[
        {
          width: '100%', flexBasis: '100%', gridColumn: '1 / -1',
          display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center',
          gap: compact ? 0.5 : 1, py: compact ? 2 : 5, px: 2, color: 'text.secondary',
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      <Box sx={{ opacity: 0.55, display: 'grid', placeItems: 'center', '& svg': { fontSize: compact ? 22 : 34 } }}>
        {icon ?? <InboxOutlinedIcon />}
      </Box>
      <Typography variant={compact ? 'body2' : 'subtitle1'} color="text.primary" sx={{ fontWeight: 600 }}>{title}</Typography>
      {hint && <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 520 }}>{hint}</Typography>}
      {action && <Box sx={{ mt: 0.5 }}>{action}</Box>}
    </Box>
  )
}

/** The same, as a row of a table. `cols` left out, the cell spans every column. */
export function TableEmptyRow({ cols = 999, children, ...rest }: EmptyStateProps & { cols?: number; children?: ReactNode }) {
  return (
    <TableRow>
      <TableCell colSpan={cols} sx={{ borderBottom: 0 }}>
        <EmptyState compact title={children ?? rest.title} {...rest} />
      </TableCell>
    </TableRow>
  )
}

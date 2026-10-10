import { Box, Button, TableCell, TableRow, Typography } from '@mui/material'
import type { SxProps, Theme } from '@mui/material'
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutlined'
import RefreshIcon from '@mui/icons-material/Refresh'
import { errorText } from './errorText'

export interface ErrorStateProps {
  /** What the request failed with. The words shown are `errorText(error)`. */
  error?: unknown
  /** Asks again. Left out, there is no button - for something that cannot be retried from here. */
  onRetry?: () => unknown
  title?: string
  /** For a slot inside a card or a table, where the full size would crowd. */
  compact?: boolean
  sx?: SxProps<Theme>
}

/**
 * A request failed, and the page says so.
 *
 * Before this a page that was not loading and had no rows said "No … found",
 * which is also what it said when the server had not answered: an operator
 * could not tell a quiet site from a broken connection. This is the third
 * answer, beside "loading" and "nothing here".
 *
 * It is an alert (a screen reader is told at once), it says what kind of
 * failure it was, and it offers the one thing to do about it. It fills the row
 * it is put in whether that is a block, a flex row or a grid.
 */
export function ErrorState({ error, onRetry, title = 'Could not load this', compact = false, sx }: ErrorStateProps) {
  return (
    <Box
      role="alert"
      sx={[
        {
          width: '100%', flexBasis: '100%', gridColumn: '1 / -1',
          display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center',
          gap: compact ? 0.75 : 1, py: compact ? 2 : 5, px: 2,
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      <ErrorOutlineIcon color="error" sx={{ fontSize: compact ? 22 : 30 }} />
      <Typography variant={compact ? 'body2' : 'subtitle1'} sx={{ fontWeight: 600 }}>{title}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 520 }}>{errorText(error)}</Typography>
      {onRetry && (
        <Button size="small" variant="outlined" startIcon={<RefreshIcon />} onClick={() => { void onRetry() }} sx={{ mt: 0.5 }}>
          Try again
        </Button>
      )}
    </Box>
  )
}

/**
 * The same, as a row of a table: put it where the rows would be.
 * `cols` is the number of columns; left out, the cell simply spans them all.
 */
export function TableErrorRow({ cols = 999, ...rest }: ErrorStateProps & { cols?: number }) {
  return (
    <TableRow>
      <TableCell colSpan={cols} sx={{ borderBottom: 0 }}>
        <ErrorState compact {...rest} />
      </TableCell>
    </TableRow>
  )
}

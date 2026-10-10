import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { Box, Button, LinearProgress, Typography } from '@mui/material'
import type { Theme } from '@mui/material'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutlined'
import { announce } from '@/motion'
import { errorText } from './errorText'

/** Red that small text can be read in: the theme's own on a dark page, its darker one on a light page. */
const readableRed = (t: Theme) => (t.palette.mode === 'dark' ? t.palette.error.main : t.palette.error.dark)

export interface ProgressStateProps {
  status: 'working' | 'done' | 'failed'
  /** What is being done, and what it became: "Building the evidence package", "Package ready". */
  label: ReactNode
  /**
   * How far along, 0 to 100 - ONLY a number the server (or the browser, for an
   * upload) reported. Leave it out when nothing reports one: the bar then
   * shows that work is going on and claims nothing about how much.
   */
  value?: number | null
  /** A line under the label: "This can take a few minutes for a long recording." */
  detail?: ReactNode
  error?: unknown
  onRetry?: () => unknown
  /** Offered only where the work can really be stopped. */
  onCancel?: () => unknown
  /** What to do with the result: a Download button. Shown when `status` is `done`. */
  action?: ReactNode
}

/**
 * Work that takes long enough to say something about: an export, a package,
 * a report.
 *
 * It never invents progress. With no `value` the bar is the moving kind that
 * means "working" and no percentage is written; "done" is shown when the
 * caller says the thing is ready and not a moment before, because the caller
 * is told so by the server.
 */
export function ProgressState({ status, label, value, detail, error, onRetry, onCancel, action }: ProgressStateProps) {
  const known = typeof value === 'number' && Number.isFinite(value)
  const percent = known ? Math.max(0, Math.min(100, Math.round(value as number))) : null

  return (
    <Box role={status === 'failed' ? 'alert' : 'status'} aria-live={status === 'failed' ? undefined : 'polite'} sx={{ width: '100%' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: status === 'working' ? 0.75 : 0 }}>
        {status === 'done' && <CheckCircleIcon color="success" fontSize="small" />}
        {status === 'failed' && <ErrorOutlineIcon color="error" fontSize="small" />}
        <Typography variant="body2" sx={{ fontWeight: 600, flex: 1, minWidth: 0 }}>{label}</Typography>
        {status === 'working' && percent !== null && (
          <Typography variant="caption" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>{percent}%</Typography>
        )}
        {status === 'working' && onCancel && <Button size="small" onClick={() => { void onCancel() }}>Cancel</Button>}
        {status === 'failed' && onRetry && <Button size="small" variant="outlined" onClick={() => { void onRetry() }}>Try again</Button>}
        {status === 'done' && action}
      </Box>
      {status === 'working' && (
        percent !== null
          ? <LinearProgress variant="determinate" value={percent} sx={{ height: 4, borderRadius: 2 }} />
          : <LinearProgress sx={{ height: 4, borderRadius: 2 }} />
      )}
      {status === 'failed' && <Typography variant="caption" sx={{ color: readableRed }}>{errorText(error)}</Typography>}
      {detail && status !== 'failed' && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>{detail}</Typography>
      )}
    </Box>
  )
}

export interface SaveStatusProps {
  state: 'idle' | 'saving' | 'saved' | 'failed'
  error?: unknown
  savingText?: string
  savedText?: string
}

/**
 * Beside a Save button: saving, saved, or why not. "Saved" is for the caller
 * to set when the server has answered; this only says it.
 */
export function SaveStatus({ state, error, savingText = 'Saving…', savedText = 'Saved' }: SaveStatusProps) {
  if (state === 'idle') return null
  if (state === 'failed') {
    return (
      <Typography role="alert" variant="caption" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, color: readableRed }}>
        <ErrorOutlineIcon sx={{ fontSize: 16 }} />Not saved: {errorText(error)}
      </Typography>
    )
  }
  return (
    <Typography
      role="status" aria-live="polite" variant="caption"
      color={state === 'saved' ? 'text.primary' : 'text.secondary'}
      sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}
    >
      {state === 'saved' && <CheckCircleIcon color="success" sx={{ fontSize: 16, animation: 'sav-tick 280ms cubic-bezier(0.0, 0.0, 0.2, 1)' }} />}
      {state === 'saved' ? savedText : savingText}
    </Typography>
  )
}

/**
 * A brief confirmation where something was done: "Snapshot saved" over a
 * camera, "Bookmark added" on a timeline. It is words and a tick; the tick's
 * small pop is the only part that moves, and it is shown only while `show` is
 * true - which the caller sets when the server has confirmed, and clears.
 *
 * A screen reader is told through the app's one announcer when it appears: a
 * wall of camera tiles each with a live region of its own would talk over
 * itself.
 */
export function SuccessTick({ show, label }: { show: boolean; label: string }) {
  useEffect(() => { if (show) announce(label) }, [show, label])
  if (!show) return null
  return (
    <Box
      sx={{
        display: 'inline-flex', alignItems: 'center', gap: 0.75, px: 1.25, py: 0.5, borderRadius: 99,
        bgcolor: 'rgba(34,197,94,0.16)', border: '1px solid rgba(34,197,94,0.45)', color: 'text.primary',
        fontSize: '0.78rem', fontWeight: 600,
        animationName: 'sav-tick', animationDuration: '280ms', animationTimingFunction: 'cubic-bezier(0.0, 0.0, 0.2, 1)',
      }}
    >
      <CheckCircleIcon color="success" sx={{ fontSize: 16 }} />{label}
    </Box>
  )
}

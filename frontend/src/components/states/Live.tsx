import { useEffect, useState } from 'react'
import { Box, Button, Chip, CircularProgress, Typography } from '@mui/material'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import SignalWifiOffIcon from '@mui/icons-material/SignalWifiOff'
import VideocamOffIcon from '@mui/icons-material/VideocamOff'
import { useAnnouncer } from '@/motion'
import { ageText } from './freshness'

const HIDDEN = {
  position: 'absolute', width: 1, height: 1, p: 0, m: -1, overflow: 'hidden',
  clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap', border: 0,
} as const

/**
 * The one place a screen reader is told what changed: whatever was last said
 * with `announce()`. In the shell, once. Polite - it waits its turn - and
 * invisible; what it says is always also shown some other way.
 */
export function LiveAnnouncer() {
  const text = useAnnouncer((s) => s.text)
  const count = useAnnouncer((s) => s.count)
  // The same words said twice are two announcements: a reader only speaks a
  // region whose text changed, so every second one carries a space it does not say.
  return (
    <Box role="status" aria-live="polite" aria-atomic="true" data-testid="live-announcer" sx={HIDDEN}>
      {text}{count % 2 === 1 ? ' ' : ''}
    </Box>
  )
}

export interface StaleBadgeProps {
  /** When the reading was taken, as the server gave it. `null`: there has been none. */
  at: string | number | Date | null | undefined
  /** Older than this, it is not live. */
  staleAfterMs?: number
  /** What a fresh reading is called. */
  liveLabel?: string
}

/**
 * Whether something that reports by itself - a drone, a vehicle, a guard's
 * phone - has been heard from lately, in words: "Live", "Stale · 3 min ago",
 * or "No data yet".
 *
 * It works the age out again every few seconds from the time the server gave,
 * so a reading goes stale on screen when it goes stale, whether or not the
 * page fetched anything in between. Nothing is to be drawn as moving or live
 * that this would call stale.
 */
export function StaleBadge({ at, staleAfterMs = 30_000, liveLabel = 'Live' }: StaleBadgeProps) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5000)
    return () => clearInterval(timer)
  }, [])

  const taken = at === null || at === undefined ? NaN : new Date(at).getTime()
  if (Number.isNaN(taken)) return <Chip size="small" variant="outlined" label="No data yet" />
  const age = now - taken
  // The words are in the text colour, readable on a light page as on a dark one; the colour is the icon's.
  return age <= staleAfterMs
    ? (
      <Chip
        size="small" variant="outlined" label={liveLabel}
        icon={<FiberManualRecordIcon color="success" sx={{ fontSize: '10px !important' }} />}
        sx={{ color: 'text.primary', borderColor: 'success.main' }}
      />
    )
    : (
      <Chip
        size="small" variant="outlined" label={`Stale · ${ageText(age)}`}
        icon={<SignalWifiOffIcon color="warning" />}
        sx={{ color: 'text.primary', borderColor: 'warning.main' }}
      />
    )
}

export interface VideoLoadingStateProps {
  /**
   * connecting  asked for, no picture yet
   * buffering   it was playing and has stopped for more
   * failed      it could not be played
   * offline     the camera is known to be off line
   */
  state: 'connecting' | 'buffering' | 'failed' | 'offline'
  /** The camera's name, or why: shown under the state. */
  detail?: string
  onRetry?: () => unknown
}

const VIDEO_WORDS: Record<VideoLoadingStateProps['state'], string> = {
  connecting: 'Connecting',
  buffering: 'Buffering',
  failed: 'No picture',
  offline: 'Camera offline',
}

/**
 * What a video tile says when it is not showing a picture, so that a tile
 * that has not connected cannot be mistaken for a dark room.
 *
 * It lies over the tile and is drawn cheaply on purpose: a flat background, a
 * small spinner, text. No blur, no shadow, no glow - anything costly here is
 * paid once for every camera on a wall. It covers the picture only when there
 * is no picture; `buffering` leaves the last frame visible beneath it.
 */
export function VideoLoadingState({ state, detail, onRetry }: VideoLoadingStateProps) {
  const failed = state === 'failed' || state === 'offline'
  return (
    <Box
      role={failed ? 'alert' : 'status'}
      sx={{
        position: 'absolute', inset: 0, zIndex: 1,
        // The tile is the container: on a wall of thirty-six the tile is small, and what does not fit is left out
        // (the camera's name first, then the button) so that the state itself is never cut off.
        containerType: 'size',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 0.5,
        bgcolor: state === 'buffering' ? 'rgba(2,6,23,0.62)' : '#05070f',
        color: failed ? 'rgba(248,250,252,0.82)' : 'rgba(248,250,252,0.72)',
        pointerEvents: onRetry && failed ? 'auto' : 'none',
        textAlign: 'center', px: 1, overflow: 'hidden',
        '& .vls-detail': { '@container (max-height: 110px)': { display: 'none' } },
        '& .vls-retry': { '@container (max-height: 150px)': { display: 'none' } },
        '& .vls-mark': { '@container (max-height: 64px)': { display: 'none' } },
      }}
    >
      {failed
        ? <VideocamOffIcon className="vls-mark" sx={{ fontSize: 22, opacity: 0.85 }} />
        : <CircularProgress className="vls-mark" size={20} thickness={4} color="inherit" />}
      <Typography variant="caption" sx={{ fontWeight: 600, letterSpacing: '0.04em', lineHeight: 1.3 }}>{VIDEO_WORDS[state]}</Typography>
      {detail && (
        <Typography className="vls-detail" variant="caption" sx={{ opacity: 0.75, maxWidth: '90%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', lineHeight: 1.3 }}>
          {detail}
        </Typography>
      )}
      {failed && onRetry && (
        <Button className="vls-retry" size="small" variant="outlined" color="inherit" onClick={() => { void onRetry() }} sx={{ mt: 0.5, py: 0.25 }}>
          Try again
        </Button>
      )}
    </Box>
  )
}

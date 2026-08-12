/**
 * VideoPlayer — the built-in player used by Playback, Recordings and Evidence.
 *
 * Wraps a plain `<video>` element, but adds the one thing a bare `<video>`
 * refuses to give you: a *visible* failure. When a browser cannot decode a
 * file it renders a black rectangle and reports nothing to the user, which is
 * exactly how recorded footage sat unplayable while looking like an empty
 * player. Any media error now surfaces as readable text naming the actual
 * cause, so a codec, permission or network problem is diagnosable from the UI
 * instead of from a codec probe on the server.
 */
import { useCallback, useRef, useState } from 'react'
import { Alert, Box, CircularProgress, type SxProps, type Theme } from '@mui/material'

/** `MediaError.code` values — see HTML spec. The wording is deliberately
 *  actionable rather than literal: an operator reads these, not a browser
 *  engineer. */
const MEDIA_ERROR_TEXT: Record<number, string> = {
  1: 'Playback was aborted.',
  2: 'Network error while loading this video — check your connection and retry.',
  3: 'This video is corrupt and cannot be decoded.',
  4: 'This video format cannot be played in the browser.',
}

interface VideoPlayerProps {
  src: string
  /** Resets playback state when it changes — pass the recording/evidence id so
   *  switching clips doesn't leave the previous one's error on screen. */
  playerKey?: string
  autoPlay?: boolean
  maxHeight?: number | string
  sx?: SxProps<Theme>
  /** Accessible name; falls back to a generic label. */
  label?: string
}

export function VideoPlayer({
  src,
  playerKey,
  autoPlay = false,
  maxHeight = '60vh',
  sx,
  label = 'Recorded video',
}: VideoPlayerProps) {
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const videoRef = useRef<HTMLVideoElement>(null)

  const handleError = useCallback(() => {
    const code = videoRef.current?.error?.code
    setLoading(false)
    setError(
      (code != null && MEDIA_ERROR_TEXT[code]) ||
        'This video could not be played.',
    )
  }, [])

  const handleLoaded = useCallback(() => {
    setLoading(false)
    setError(null)
  }, [])

  return (
    <Box sx={{ position: 'relative', ...sx }}>
      <Box
        component="video"
        ref={videoRef}
        // Remounts on clip change so the element re-reads src from scratch
        // rather than keeping the previous clip's decoded state.
        key={playerKey ?? src}
        controls
        preload="metadata"
        autoPlay={autoPlay}
        playsInline
        aria-label={label}
        src={src}
        onLoadedMetadata={handleLoaded}
        onError={handleError}
        sx={{
          width: '100%',
          maxHeight,
          display: 'block',
          borderRadius: 1,
          background: '#000',
          // A failed video collapses to near-zero height, which makes the
          // error message float over nothing; keep the frame reserved.
          minHeight: error ? 0 : 180,
        }}
      />

      {loading && !error && (
        <Box
          sx={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            pointerEvents: 'none',
          }}
        >
          <CircularProgress size={28} />
        </Box>
      )}

      {error && (
        <Alert severity="error" variant="outlined" sx={{ mt: 1 }}>
          {error}
        </Alert>
      )}
    </Box>
  )
}

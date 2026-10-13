/**
 * HlsPlayer (Gap 90) — plays an HLS (.m3u8) stream in a <video> element via
 * hls.js, with native-HLS fallback (Safari/iOS). Retries the manifest while
 * the backend's ffmpeg session warms up (the playlist 503s until the first
 * segment lands), and self-heals on network/media errors so a control-room
 * wall recovers without a manual refresh.
 *
 * It says what it is doing. A <video> that has not connected is a black
 * rectangle, and so is a camera looking at a dark room: until there is a
 * first frame the tile says "Connecting", a picture that was playing and has
 * stopped for more says "Buffering" over its last frame, and a stream that
 * could not be played says "No picture" and can be started again. What it
 * says comes from the element and from hls.js, never from a timer, and it is
 * taken away the moment there is a picture.
 */
import { useEffect, useRef, useState } from 'react'
import Hls from 'hls.js'
import type { SxProps, Theme } from '@mui/material'
import { Box } from '@mui/material'
import { VideoLoadingState } from '@/components/states'

interface Props {
  src: string
  /** Where the player sits and how big it is: given to the box that holds the picture and what is said over it. */
  sx?: SxProps<Theme>
  alt?: string
}

type Picture = 'connecting' | 'playing' | 'buffering' | 'failed'

/** Whether this browser can play the stream at all: through hls.js, or by itself (Safari, iOS, some TV webviews). */
function canPlayHls(): boolean {
  if (Hls.isSupported()) return true
  try {
    return Boolean(document.createElement('video').canPlayType('application/vnd.apple.mpegurl'))
  } catch {
    return false
  }
}

export function HlsPlayer({ src, sx, alt }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [supported] = useState(canPlayHls)
  const [picture, setPicture] = useState<Picture>('connecting')
  // "Try again" starts the stream afresh, as a new address does.
  const [attempt, setAttempt] = useState(0)
  const [shown, setShown] = useState(src)
  if (shown !== src) {
    setShown(src)
    setPicture('connecting')
  }

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    let hls: Hls | null = null

    // A first frame, or playback under way: there is a picture, whatever was said before.
    const hasPicture = () => setPicture('playing')
    // Stopped for more data. Before the first frame that is still "connecting".
    const stopped = () => setPicture((was) => (was === 'playing' ? 'buffering' : was))
    const failed = () => setPicture('failed')
    video.addEventListener('loadeddata', hasPicture)
    video.addEventListener('playing', hasPicture)
    video.addEventListener('waiting', stopped)

    if (Hls.isSupported()) {
      hls = new Hls({
        lowLatencyMode: true,
        backBufferLength: 15,
        // Tolerate the warm-up window: the playlist 503s until ffmpeg emits
        // the first segment (~2-3s).
        manifestLoadingMaxRetry: 10,
        manifestLoadingRetryDelay: 1000,
        manifestLoadingMaxRetryTimeout: 20000,
        levelLoadingMaxRetry: 10,
        fragLoadingMaxRetry: 10,
      })
      hls.loadSource(src)
      hls.attachMedia(video)
      hls.on(Hls.Events.MANIFEST_PARSED, () => { void video.play().catch(() => {}) })
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        if (!data.fatal || !hls) return
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
          // hls.js has given up after its own retries. It is asked to go on, as it always was, and the tile
          // says there is no picture; if the stream comes back, "playing" takes that away again.
          failed()
          hls.startLoad()
        } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          hls.recoverMediaError()
        } else {
          failed()
          hls.destroy()
        }
      })
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS (Safari, iOS, some smart-TV webviews)
      video.addEventListener('error', failed)
      video.src = src
      void video.play().catch(() => {})
    }

    return () => {
      video.removeEventListener('loadeddata', hasPicture)
      video.removeEventListener('playing', hasPicture)
      video.removeEventListener('waiting', stopped)
      video.removeEventListener('error', failed)
      hls?.destroy()
    }
  }, [src, attempt])

  const retry = () => {
    setPicture('connecting')
    setAttempt((n) => n + 1)
  }

  return (
    <Box
      sx={[
        { position: 'relative', width: '100%', height: '100%', overflow: 'hidden', background: '#000' },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      <Box
        component="video"
        ref={videoRef}
        muted
        autoPlay
        playsInline
        aria-label={alt}
        // The box above has the caller's size; the picture fills it, and is held to the same height limit.
        sx={{ width: '100%', height: '100%', maxHeight: 'inherit', objectFit: 'contain', display: 'block', background: '#000' }}
      />
      {!supported
        ? <VideoLoadingState state="failed" detail="This browser cannot play this stream" />
        : picture !== 'playing' && <VideoLoadingState state={picture} onRetry={picture === 'failed' ? retry : undefined} />}
    </Box>
  )
}

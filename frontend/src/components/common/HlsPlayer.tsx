/**
 * HlsPlayer (Gap 90) — plays an HLS (.m3u8) stream in a <video> element via
 * hls.js, with native-HLS fallback (Safari/iOS). Retries the manifest while
 * the backend's ffmpeg session warms up (the playlist 503s until the first
 * segment lands), and self-heals on network/media errors so a control-room
 * wall recovers without a manual refresh.
 */
import { useEffect, useRef } from 'react'
import Hls from 'hls.js'
import type { SxProps, Theme } from '@mui/material'
import { Box } from '@mui/material'

interface Props {
  src: string
  sx?: SxProps<Theme>
  alt?: string
}

export function HlsPlayer({ src, sx }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    let hls: Hls | null = null

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
          hls.startLoad()
        } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          hls.recoverMediaError()
        } else {
          hls.destroy()
        }
      })
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS (Safari, iOS, some smart-TV webviews)
      video.src = src
      void video.play().catch(() => {})
    }

    return () => { hls?.destroy() }
  }, [src])

  return (
    <Box
      component="video"
      ref={videoRef}
      muted
      autoPlay
      playsInline
      sx={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block', background: '#000', ...sx }}
    />
  )
}

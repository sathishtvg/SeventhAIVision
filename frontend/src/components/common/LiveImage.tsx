/**
 * LiveImage — a camera's live view as the server sends it frame by frame
 * (MJPEG), which is the view a privacy zone is painted into.
 *
 * A bare <img> fed by a stream shows nothing until the first frame and
 * nothing when the stream could not be opened: an empty tile, which is also
 * what a camera in a dark room looks like. This one says "Connecting" until
 * there is a frame and "No picture" when there could not be one, and can be
 * asked again.
 *
 * It only ever looks at whether the element has a picture. It does not read
 * the picture, and it shows the address it is given and no other.
 */
import { useEffect, useRef, useState } from 'react'
import type { SxProps, Theme } from '@mui/material'
import { Box } from '@mui/material'
import { VideoLoadingState } from '@/components/states'

interface Props {
  src: string
  alt: string
  /** Where the view sits and how big it is: given to the box that holds the picture and what is said over it. */
  sx?: SxProps<Theme>
  /**
   * `true` (the default): the picture fills the box and is letterboxed, as a
   * tile on the wall. `false`: the picture is as wide as the box and as tall
   * as it comes, as in a dialog.
   */
  fill?: boolean
}

type Picture = 'connecting' | 'playing' | 'failed'

/** How often a tile that is still connecting looks for its first frame. */
const LOOK_EVERY_MS = 400

export function LiveImage({ src, alt, sx, fill = true }: Props) {
  const imgRef = useRef<HTMLImageElement>(null)
  const [picture, setPicture] = useState<Picture>('connecting')
  // "Try again" asks for the stream afresh: a new element, so the browser opens a new connection.
  const [attempt, setAttempt] = useState(0)

  // The size of the first frame is known as soon as it has been read, in every browser. Whether an <img> fed by a
  // stream that never ends says "load" is not the same in every browser, and a tile must never go on saying
  // "Connecting" over a picture that is there. So it is looked for - until it is found, and then no more.
  useEffect(() => {
    if (picture !== 'connecting') return
    const timer = setInterval(() => {
      if ((imgRef.current?.naturalWidth ?? 0) > 0) setPicture('playing')
    }, LOOK_EVERY_MS)
    return () => clearInterval(timer)
  }, [picture, attempt])

  const retry = () => {
    setPicture('connecting')
    setAttempt((n) => n + 1)
  }

  return (
    <Box
      sx={[
        { position: 'relative', width: '100%', ...(fill ? { height: '100%' } : { minHeight: picture === 'playing' ? 0 : 220 }) },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      <Box
        component="img"
        ref={imgRef}
        // A new address is shown in the same element: the last frame stays until the new stream's first one, so
        // a wall does not blink each time the session's token is renewed.
        key={attempt}
        src={src}
        alt={alt}
        onLoad={() => setPicture('playing')}
        onError={() => setPicture('failed')}
        sx={fill
          ? { width: '100%', height: '100%', objectFit: 'contain', display: 'block' }
          : { width: '100%', display: 'block' }}
      />
      {picture !== 'playing' && <VideoLoadingState state={picture} onRetry={picture === 'failed' ? retry : undefined} />}
    </Box>
  )
}

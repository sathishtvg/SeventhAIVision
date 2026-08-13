/**
 * MediaViewer — the one full-screen viewer for every image, snapshot, live
 * stream and recording in the app.
 *
 * WHY THIS EXISTS
 *     Each surface had rolled its own lightbox, and every one of them sized
 *     media by width alone inside a MUI DialogContent. DialogContent scrolls
 *     by default, so a 16:9 frame in a 900px-wide dialog became ~506px tall,
 *     overflowed a short window, and the operator got a scrollbar through a
 *     CCTV frame — the exact thing they need to take in at a glance.
 *
 *     The fix is not "add maxHeight". It is `minHeight: 0` on the flex child
 *     holding the media. A flex item defaults to `min-height: auto`, which
 *     refuses to shrink below its content, so every height cap on an ancestor
 *     is silently ignored and the overflow reappears the moment the media is
 *     taller than the viewport. That one line is why this component can promise
 *     "never scrolls" and mean it.
 *
 *     Media is sized `width/height: 100%` + `objectFit: contain` rather than
 *     `maxWidth: 100%`: on a large monitor an operator wants the frame to fill
 *     the space available, not sit small in the middle at its natural pixel
 *     size.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Box, Dialog, Divider, IconButton, Stack, Tooltip, Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import ZoomInIcon from '@mui/icons-material/ZoomIn'
import ZoomOutIcon from '@mui/icons-material/ZoomOut'
import FitScreenIcon from '@mui/icons-material/FitScreen'
import CropFreeIcon from '@mui/icons-material/CropFree'
import RotateRightIcon from '@mui/icons-material/RotateRight'
import FullscreenIcon from '@mui/icons-material/Fullscreen'
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit'
import DownloadIcon from '@mui/icons-material/Download'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import { VideoPlayer } from './VideoPlayer'

export interface MediaItem {
  /** Stable identity — drives remount so switching items never shows the
   *  previous one's decoded frame or error. */
  id: string
  kind: 'image' | 'video'
  src: string
  /** Shown in the header; falls back to the viewer's own title. */
  label?: string
  /** Omit to hide the download button (live streams have nothing to save). */
  downloadUrl?: string
  /** Live streams never stop loading, so the spinner would never clear. */
  live?: boolean
}

interface MediaViewerProps {
  open: boolean
  onClose: () => void
  items: MediaItem[]
  /** Which item to show first; the viewer owns the index after that. */
  startIndex?: number
  title?: string
}

const MIN_ZOOM = 1
const MAX_ZOOM = 8
const STEP = 1.25

export function MediaViewer({ open, onClose, items, startIndex = 0, title }: MediaViewerProps) {
  const [index, setIndex] = useState(startIndex)
  const [zoom, setZoom] = useState(1)
  const [rotation, setRotation] = useState(0)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isFullscreen, setIsFullscreen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null)

  const item = items[index]
  const multiple = items.length > 1

  const resetView = useCallback(() => {
    setZoom(1); setRotation(0); setPan({ x: 0, y: 0 })
  }, [])

  // Re-entering the viewer, or moving to another item, must start from a clean
  // view — inheriting the last item's 4x zoom would look like a broken image.
  useEffect(() => { if (open) { setIndex(startIndex); resetView() } }, [open, startIndex, resetView])
  useEffect(() => { resetView() }, [index, resetView])

  const zoomBy = useCallback((factor: number) => {
    setZoom((z) => {
      const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * factor))
      // Panning only means something while zoomed in; recentre on the way out
      // so the image cannot get stranded off-screen.
      if (next <= 1) setPan({ x: 0, y: 0 })
      return next
    })
  }, [])

  const step = useCallback((delta: number) => {
    if (!multiple) return
    setIndex((i) => (i + delta + items.length) % items.length)
  }, [multiple, items.length])

  const toggleFullscreen = useCallback(async () => {
    const el = rootRef.current
    if (!el) return
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else await el.requestFullscreen()
    } catch {
      // Fullscreen can be refused (permissions policy, or an unfocused
      // pop-out window). The dialog is already near-viewport, so there is
      // nothing to recover from — just don't crash the viewer.
    }
  }, [])

  useEffect(() => {
    const onChange = () => setIsFullscreen(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  // Keyboard shortcuts. Escape is left to the Dialog so one key always means
  // the same thing, and fullscreen is exited by the browser's own handler.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      switch (e.key) {
        case '+': case '=': zoomBy(STEP); break
        case '-': case '_': zoomBy(1 / STEP); break
        case '0': resetView(); break
        case '1': setZoom(1); setPan({ x: 0, y: 0 }); break
        case 'r': case 'R': setRotation((r) => (r + 90) % 360); break
        case 'f': case 'F': void toggleFullscreen(); break
        case 'ArrowLeft': step(-1); break
        case 'ArrowRight': step(1); break
        default: return
      }
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, zoomBy, resetView, step, toggleFullscreen])

  const onWheel = (e: React.WheelEvent) => {
    // Only take over the wheel while zoomed or when the user asks with a
    // modifier — otherwise a trackpad scroll over a gallery hijacks the page.
    if (!e.ctrlKey && zoom === 1) return
    zoomBy(e.deltaY < 0 ? STEP : 1 / STEP)
  }

  const onPointerDown = (e: React.PointerEvent) => {
    if (zoom <= 1) return
    dragRef.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y }
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current
    if (!d) return
    setPan({ x: d.panX + (e.clientX - d.x), y: d.panY + (e.clientY - d.y) })
  }
  const onPointerUp = () => { dragRef.current = null }

  if (!item) return null

  const isImage = item.kind === 'image'
  const transform = `translate(${pan.x}px, ${pan.y}px) scale(${zoom}) rotate(${rotation}deg)`

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      slotProps={{
        paper: {
          sx: {
            width: '96vw', height: '95vh',
            maxWidth: 'none', maxHeight: 'none',
            m: 0, background: '#05050f',
            display: 'flex', flexDirection: 'column',
            // The viewer owns its own layout end to end; nothing inside may
            // introduce a scrollbar.
            overflow: 'hidden',
          },
        },
      }}
    >
      {/* Everything lives inside this box rather than the Dialog's Paper so
          requestFullscreen has an element it can own. Fullscreening the Paper
          itself would fight MUI's own positioning of it. */}
      <Box
        ref={rootRef}
        sx={{
          flex: 1, minHeight: 0,
          display: 'flex', flexDirection: 'column',
          background: '#05050f',
        }}
      >
      {/* Header */}
      <Stack
        direction="row" spacing={1}
        // alignItems lives in sx, not as a prop — this MUI version's Stack
        // typing rejects it directly, which is why the codebase carries its
        // own Stack wrapper elsewhere.
        sx={{
          px: 2, py: 1, flexShrink: 0, alignItems: 'center',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
        }}
      >
        <Typography variant="subtitle2" noWrap sx={{ flex: 1, minWidth: 0 }}>
          {item.label ?? title ?? 'Media'}
          {multiple && (
            <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
              {index + 1} of {items.length}
            </Typography>
          )}
        </Typography>

        <Tooltip title="Zoom out  (−)">
          <span>
            <IconButton size="small" onClick={() => zoomBy(1 / STEP)} disabled={zoom <= MIN_ZOOM} aria-label="Zoom out">
              <ZoomOutIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Typography
          variant="caption" color="text.secondary"
          sx={{ minWidth: 44, textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}
        >
          {Math.round(zoom * 100)}%
        </Typography>
        <Tooltip title="Zoom in  (+)">
          <span>
            <IconButton size="small" onClick={() => zoomBy(STEP)} disabled={zoom >= MAX_ZOOM} aria-label="Zoom in">
              <ZoomInIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title="Fit to screen  (0)">
          <IconButton size="small" onClick={resetView} aria-label="Fit to screen">
            <FitScreenIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Actual size  (1)">
          <IconButton
            size="small" aria-label="Actual size"
            onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }) }}
          >
            <CropFreeIcon fontSize="small" />
          </IconButton>
        </Tooltip>

        {isImage && (
          <Tooltip title="Rotate  (R)">
            <IconButton size="small" onClick={() => setRotation((r) => (r + 90) % 360)} aria-label="Rotate">
              <RotateRightIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}

        <Divider orientation="vertical" flexItem sx={{ mx: 0.5, borderColor: 'rgba(255,255,255,0.12)' }} />

        {item.downloadUrl && (
          <Tooltip title="Download">
            <IconButton
              size="small" aria-label="Download"
              component="a" href={item.downloadUrl} download target="_blank" rel="noreferrer"
            >
              <DownloadIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        <Tooltip title={isFullscreen ? 'Exit full screen  (F)' : 'Full screen  (F)'}>
          <IconButton size="small" onClick={toggleFullscreen} aria-label="Toggle full screen">
            {isFullscreen ? <FullscreenExitIcon fontSize="small" /> : <FullscreenIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
        <Tooltip title="Close  (Esc)">
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Stack>

      {/* Stage.
          flex: 1 gives it the leftover height; minHeight: 0 is what actually
          lets it shrink — without it this box refuses to go below its content
          height and the whole dialog scrolls again. */}
      <Box
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={() => (zoom === 1 ? zoomBy(STEP * STEP) : resetView())}
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
          position: 'relative',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: '#000',
          cursor: zoom > 1 ? (dragRef.current ? 'grabbing' : 'grab') : 'default',
          touchAction: 'none',
        }}
      >
        {isImage ? (
          <Box
            component="img"
            key={item.id}
            src={item.src}
            alt={item.label ?? 'Media'}
            draggable={false}
            sx={{
              width: '100%', height: '100%',
              objectFit: 'contain',
              transform,
              transition: dragRef.current ? 'none' : 'transform 0.12s ease-out',
              userSelect: 'none',
            }}
          />
        ) : (
          <Box sx={{ width: '100%', height: '100%', transform, transition: 'transform 0.12s ease-out' }}>
            <VideoPlayer
              src={item.src}
              playerKey={item.id}
              label={item.label ?? 'Recorded video'}
              fit
            />
          </Box>
        )}

        {multiple && (
          <>
            <NavButton side="left" onClick={() => step(-1)} />
            <NavButton side="right" onClick={() => step(1)} />
          </>
        )}
      </Box>
      </Box>
    </Dialog>
  )
}

function NavButton({ side, onClick }: { side: 'left' | 'right'; onClick: () => void }) {
  return (
    <IconButton
      onClick={onClick}
      aria-label={side === 'left' ? 'Previous' : 'Next'}
      sx={{
        position: 'absolute', top: '50%', transform: 'translateY(-50%)',
        [side]: 12,
        background: 'rgba(0,0,0,0.55)',
        '&:hover': { background: 'rgba(0,0,0,0.75)' },
      }}
    >
      {side === 'left' ? <ChevronLeftIcon /> : <ChevronRightIcon />}
    </IconButton>
  )
}

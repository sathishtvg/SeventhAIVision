/**
 * Top-level restricted-zone polygon editor: freezes a still frame from the
 * selected camera's live feed (drawing on a constantly-refreshing MJPEG img
 * would move the reference content mid-polygon), then composes
 * ZoneDrawOverlay on top of it plus a small toolbar. Fully controlled —
 * reports the polygon via onChange, doesn't own the "committed" value.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Box, Button, ButtonGroup, Stack, Typography, CircularProgress } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { getStreams } from '@/api/cameras'
import { apiClient } from '@/api/client'
import { useAuthStore } from '@/store/auth'
import { computeContainRect, type MediaRect, type RectLike } from '@/lib/videoCoords'
import { ZoneDrawOverlay, type ZonePoint } from './ZoneDrawOverlay'

interface ZonePolygonEditorProps {
  cameraId: string | null
  value: ZonePoint[]
  onChange: (points: ZonePoint[]) => void
  severityColor?: string
}

export function ZonePolygonEditor({ cameraId, value, onChange, severityColor }: ZonePolygonEditorProps) {
  const token = useAuthStore((s) => s.accessToken)
  const containerRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)

  const [closed, setClosed] = useState(value.length >= 3)
  const [previewPoint, setPreviewPoint] = useState<ZonePoint | null>(null)
  const [capturedFrameUrl, setCapturedFrameUrl] = useState<string | null>(null)
  const [frameReady, setFrameReady] = useState(false)
  const [containerRect, setContainerRect] = useState<RectLike | null>(null)
  const [mediaRect, setMediaRect] = useState<MediaRect | null>(null)
  const [captureError, setCaptureError] = useState<string | null>(null)

  const { data: streams } = useQuery({
    queryKey: ['streams', cameraId],
    queryFn: () => getStreams(cameraId!),
    enabled: !!cameraId,
  })
  const streamId = streams?.[0]?.id ?? null

  const liveUrl = token && cameraId && streamId
    ? `${apiClient.defaults.baseURL}/api/v1/cameras/${cameraId}/streams/${streamId}/live?token=${token}`
    : null
  const displayUrl = capturedFrameUrl ?? liveUrl

  const recomputeRects = useCallback(() => {
    const container = containerRef.current
    const img = imgRef.current
    if (!container || !img || !img.naturalWidth || !img.naturalHeight) return
    const cRect = container.getBoundingClientRect()
    const rectLike: RectLike = { left: cRect.left, top: cRect.top, width: cRect.width, height: cRect.height }
    setContainerRect(rectLike)
    setMediaRect(computeContainRect(cRect.width, cRect.height, img.naturalWidth, img.naturalHeight))
  }, [])

  useEffect(() => {
    window.addEventListener('resize', recomputeRects)
    return () => window.removeEventListener('resize', recomputeRects)
  }, [recomputeRects])

  const handleImgLoad = () => {
    setFrameReady(true)
    recomputeRects()
  }

  const handleCapture = () => {
    const img = imgRef.current
    if (!img || !img.naturalWidth) return
    try {
      const canvas = document.createElement('canvas')
      canvas.width = img.naturalWidth
      canvas.height = img.naturalHeight
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.drawImage(img, 0, 0)
      const dataUrl = canvas.toDataURL('image/jpeg', 0.92)
      setCapturedFrameUrl(dataUrl)
      setCaptureError(null)
    } catch {
      setCaptureError('Capture failed — draw on the live feed instead.')
    }
  }

  const handleRetake = () => {
    setCapturedFrameUrl(null)
    setFrameReady(false)
    onChange([])
    setClosed(false)
    setPreviewPoint(null)
  }

  const handleAddPoint = (p: ZonePoint) => onChange([...value, p])
  const handleClosePolygon = () => setClosed(true)
  const handleUndo = () => onChange(value.slice(0, -1))
  const handleFinish = () => { if (value.length >= 3) setClosed(true) }
  const handleClear = () => { onChange([]); setClosed(false); setPreviewPoint(null) }
  const handleMoveVertex = (index: number, p: ZonePoint) => {
    const next = [...value]
    next[index] = p
    onChange(next)
  }

  return (
    <Box>
      <Box
        ref={containerRef}
        sx={{ position: 'relative', width: '100%', aspectRatio: '16/9', bgcolor: 'rgba(0,0,0,0.7)', borderRadius: 1, overflow: 'hidden' }}
      >
        {displayUrl ? (
          <Box
            component="img"
            ref={imgRef}
            src={displayUrl}
            alt="Camera feed"
            crossOrigin={capturedFrameUrl ? undefined : 'anonymous'}
            onLoad={handleImgLoad}
            sx={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
          />
        ) : (
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <Typography variant="caption" color="text.disabled">
              {cameraId ? 'Waiting for camera feed…' : 'Select a camera first'}
            </Typography>
          </Box>
        )}

        {!frameReady && displayUrl && (
          <Box sx={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <CircularProgress size={28} />
          </Box>
        )}

        {frameReady && (
          <ZoneDrawOverlay
            points={value}
            closed={closed}
            previewPoint={previewPoint}
            severityColor={severityColor}
            containerRect={containerRect}
            mediaRect={mediaRect}
            onAddPoint={handleAddPoint}
            onClosePolygon={handleClosePolygon}
            onMoveVertex={handleMoveVertex}
            onPreviewMove={setPreviewPoint}
          />
        )}
      </Box>

      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mt: 1 }}>
        {!capturedFrameUrl ? (
          <Button size="small" variant="outlined" disabled={!frameReady} onClick={handleCapture}>
            Capture Frame
          </Button>
        ) : (
          <Button size="small" variant="outlined" onClick={handleRetake}>Retake</Button>
        )}
        <ButtonGroup size="small">
          <Button disabled={value.length === 0 || closed} onClick={handleUndo}>Undo</Button>
          <Button disabled={value.length < 3 || closed} onClick={handleFinish}>Finish</Button>
          <Button disabled={value.length === 0} onClick={handleClear}>Clear</Button>
        </ButtonGroup>
        <Typography variant="caption" color="text.secondary">
          {closed
            ? `${value.length} points — closed (drag vertices to adjust)`
            : value.length === 0
              ? 'Click on the frame to start drawing'
              : `${value.length} point${value.length === 1 ? '' : 's'} — click first point or press Finish to close`}
        </Typography>
      </Stack>
      {captureError && (
        <Typography variant="caption" color="error">{captureError}</Typography>
      )}
    </Box>
  )
}

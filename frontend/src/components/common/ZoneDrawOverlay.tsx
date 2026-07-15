/**
 * Interactive polygon-drawing SVG layer for the restricted-zone editor.
 * Sibling to DetectionOverlay.tsx (same viewBox/rendering conventions —
 * VB=1000, preserveAspectRatio="none") but pointerEvents:'auto' with its
 * own click/drag handling, since read-only annotation and interactive
 * editing are different contracts. Fully controlled: owns no vertex state,
 * just reports pointer gestures translated into normalized coordinates.
 */
import { useCallback, useRef, useState } from 'react'
import { Box } from '@mui/material'
import { screenToNormalized, type MediaRect, type RectLike } from '@/lib/videoCoords'

const VB = 1000
const CLOSE_HIT_PX = 14

export interface ZonePoint { x: number; y: number }

interface ZoneDrawOverlayProps {
  points: ZonePoint[]
  closed: boolean
  previewPoint: ZonePoint | null
  severityColor?: string
  containerRect: RectLike | null
  mediaRect: MediaRect | null
  onAddPoint: (p: ZonePoint) => void
  onClosePolygon: () => void
  onMoveVertex: (index: number, p: ZonePoint) => void
  onPreviewMove: (p: ZonePoint | null) => void
}

export function ZoneDrawOverlay({
  points, closed, previewPoint, severityColor = '#6C63FF',
  containerRect, mediaRect, onAddPoint, onClosePolygon, onMoveVertex, onPreviewMove,
}: ZoneDrawOverlayProps) {
  const [draggingIndex, setDraggingIndex] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const toNormalized = useCallback(
    (clientX: number, clientY: number): ZonePoint | null => {
      if (!containerRect || !mediaRect) return null
      return screenToNormalized(clientX, clientY, containerRect, mediaRect)
    },
    [containerRect, mediaRect],
  )

  const handleRootPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (closed || draggingIndex !== null) return
    const p = toNormalized(e.clientX, e.clientY)
    if (!p) return

    if (points.length >= 3 && containerRect && mediaRect) {
      const first = points[0]
      const firstScreenX = containerRect.left + mediaRect.offsetX + first.x * mediaRect.renderedW
      const firstScreenY = containerRect.top + mediaRect.offsetY + first.y * mediaRect.renderedH
      const dist = Math.hypot(e.clientX - firstScreenX, e.clientY - firstScreenY)
      if (dist <= CLOSE_HIT_PX) {
        onClosePolygon()
        return
      }
    }
    onAddPoint(p)
  }

  const handleRootPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (draggingIndex !== null) {
      const p = toNormalized(e.clientX, e.clientY)
      if (p) onMoveVertex(draggingIndex, p)
      return
    }
    if (closed || points.length === 0) return
    onPreviewMove(toNormalized(e.clientX, e.clientY))
  }

  const handleVertexPointerDown = (index: number) => (e: React.PointerEvent<SVGCircleElement>) => {
    if (!closed) return
    e.stopPropagation()
    ;(e.target as Element).setPointerCapture(e.pointerId)
    setDraggingIndex(index)
  }

  const handlePointerUp = () => setDraggingIndex(null)

  const toSvg = (p: ZonePoint) => `${p.x * VB},${p.y * VB}`
  const linePoints = points.map(toSvg).join(' ')
  const canClose = points.length >= 3 && !closed

  return (
    <Box
      component="svg"
      ref={svgRef}
      viewBox={`0 0 ${VB} ${VB}`}
      preserveAspectRatio="none"
      sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%', cursor: closed ? 'default' : 'crosshair' }}
      onPointerDown={handleRootPointerDown}
      onPointerMove={handleRootPointerMove}
      onPointerUp={handlePointerUp}
      onPointerLeave={() => { if (draggingIndex === null) onPreviewMove(null) }}
    >
      {closed && points.length >= 3 ? (
        <polygon points={linePoints} fill={`${severityColor}22`} stroke={severityColor} strokeWidth={4} />
      ) : points.length > 0 ? (
        <>
          <polyline points={linePoints} fill="none" stroke={severityColor} strokeWidth={4} />
          {previewPoint && (
            <line
              x1={points[points.length - 1].x * VB} y1={points[points.length - 1].y * VB}
              x2={previewPoint.x * VB} y2={previewPoint.y * VB}
              stroke={severityColor} strokeWidth={3} strokeDasharray="6 4"
            />
          )}
        </>
      ) : null}

      {points.map((p, i) => (
        <circle
          key={i}
          cx={p.x * VB} cy={p.y * VB}
          r={i === 0 && canClose ? 16 : closed ? 12 : 10}
          fill={severityColor}
          stroke="#fff" strokeWidth={2}
          style={{ cursor: closed ? 'move' : i === 0 && canClose ? 'pointer' : 'default' }}
          onPointerDown={handleVertexPointerDown(i)}
          onPointerUp={handlePointerUp}
        >
          {i === 0 && canClose && (
            <animate attributeName="r" values="16;20;16" dur="1.2s" repeatCount="indefinite" />
          )}
        </circle>
      ))}
    </Box>
  )
}

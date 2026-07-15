/**
 * DetectionOverlay (Gap 92) — an SVG annotation layer drawn over a live feed.
 *
 *  - Restricted zones: normalized 0-1 polygons → always precise.
 *  - Recent detections: pixel bounding boxes scaled by the source frame
 *    dimensions the AI worker recorded (drawn only when those dims are known).
 *
 * The layer is pointer-events:none and fills its positioned parent. It assumes
 * the media fills the box (the LiveWall cells are 16:9, matching typical camera
 * output); minor letterboxing on odd aspect ratios is acceptable for an
 * at-a-glance "the AI is watching" annotation.
 */
import { useMemo } from 'react'
import { Box } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

const VB = 1000 // viewBox extent

export interface OverlayBox { x: number; y: number; w: number; h: number }

/** Pure: pixel bbox → 0..VB rect, or null when frame dims are unknown. */
export function bboxToRect(
  bbox: { x1: number; y1: number; x2: number; y2: number },
  frameW: number | null | undefined,
  frameH: number | null | undefined,
): OverlayBox | null {
  if (!frameW || !frameH) return null
  const x = (Math.min(bbox.x1, bbox.x2) / frameW) * VB
  const y = (Math.min(bbox.y1, bbox.y2) / frameH) * VB
  const w = (Math.abs(bbox.x2 - bbox.x1) / frameW) * VB
  const h = (Math.abs(bbox.y2 - bbox.y1) / frameH) * VB
  return { x, y, w, h }
}

const SEVERITY_STROKE: Record<string, string> = {
  critical: '#FF4560', high: '#FF7F50', medium: '#FFA500', low: '#00E396',
}

interface OverlayData {
  zones: { id: string; name: string; polygon: { x: number; y: number }[]; severity: string; applies_to_modules: string[] }[]
  detections: {
    id: string; module_type: string; confidence: number | null
    bounding_box: { x1: number; y1: number; x2: number; y2: number }
    frame_width: number | null; frame_height: number | null
  }[]
}

interface DetectionOverlayProps {
  cameraId: string
  enabled: boolean
  /** Restrict which modules' zones/detections render. Omitted or undefined =
   * show everything (Zones.tsx's read-only usage doesn't filter by module). */
  moduleFilter?: string[]
}

export function DetectionOverlay({ cameraId, enabled, moduleFilter }: DetectionOverlayProps) {
  const { data } = useQuery<OverlayData>({
    queryKey: ['camera-overlay', cameraId],
    queryFn: () => apiClient.get(`/api/v1/cameras/${cameraId}/overlay`).then((r) => r.data),
    enabled,
    refetchInterval: 2000,
  })

  const visibleDetections = useMemo(
    () => moduleFilter ? (data?.detections ?? []).filter((d) => moduleFilter.includes(d.module_type)) : (data?.detections ?? []),
    [data, moduleFilter],
  )
  const visibleZones = useMemo(
    () => moduleFilter
      ? (data?.zones ?? []).filter((z) => z.applies_to_modules?.some((m) => moduleFilter.includes(m)))
      : (data?.zones ?? []),
    [data, moduleFilter],
  )

  const boxes = useMemo(
    () => visibleDetections
      .map((d) => ({ d, rect: bboxToRect(d.bounding_box, d.frame_width, d.frame_height) }))
      .filter((b) => b.rect !== null),
    [visibleDetections],
  )

  if (!enabled || !data) return null

  return (
    <Box
      component="svg"
      viewBox={`0 0 ${VB} ${VB}`}
      preserveAspectRatio="none"
      sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
    >
      {/* Restricted zones */}
      {visibleZones.map((z) => (
        <g key={z.id}>
          <polygon
            points={z.polygon.map((p) => `${p.x * VB},${p.y * VB}`).join(' ')}
            fill={(SEVERITY_STROKE[z.severity] ?? '#6C63FF') + '22'}
            stroke={SEVERITY_STROKE[z.severity] ?? '#6C63FF'}
            strokeWidth={3}
            strokeDasharray="10 6"
          />
          {z.polygon[0] && (
            <text x={z.polygon[0].x * VB + 8} y={z.polygon[0].y * VB + 26}
                  fill={SEVERITY_STROKE[z.severity] ?? '#6C63FF'} fontSize={26} fontWeight={700}>
              {z.name}
            </text>
          )}
        </g>
      ))}

      {/* Recent detection boxes */}
      {boxes.map(({ d, rect }) => (
        <g key={d.id}>
          <rect x={rect!.x} y={rect!.y} width={rect!.w} height={rect!.h}
                fill="none" stroke="#00E396" strokeWidth={3} />
          <text x={rect!.x + 4} y={rect!.y - 6} fill="#00E396" fontSize={24} fontWeight={700}>
            {d.module_type}{d.confidence != null ? ` ${Math.round(d.confidence * 100)}%` : ''}
          </text>
        </g>
      ))}
    </Box>
  )
}

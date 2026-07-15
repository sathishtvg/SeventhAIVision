import React, { useState } from 'react'
import { LayoutChangeEvent, Pressable, StyleSheet, View } from 'react-native'
import { colors } from '@/theme'
import { normalizedToScreen, screenToNormalized } from '@/lib/videoCoords'

const CLOSE_HIT_RADIUS = 24

export interface ZonePoint {
  x: number
  y: number
}

interface Props {
  points: ZonePoint[]
  closed: boolean
  onAddPoint: (point: ZonePoint) => void
  onClose: () => void
  color?: string
}

/**
 * Absolutely-positioned touch-capture + rendering layer, stacked on top of
 * the live-feed WebView. Draws vertex dots and connecting edges using plain
 * rotated Views (no react-native-svg dependency — this app doesn't have one
 * installed, and adding it just for this is avoidable; the standard
 * zero-dependency RN "draw a line with a View" trick is used instead).
 */
export function ZoneDrawOverlay({ points, closed, onAddPoint, onClose, color }: Props) {
  const [size, setSize] = useState({ width: 0, height: 0 })
  const tintColor = color ?? colors.primary

  const onLayout = (e: LayoutChangeEvent) => {
    const { width, height } = e.nativeEvent.layout
    setSize({ width, height })
  }

  const handlePress = (evt: any) => {
    if (closed || size.width === 0 || size.height === 0) return
    const { locationX, locationY } = evt.nativeEvent

    if (points.length >= 3) {
      const first = normalizedToScreen(points[0].x, points[0].y, size.width, size.height)
      const dist = Math.hypot(locationX - first.x, locationY - first.y)
      if (dist <= CLOSE_HIT_RADIUS) {
        onClose()
        return
      }
    }

    onAddPoint(screenToNormalized(locationX, locationY, size.width, size.height))
  }

  const screenPoints = points.map((p) => normalizedToScreen(p.x, p.y, size.width, size.height))

  return (
    <Pressable style={StyleSheet.absoluteFill} onLayout={onLayout} onPress={handlePress}>
      {/* Connecting edges — RN rotates a View around its own center (unlike
          CSS's transformOrigin, which RN doesn't support), so each edge bar
          is centered on the segment's midpoint and rotated in place, rather
          than positioned at the start point and rotated around a corner. */}
      {screenPoints.map((p, i) => {
        const next = i < screenPoints.length - 1 ? screenPoints[i + 1] : (closed ? screenPoints[0] : null)
        if (!next) return null
        const dx = next.x - p.x
        const dy = next.y - p.y
        const length = Math.hypot(dx, dy)
        const angle = Math.atan2(dy, dx)
        const midX = (p.x + next.x) / 2
        const midY = (p.y + next.y) / 2
        return (
          <View
            key={`edge-${i}`}
            pointerEvents="none"
            style={[
              styles.edge,
              {
                left: midX - length / 2,
                top: midY - 1,
                width: length,
                backgroundColor: tintColor,
                transform: [{ rotate: `${angle}rad` }],
              },
            ]}
          />
        )
      })}

      {/* Vertex dots */}
      {screenPoints.map((p, i) => (
        <View
          key={`vertex-${i}`}
          pointerEvents="none"
          style={[
            styles.vertex,
            { left: p.x - 6, top: p.y - 6, borderColor: tintColor },
            i === 0 && styles.firstVertex,
          ]}
        />
      ))}
    </Pressable>
  )
}

const styles = StyleSheet.create({
  edge: {
    position: 'absolute',
    height: 2,
  },
  vertex: {
    position: 'absolute',
    width: 12,
    height: 12,
    borderRadius: 6,
    borderWidth: 2,
    backgroundColor: 'rgba(0,0,0,0.4)',
  },
  firstVertex: {
    backgroundColor: 'rgba(255,255,255,0.6)',
  },
})

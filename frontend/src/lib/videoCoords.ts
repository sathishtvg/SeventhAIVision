/**
 * Pure coordinate math for drawing on top of a video/image rendered with
 * `objectFit: 'contain'`. The media scales to fit entirely inside its
 * container while preserving its own aspect ratio, so one axis is usually
 * letterboxed — a raw fraction of container size maps clicks in the
 * letterbox bars to the wrong place. No React/DOM dependency: everything
 * here takes plain numbers/DOMRect-shaped objects so it's unit-testable in
 * isolation.
 */

export interface MediaRect {
  offsetX: number
  offsetY: number
  renderedW: number
  renderedH: number
}

export interface RectLike {
  left: number
  top: number
  width: number
  height: number
}

/** Where the media actually renders inside a `containerW x containerH` box under objectFit: contain. */
export function computeContainRect(containerW: number, containerH: number, mediaW: number, mediaH: number): MediaRect {
  const containerAspect = containerW / containerH
  const mediaAspect = mediaW / mediaH

  if (mediaAspect > containerAspect) {
    // Media is relatively wider than the container -> letterboxed top/bottom.
    const renderedW = containerW
    const renderedH = containerW / mediaAspect
    return { offsetX: 0, offsetY: (containerH - renderedH) / 2, renderedW, renderedH }
  }
  // Media is relatively taller (or matches) -> letterboxed left/right.
  const renderedH = containerH
  const renderedW = containerH * mediaAspect
  return { offsetX: (containerW - renderedW) / 2, offsetY: 0, renderedW, renderedH }
}

/** Viewport-relative click -> normalized {x,y} in 0..1, or null if the click landed in a letterbox bar. */
export function screenToNormalized(
  clientX: number,
  clientY: number,
  containerRect: RectLike,
  mediaRect: MediaRect,
): { x: number; y: number } | null {
  const localX = clientX - containerRect.left
  const localY = clientY - containerRect.top
  const { offsetX, offsetY, renderedW, renderedH } = mediaRect

  if (
    localX < offsetX || localX > offsetX + renderedW ||
    localY < offsetY || localY > offsetY + renderedH
  ) {
    return null
  }

  const x = (localX - offsetX) / renderedW
  const y = (localY - offsetY) / renderedH
  return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) }
}

/** Inverse of screenToNormalized — normalized {x,y} -> viewport-relative screen coords. */
export function normalizedToScreen(
  x: number,
  y: number,
  containerRect: RectLike,
  mediaRect: MediaRect,
): { x: number; y: number } {
  const { offsetX, offsetY, renderedW, renderedH } = mediaRect
  return {
    x: containerRect.left + offsetX + x * renderedW,
    y: containerRect.top + offsetY + y * renderedH,
  }
}

/**
 * Pure coordinate math for the mobile zone-draw overlay. Unlike the web
 * version (frontend/src/lib/videoCoords.ts), which corrects for
 * `objectFit: contain` letterboxing, the mobile zone-draw screen renders
 * its live feed with `object-fit: fill` (see ZoneDrawScreen.tsx) — the
 * image always exactly fills its container, so there's no letterbox to
 * correct for and the mapping is a trivial 1:1 normalization. Kept as a
 * separate pure module (not inlined) so it stays independently testable,
 * mirroring the web module's intent.
 */

export function screenToNormalized(
  localX: number,
  localY: number,
  containerW: number,
  containerH: number,
): { x: number; y: number } {
  const x = containerW > 0 ? localX / containerW : 0
  const y = containerH > 0 ? localY / containerH : 0
  return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) }
}

export function normalizedToScreen(
  x: number,
  y: number,
  containerW: number,
  containerH: number,
): { x: number; y: number } {
  return { x: x * containerW, y: y * containerH }
}

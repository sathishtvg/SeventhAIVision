/**
 * Zone-draw coordinate math.
 *
 * A zone polygon drawn on a guard's phone is stored in normalized 0..1 space
 * and later replayed by the AI worker against a full-resolution frame. If this
 * mapping is wrong the drawn zone silently covers the wrong part of the scene,
 * and an intrusion detector either never fires or fires constantly. Pure
 * functions, so this is cheap to pin exhaustively.
 */
import { normalizedToScreen, screenToNormalized } from './videoCoords'

// ── screenToNormalized ────────────────────────────────────────────────────

test('maps a tap to its proportional position', () => {
  expect(screenToNormalized(100, 50, 400, 200)).toEqual({ x: 0.25, y: 0.25 })
})

test('corners map to the unit square corners', () => {
  expect(screenToNormalized(0, 0, 400, 200)).toEqual({ x: 0, y: 0 })
  expect(screenToNormalized(400, 200, 400, 200)).toEqual({ x: 1, y: 1 })
})

test('centre maps to 0.5, 0.5', () => {
  expect(screenToNormalized(200, 100, 400, 200)).toEqual({ x: 0.5, y: 0.5 })
})

test('a tap past the edge clamps into range', () => {
  // Pointer events can report slightly outside bounds during a drag. An
  // unclamped value would be stored as a polygon vertex outside the frame,
  // which the worker's point-in-polygon test would treat as valid geometry.
  expect(screenToNormalized(500, 300, 400, 200)).toEqual({ x: 1, y: 1 })
  expect(screenToNormalized(-30, -10, 400, 200)).toEqual({ x: 0, y: 0 })
})

test('zero-sized container yields 0 rather than NaN', () => {
  // The container measures 0 for one frame before layout settles. NaN here
  // would serialize into the polygon JSON and poison the stored zone.
  const r = screenToNormalized(10, 10, 0, 0)
  expect(r).toEqual({ x: 0, y: 0 })
  expect(Number.isNaN(r.x)).toBe(false)
  expect(Number.isNaN(r.y)).toBe(false)
})

test('non-square containers scale each axis independently', () => {
  expect(screenToNormalized(160, 90, 320, 180)).toEqual({ x: 0.5, y: 0.5 })
})

// ── normalizedToScreen ────────────────────────────────────────────────────

test('projects normalized coords back to pixels', () => {
  expect(normalizedToScreen(0.25, 0.5, 400, 200)).toEqual({ x: 100, y: 100 })
})

test('round-trips within floating-point tolerance', () => {
  const w = 393
  const h = 852 // iPhone 15 logical size — deliberately not a round number
  for (const [px, py] of [[0, 0], [17, 233], [200, 400], [393, 852]]) {
    const n = screenToNormalized(px, py, w, h)
    const back = normalizedToScreen(n.x, n.y, w, h)
    expect(back.x).toBeCloseTo(px, 6)
    expect(back.y).toBeCloseTo(py, 6)
  }
})

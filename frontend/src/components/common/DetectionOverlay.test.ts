/** Gap 92 — detection bbox → overlay rect coordinate math (pure) */
import { describe, expect, it } from 'vitest'
import { bboxToRect } from './DetectionOverlay'

describe('bboxToRect', () => {
  it('scales a pixel bbox by the frame dimensions into 0..1000 space', () => {
    const r = bboxToRect({ x1: 192, y1: 108, x2: 960, y2: 540 }, 1920, 1080)
    expect(r).not.toBeNull()
    expect(r!.x).toBeCloseTo(100)   // 192/1920*1000
    expect(r!.y).toBeCloseTo(100)   // 108/1080*1000
    expect(r!.w).toBeCloseTo(400)   // (960-192)/1920*1000
    expect(r!.h).toBeCloseTo(400)   // (540-108)/1080*1000
  })

  it('normalizes regardless of corner order', () => {
    const r = bboxToRect({ x1: 960, y1: 540, x2: 192, y2: 108 }, 1920, 1080)
    expect(r!.x).toBeCloseTo(100)
    expect(r!.w).toBeCloseTo(400)
  })

  it('returns null when frame dimensions are unknown', () => {
    expect(bboxToRect({ x1: 0, y1: 0, x2: 10, y2: 10 }, null, 1080)).toBeNull()
    expect(bboxToRect({ x1: 0, y1: 0, x2: 10, y2: 10 }, 1920, null)).toBeNull()
    expect(bboxToRect({ x1: 0, y1: 0, x2: 10, y2: 10 }, 0, 0)).toBeNull()
  })
})

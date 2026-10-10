import type { SxProps, Theme } from '@mui/material'
import { duration, easing, stagger } from './tokens'

/**
 * Staggered fade-up entrance for a card/tile at position `index` in a list.
 * Uses longhand animation-* properties, not the `animation` shorthand — emotion's
 * shorthand expansion mis-parses `var(--ease-out)` mixed into a multi-value
 * shorthand and silently drops every sub-property (verified empty computed values).
 */
export function fadeUpSx(index: number, opts?: { stepMs?: number; maxSteps?: number; durationS?: number }): SxProps<Theme> {
  const stepMs = opts?.stepMs ?? 60
  const maxSteps = opts?.maxSteps ?? 10
  const durationS = opts?.durationS ?? 0.4
  return {
    animationName: 'fade-up',
    animationDuration: `${durationS}s`,
    animationTimingFunction: easing.out,
    animationFillMode: 'both',
    animationDelay: `${Math.min(index, maxSteps) * stepMs}ms`,
  }
}

/** How long the item at `index` waits its turn. After `stagger.most` items the rest arrive together. */
export function staggerDelay(index: number, step: number = stagger.step, most: number = stagger.most): number {
  return Math.min(Math.max(index, 0), most) * step
}

/**
 * An item entering once, in its turn: a short fade, and a rise of a few pixels
 * unless `rise` is false. For something drawn once when its page opens. It
 * plays when the element is first put in the page and never again, so a list
 * that redraws does not replay it; give rows that arrive later their own mark
 * (`useNewItems`) and not this.
 */
export function enterSx(index = 0, opts?: { rise?: boolean; time?: number }): SxProps<Theme> {
  return {
    animationName: opts?.rise === false ? 'sav-fade-in' : 'sav-fade-up',
    animationDuration: `${opts?.time ?? duration.page}ms`,
    animationTimingFunction: easing.out,
    animationFillMode: 'both',
    animationDelay: `${staggerDelay(index)}ms`,
  }
}

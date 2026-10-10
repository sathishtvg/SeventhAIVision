import type { Key, ReactNode } from 'react'
import { Box, Collapse, Skeleton } from '@mui/material'
import { TransitionGroup } from 'react-transition-group'
import { duration, useCountUp, useReducedMotion } from '@/motion'

const HIDDEN = {
  position: 'absolute', width: 1, height: 1, p: 0, m: -1, overflow: 'hidden',
  clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap', border: 0,
} as const

export interface AnimatedCounterProps {
  /** The figure, once it has been loaded. `undefined` or `null` while it has not: a placeholder is shown, never a 0. */
  value: number | null | undefined
  format?: (n: number) => string
  /** The placeholder's width while there is no figure yet. */
  width?: number | string
}

/**
 * A figure that counts up to what was loaded.
 *
 * It counts from the last figure to the new one, so a refresh that changes
 * 41 to 43 moves two and does not start again from nothing. What it counts
 * through is decoration: a screen reader is given the real figure, once, and
 * with reduced motion the figure is simply there.
 */
export function AnimatedCounter({ value, format = (n) => n.toLocaleString(), width = 56 }: AnimatedCounterProps) {
  const shown = useCountUp(value ?? undefined)
  if (value === null || value === undefined) return <Skeleton width={width} sx={{ display: 'inline-block' }} />
  return (
    <Box component="span" sx={{ position: 'relative', fontVariantNumeric: 'tabular-nums' }}>
      <span aria-hidden="true">{format(shown)}</span>
      <Box component="span" sx={HIDDEN}>{format(value)}</Box>
    </Box>
  )
}

export interface AnimatedListProps<T> {
  items: readonly T[]
  idOf: (item: T) => Key
  children: (item: T, index: number) => ReactNode
}

/**
 * A list whose items open into place when they are added and close when they
 * are taken away, so the items around them move aside and do not jump.
 *
 * For cards and list items - not table rows, which cannot be collapsed; a
 * table marks what is new (`useNewItems`) and lets a row go without ceremony.
 * The order is the order of `items`: this animates what the list is given and
 * decides nothing about it. With reduced motion items are simply there or not.
 */
export function AnimatedList<T>({ items, idOf, children }: AnimatedListProps<T>) {
  const reduced = useReducedMotion()
  const time = reduced ? 0 : duration.standard
  return (
    <TransitionGroup component={null}>
      {items.map((item, index) => (
        <Collapse key={idOf(item)} timeout={time}>
          {children(item, index)}
        </Collapse>
      ))}
    </TransitionGroup>
  )
}

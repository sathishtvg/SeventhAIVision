import { useEffect, useRef, useState } from 'react'
import { reducedMotion } from './preference'

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}

/**
 * Animates a KPI number counting up from its previous value to `target` whenever
 * `target` changes. Always starts from 0 the first time a real number arrives, so
 * callers only need to render this while `target !== undefined` (e.g. keep showing
 * a loading skeleton until then) — it never flashes the final value before counting.
 * Respects the motion setting: with reduced motion the number is simply there.
 */
export function useCountUp(target: number | undefined, durationMs = 800): number {
  const [display, setDisplay] = useState(0)
  const fromRef = useRef(0)
  const rafRef = useRef<number | undefined>(undefined)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => {
    if (target === undefined) return
    if (reducedMotion()) {
      setDisplay(target)
      fromRef.current = target
      return
    }
    const from = fromRef.current
    const delta = target - from
    if (delta === 0) return
    const start = performance.now()

    const finish = () => {
      setDisplay(target)
      fromRef.current = target
    }

    const tick = (now: number) => {
      const elapsed = now - start
      const t = Math.min(1, elapsed / durationMs)
      setDisplay(Math.round(from + delta * easeOutCubic(t)))
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        finish()
      }
    }
    rafRef.current = requestAnimationFrame(tick)
    // Safety net: requestAnimationFrame is throttled or never fires on a
    // backgrounded/unfocused tab (e.g. an always-open second-monitor ops
    // dashboard), which would otherwise leave the KPI stuck at 0 forever.
    timeoutRef.current = setTimeout(finish, durationMs + 200)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, durationMs])

  return display
}

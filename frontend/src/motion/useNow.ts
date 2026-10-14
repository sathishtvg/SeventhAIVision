import { useEffect, useState } from 'react'

/**
 * The time, read again every few seconds.
 *
 * A position or a reading goes stale by the clock, not when the page next
 * fetches something: a screen that works out "is this live?" only when data
 * arrives goes on calling the last reading live for as long as none does -
 * which is exactly when it is not. A component that draws anything as live
 * reads the time from here, so it is drawn again when the time has moved on.
 */
export function useNow(everyMs = 5000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), everyMs)
    return () => clearInterval(timer)
  }, [everyMs])
  return now
}

/** Whether something last heard from at `at` is still to be called live at `now`. No time, or a bad one, is not. */
export function freshAt(at: string | number | Date | null | undefined, staleAfterMs: number, now: number): boolean {
  if (at === null || at === undefined) return false
  const taken = new Date(at).getTime()
  return !Number.isNaN(taken) && now - taken <= staleAfterMs
}

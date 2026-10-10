import { useEffect, useRef } from 'react'
import { reloadIfRebuilt } from '@/lib/freshBuild'

/** Coming back to a tab fires "visible" and "focus" together: the server is asked once. */
export const LEAST_GAP_MS = 15_000
/** A screen left on this page and never left - a control-room PC overnight - is still asked. */
export const EVERY_MS = 5 * 60_000

/**
 * Keeps a page that can be left open for days from being an old build when it
 * is next used (see lib/freshBuild). It asks when the page opens, when its tab
 * is come back to, and every few minutes while it is in front.
 *
 * `idle` is false while somebody is in the middle of something a reload would
 * lose - a request on its way, a code being typed. Nothing is asked then, and
 * a reload that was about to happen does not.
 */
export function useFreshBuild(idle: boolean): void {
  const idleNow = useRef(idle)
  useEffect(() => { idleNow.current = idle }, [idle])

  useEffect(() => {
    let asked = -Infinity
    const look = () => {
      if (document.visibilityState !== 'visible' || !idleNow.current) return
      const now = Date.now()
      if (now - asked < LEAST_GAP_MS) return
      asked = now
      void reloadIfRebuilt({ mayReload: () => idleNow.current })
    }
    look()
    document.addEventListener('visibilitychange', look)
    window.addEventListener('focus', look)
    const timer = window.setInterval(look, EVERY_MS)
    return () => {
      document.removeEventListener('visibilitychange', look)
      window.removeEventListener('focus', look)
      window.clearInterval(timer)
    }
  }, [])
}

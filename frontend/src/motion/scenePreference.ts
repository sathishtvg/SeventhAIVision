import { useCallback, useSyncExternalStore } from 'react'
import { useAuthStore } from '@/store/auth'

/**
 * Whether each page's own scene is played, for the person signed in. On
 * unless they switch it off in Settings; kept on this device, for their
 * account, like their theme.
 *
 * Somebody who moves between pages all shift may well want the pages simply
 * to appear. That is theirs to choose, apart from "less motion" - which stops
 * the scenes too, along with everything else.
 */
const key = (userId?: string | null) => `seventh-ai-page-scenes:${userId ?? 'anon'}`

const listeners = new Set<() => void>()
const subscribe = (listener: () => void) => {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function read(userId?: string | null): boolean {
  try {
    return localStorage.getItem(key(userId)) !== 'off'
  } catch {
    return true
  }
}

export function setPageScenes(userId: string | null | undefined, on: boolean) {
  try {
    if (on) localStorage.removeItem(key(userId))
    else localStorage.setItem(key(userId), 'off')
  } catch {
    // Private browsing or a full quota: the choice holds until the page is reloaded.
  }
  listeners.forEach((listener) => listener())
}

/** `[on, set]`, for the person signed in. */
export function usePageScenes(): [boolean, (on: boolean) => void] {
  const userId = useAuthStore((s) => s.user?.id ?? null)
  const on = useSyncExternalStore(subscribe, () => read(userId), () => true)
  const set = useCallback((next: boolean) => setPageScenes(userId, next), [userId])
  return [on, set]
}

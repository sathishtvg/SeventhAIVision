import { create } from 'zustand'

/**
 * The safety net under every request for data.
 *
 * A page says so itself when its own list fails (`ErrorState`). But a page
 * has many small requests beside its main one - the sites for a filter, a
 * count for a badge - and none of those has anywhere to say it failed. This
 * is told of every failed query (`main.tsx`) and shows one notice, so that
 * nothing fails without a word.
 *
 * One notice, not one per request: when the network drops, a dozen requests
 * fail together and go on failing every few seconds. It is shown once and not
 * again for a while.
 */
export const NOTICE_GAP_MS = 60_000

interface LoadFailures {
  open: boolean
  /** When the notice was last raised, to keep failures that come together to one notice. */
  raisedAt: number
  report: (error: unknown, now?: number) => void
  close: () => void
}

/** A failure that is somebody else's to report, or not a failure at all. */
export function isQuiet(error: unknown): boolean {
  const e = error as { response?: { status?: number }; code?: string; name?: string } | null | undefined
  // Signed out: the session handling takes the person to the sign-in page and says why.
  if (e?.response?.status === 401) return true
  // Cancelled because the page moved on. Nothing was wanted any more.
  return e?.code === 'ERR_CANCELED' || e?.name === 'CanceledError' || e?.name === 'AbortError'
}

export const useLoadFailures = create<LoadFailures>((set, get) => ({
  open: false,
  raisedAt: -Infinity,
  report(error, now = Date.now()) {
    if (isQuiet(error)) return
    if (now - get().raisedAt < NOTICE_GAP_MS) return
    set({ open: true, raisedAt: now })
  },
  close() {
    set({ open: false })
  },
}))

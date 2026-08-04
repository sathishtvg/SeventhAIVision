import { create } from 'zustand'
import type { VisitorEntryPrompt } from '@/api/vms'

interface VisitorEntryState {
  /** Pending prompts, oldest first. The dialog always works on queue[0]. */
  queue: VisitorEntryPrompt[]
  enqueue: (p: VisitorEntryPrompt) => void
  /** Remove the prompt currently being handled (submitted or dismissed). */
  resolveCurrent: () => void
  clear: () => void
}

/**
 * Pending visitor-entry prompts pushed by the entry LPR camera.
 *
 * A QUEUE, not a single slot: two vehicles can arrive back to back at a busy
 * gate, and overwriting the first prompt would silently lose that visitor —
 * their record would sit at "Pending — <plate>" forever while the parking
 * clock ran. Each arrival is held until an operator deals with it.
 *
 * De-duplicated on visitor_id because the backend can re-publish (a reconnect
 * replays nothing, but a plate re-read on the same vehicle would create a
 * second prompt for the same visit).
 */
export const useVisitorEntryStore = create<VisitorEntryState>((set) => ({
  queue: [],
  enqueue: (p) =>
    set((s) =>
      s.queue.some((q) => q.visitor_id === p.visitor_id)
        ? s
        : { queue: [...s.queue, p] },
    ),
  resolveCurrent: () => set((s) => ({ queue: s.queue.slice(1) })),
  clear: () => set({ queue: [] }),
}))

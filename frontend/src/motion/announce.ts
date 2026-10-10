import { create } from 'zustand'

/**
 * What a screen reader is told when something changes that sight would catch
 * by a tint or a movement: rows arriving, a list being refreshed, a snapshot
 * saved. One polite region for the whole app (`LiveAnnouncer`, in the shell)
 * reads out whatever was said last; it waits for the reader to finish what it
 * was saying and never takes the keyboard's place.
 *
 * Not for errors and not for alarms: an error is an alert where it happened,
 * and an alarm has its own sound and toast.
 */
interface Announcer {
  text: string
  /** Counts up with every announcement, so the same words said twice are said twice. */
  count: number
  say: (text: string) => void
}

export const useAnnouncer = create<Announcer>((set) => ({
  text: '',
  count: 0,
  say: (text) => set((s) => ({ text, count: s.count + 1 })),
}))

/** Says it, from anywhere - a hook, a callback, a store. */
export function announce(text: string): void {
  useAnnouncer.getState().say(text)
}

/** "1 new alert", "3 new alerts". */
export function countOf(n: number, one: string, many: string = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

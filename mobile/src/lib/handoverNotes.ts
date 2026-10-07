/**
 * The rules of the phone's two handover cards, apart from the cards so they can
 * be tested with nothing mounted: which instructions come first, what the
 * summary card offers at each point, and the words for both.
 */
import type { Instruction, ShiftSummary } from '@/api/occurrenceBook'
import type { Shift } from '@/api/patrols'

/** Unread first — those are the ones the guard has not yet been told — then newest. */
export function unreadFirst(items: Instruction[]): Instruction[] {
  return [...items].sort((a, b) =>
    Number(a.read_by_me) - Number(b.read_by_me) || b.issued_at.localeCompare(a.issued_at))
}

export function unreadCount(items: Instruction[]): number {
  return items.filter((i) => !i.read_by_me).length
}

/** The line at the top of the instructions card. */
export function instructionsHeading(items: Instruction[]): string {
  const unread = unreadCount(items)
  const total = `${items.length} instruction${items.length === 1 ? '' : 's'} in force`
  return unread ? `${total} — ${unread} you have not read` : total
}

/** Who issued one, and until when it stands. */
export function instructionMeta(n: Instruction, format: (iso: string) => string): string {
  const by = [n.site_name, n.issued_by_name, format(n.issued_at)].filter(Boolean).join(' · ')
  return n.expires_at ? `${by} · until ${format(n.expires_at)}` : by
}

/**
 * The shift whose summary the card is about: the one running now, else the
 * one that ended most recently within the day — a guard writes the summary as
 * the shift ends, or just after.
 */
export function shiftToSummarise(shifts: Shift[], now: Date = new Date()): Shift | null {
  const running = shifts.find((s) => s.status === 'active' && s.actual_start && !s.actual_end)
  if (running) return running
  const dayAgo = now.getTime() - 24 * 3600 * 1000
  const ended = shifts
    .filter((s) => s.actual_start && s.actual_end && new Date(s.actual_end).getTime() >= dayAgo)
    .sort((a, b) => (b.actual_end ?? '').localeCompare(a.actual_end ?? ''))
  return ended[0] ?? null
}

export type SummaryStep = 'draft' | 'review' | 'done'

/** What the card offers: draft one, read and confirm the draft, or read what was confirmed. */
export function summaryStep(summary: ShiftSummary | null | undefined): SummaryStep {
  if (!summary) return 'draft'
  return summary.state === 'CONFIRMED' ? 'done' : 'review'
}

export const SUMMARY_LINE: Record<SummaryStep, string> = {
  draft: 'Not written yet. The app drafts it from what was recorded; you check it and confirm it.',
  review: 'A draft is waiting for you to read, correct and confirm.',
  done: 'Confirmed. It is with the handover.',
}

export const SUMMARY_BUTTON: Record<SummaryStep, string> = {
  draft: 'Draft the summary', review: 'Read and confirm', done: 'Read it',
}

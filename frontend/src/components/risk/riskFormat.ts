/** The words and the shading the risk and advice screen uses. */
import type { Answer, Finding, Level, PatternSource } from '@/api/securityAdvice'

/** Confidence is how much history a statement rests on. The label says that, not "likely". */
export const LEVEL_LABEL: Record<Level, string> = {
  HIGH: 'Rests on much history', MEDIUM: 'Rests on some history', LOW: 'Rests on little history',
}

export const ANSWER_LABEL: Record<Answer, string> = { ACCEPTED: 'Accepted', NOT_ACCEPTED: 'Not accepted' }

export const WEEK_CHOICES = [1, 2, 4, 8, 12]

export const weeksLabel = (weeks: number) => (weeks === 1 ? 'The last week' : `The last ${weeks} weeks`)

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** The most in any one hour of the week: what the darkest cell stands for. */
export function peak(grid: number[][]): number {
  return Math.max(0, ...grid.flat())
}

/** Four steps of shade, and none for an hour with nothing in it. A step, not a continuous scale, so two cells that
 *  look alike are alike. */
export const STEPS = 4

export function step(count: number, most: number): number {
  if (!count || !most) return 0
  return Math.max(1, Math.ceil((count / most) * STEPS))
}

/** The counts one shade stands for, for the key under the grid — or null when no count falls on that shade. */
export function stepRange(n: number, most: number): string | null {
  const from = Math.floor(((n - 1) / STEPS) * most) + 1
  const to = Math.floor((n / STEPS) * most)
  if (from > to) return null
  return from === to ? String(from) : `${from}–${to}`
}

/** What a cell says to somebody who cannot see its shade. */
export function cellTitle(day: string, hour: number, count: number): string {
  const until = String((hour + 1) % 24).padStart(2, '0')
  return `${day} ${String(hour).padStart(2, '0')}:00–${until}:00: ${count}`
}

/** A person's answer to a piece of advice, in a line. */
export function answerLine(a: NonNullable<Finding['answer']>): string {
  const who = a.answered_by_name ?? 'somebody no longer on the system'
  return `${ANSWER_LABEL[a.answer]} by ${who}, ${fmt(a.answered_at)}${a.reason ? `: ${a.reason}` : ''}`
}

/** How many of a kind were counted, and that the count was cut when it was. */
export function totalLine(s: Pick<PatternSource, 'total' | 'cut_at'>): string {
  if (s.cut_at) return `More than ${s.cut_at.toLocaleString('en')} — the first ${s.cut_at.toLocaleString('en')} are counted`
  return s.total === 1 ? '1 in the period' : `${s.total.toLocaleString('en')} in the period`
}

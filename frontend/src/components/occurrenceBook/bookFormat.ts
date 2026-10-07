/** The words the occurrence book screens share. */
import type { Entry, Instruction, ReviewState, ShiftSummary } from '@/api/occurrenceBook'

export const REVIEW_LABEL: Record<ReviewState, string> = {
  unreviewed: 'Not reviewed', noted: 'Noted', follow_up: 'To be followed up', closed: 'Followed up',
}

export const REVIEW_COLOUR: Record<ReviewState, 'default' | 'success' | 'warning' | 'info'> = {
  unreviewed: 'default', noted: 'success', follow_up: 'warning', closed: 'info',
}

export const kindLabel = (key: string) => {
  const words = key.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** How an entry stands to another: a correction of one, or corrected by one. Null when neither. */
export function correctionMark(entry: Pick<Entry, 'corrects_entry_id' | 'corrected_by_entry_id'>): string | null {
  if (entry.corrected_by_entry_id && entry.corrects_entry_id) return 'A correction, itself corrected'
  if (entry.corrected_by_entry_id) return 'Corrected by a later entry'
  if (entry.corrects_entry_id) return 'A correction'
  return null
}

/** Until when an instruction stands. */
export function standsUntil(n: Pick<Instruction, 'in_force' | 'closed_at' | 'expires_at' | 'closed_by_name' | 'close_note'>): string {
  if (n.closed_at) return `Closed ${fmt(n.closed_at)}${n.closed_by_name ? ` by ${n.closed_by_name}` : ''}: ${n.close_note ?? ''}`
  if (!n.in_force) return `Ran out ${fmt(n.expires_at)}`
  return n.expires_at ? `In force until ${fmt(n.expires_at)}` : 'In force until it is closed'
}

/** Where a summary stands, and by whose hand. */
export function summaryState(s: Pick<ShiftSummary, 'state' | 'confirmed_by_name' | 'confirmed_at' | 'edited'>): string {
  if (s.state === 'CONFIRMED') {
    return `Confirmed by ${s.confirmed_by_name ?? 'somebody no longer on the system'}, ${fmt(s.confirmed_at)}`
      + (s.edited ? ' — corrected before it was confirmed' : ' — as the platform drafted it')
  }
  return s.edited ? 'A draft, corrected, not yet confirmed' : 'A draft, not yet confirmed'
}

/** Midnight today, a day ago and a week ago, as the server reads them. */
export function since(period: 'today' | 'day' | 'week' | 'all', now = new Date()): string | undefined {
  if (period === 'all') return undefined
  if (period === 'today') return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString()
  return new Date(now.getTime() - (period === 'day' ? 1 : 7) * 86_400_000).toISOString()
}

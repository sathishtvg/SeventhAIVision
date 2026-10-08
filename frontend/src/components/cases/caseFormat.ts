/** The words the cases screen uses. */
import type { CaseFile, Entry, Link, Priority, Status, Task } from '@/api/cases'

export const STATUS_COLOUR: Record<Status, 'info' | 'warning' | 'default'> = {
  OPEN: 'info', AWAITING_APPROVAL: 'warning', CLOSED: 'default',
}
export const PRIORITY_LABEL: Record<Priority, string> = { LOW: 'Low priority', NORMAL: 'Normal priority', HIGH: 'High priority' }
export const TASK_LABEL: Record<Task['state'], string> = { OPEN: 'To do', DONE: 'Done', DROPPED: 'Dropped' }

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export const named = (name: string | null | undefined) => name ?? 'Somebody no longer on the system'

/** A linked record as the reader may see it. One they may not read is a kind and why, and nothing else. */
export function linkLine(x: Link): string {
  if (x.state === 'SHOWN') return `${x.label}${x.detail ? ` (${x.detail})` : ''}`
  const what = `${/^[AEIOU]/.test(x.kind_label) ? 'An' : 'A'} ${x.kind_label.toLowerCase()}`
  return x.state === 'NOT_PERMITTED'
    ? `${what} you may not read — it is read under ${x.needs}`
    : `${what} at a site you are not shown, or no longer there`
}

/** A task in a line: what it is, who has it, and how it ended when it has. */
export function taskLine(t: Task): string {
  const who = t.assigned_to_name ? `given to ${t.assigned_to_name}` : 'given to nobody yet'
  if (t.state === 'DONE') return `${t.title} — done by ${named(t.done_by_name)}${t.done_note ? `: ${t.done_note}` : ''}`
  if (t.state === 'DROPPED') return `${t.title} — dropped: ${t.dropped_reason}`
  return `${t.title} — ${who}`
}

/** One step of a case's history: who did what, when, and what they wrote. */
export function entryLine(e: Entry): string {
  return `${named(e.actor_name)}: ${e.words.charAt(0).toLowerCase()}${e.words.slice(1)}, ${fmt(e.occurred_at)}`
}

/** Where a case stands, in a sentence — who it is waiting on, when it is waiting. */
export function standing(c: CaseFile): string {
  if (c.status === 'CLOSED') return `Closed ${fmt(c.closed_at)}: asked for by ${named(c.close_requested_by_name)}, approved by ${named(c.closed_by_name)}.`
  if (c.status === 'AWAITING_APPROVAL') {
    return `${named(c.close_requested_by_name)} asked for it to be closed, ${fmt(c.close_requested_at)}. Somebody else approves or declines.`
  }
  return c.tasks_open ? `Open, with ${c.tasks_open} task${c.tasks_open === 1 ? '' : 's'} to do.` : 'Open.'
}

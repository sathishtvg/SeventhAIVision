/**
 * The words of the three screens that bring desk work to the phone — a
 * published briefing, a work order given to this person, a case task given to
 * them — apart from the screens so they can be tested with nothing mounted.
 */
import type { AsAt, BriefingSummary } from '@/api/briefings'
import type { CaseDetail, CaseTask } from '@/api/cases'
import type { OrderState, WorkOrder } from '@/api/maintenance'

const day = (iso: string) =>
  new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })

// ── Briefings ────────────────────────────────────────────────────────────────

/** What a line of a briefing is true of. Only what is not simply "the day" is marked. */
export const AS_AT_LABEL: Record<AsAt, string | null> = {
  PERIOD: null, DRAFTING: 'when it was drafted', WEEKS: 'the weeks before',
}

/** Whose day it is: a site's, or every site's together. */
export const briefingWhere = (b: BriefingSummary) => b.site?.name ?? 'Every site'

/** A briefing a later revision has replaced is still there to read, and says so. */
export const briefingStanding = (b: BriefingSummary) =>
  b.replaced_by ? 'Replaced by a later revision' : b.revision > 1 ? `Revision ${b.revision}` : null

// ── Work orders ──────────────────────────────────────────────────────────────

export const ORDER_STATE_LABEL: Record<OrderState, string> = {
  SUGGESTED: 'Suggested', OPEN: 'To do', IN_PROGRESS: 'In hand', DONE: 'Done', CANCELLED: 'Cancelled', DISMISSED: 'Dismissed',
}

/** What the order is on and where. */
export function orderWhere(o: WorkOrder): string {
  const asset = o.asset_name ? `${o.asset_name}${o.asset_code ? ` (${o.asset_code})` : ''}` : null
  return [asset, o.site_name].filter(Boolean).join(' · ') || 'No asset or site named'
}

/** When it is due, and that it is late when it is. */
export const orderDue = (o: WorkOrder) =>
  !o.due_at ? 'No date set' : o.overdue ? `Was due ${day(o.due_at)} — overdue` : `Due ${day(o.due_at)}`

/** Orders in hand first, then what is overdue, then the rest by when it is due. */
export function inOrder(orders: WorkOrder[]): WorkOrder[] {
  const rank = (o: WorkOrder) => (o.state === 'IN_PROGRESS' ? 0 : o.overdue ? 1 : 2)
  return [...orders].sort((a, b) => rank(a) - rank(b) || (a.due_at ?? '9').localeCompare(b.due_at ?? '9'))
}

// ── Case tasks ───────────────────────────────────────────────────────────────

export interface MyTask { caseId: string; caseNumber: string; caseTitle: string; task: CaseTask }

/** The tasks of these cases that this person may finish and that are still to do. */
export function myOpenTasks(cases: (CaseDetail | undefined)[]): MyTask[] {
  const out: MyTask[] = []
  for (const c of cases) {
    if (!c || c.status !== 'OPEN') continue
    for (const task of c.tasks) {
      if (task.state === 'OPEN' && task.may_finish) out.push({ caseId: c.id, caseNumber: c.case_number, caseTitle: c.title, task })
    }
  }
  return out.sort((a, b) => (a.task.due_at ?? '9').localeCompare(b.task.due_at ?? '9'))
}

export const taskDue = (t: CaseTask) => (t.due_at ? `Due ${day(t.due_at)}` : 'No date set')

/** A task is dropped with why; done may say what was done. */
export const noteNeeded = (how: 'done' | 'drop', note: string) => how === 'drop' && !note.trim()

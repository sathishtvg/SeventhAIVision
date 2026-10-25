/**
 * What the roster page tells a planner, in one place so each sentence can be tested.
 *
 * Owner's report, 25 October 2026: "draft roster also I can't create it ... give me proper msg when try roster".
 * A draft with no posts, a draft with open posts, a 7-day run that added nothing and a draft that overlaps another
 * were all shown without a word of why.
 */
import type { DraftShift, RosterBatch, RosterBatchListItem } from '@/api/roster'

export type Tone = 'success' | 'info' | 'warning' | 'error'
export interface Said { tone: Tone; text: string }

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** The busiest day's number of posts, by the day each post starts on the viewer's calendar. */
export function busiestDay(shifts: Pick<DraftShift, 'scheduled_start'>[]): number {
  const perDay = new Map<string, number>()
  for (const s of shifts) {
    const day = new Date(s.scheduled_start).toDateString()
    perDay.set(day, (perDay.get(day) ?? 0) + 1)
  }
  return Math.max(0, ...perDay.values())
}

/** Why a draft has open posts: too few people, or the rules. */
function whyOpen(summary: RosterBatch['rules_summary'], shifts: DraftShift[]): string {
  const people = summary?.people_available
  const busiest = busiestDay(shifts)
  if (people !== undefined && people < busiest) {
    return `The busiest day needs ${plural(busiest, 'person', 'people')}, but only ${people} can be rostered ` +
      '(guards, operators and supervisors), and each works at most one shift a day. Add staff, or lower the ' +
      'day and night guards each site needs (Sites).'
  }
  return 'The rest, consecutive-day, leave and night rules left them open. Relax a rule in Auto-Schedule, or fill ' +
    'them by hand.'
}

/** The sentence above a draft's posts. */
export function draftExplanation(summary: RosterBatch['rules_summary'], shifts: DraftShift[]): Said {
  const posts = shifts.length
  const filled = shifts.filter((s) => s.guard_user_id).length
  if (posts === 0) {
    return {
      tone: 'error',
      text: 'This draft has no posts, so there is nothing to publish. It was made before the 25 October fix, or ' +
        'when there was nothing to plan. Discard it and run Auto-Schedule again.',
    }
  }
  if (filled === 0) {
    return {
      tone: 'error',
      text: `None of the ${plural(posts, 'post')} could be filled, so publishing would add nothing. ` +
        whyOpen(summary, shifts),
    }
  }
  if (filled < posts) {
    return {
      tone: 'warning',
      text: `${filled} of ${plural(posts, 'post')} are filled; ${posts - filled} are open. ${whyOpen(summary, shifts)} ` +
        'Fill open posts by picking a person below. If you publish now, open posts are left out of the roster.',
    }
  }
  const all = posts === 1 ? 'The one post is filled.' : `All ${posts} posts are filled.`
  return { tone: 'success', text: `${all} Check the draft, then Publish & Notify.` }
}

/** After "Publish & Notify". */
export function publishedMessage(shiftsCreated: number): Said {
  return shiftsCreated > 0
    ? { tone: 'success', text: `Roster published: ${plural(shiftsCreated, 'shift')} added, and the guards were told.` }
    : { tone: 'warning', text: 'Published, but no shift was added: every post in the draft was open.' }
}

/** After "Generate next 7 days", which only repeats roster patterns. */
export function generatedMessage(created: number, activePatterns: number): Said {
  if (created > 0) {
    return { tone: 'success', text: `${plural(created, 'shift')} added for the next 7 days from the roster patterns.` }
  }
  if (activePatterns === 0) {
    return {
      tone: 'info',
      text: 'No shifts were added: there are no active roster patterns, and Generate next 7 days only repeats ' +
        'patterns. To plan from your shift definitions and the guards each site needs, use Auto-Schedule.',
    }
  }
  return { tone: 'info', text: 'No shifts were added: the next 7 days already have every shift their patterns call for.' }
}

/** Do two saved drafts cover the same days for the same place ("every site" covers each site)? */
function overlaps(a: RosterBatchListItem, b: RosterBatchListItem): boolean {
  const samePlace = !a.site_id || !b.site_id || a.site_id === b.site_id
  return samePlace && a.period_start <= b.period_end && b.period_start <= a.period_end
}

/** A note beside a saved draft in the list, or null when there is nothing to say. */
export function draftListNote(draft: RosterBatchListItem, all: RosterBatchListItem[]): Said | null {
  if (draft.posts === 0) return { tone: 'error', text: 'Empty: nothing to publish. Discard it.' }
  if (all.some((other) => other.id !== draft.id && other.posts > 0 && overlaps(draft, other))) {
    return {
      tone: 'warning',
      text: 'Covers the same days as another draft. Publish only one of them, or the shifts may be added twice.',
    }
  }
  return null
}

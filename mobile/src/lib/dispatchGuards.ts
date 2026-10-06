/**
 * Who can be sent to an incident: the guards on duty now.
 *
 * Read from the live attendance board, which everyone who may dispatch may
 * also read (the users list is not: an operator may dispatch and may not list
 * users). A guard is on duty when they have checked in to today's shift and
 * not yet checked out.
 *
 * Ordered for the person choosing: guards at the incident's own site first,
 * those not on a break before those who are, then by name. A guard rostered
 * twice today appears once.
 */
import type { LiveAttendanceShift } from '@/api/attendance'

export interface DispatchableGuard {
  user_id: string
  name: string
  site_name: string | null
  /** On duty at the site the incident is at. */
  at_site: boolean
  on_break: boolean
}

export function dispatchable(shifts: LiveAttendanceShift[], incidentSiteId?: string | null): DispatchableGuard[] {
  const byGuard = new Map<string, DispatchableGuard>()
  for (const s of shifts) {
    if (!s.guard_user_id || !s.actual_start || s.actual_end) continue      // not on duty now
    const guard: DispatchableGuard = {
      user_id: s.guard_user_id,
      name: s.guard_name ?? 'Unnamed guard',
      site_name: s.site_name,
      at_site: !!incidentSiteId && s.site_id === incidentSiteId,
      on_break: !!s.on_break,
    }
    const seen = byGuard.get(guard.user_id)
    // Kept once; the shift at the incident's site is the one worth showing.
    if (!seen || (guard.at_site && !seen.at_site)) byGuard.set(guard.user_id, guard)
  }
  return [...byGuard.values()].sort((a, b) =>
    Number(b.at_site) - Number(a.at_site) || Number(a.on_break) - Number(b.on_break) || a.name.localeCompare(b.name))
}

/** One line under a guard's name: where they are, and whether on a break. */
export function guardLine(g: DispatchableGuard): string {
  const where = g.at_site ? 'On duty at this site' : `On duty at ${g.site_name ?? 'another site'}`
  return g.on_break ? `${where} · on a break` : where
}

/** What is sent as the dispatch's notes: the server keeps one text and has no
 *  field for a time of arrival, so an ETA that was typed is said in words
 *  rather than dropped. */
export function dispatchNotes(eta: string, notes: string): string | undefined {
  const minutes = parseInt(eta, 10)
  const parts = [Number.isFinite(minutes) && minutes > 0 ? `ETA ${minutes} min.` : '', notes.trim()].filter(Boolean)
  return parts.length ? parts.join(' ') : undefined
}

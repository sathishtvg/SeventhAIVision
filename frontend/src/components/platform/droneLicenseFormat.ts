/** What the Drone Patrol licence form holds while it is being edited, and how it is read back. */
import type { DroneLicense, DroneLicenseBody } from '@/api/platformDroneLicense'

export interface Draft { on: boolean; until: string; drones: string; missions: string; sites: string; notes: string }

const asText = (n: number | null) => (n == null ? '' : String(n))

/** What was typed as a limit: nothing is no limit; otherwise a whole number, or NaN when it is not one. */
export const asLimit = (s: string): number | null =>
  (s.trim() === '' ? null : /^\d+$/.test(s.trim()) ? Number(s.trim()) : NaN)

export function draftOf(l: DroneLicense): Draft {
  return { on: l.is_enabled, until: l.expires_at ? l.expires_at.slice(0, 10) : '', drones: asText(l.max_drones),
           missions: asText(l.max_missions), sites: asText(l.max_sites), notes: l.notes ?? '' }
}

/** A day typed as the last day of the licence: it runs to the end of that day, UTC. */
export const untilEndOf = (date: string): string | null => (date ? `${date}T23:59:59Z` : null)

/** Whether every limit typed is a whole number or empty. */
export const limitsValid = (d: Draft) =>
  [d.drones, d.missions, d.sites].every((s) => { const n = asLimit(s); return n === null || !Number.isNaN(n) })

export function bodyOf(d: Draft): DroneLicenseBody {
  return { is_enabled: d.on, expires_at: untilEndOf(d.until), max_drones: asLimit(d.drones), max_missions: asLimit(d.missions),
           max_sites: asLimit(d.sites), notes: d.notes.trim() || null }
}

export const licenceDay = (iso: string | null) =>
  (iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : null)

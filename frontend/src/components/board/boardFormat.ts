/** The words the operations board and the daily briefing use. */
import type { AsAt, BriefingState, BriefingSummary, DeviceState, Figures, PatrolFigures, PatrolKind } from '@/api/operationsBoard'

export const PERIODS = [1, 7, 30]

export const periodLabel = (days: number) => (days === 1 ? 'The last 24 hours' : `The last ${days} days`)

export const PATROL_LABEL: Record<PatrolKind, string> = { tours: 'Guard tours', virtual: 'Virtual patrols', drone: 'Drone patrols' }
export const PATROL_KINDS: PatrolKind[] = ['tours', 'virtual', 'drone']

/** "No reading" is its own word: a device nothing is known of is not called working. */
export const DEVICE_LABEL: Record<DeviceState, string> = {
  DOWN: 'Down', DEGRADED: 'Degraded', NOT_KNOWN: 'No reading', OK: 'Working', OFF: 'Switched off',
}
export const DEVICE_COLOUR: Record<DeviceState, 'error' | 'warning' | 'info' | 'success' | 'default'> = {
  DOWN: 'error', DEGRADED: 'warning', NOT_KNOWN: 'info', OK: 'success', OFF: 'default',
}
export const DEVICE_ORDER: DeviceState[] = ['DOWN', 'DEGRADED', 'NOT_KNOWN', 'OK', 'OFF']

export const STATE_LABEL: Record<BriefingState, string> = { DRAFT: 'Draft', PUBLISHED: 'Published', DISCARDED: 'Set aside' }
export const STATE_COLOUR: Record<BriefingState, 'warning' | 'success' | 'default'> = {
  DRAFT: 'warning', PUBLISHED: 'success', DISCARDED: 'default',
}

/** What a line is true of, when that is not the day itself. */
export const AS_AT_LABEL: Record<AsAt, string | null> = { PERIOD: null, DRAFTING: 'when drafted', WEEKS: 'the 4 weeks before' }

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** A calendar day as it is written, from `2026-10-07`. Read as a local date, so it is the day it says. */
export function fmtDay(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
}

/** A day as the server takes it, so many days before today here. */
export function dayBefore(days: number, today: Date = new Date()): string {
  const at = new Date(today.getFullYear(), today.getMonth(), today.getDate() - days)
  return `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, '0')}-${String(at.getDate()).padStart(2, '0')}`
}

/** A length of time in words. Nothing measured is a dash, not zero. */
export function lasting(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return 'under a minute'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'}`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'}${rest ? ` ${rest} minute${rest === 1 ? '' : 's'}` : ''}`
  const days = Math.floor(hours / 24)
  const left = hours % 24
  return `${days} day${days === 1 ? '' : 's'}${left ? ` ${left} hour${left === 1 ? '' : 's'}` : ''}`
}

/** How many patrols of one kind are over, one way or another. */
export const over = (p: PatrolFigures) => p.done + p.partial + p.missed + p.failed

/** One kind of patrol in a line: what was done of what is over, and what is still to do. */
export function patrolLine(p: PatrolFigures): string {
  const done = over(p)
  if (!done && !p.open) return 'None fell due'
  if (!done) return `${p.open} still to be done; none is over yet`
  const rest = [[p.partial, 'done in part'], [p.missed, 'missed'], [p.failed, 'failed']] as const
  const said = rest.filter(([n]) => n).map(([n, word]) => `${n} ${word}`)
  return `${p.done} done of ${done} over${said.length ? `; ${said.join(', ')}` : ''}${p.open ? `; ${p.open} still to be done` : ''}`
}

/** Patrols missed or failed across the kinds the caller may read — or null when none may be read. */
export function patrolsNotDone(f: Figures): number | null {
  if (!f.PATROLS) return null
  return PATROL_KINDS.reduce((n, kind) => n + (f.PATROLS![kind]?.missed ?? 0) + (f.PATROLS![kind]?.failed ?? 0), 0)
}

export const clocksMissed = (f: Figures): number | null =>
  f.RESPONSE ? f.RESPONSE.missed.acknowledge + f.RESPONSE.missed.arrival + f.RESPONSE.missed.resolve : null

/** The columns of the table of sites: one figure from each section, or null where the section is not read. */
export const COLUMNS: { key: string; label: string; of: (f: Figures) => number | null }[] = [
  { key: 'opened', label: 'Incidents opened', of: (f) => f.INCIDENTS?.opened ?? null },
  { key: 'open', label: 'Open now', of: (f) => f.INCIDENTS?.open_now ?? null },
  { key: 'clocks', label: 'Clocks missed', of: clocksMissed },
  { key: 'patrols', label: 'Patrols not done', of: patrolsNotDone },
  { key: 'guards', label: 'On shift now', of: (f) => f.GUARDS?.on_shift_now ?? null },
  { key: 'devices', label: 'Devices down', of: (f) => f.DEVICES?.by_state.DOWN ?? null },
  { key: 'visitors', label: 'Visitors on site', of: (f) => f.VISITORS?.on_site_now ?? null },
  { key: 'orders', label: 'Orders overdue', of: (f) => f.MAINTENANCE?.overdue_now ?? null },
]

/** A figure in a cell: a number, or a dash for what is not read. */
export const cell = (n: number | null) => (n === null ? '—' : n.toLocaleString('en'))

export const briefingFor = (b: Pick<BriefingSummary, 'site'>) => b.site?.name ?? 'Every site together'

/** Who did what to a briefing last, in a line. */
export function briefingLine(b: BriefingSummary): string {
  if (b.state === 'PUBLISHED') {
    return `Published by ${b.published_by_name ?? 'somebody no longer on the system'}, ${fmt(b.published_at)}`
  }
  return `Drafted by ${b.drafted_by_name ?? 'somebody no longer on the system'}, ${fmt(b.drafted_at)}`
}

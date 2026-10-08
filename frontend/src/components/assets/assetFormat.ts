/** The words the device health, asset register and maintenance screens share. */
import type { Asset, AssetStatus, Down, HealthState, Reading, Warranty } from '@/api/securityAssets'
import type { OrderKind, OrderState, Origin, Priority, Schedule, WorkOrder } from '@/api/maintenance'

export const HEALTH_LABEL: Record<HealthState, string> = {
  OK: 'Working', DEGRADED: 'Degraded', DOWN: 'Down', NOT_KNOWN: 'Not known', OFF: 'Switched off',
}

/** "Not known" is its own colour: it is neither working nor down. */
export const HEALTH_COLOUR: Record<HealthState, 'success' | 'warning' | 'error' | 'info' | 'default'> = {
  OK: 'success', DEGRADED: 'warning', DOWN: 'error', NOT_KNOWN: 'info', OFF: 'default',
}

export const STATUS_LABEL: Record<AssetStatus, string> = {
  IN_SERVICE: 'In service', UNDER_REPAIR: 'Under repair', SPARE: 'Spare', RETIRED: 'Retired',
}

export const WARRANTY_LABEL: Record<Warranty, string> = { IN: 'In warranty', OUT: 'Out of warranty', NOT_RECORDED: 'Not recorded' }

export const ORDER_LABEL: Record<OrderState, string> = {
  SUGGESTED: 'Put forward', OPEN: 'Open', IN_PROGRESS: 'In progress', DONE: 'Done', CANCELLED: 'Cancelled',
  DISMISSED: 'Dismissed',
}

export const ORDER_COLOUR: Record<OrderState, 'info' | 'default' | 'warning' | 'success'> = {
  SUGGESTED: 'info', OPEN: 'default', IN_PROGRESS: 'warning', DONE: 'success', CANCELLED: 'default', DISMISSED: 'default',
}

export const KIND_LABEL: Record<OrderKind, string> = { CORRECTIVE: 'Corrective', PREVENTIVE: 'Preventive', INSPECTION: 'Inspection' }
export const PRIORITY_LABEL: Record<Priority, string> = { LOW: 'Low', NORMAL: 'Normal', HIGH: 'High', URGENT: 'Urgent' }

/** Where an order came from. A suggestion says it was the platform's, and what from. */
export const ORIGIN_LABEL: Record<Origin, string> = {
  PERSON: 'Raised by a person', DEFECT: 'Raised for a facility defect', HEALTH: 'Put forward from device health',
  SCHEDULE: 'Put forward by a schedule',
}

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** A `datetime-local` value as the moment it names, or null when it is empty. */
export const iso = (local: string): string | null => (local ? new Date(local).toISOString() : null)

/** A length of time in the words a person would use. */
export function span(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return 'less than a minute'
  const minutes = Math.floor(s / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`
  const days = Math.floor(hours / 24)
  return `${days} days`
}

/**
 * For how long a device has been as it is. When all that is known is when the
 * platform first read it so, that is what is said: "for at least".
 */
export function sinceLine(r: Pick<Reading, 'since' | 'since_is_when_first_read'>, now: Date = new Date()): string {
  if (!r.since) return ''
  const length = span((now.getTime() - new Date(r.since).getTime()) / 1000)
  return r.since_is_when_first_read ? `for at least ${length}` : `for ${length}`
}

const FACT: Record<string, string> = {
  last_frame_at: 'Last frame', last_probe_at: 'Last probed', last_reading_at: 'Last reading',
  last_heartbeat_at: 'Last heartbeat', last_seen_at: 'Last seen', last_contact_at: 'Last contact',
}

/** What a reading was made of, each with how long ago. Nothing that was not reported is added. */
export function factLines(facts: Reading['facts'], now: Date = new Date()): string[] {
  const lines: string[] = []
  for (const [key, label] of Object.entries(FACT)) {
    const value = facts[key]
    if (typeof value === 'string') lines.push(`${label} ${span((now.getTime() - new Date(value).getTime()) / 1000)} ago`)
  }
  if (typeof facts.disconnects_24h === 'number' && facts.disconnects_24h > 0) {
    lines.push(`Disconnected ${facts.disconnects_24h} ${facts.disconnects_24h === 1 ? 'time' : 'times'} in 24 hours`)
  }
  if (typeof facts.expected_interval_seconds === 'number') lines.push(`A reading is expected every ${span(facts.expected_interval_seconds)}`)
  if (typeof facts.battery_level === 'number') lines.push(`Battery ${facts.battery_level}%`)
  if (typeof facts.storage_free_pct === 'number') lines.push(`Storage ${facts.storage_free_pct}% free`)
  if (typeof facts.reports === 'string') lines.push(`It reports ${facts.reports.replace(/_/g, ' ').toLowerCase()}`)
  return lines
}

/** How long a device was down in a period — and from when anything is known of it. */
export function downLine(d: Down, now: Date = new Date()): string {
  if (!d.known_from) return `Last ${d.days} days: nothing has been kept of it yet.`
  const known = span((now.getTime() - new Date(d.known_from).getTime()) / 1000)
  const outages = d.times_down === 1 ? '1 outage' : `${d.times_down} outages`
  const down = d.down_seconds ? `down ${span(d.down_seconds)} in ${outages}` : 'not read as down'
  return `Last ${d.days} days: ${down}, of the ${known} that are known.`
}

export function warrantyLine(a: Pick<Asset, 'warranty' | 'warranty_until' | 'warranty_days_left'>): string {
  if (a.warranty === 'NOT_RECORDED') return 'Warranty not recorded'
  const days = Math.abs(a.warranty_days_left ?? 0)
  return a.warranty === 'IN' ? `In warranty until ${fmtDate(a.warranty_until)} (${days} days)`
    : `Out of warranty since ${fmtDate(a.warranty_until)}`
}

/** What an asset is, in a line: its make, model and serial number where they are recorded. */
export function madeBy(a: Pick<Asset, 'make' | 'model' | 'serial_number'>): string {
  const what = [a.make, a.model].filter(Boolean).join(' ')
  return [what, a.serial_number ? `S/N ${a.serial_number}` : ''].filter(Boolean).join(' · ')
}

/** Who has an order: one of the organisation's people, a named vendor, or nobody. */
export function heldBy(o: Pick<WorkOrder, 'assigned_to_user_name' | 'assigned_to_name'>): string {
  return [o.assigned_to_user_name, o.assigned_to_name].filter(Boolean).join(' · ') || 'Nobody yet'
}

/** When a schedule next falls due, in days. */
export function dueIn(s: Pick<Schedule, 'days_until_due' | 'is_active'>): string {
  if (!s.is_active) return 'Switched off'
  if (s.days_until_due === 0) return 'Due today'
  if (s.days_until_due < 0) return `Overdue by ${-s.days_until_due} ${s.days_until_due === -1 ? 'day' : 'days'}`
  return `Due in ${s.days_until_due} ${s.days_until_due === 1 ? 'day' : 'days'}`
}

export const every = (s: Pick<Schedule, 'every_days'>) => (s.every_days === 1 ? 'Every day' : `Every ${s.every_days} days`)

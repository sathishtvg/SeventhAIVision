/**
 * Smart Investigation — the words the investigation screens share.
 */
import type { Found, Kind, Ref } from '@/api/investigations'

/** One record of the kind, as a person would say it. */
export const KIND_LABEL: Record<Kind | 'NOTE', string> = {
  ALERT: 'Alert', INCIDENT: 'Incident', PLATE_READ: 'Plate read', FACE_MATCH: 'Watchlist face match',
  DETECTION: 'Raw detection', ACCESS: 'Door event', VISITOR: 'Visitor movement', OCCURRENCE: 'Occurrence book',
  DRONE: 'Drone sighting', ALARM: 'Alarm panel', PATROL_SCAN: 'Patrol scan', MAN_DOWN: 'Guard emergency',
  SITUATION: 'Situation', SENSOR: 'Sensor alert', NOTE: 'Note',
}

/** The screen each kind of record already lives on. Two kinds have a page of
 *  their own for one record; the rest open the list it is found in. */
const HOME: Record<Kind, { path: string; screen: string }> = {
  ALERT: { path: '/alerts', screen: 'Alerts' },
  INCIDENT: { path: '/incidents', screen: 'Incidents' },
  PLATE_READ: { path: '/detections', screen: 'Detections' },
  FACE_MATCH: { path: '/detections', screen: 'Detections' },
  DETECTION: { path: '/detections', screen: 'Detections' },
  ACCESS: { path: '/access', screen: 'Access Control' },
  VISITOR: { path: '/vms-onsite', screen: 'Visitors' },
  OCCURRENCE: { path: '/guard-ops', screen: 'Guard Operations' },
  DRONE: { path: '/drone-events', screen: 'Drone Events' },
  ALARM: { path: '/alarms', screen: 'Alarms' },
  PATROL_SCAN: { path: '/guard-ops', screen: 'Guard Operations' },
  MAN_DOWN: { path: '/man-down', screen: 'Man Down' },
  SITUATION: { path: '/situations', screen: 'Situations' },
  SENSOR: { path: '/iot', screen: 'IoT Sensors' },
}

export function home(record: Pick<Found, 'kind' | 'id'>): { path: string; screen: string; exact: boolean } {
  const where = HOME[record.kind]
  const exact = record.kind === 'SITUATION' || record.kind === 'DRONE'
  return { ...where, path: exact ? `${where.path}/${record.id}` : where.path, exact }
}

export const refOf = (record: Pick<Found, 'kind' | 'id' | 'occurred_at'>): Ref =>
  ({ kind: record.kind, id: record.id, occurred_at: record.occurred_at })

export const keyOf = (record: Pick<Found, 'kind' | 'id'>) => `${record.kind}:${record.id}`

export const pretty = (s: string | null | undefined) =>
  (s ?? '').replace(/[_.]/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase())

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' })
}

/** The time between two sightings, in the words a person would use. */
export function gap(seconds: number): string {
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`
  return `${Math.round(hours / 24)} days`
}

/** How far apart two cameras are — or that it is not known. */
export function distance(metres: number | null): string {
  if (metres === null) return 'distance not known'
  if (metres === 0) return 'the same place'
  return metres < 1000 ? `${metres} m` : `${(metres / 1000).toFixed(1)} km`
}

/** An ISO time as the value of a `datetime-local` box, in the viewer's own zone. */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** The value of a `datetime-local` box as an ISO time with its zone. */
export function fromLocalInput(value: string): string | undefined {
  if (!value) return undefined
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString()
}

/** Who or what a record is about, as far as this reader may be told. */
export function subject(record: Found): string | null {
  if (!record.subject_kind) return null
  if (record.subject_label) return record.subject_label
  return record.kind === 'FACE_MATCH' ? 'Somebody on the watchlist — the name is the watchlist keeper’s to see' : null
}

/** Whether a record's subject is one the platform can follow from camera to camera. */
export function followable(record: Found): { plate?: string; watchlist_entry_id?: string } | null {
  if (record.kind === 'PLATE_READ' && record.subject_ref) return { plate: record.subject_ref }
  if (record.kind === 'FACE_MATCH' && record.subject_ref) return { watchlist_entry_id: record.subject_ref }
  return null
}

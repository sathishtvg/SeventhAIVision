/**
 * Words for geofences, their rules and events, and the draft a person draws. The server checks every shape and
 * rule again; these say what it will say before it is asked.
 */
import type { Geofence, GeofenceEvent, GeofenceInput, GeofenceKind, GeofenceRule, LatLon, RuleInput, Severity } from '@/api/fleet'

export const KIND_WORDS: Record<GeofenceKind, string> = {
  entry: 'Enters', exit: 'Leaves', dwell_inside: 'Inside longer than', dwell_outside: 'Outside longer than',
  overspeed: 'Faster than, inside',
}
export const KINDS: GeofenceKind[] = ['entry', 'exit', 'dwell_inside', 'dwell_outside', 'overspeed']
export const SEVERITIES: Severity[] = ['low', 'medium', 'high', 'critical']
export const SEVERITY_COLOUR: Record<Severity, 'default' | 'info' | 'warning' | 'error'> = {
  low: 'default', medium: 'info', high: 'warning', critical: 'error',
}

const EVENT_WORDS: Record<string, string> = {
  entry: 'Entered', exit: 'Left', dwell_inside: 'Stayed inside', dwell_outside: 'Stayed outside',
  speed_violation: 'Over the limit in',
}
export const eventWords = (e: Pick<GeofenceEvent, 'event_type'>) => EVENT_WORDS[e.event_type] ?? e.event_type

/** When it happened: a time, or "between" two when nothing was heard while the vehicle crossed. */
export function eventWhen(e: Pick<GeofenceEvent, 'occurred_at' | 'between_start' | 'between_end'>): string {
  if (e.between_start && e.between_end) {
    return `between ${new Date(e.between_start).toLocaleString()} and ${new Date(e.between_end).toLocaleString()}`
  }
  return new Date(e.occurred_at).toLocaleString()
}

export function ruleWords(r: Pick<GeofenceRule, 'kind' | 'minutes' | 'speed_kmh'>): string {
  if (r.kind === 'dwell_inside' || r.kind === 'dwell_outside') return `${KIND_WORDS[r.kind]} ${r.minutes} min`
  if (r.kind === 'overspeed') return `Faster than ${r.speed_kmh} km/h inside`
  return KIND_WORDS[r.kind]
}

export const activeRules = (g: Pick<Geofence, 'rules'>) => g.rules.filter((r) => r.is_active)

export function shapeWords(g: Pick<Geofence, 'shape' | 'radius_m' | 'polygon'>): string {
  if (g.shape === 'circle' && g.radius_m != null) {
    return g.radius_m >= 1000 ? `Circle, ${(g.radius_m / 1000).toFixed(1)} km radius` : `Circle, ${Math.round(g.radius_m)} m radius`
  }
  return `Polygon, ${g.polygon.length} points`
}

// ── the draft being drawn ───────────────────────────────────────────────────

export type Draft =
  | { kind: 'circle'; center: LatLon | null; radius: string }
  | { kind: 'polygon'; points: LatLon[] }

export function draftOf(g: Geofence | null): Draft {
  if (g?.shape === 'circle' && g.center) return { kind: 'circle', center: g.center, radius: String(g.radius_m ?? '') }
  if (g) return { kind: 'polygon', points: g.polygon }
  return { kind: 'circle', center: null, radius: '200' }
}

/** What is wrong with the draft, in words, or null when it can be sent. */
export function draftProblem(d: Draft, limits = { least_radius_m: 25, most_radius_m: 50000, least_points: 3, most_points: 200 }): string | null {
  if (d.kind === 'circle') {
    if (!d.center) return 'Click the map where the centre is.'
    const r = Number(d.radius)
    if (!d.radius || !Number.isFinite(r) || r < limits.least_radius_m || r > limits.most_radius_m) {
      return `The radius is ${limits.least_radius_m} m to ${limits.most_radius_m / 1000} km.`
    }
    return null
  }
  if (d.points.length < limits.least_points) return `Click the map at each corner: at least ${limits.least_points}.`
  if (d.points.length > limits.most_points) return `At most ${limits.most_points} corners.`
  if (selfCrossing(d.points)) return 'Two edges cross: move or take away a corner.'
  return null
}

function turn(p: LatLon, q: LatLon, r: LatLon): number {
  const v = (q.lon - p.lon) * (r.lat - q.lat) - (q.lat - p.lat) * (r.lon - q.lon)
  return Math.abs(v) < 1e-15 ? 0 : v > 0 ? 1 : -1
}

export function selfCrossing(points: LatLon[]): boolean {
  const n = points.length
  for (let i = 0; i < n; i += 1) {
    for (let j = i + 1; j < n; j += 1) {
      if (Math.abs(i - j) === 1 || Math.abs(i - j) === n - 1) continue
      const [a, b, c, d] = [points[i], points[(i + 1) % n], points[j], points[(j + 1) % n]]
      const t = [turn(a, b, c), turn(a, b, d), turn(c, d, a), turn(c, d, b)]
      if (!t.includes(0) && t[0] !== t[1] && t[2] !== t[3]) return true
    }
  }
  return false
}

export function shapeInput(d: Draft): NonNullable<GeofenceInput['shape']> | null {
  if (draftProblem(d)) return null
  if (d.kind === 'circle') return { kind: 'circle', center: d.center as LatLon, radius_m: Number(d.radius) }
  return { kind: 'polygon', points: d.points }
}

// ── the rules being edited ──────────────────────────────────────────────────

export interface RuleDraft { on: boolean; minutes: string; speed: string; severity: Severity; user_ids: string[]; role_ids: number[] }

export function ruleDrafts(g: Geofence | null): Record<GeofenceKind, RuleDraft> {
  const out = {} as Record<GeofenceKind, RuleDraft>
  for (const kind of KINDS) {
    const r = g?.rules.find((x) => x.kind === kind)
    out[kind] = {
      on: !!r?.is_active, minutes: String(r?.minutes ?? 30), speed: String(r?.speed_kmh ?? 60),
      severity: r?.severity ?? (kind === 'overspeed' ? 'high' : 'medium'), user_ids: r?.user_ids ?? [], role_ids: r?.role_ids ?? [],
    }
  }
  return out
}

export function ruleProblem(kind: GeofenceKind, r: RuleDraft): string | null {
  if (!r.on) return null
  if (kind === 'dwell_inside' || kind === 'dwell_outside') {
    const m = Number(r.minutes)
    if (!Number.isInteger(m) || m < 1 || m > 1440) return 'Minutes are 1 to 1440.'
  }
  if (kind === 'overspeed') {
    const s = Number(r.speed)
    if (!Number.isFinite(s) || s < 5 || s > 300) return 'The limit is 5 to 300 km/h.'
  }
  return null
}

export function rulesInput(drafts: Record<GeofenceKind, RuleDraft>): RuleInput[] {
  return KINDS.filter((k) => drafts[k].on).map((kind) => {
    const r = drafts[kind]
    return {
      kind, severity: r.severity, user_ids: r.user_ids, role_ids: r.role_ids,
      minutes: kind === 'dwell_inside' || kind === 'dwell_outside' ? Number(r.minutes) : null,
      speed_kmh: kind === 'overspeed' ? Number(r.speed) : null,
    }
  })
}

/** How long before or after an event something else was recorded. */
export function gapWords(seconds: number): string {
  if (seconds === 0) return 'during it'
  const a = Math.abs(seconds)
  const span = a >= 60 ? `${Math.floor(a / 60)} min ${a % 60 ? `${a % 60} s` : ''}`.trim() : `${a} s`
  return seconds < 0 ? `${span} before` : `${span} after`
}

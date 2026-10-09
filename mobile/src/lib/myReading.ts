/**
 * The words of a person's own reading, apart from the screen so they can be
 * tested with nothing mounted.
 *
 * A count is never said without how much there was to do: "3 of 4 shifts
 * worked", not "1 missed". The same sentences as the web console writes, so a
 * guard and their supervisor read the same thing.
 */
import type { Figures, SectionKey, ViolationType } from '@/api/workforce'

const VIOLATION_LABEL: Record<ViolationType, string> = {
  no_show: 'no-show', late_checkin: 'late check-in', geofence_failure: 'outside the site', early_departure: 'left early',
  manual: 'recorded by a supervisor',
}

/** So many of so many. */
export const of = (part: number, whole: number) => `${part} of ${whole}`

/** A length of time as people say it. */
export function lasting(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 90) return `${Math.round(seconds)} seconds`
  const minutes = Math.round(seconds / 60)
  return minutes < 90 ? `${minutes} minute${minutes === 1 ? '' : 's'}` : `${Math.round(minutes / 6) / 10} hours`
}

export const periodLabel = (days: number) => (days === 7 ? 'Last 7 days' : days === 28 ? 'Last 4 weeks' : `Last ${days} days`)

/** One section in lines, each a count beside what it is a count of. Nothing when the section was not read. */
export function readingLines(key: SectionKey, f: Figures): string[] {
  if (key === 'SHIFTS' && f.SHIFTS) {
    const s = f.SHIFTS
    if (!s.shifts) return ['No shifts were due to begin']
    return [
      `${of(s.worked, s.shifts)} shifts worked`,
      s.late ? `${of(s.late, s.worked)} started late — ${s.late_minutes} minute${s.late_minutes === 1 ? '' : 's'} in all` : 'None started late',
      s.not_started ? `${of(s.not_started, s.shifts)} not started` : 'None not started',
    ]
  }
  if (key === 'PATROLS' && f.PATROLS) {
    const p = f.PATROLS
    const due = p.tours_done + p.tours_missed
    return [
      due ? `${of(p.tours_done, due)} assigned tours done; ${p.tours_missed} missed` : 'No assigned tour fell due',
      p.walked ? `${p.walked} patrol${p.walked === 1 ? '' : 's'} walked: ${of(p.checkpoints_scanned, p.checkpoints_total)} checkpoints scanned`
        : 'No patrol walked',
    ]
  }
  if (key === 'RESPONSES' && f.RESPONSES) {
    const r = f.RESPONSES
    if (!r.sent) return ['Not sent to an incident']
    return [
      `Sent ${r.sent} time${r.sent === 1 ? '' : 's'}: ${r.accepted} accepted, ${r.declined} declined, ${r.arrived} arrived`,
      r.arrived ? `Half arrived within ${lasting(r.arrive_seconds)}` : 'No arrival to time',
    ]
  }
  if (key === 'VIOLATIONS' && f.VIOLATIONS) {
    const v = f.VIOLATIONS
    if (!v.recorded) return ['None recorded']
    const kinds = (Object.keys(v.by_type) as ViolationType[]).filter((k) => v.by_type[k]).map((k) => `${v.by_type[k]} ${VIOLATION_LABEL[k]}`)
    return [
      `${v.recorded} recorded: ${v.waived} waived by a reviewer, ${v.disputed} disputed by you`,
      kinds.length ? `Not waived: ${kinds.join(', ')}` : 'All of them were waived',
    ]
  }
  if (key === 'TRAINING' && f.TRAINING) {
    const t = f.TRAINING
    return [
      `${t.completed} course${t.completed === 1 ? '' : 's'} passed in the period`,
      `Courses, today: ${t.courses_lapsed_now} lapsed, ${t.courses_lapsing_now} lapsing within 30 days`,
      `Certificates, today: ${t.certificates_lapsed_now} lapsed, ${t.certificates_lapsing_now} lapsing within 30 days`,
      t.shifts_at_risk_now ? `${t.shifts_at_risk_now} rostered shift${t.shifts_at_risk_now === 1 ? ' needs' : 's need'} a certificate not held`
        : 'No rostered shift needs a certificate not held',
    ]
  }
  if (key === 'HANDOVERS' && f.HANDOVERS) {
    const h = f.HANDOVERS
    if (!h.given) return ['No handover given']
    return [`${h.given} given: ${h.accepted} accepted, ${h.disputed} disputed by the incoming guard`]
  }
  return []
}

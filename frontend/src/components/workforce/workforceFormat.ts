/** The words the workforce readings and recommendations use. */
import type { Answer, Figures, GivenAnswer, Recommendation, SectionKey, ViolationType } from '@/api/workforce'
import { lasting } from '@/components/board/boardFormat'

export const PERIODS = [7, 28, 90]

export const periodLabel = (days: number) => (days === 28 ? 'The last 4 weeks' : `The last ${days} days`)

export const ANSWER_LABEL: Record<Answer, string> = { ACCEPTED: 'Accepted', NOT_ACCEPTED: 'Not accepted' }

export const VIOLATION_LABEL: Record<ViolationType, string> = {
  no_show: 'no show', late_checkin: 'late check-in', geofence_failure: 'outside the geofence',
  early_departure: 'early departure', manual: 'recorded by hand',
}

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** So many of so many: a count is never shown without how much there was to do. */
export const of = (part: number, whole: number) => `${part} of ${whole}`

/** One section in lines, each a count beside what it is a count of. */
export function lines(key: SectionKey, f: Figures): string[] {
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
      `${v.recorded} recorded: ${v.waived} waived by a reviewer, ${v.disputed} disputed by the guard`,
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

/** The columns of the table of guards and of sites: each a count beside what it is a count of. */
export const COLUMNS: { key: string; label: string; of: (f: Figures) => string }[] = [
  { key: 'worked', label: 'Shifts worked', of: (f) => (f.SHIFTS ? of(f.SHIFTS.worked, f.SHIFTS.shifts) : '—') },
  { key: 'late', label: 'Started late', of: (f) => (f.SHIFTS ? of(f.SHIFTS.late, f.SHIFTS.worked) : '—') },
  { key: 'unstarted', label: 'Not started', of: (f) => (f.SHIFTS ? of(f.SHIFTS.not_started, f.SHIFTS.shifts) : '—') },
  { key: 'tours', label: 'Tours done', of: (f) => (f.PATROLS ? of(f.PATROLS.tours_done, f.PATROLS.tours_done + f.PATROLS.tours_missed) : '—') },
  { key: 'arrived', label: 'Arrived when sent', of: (f) => (f.RESPONSES ? of(f.RESPONSES.arrived, f.RESPONSES.sent) : '—') },
  { key: 'violations', label: 'Violations not waived',
    of: (f) => (f.VIOLATIONS ? of(f.VIOLATIONS.recorded - f.VIOLATIONS.waived, f.VIOLATIONS.recorded) : '—') },
]

/** A person's name, or that they are no longer named on the system. */
export const named = (name: string | null | undefined) => name ?? 'Somebody no longer on the system'

/** Who or what a recommendation is about. */
export const about = (r: Pick<Recommendation, 'subject' | 'site'>) => (r.subject ? named(r.subject.name) : r.site?.name ?? '')

/** A manager's answer to a recommendation, in a line. */
export function answerLine(a: NonNullable<Recommendation['answer']>): string {
  return `${ANSWER_LABEL[a.answer]} by ${named(a.answered_by_name)}, ${fmt(a.answered_at)}${a.reason ? `: ${a.reason}` : ''}`
}

export const givenAbout = (a: GivenAnswer) => a.guard_name ?? a.site_name ?? ''

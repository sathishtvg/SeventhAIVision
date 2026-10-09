/**
 * The words of the newer phone screens.
 *
 * WHY THIS MATTERS
 *   A reading is not an appraisal, and the difference is in the words: a count
 *   is never said without how much there was to do. And a task or an order a
 *   person may not finish must not be offered to them as theirs.
 */
import type { CaseDetail } from '@/api/cases'
import type { WorkOrder } from '@/api/maintenance'
import type { Figures } from '@/api/workforce'
import { apiErrorText } from './apiErrorText'
import {
  AS_AT_LABEL, ORDER_STATE_LABEL, briefingStanding, briefingWhere, inOrder, myOpenTasks, noteNeeded, orderDue, orderWhere,
  taskDue,
} from './deskWork'
import { lasting, of, periodLabel, readingLines } from './myReading'

const MINE: Required<Figures> = {
  SHIFTS: { shifts: 4, worked: 3, late: 1, late_minutes: 12, not_started: 1 },
  PATROLS: { tours_done: 2, tours_missed: 3, walked: 2, checkpoints_scanned: 16, checkpoints_total: 20 },
  RESPONSES: { sent: 3, accepted: 2, declined: 1, arrived: 2, arrive_seconds: 480 },
  VIOLATIONS: { recorded: 3, waived: 1, disputed: 1, by_type: { no_show: 0, late_checkin: 1, geofence_failure: 1, early_departure: 0, manual: 0 } },
  TRAINING: { completed: 1, courses_lapsed_now: 1, courses_lapsing_now: 0, certificates_lapsed_now: 0, certificates_lapsing_now: 1, shifts_at_risk_now: 2 },
  HANDOVERS: { given: 2, accepted: 1, disputed: 1 },
}
/** Words that would turn a count into a judgement of a person. */
const JUDGING = /poor|bad|worst|best|underperform|disciplin|warning|score|rank|rating|unreliable/i

describe('a reading', () => {
  it('never says a count without how much there was to do', () => {
    expect(of(3, 4)).toBe('3 of 4')
    expect(readingLines('SHIFTS', MINE)).toEqual(['3 of 4 shifts worked', '1 of 3 started late — 12 minutes in all', '1 of 4 not started'])
    expect(readingLines('PATROLS', MINE)).toEqual(['2 of 5 assigned tours done; 3 missed', '2 patrols walked: 16 of 20 checkpoints scanned'])
    expect(readingLines('RESPONSES', MINE)).toEqual(['Sent 3 times: 2 accepted, 1 declined, 2 arrived', 'Half arrived within 8 minutes'])
    expect(readingLines('HANDOVERS', MINE)).toEqual(['2 given: 1 accepted, 1 disputed by the incoming guard'])
    expect(readingLines('VIOLATIONS', MINE)).toEqual([
      '3 recorded: 1 waived by a reviewer, 1 disputed by you', 'Not waived: 1 late check-in, 1 outside the site'])
    expect(readingLines('TRAINING', MINE)[3]).toBe('2 rostered shifts need a certificate not held')
  })

  it('says nothing was due rather than nought of nought, and nothing of a section it was not given', () => {
    expect(readingLines('SHIFTS', { SHIFTS: { shifts: 0, worked: 0, late: 0, late_minutes: 0, not_started: 0 } })).toEqual(['No shifts were due to begin'])
    expect(readingLines('RESPONSES', { RESPONSES: { sent: 0, accepted: 0, declined: 0, arrived: 0, arrive_seconds: null } })).toEqual(['Not sent to an incident'])
    expect(readingLines('VIOLATIONS', { VIOLATIONS: { ...MINE.VIOLATIONS, recorded: 0 } })).toEqual(['None recorded'])
    expect(readingLines('SHIFTS', {})).toEqual([])
    expect(readingLines('TRAINING', { SHIFTS: MINE.SHIFTS })).toEqual([])
  })

  it('judges nobody in any line, and names its periods plainly', () => {
    for (const key of ['SHIFTS', 'PATROLS', 'RESPONSES', 'VIOLATIONS', 'TRAINING', 'HANDOVERS'] as const) {
      for (const line of readingLines(key, MINE)) expect(line).not.toMatch(JUDGING)
    }
    expect([7, 28, 90].map(periodLabel)).toEqual(['Last 7 days', 'Last 4 weeks', 'Last 90 days'])
    expect(lasting(45)).toBe('45 seconds')
    expect(lasting(60 * 60 * 3)).toBe('3 hours')
    expect(lasting(null)).toBe('—')
  })
})

describe('a briefing', () => {
  const b = { id: 'b1', site: null, briefing_date: '2026-10-08', revision: 1, state: 'PUBLISHED' as const, replaced_by: null,
              published_at: null, published_by_name: null, note: null, left_out: [] }
  it('says whose day it is and how it stands', () => {
    expect(briefingWhere(b)).toBe('Every site')
    expect(briefingWhere({ ...b, site: { id: 's1', name: 'Factory A' } })).toBe('Factory A')
    expect(briefingStanding(b)).toBeNull()
    expect(briefingStanding({ ...b, revision: 2 })).toBe('Revision 2')
    expect(briefingStanding({ ...b, replaced_by: 'b2' })).toBe('Replaced by a later revision')
  })
  it('marks only a line that is not simply about the day', () => {
    expect(AS_AT_LABEL).toEqual({ PERIOD: null, DRAFTING: 'when it was drafted', WEEKS: 'the weeks before' })
  })
})

describe('a work order', () => {
  const o: WorkOrder = { id: 'w1', number: 'WO-0001', site_name: 'Factory A', asset_code: 'CAM-01', asset_name: 'Loading bay camera',
                         title: 'Loading bay: down', description: null, kind: 'CORRECTIVE', priority: 'HIGH', state: 'OPEN',
                         due_at: '2026-10-10T02:00:00Z', started_at: null, overdue: false, assigned_to_me: true,
                         may: { start: true, complete: false } }
  it('says what it is on, where, and when it is due', () => {
    expect(orderWhere(o)).toBe('Loading bay camera (CAM-01) · Factory A')
    expect(orderWhere({ ...o, asset_name: null, asset_code: null, site_name: null })).toBe('No asset or site named')
    expect(orderDue(o)).toMatch(/^Due /)
    expect(orderDue({ ...o, overdue: true })).toMatch(/^Was due .+ — overdue$/)
    expect(orderDue({ ...o, due_at: null })).toBe('No date set')
    expect(ORDER_STATE_LABEL.IN_PROGRESS).toBe('In hand')
  })
  it('puts what is in hand first, then what is overdue, then the rest by date', () => {
    const list = [{ ...o, id: 'later', due_at: '2026-10-20T00:00:00Z' }, { ...o, id: 'late', overdue: true },
                  { ...o, id: 'none', due_at: null }, { ...o, id: 'hand', state: 'IN_PROGRESS' as const }, { ...o, id: 'soon' }]
    expect(inOrder(list).map((x) => x.id)).toEqual(['hand', 'late', 'soon', 'later', 'none'])
    expect(list[0].id).toBe('later')
  })
})

describe('a case task', () => {
  const task = { id: 't1', title: 'Ask the haulier', detail: null, assigned_to_user_id: 'me', due_at: '2026-10-12T00:00:00Z',
                 state: 'OPEN' as const, created_by_name: 'Siti Rahman', may_finish: true }
  const c: CaseDetail = { id: 'c1', case_number: 'CASE-0001', title: 'Forced gate', status: 'OPEN', site: { id: 's1', name: 'Factory A' },
                          tasks_open: 3, tasks: [task, { ...task, id: 't2', may_finish: false }, { ...task, id: 't3', state: 'DONE' },
                                                 { ...task, id: 't4', due_at: null }] }
  it('is offered only when it is still to do and the server says this person may finish it', () => {
    expect(myOpenTasks([c]).map((t) => t.task.id)).toEqual(['t1', 't4'])
    expect(myOpenTasks([c])[0]).toMatchObject({ caseId: 'c1', caseNumber: 'CASE-0001', caseTitle: 'Forced gate' })
    // A case that is waiting to be closed, or closed, takes no more work; one still loading is not there yet.
    expect(myOpenTasks([{ ...c, status: 'AWAITING_APPROVAL' }, { ...c, status: 'CLOSED' }, undefined])).toEqual([])
  })
  it('is dropped with why, and may be done without a word', () => {
    expect(noteNeeded('drop', '   ')).toBe(true)
    expect(noteNeeded('drop', 'The client declined.')).toBe(false)
    expect(noteNeeded('done', '')).toBe(false)
    expect(taskDue(task)).toMatch(/^Due /)
    expect(taskDue({ ...task, due_at: null })).toBe('No date set')
  })
})

describe('a refusal', () => {
  const refusal = (detail: unknown, status = 409) => ({ response: { status, data: { detail } } })
  it('is said in the server\'s own words', () => {
    expect(apiErrorText(refusal('That task is finished already.'))).toBe('That task is finished already.')
    expect(apiErrorText(refusal({ message: 'Only work that has been started can be completed.' }))).toBe(
      'Only work that has been started can be completed.')
    expect(apiErrorText(refusal([{ msg: 'Field required' }], 422))).toBe('Field required')
    expect(apiErrorText(refusal('x', 429))).toMatch(/Too many requests/)
    expect(apiErrorText(new Error('Network Error'))).toBe('Could not reach the server. Check the connection and try again.')
  })
})

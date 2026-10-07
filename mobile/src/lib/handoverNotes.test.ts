/**
 * The rules of the two handover cards.
 *
 * Which instruction is at the top, and which shift the summary is of, are what
 * a guard acts on at the start and at the end of a shift. Showing a read
 * instruction above an unread one, or yesterday's shift instead of today's,
 * both look perfectly normal in a screenshot.
 */
import {
  SUMMARY_BUTTON, SUMMARY_LINE, instructionMeta, instructionsHeading, shiftToSummarise, summaryStep, unreadCount,
  unreadFirst,
} from './handoverNotes'
import type { Instruction, ShiftSummary } from '@/api/occurrenceBook'
import type { Shift } from '@/api/patrols'

const NOW = new Date('2026-10-07T12:00:00Z')

const note = (over: Partial<Instruction>): Instruction => ({
  id: 'n1', site_id: 's1', site_name: 'Factory A', body: 'Gate 3 stays locked.', issued_by_name: 'Lim Mei Ling',
  issued_at: '2026-10-05T02:00:00Z', expires_at: null, in_force: true, read_by_me: false, ...over })

const shift = (over: Partial<Shift>): Shift => ({
  id: 'x', guard_user_id: 'g', site_id: 's', scheduled_start: '2026-10-07T00:00:00Z', scheduled_end: '2026-10-07T12:00:00Z',
  actual_start: null, actual_end: null, status: 'scheduled', is_late: null, late_minutes: null, overtime_minutes: null,
  is_within_geofence: null, on_break: false, guard_name: 'Tan Wei Ming', site_name: 'Factory A', ...over })

const summary = (over: Partial<ShiftSummary>): ShiftSummary => ({
  id: 'm1', shift_id: 'x', site_name: 'Factory A', drafted_text: 'Drafted', final_text: 'Drafted', state: 'DRAFT',
  confirmed_at: null, edited: false, note: 'Check it.', may: { edit: true, confirm: true, discard: true }, ...over })

describe('instructions', () => {
  const read = note({ id: 'read', read_by_me: true, issued_at: '2026-10-07T01:00:00Z' })
  const old = note({ id: 'old', issued_at: '2026-10-01T01:00:00Z' })
  const fresh = note({ id: 'fresh', issued_at: '2026-10-06T01:00:00Z' })

  it('puts the unread first, and the newest first among those', () => {
    expect(unreadFirst([read, old, fresh]).map((n) => n.id)).toEqual(['fresh', 'old', 'read'])
    expect(unreadFirst([])).toEqual([])
  })

  it('does not reorder the list it was given', () => {
    const given = [read, old, fresh]
    unreadFirst(given)
    expect(given.map((n) => n.id)).toEqual(['read', 'old', 'fresh'])
  })

  it('says how many are in force and how many are unread', () => {
    expect(unreadCount([read, old, fresh])).toBe(2)
    expect(instructionsHeading([read, old, fresh])).toBe('3 instructions in force — 2 you have not read')
    expect(instructionsHeading([read])).toBe('1 instruction in force')
  })

  it('says where it is for, who issued it, and until when', () => {
    const day = (iso: string) => iso.slice(0, 10)
    expect(instructionMeta(old, day)).toBe('Factory A · Lim Mei Ling · 2026-10-01')
    expect(instructionMeta(note({ issued_by_name: null, expires_at: '2026-10-09T10:00:00Z' }), day))
      .toBe('Factory A · 2026-10-05 · until 2026-10-09')
  })
})

describe('shiftToSummarise', () => {
  it('is the shift running now', () => {
    const running = shift({ id: 'running', status: 'active', actual_start: '2026-10-07T00:02:00Z' })
    const ended = shift({ id: 'ended', status: 'completed', actual_start: '2026-10-06T12:00:00Z', actual_end: '2026-10-07T00:00:00Z' })
    expect(shiftToSummarise([ended, running], NOW)?.id).toBe('running')
  })

  it('is otherwise the one that ended most recently, within the day', () => {
    const earlier = shift({ id: 'earlier', status: 'completed', actual_start: '2026-10-06T00:00:00Z', actual_end: '2026-10-06T13:00:00Z' })
    const later = shift({ id: 'later', status: 'completed', actual_start: '2026-10-06T22:00:00Z', actual_end: '2026-10-07T10:00:00Z' })
    expect(shiftToSummarise([earlier, later], NOW)?.id).toBe('later')
  })

  it('is nothing when the last shift ended more than a day ago, or none has started', () => {
    const long = shift({ status: 'completed', actual_start: '2026-10-04T00:00:00Z', actual_end: '2026-10-04T12:00:00Z' })
    expect(shiftToSummarise([long, shift({ id: 'tomorrow' })], NOW)).toBeNull()
    expect(shiftToSummarise([], NOW)).toBeNull()
  })
})

describe('the summary card', () => {
  it('offers to draft, then to read and confirm, then to read', () => {
    expect(summaryStep(null)).toBe('draft')
    expect(summaryStep(undefined)).toBe('draft')
    expect(summaryStep(summary({}))).toBe('review')
    expect(summaryStep(summary({ state: 'CONFIRMED' }))).toBe('done')
    expect(SUMMARY_BUTTON).toEqual({ draft: 'Draft the summary', review: 'Read and confirm', done: 'Read it' })
  })

  it('says the app drafts it and the guard confirms it', () => {
    expect(SUMMARY_LINE.draft).toMatch(/drafts it from what was recorded; you check it and confirm it/)
    expect(SUMMARY_LINE.done).toMatch(/with the handover/)
  })
})

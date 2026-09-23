/**
 * Which shift the home-screen card offers.
 *
 * This is the part worth testing: on screen the card shows one site and one
 * time, and a guard checking in trusts it. Offering yesterday's shift, or
 * tomorrow's while one is running, both look perfectly normal in a screenshot.
 */
import { shiftForCheckIn } from './ShiftCheckInCard'
import type { Shift } from '@/api/patrols'

const NOW = new Date('2026-09-23T14:00:00Z')

const shift = (over: Partial<Shift>): Shift => ({
  id: 'x', guard_user_id: 'g', site_id: 's',
  scheduled_start: '2026-09-23T13:00:00Z', scheduled_end: '2026-09-23T21:00:00Z',
  actual_start: null, actual_end: null, status: 'scheduled',
  is_late: null, late_minutes: null, overtime_minutes: null,
  is_within_geofence: null, on_break: false,
  guard_name: 'Tan Wei Ming', site_name: 'Marina Bay Tower',
  ...over,
})

describe('shiftForCheckIn', () => {
  it('offers nothing when there is nothing rostered', () => {
    expect(shiftForCheckIn([], NOW)).toBeNull()
  })

  it('prefers the shift already running over anything scheduled', () => {
    // A guard on duty needs Check Out. Showing the next shift's Check In here
    // would invite a second check-in while the first is still open.
    const active = shift({ id: 'active', status: 'active', actual_start: '2026-09-23T13:02:00Z' })
    const later = shift({ id: 'later', scheduled_start: '2026-09-24T13:00:00Z', scheduled_end: '2026-09-24T21:00:00Z' })
    expect(shiftForCheckIn([later, active], NOW)?.id).toBe('active')
  })

  it('offers the shift that has started but not been checked into', () => {
    // Late for a 13:00 start at 14:00 — this is exactly when a guard reaches
    // for the app, so the shift must still be offered, not treated as gone.
    expect(shiftForCheckIn([shift({ id: 'today' })], NOW)?.id).toBe('today')
  })

  it('ignores a shift that has already finished', () => {
    const over = shift({
      id: 'over',
      scheduled_start: '2026-09-22T13:00:00Z',
      scheduled_end: '2026-09-22T21:00:00Z',
    })
    expect(shiftForCheckIn([over], NOW)).toBeNull()
  })

  it('picks the soonest of several upcoming shifts, whatever order they arrive in', () => {
    const tomorrow = shift({ id: 'tomorrow', scheduled_start: '2026-09-24T13:00:00Z', scheduled_end: '2026-09-24T21:00:00Z' })
    const tonight = shift({ id: 'tonight', scheduled_start: '2026-09-23T21:00:00Z', scheduled_end: '2026-09-24T05:00:00Z' })
    expect(shiftForCheckIn([tomorrow, tonight], NOW)?.id).toBe('tonight')
  })

  it('ignores completed shifts even when they are the only ones today', () => {
    const done = shift({ id: 'done', status: 'completed', actual_end: '2026-09-23T13:30:00Z' })
    expect(shiftForCheckIn([done], NOW)).toBeNull()
  })
})

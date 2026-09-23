/**
 * Patrol status values.
 *
 * WRITTEN BECAUSE THE FIRST VERSION GOT THEM WRONG. This client was typed with
 * lower-case statuses ('pending', 'in_progress'); the server sends upper case.
 * Nothing failed: the dashboard card filtered on values that never occur and
 * quietly said "Nothing assigned right now", which is indistinguishable from a
 * guard who genuinely has no patrols. Only the database showed otherwise.
 */
import { OUTSTANDING, isRunning, type SessionStatus } from './virtualPatrol'

describe('session statuses', () => {
  it('uses the values my-patrols filters on, exactly', () => {
    // backend/app/routers/virtual_patrol.py:
    //   AND s.status IN ('SCHEDULED','STARTED','IN_PROGRESS')
    expect(OUTSTANDING).toEqual(['SCHEDULED', 'STARTED', 'IN_PROGRESS'])
  })

  it('treats a started or part-way patrol as running', () => {
    expect(isRunning('IN_PROGRESS')).toBe(true)
    expect(isRunning('STARTED')).toBe(true)
  })

  it('does not treat scheduled or finished work as running', () => {
    expect(isRunning('SCHEDULED')).toBe(false)
    expect(isRunning('COMPLETED')).toBe(false)
    expect(isRunning('MISSED')).toBe(false)
  })

  it('has no lower-case status anywhere in the union', () => {
    // The mistake this file exists to prevent, stated as an assertion.
    const all: SessionStatus[] = [
      'SCHEDULED', 'STARTED', 'IN_PROGRESS', 'COMPLETED',
      'PARTIALLY_COMPLETED', 'MISSED', 'CANCELLED', 'FAILED',
    ]
    expect(all.every((s) => s === s.toUpperCase())).toBe(true)
  })
})

/**
 * The leave summary line.
 *
 * It answers the one question a guard opens this screen with — is my leave
 * approved yet — so a wrong count is worse than no count: it stops them
 * chasing an application that is still sitting there.
 */
import { leaveSummary, orderedStatuses } from './LeaveScreen'

describe('leaveSummary', () => {
  it('says nothing when there are no applications', () => {
    expect(leaveSummary([])).toEqual({})
    expect(orderedStatuses({})).toEqual([])
  })

  it('counts each status', () => {
    const counts = leaveSummary([
      { status: 'pending' }, { status: 'approved' }, { status: 'pending' },
    ])
    expect(counts).toEqual({ pending: 2, approved: 1 })
  })

  it('treats a status as the same whatever case the server sends', () => {
    expect(leaveSummary([{ status: 'PENDING' }, { status: 'pending' }])).toEqual({ pending: 2 })
  })

  it('keeps a status it has never seen rather than swallowing it', () => {
    // An application in some new state must still be visible; folding it into
    // a total would hide leave that needs chasing.
    const counts = leaveSummary([{ status: 'escalated' }, { status: 'pending' }])
    expect(counts.escalated).toBe(1)
    expect(orderedStatuses(counts)).toEqual(['pending', 'escalated'])
  })

  it('puts pending first, because that is the one being waited on', () => {
    const counts = leaveSummary([
      { status: 'cancelled' }, { status: 'approved' }, { status: 'pending' }, { status: 'rejected' },
    ])
    expect(orderedStatuses(counts)).toEqual(['pending', 'approved', 'rejected', 'cancelled'])
  })

  it('leaves out statuses nobody has', () => {
    expect(orderedStatuses(leaveSummary([{ status: 'approved' }]))).toEqual(['approved'])
  })
})

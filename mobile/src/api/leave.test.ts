/**
 * Whether a leave application can be submitted, and why not.
 *
 * The old form was a boolean: Submit went grey and said nothing, so a guard
 * with a mistyped date had no way to know which field was wrong. Each rule here
 * is one a guard will actually hit.
 */
import { leaveRequestBlocker } from './leave'
import type { LeaveType } from './leave'

const annual: LeaveType = {
  id: 'a', name: 'Annual Leave', default_annual_days: 14,
  requires_document: false, is_active: true,
} as LeaveType

const medical: LeaveType = { ...annual, id: 'm', name: 'Medical Leave', requires_document: true }

const ok = { type: annual, startDate: '2026-09-23', endDate: '2026-09-24', attachment: null }

describe('leaveRequestBlocker', () => {
  it('allows a complete application', () => {
    expect(leaveRequestBlocker(ok)).toBeNull()
  })

  it('asks for the missing piece by name', () => {
    expect(leaveRequestBlocker({ ...ok, type: undefined })).toMatch(/leave type/i)
    expect(leaveRequestBlocker({ ...ok, startDate: '' })).toMatch(/start date/i)
    expect(leaveRequestBlocker({ ...ok, endDate: '' })).toMatch(/end date/i)
  })

  it('catches an end date before the start', () => {
    expect(leaveRequestBlocker({ ...ok, startDate: '2026-09-24', endDate: '2026-09-23' }))
      .toMatch(/before the start/i)
  })

  it('allows a single day, where start and end are the same', () => {
    expect(leaveRequestBlocker({ ...ok, startDate: '2026-09-23', endDate: '2026-09-23' })).toBeNull()
  })

  it('requires a document when the leave type says so', () => {
    const blocked = leaveRequestBlocker({ ...ok, type: medical })
    expect(blocked).toMatch(/Medical Leave needs a supporting document/)
  })

  it('accepts medical leave once the certificate is attached', () => {
    expect(leaveRequestBlocker({ ...ok, type: medical, attachment: { uri: 'file://mc.jpg' } }))
      .toBeNull()
  })

  it('decides on the flag, not on the word "medical"', () => {
    // An agency calling it "Sick Leave" must still demand the certificate, and
    // one that does not require a document for medical leave must not be asked.
    const sick = { ...medical, name: 'Sick Leave' }
    expect(leaveRequestBlocker({ ...ok, type: sick })).toMatch(/Sick Leave needs/)
    const medicalNoDoc = { ...medical, requires_document: false }
    expect(leaveRequestBlocker({ ...ok, type: medicalNoDoc })).toBeNull()
  })
})

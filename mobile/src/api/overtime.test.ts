/**
 * Asking for overtime on the phone (docs/plans/2026-10-25-roster-autoclose-overtime-design.md, section 3).
 */
import { askOvertime, canAskOvertime, getMyOvertimeRequests, overtimeStatusLine, stepHours, type OvertimeRequest } from './overtime'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: {
    post: jest.fn(() => Promise.resolve({ data: { status: 'pending' } })),
    get: jest.fn(() => Promise.resolve({ data: [] })),
  },
}))

const mockPost = apiClient.post as jest.Mock
const mockGet = apiClient.get as jest.Mock
beforeEach(() => { mockPost.mockClear(); mockGet.mockClear() })

const NOW = new Date('2026-10-25T12:00:00Z')

test('a request carries the shift, the hours and the reason', async () => {
  await askOvertime('shift-1', 2.5, 'Relief late')
  expect(mockPost).toHaveBeenCalledWith('/api/v1/overtime-requests', { shift_id: 'shift-1', hours: 2.5, reason: 'Relief late' })
  await getMyOvertimeRequests('shift-1')
  expect(mockGet).toHaveBeenCalledWith('/api/v1/overtime-requests', { params: { shift_id: 'shift-1' } })
})

test('overtime can be asked while on duty, or up to 48 hours after the shift ended', () => {
  expect(canAskOvertime({ status: 'active', actual_end: null }, NOW)).toBe(true)
  expect(canAskOvertime({ status: 'completed', actual_end: '2026-10-24T13:00:00Z' }, NOW)).toBe(true)
  expect(canAskOvertime({ status: 'completed', actual_end: '2026-10-23T11:00:00Z' }, NOW)).toBe(false)
  expect(canAskOvertime({ status: 'scheduled', actual_end: null }, NOW)).toBe(false)
})

test('the hours go in half hours, from half an hour to twelve', () => {
  expect(stepHours(1, 0.5)).toBe(1.5)
  expect(stepHours(0.5, -0.5)).toBe(0.5)
  expect(stepHours(12, 0.5)).toBe(12)
})

const request = (status: OvertimeRequest['status'], note: string | null = null): OvertimeRequest => ({
  id: 'r', shift_id: 's', hours_requested: 2, reason: 'x', status, decision_note: note, decided_by_name: null,
  created_at: '2026-10-25T10:00:00Z',
})

test('the guard is told what became of the last request', () => {
  expect(overtimeStatusLine([])).toBeNull()
  expect(overtimeStatusLine([request('pending')])).toContain('waiting for your supervisor')
  expect(overtimeStatusLine([request('approved')])).toContain('approved')
  expect(overtimeStatusLine([request('rejected', 'Relief arrived')])).toContain('not approved: Relief arrived')
})

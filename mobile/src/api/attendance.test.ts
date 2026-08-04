/**
 * Live attendance client.
 *
 * Thin, but the site filter is load-bearing: a supervisor covering one site
 * needs that site's board, and silently sending the whole tenant's would bury
 * the two people they actually need to chase.
 */
import { getLiveAttendance } from './attendance'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: { get: jest.fn(() => Promise.resolve({ data: { shifts: [], summary: {} } })) },
}))

const mockGet = apiClient.get as jest.Mock

beforeEach(() => {
  mockGet.mockClear().mockResolvedValue({ data: { shifts: [], summary: {} } })
})

test('fetches the live board', async () => {
  await getLiveAttendance()
  expect(mockGet.mock.calls[0][0]).toBe('/api/v1/attendance/live')
})

test('omits params entirely when no site filter is given', async () => {
  await getLiveAttendance()
  expect(mockGet.mock.calls[0][1].params).toBeUndefined()
})

test('passes site_id through when filtering to one site', async () => {
  await getLiveAttendance('site-3')
  expect(mockGet.mock.calls[0][1].params).toEqual({ site_id: 'site-3' })
})

test('unwraps shifts and summary from the response envelope', async () => {
  mockGet.mockResolvedValueOnce({
    data: {
      shifts: [{ id: 's1', guard_name: 'A Tan', live_status: 'late' }],
      summary: { checked_in: 2, on_break: 0, late: 1, not_started: 1, checked_out: 3 },
    },
  })
  const r = await getLiveAttendance()
  expect(r.shifts).toHaveLength(1)
  expect(r.shifts[0].guard_name).toBe('A Tan')
  expect(r.summary.late).toBe(1)
})

test('an empty board is a valid response, not an error', async () => {
  mockGet.mockResolvedValueOnce({
    data: { shifts: [], summary: { checked_in: 0, on_break: 0, late: 0, not_started: 0, checked_out: 0 } },
  })
  await expect(getLiveAttendance()).resolves.toMatchObject({ shifts: [] })
})

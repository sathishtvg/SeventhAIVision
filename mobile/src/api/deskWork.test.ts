/**
 * The four modules that bring the newer work to the phone: what goes on the wire.
 *
 * WHY THIS MATTERS
 *   The server's bodies forbid anything they have no field for, so a request
 *   with a stray key is refused whole — and somebody who has just fixed a gate
 *   motor would be told it was not recorded. And each list asks only for what
 *   is this person's: their own reading, their own orders, their own cases.
 */
import { getBriefing, listPublishedBriefings } from './briefings'
import { finishTask, getCase, listMyCases } from './cases'
import { apiClient } from './client'
import { completeOrder, listMyOrders, startOrder } from './maintenance'
import { READING_DAYS, getMyReading } from './workforce'

jest.mock('./client', () => ({
  apiClient: {
    get: jest.fn(() => Promise.resolve({ data: { items: [{ id: 'x1' }] } })),
    post: jest.fn(() => Promise.resolve({ data: { id: 'x1' } })),
  },
}))

const mockGet = apiClient.get as jest.Mock
const mockPost = apiClient.post as jest.Mock

beforeEach(() => { mockGet.mockClear(); mockPost.mockClear() })

test('a reading is asked for as one\'s own, over one of the server\'s periods', async () => {
  await getMyReading(28)
  expect(mockGet).toHaveBeenCalledWith('/api/v1/workforce/me', { params: { days: 28 } })
  expect([...READING_DAYS]).toEqual([7, 28, 90])
})

test('the phone reads published briefings, and one of them whole', async () => {
  await expect(listPublishedBriefings()).resolves.toEqual([{ id: 'x1' }])
  expect(mockGet).toHaveBeenLastCalledWith('/api/v1/daily-briefings', { params: { state: 'PUBLISHED' } })
  await getBriefing('b1')
  expect(mockGet).toHaveBeenLastCalledWith('/api/v1/daily-briefings/b1')
  expect(mockPost).not.toHaveBeenCalled()
})

test('work orders are asked for as mine, and each step goes to its own route', async () => {
  await expect(listMyOrders()).resolves.toEqual([{ id: 'x1' }])
  expect(mockGet).toHaveBeenLastCalledWith('/api/v1/maintenance/work-orders', { params: { mine: true } })
  await startOrder('w1')
  expect(mockPost).toHaveBeenLastCalledWith('/api/v1/maintenance/work-orders/w1/start')
})

test('what was done is sent in words, with parts and time out of use only when there is something to say', async () => {
  await completeOrder('w1', '  Power supply replaced.  ')
  expect(mockPost).toHaveBeenLastCalledWith('/api/v1/maintenance/work-orders/w1/complete', { completion_note: 'Power supply replaced.' })
  await completeOrder('w1', 'Replaced.', '   ', null)
  expect(mockPost).toHaveBeenLastCalledWith('/api/v1/maintenance/work-orders/w1/complete', { completion_note: 'Replaced.' })
  await completeOrder('w1', 'Replaced.', ' 12 V supply ', 310)
  expect(mockPost).toHaveBeenLastCalledWith('/api/v1/maintenance/work-orders/w1/complete', {
    completion_note: 'Replaced.', parts_used: '12 V supply', downtime_minutes: 310 })
  await completeOrder('w1', 'Checked.', null, 0)
  expect(mockPost).toHaveBeenLastCalledWith('/api/v1/maintenance/work-orders/w1/complete', { completion_note: 'Checked.', downtime_minutes: 0 })
})

test('cases are asked for as mine, and a task is finished or dropped on its own route with a note and nothing else', async () => {
  await expect(listMyCases()).resolves.toEqual([{ id: 'x1' }])
  expect(mockGet).toHaveBeenLastCalledWith('/api/v1/cases', { params: { mine: true } })
  await getCase('c1')
  expect(mockGet).toHaveBeenLastCalledWith('/api/v1/cases/c1')
  await finishTask('c1', 't1', 'done', '  Register received. ')
  expect(mockPost).toHaveBeenLastCalledWith('/api/v1/cases/c1/tasks/t1/done', { note: 'Register received.' })
  await finishTask('c1', 't1', 'drop', 'The client declined.')
  expect(mockPost).toHaveBeenLastCalledWith('/api/v1/cases/c1/tasks/t1/drop', { note: 'The client declined.' })
})

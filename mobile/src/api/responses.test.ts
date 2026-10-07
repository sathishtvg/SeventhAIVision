/**
 * A guard's steps on a dispatch: what goes on the wire.
 *
 * WHY THIS MATTERS
 *   The server's bodies forbid anything they have no field for, so a step sent
 *   with a stray key is refused whole — and a guard standing at a forced gate
 *   would be told "not recorded" for having arrived. A decline is `reason`, a
 *   report is `note`; a position is both of latitude and longitude or neither.
 */
import { acceptResponse, arrivedAt, declineResponse, getMySendings, reportFromGround, setOff } from './responses'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: {
    get: jest.fn(() => Promise.resolve({ data: { items: [], as_of: '2026-10-07T03:00:00Z' } })),
    post: jest.fn(() => Promise.resolve({ data: { response: { state: 'ACCEPTED' } } })),
  },
}))

const mockGet = apiClient.get as jest.Mock
const mockPost = apiClient.post as jest.Mock
const HERE = { latitude: 1.3, longitude: 103.8 }

beforeEach(() => { mockGet.mockClear(); mockPost.mockClear() })

test('asks for what the caller has been sent on', async () => {
  await expect(getMySendings()).resolves.toEqual({ items: [], as_of: '2026-10-07T03:00:00Z' })
  expect(mockGet).toHaveBeenCalledWith('/api/v1/incident-responses/mine')
})

test('each step goes to its own route, for the incident', async () => {
  await acceptResponse('i1')
  await setOff('i1')
  await arrivedAt('i1')
  await reportFromGround('i1', 'Padlock cut')
  await declineResponse('i1', 'Holding a visitor')
  expect(mockPost.mock.calls.map((c) => c[0])).toEqual([
    '/api/v1/incident-responses/i1/accept', '/api/v1/incident-responses/i1/en-route',
    '/api/v1/incident-responses/i1/arrived', '/api/v1/incident-responses/i1/report',
    '/api/v1/incident-responses/i1/decline'])
})

test('a step with no position sends no half of one, and nothing the server has no field for', async () => {
  await acceptResponse('i1')
  await setOff('i1')
  await arrivedAt('i1')
  for (const call of mockPost.mock.calls) expect(call[1]).toEqual({})
})

test('a step says where it was said from when the phone will say', async () => {
  await arrivedAt('i1', HERE)
  expect(mockPost.mock.calls[0][1]).toEqual({ latitude: 1.3, longitude: 103.8 })
})

test('not coming is sent as a reason, and a report as a note', async () => {
  await declineResponse('i1', 'Holding a visitor', HERE)
  await reportFromGround('i1', 'Padlock cut', HERE)
  await reportFromGround('i1', 'Nobody on site')
  expect(mockPost.mock.calls[0][1]).toEqual({ reason: 'Holding a visitor', latitude: 1.3, longitude: 103.8 })
  expect(mockPost.mock.calls[1][1]).toEqual({ note: 'Padlock cut', latitude: 1.3, longitude: 103.8 })
  expect(mockPost.mock.calls[2][1]).toEqual({ note: 'Nobody on site' })
  expect(mockPost.mock.calls[0][1]).not.toHaveProperty('note')
})

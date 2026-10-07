/**
 * Visitor authorisation: what goes on the wire.
 *
 * WHY THIS MATTERS
 *   The server's bodies forbid anything they have no field for. A no is sent
 *   as `reason` and the document that was seen as `kind`; under any other name
 *   the request is refused whole, and a host standing in a corridor is told
 *   their answer could not be recorded. And nothing here may ever carry the
 *   number on a visitor's ID.
 */
import { approve, askForVisit, decline, getStanding, getWaitingForMe, recordIdSeen } from './visitorAuth'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: { get: jest.fn(), post: jest.fn(() => Promise.resolve({ data: { id: 'a1' } })) },
}))

const mockGet = apiClient.get as jest.Mock
const mockPost = apiClient.post as jest.Mock
const BASE = '/api/v1/visitor-authorizations'

beforeEach(() => { mockGet.mockReset(); mockPost.mockClear() })

test('asks for what waits for this person, and gives the list', async () => {
  mockGet.mockResolvedValue({ data: { items: [{ id: 'a1' }] } })
  await expect(getWaitingForMe()).resolves.toEqual([{ id: 'a1' }])
  expect(mockGet).toHaveBeenCalledWith(`${BASE}/mine`)
})

test('asks what stands for one visit, by the visit', async () => {
  const stands = { standing: 'NOT_ASKED', says: ['No authorisation has been asked for.'], authorization: null, note: 'n' }
  mockGet.mockResolvedValue({ data: stands })
  await expect(getStanding('v1')).resolves.toEqual(stands)
  expect(mockGet).toHaveBeenCalledWith(`${BASE}/standing`, { params: { visitor_id: 'v1' } })
})

test('asks the host with the visit and nothing else: the host and the period are the visit\'s own', async () => {
  await askForVisit('v1')
  expect(mockPost).toHaveBeenCalledWith(BASE, { visitor_id: 'v1' })
})

test('says yes with an empty body, and no under the field the server reads', async () => {
  await approve('a1')
  await decline('a1', 'Not expected today.')
  expect(mockPost.mock.calls[0]).toEqual([`${BASE}/a1/approve`, {}])
  expect(mockPost.mock.calls[1]).toEqual([`${BASE}/a1/decline`, { reason: 'Not expected today.' }])
})

test('records the kind of document that was seen, and only the kind', async () => {
  await recordIdSeen('a1', 'Work pass')
  expect(mockPost).toHaveBeenCalledWith(`${BASE}/a1/id-seen`, { kind: 'Work pass' })
  expect(Object.keys(mockPost.mock.calls[0][1])).toEqual(['kind'])
})

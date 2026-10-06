/**
 * Dispatching a guard: what goes on the wire.
 *
 * WHY THIS MATTERS
 *   The server's body for a dispatch is a guard and one text of notes. The
 *   phone sent an empty guard, `eta_minutes` and `notes` — so the server could
 *   not store the dispatch at all, and had it been able to, every instruction
 *   typed for the guard would have been dropped, because `notes` is not a
 *   field it reads. Nothing said so on either side.
 */
import { dispatchToIncident, markArrived } from './dispatch'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: { post: jest.fn(() => Promise.resolve({ data: { id: 'i1', status: 'in_progress' } })) },
}))

const mockPost = apiClient.post as jest.Mock

beforeEach(() => mockPost.mockClear())

test('names the guard and sends the notes under the field the server reads', async () => {
  await dispatchToIncident('i1', { guard_user_id: 'g7', dispatch_notes: 'ETA 5 min. Use the north gate.' })
  expect(mockPost).toHaveBeenCalledTimes(1)
  expect(mockPost.mock.calls[0][0]).toBe('/api/v1/dispatch/incidents/i1')
  expect(mockPost.mock.calls[0][1]).toEqual({ guard_user_id: 'g7', dispatch_notes: 'ETA 5 min. Use the north gate.' })
})

test('sends nothing the server has no field for', async () => {
  await dispatchToIncident('i1', { guard_user_id: 'g7' })
  const body = mockPost.mock.calls[0][1] as Record<string, unknown>
  expect(Object.keys(body)).toEqual(['guard_user_id'])
  expect(body).not.toHaveProperty('eta_minutes')
  expect(body).not.toHaveProperty('notes')
})

test('marks the guard as arrived at the incident', async () => {
  await markArrived('i1')
  expect(mockPost.mock.calls[0][0]).toBe('/api/v1/dispatch/incidents/i1/arrived')
})

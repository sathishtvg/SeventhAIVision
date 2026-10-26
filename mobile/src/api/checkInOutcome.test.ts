/**
 * A check-in the server saved but the phone was not told about is reported as saved, not failed (owner's phone test,
 * 26 October 2026).
 */
import { getShift, savedMessage, sendStep, stepWentThrough, worthRechecking } from './checkInOutcome'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: { post: jest.fn(), get: jest.fn() },
}))

const mockPost = apiClient.post as jest.Mock
const mockGet = apiClient.get as jest.Mock
beforeEach(() => { mockPost.mockReset(); mockGet.mockReset() })

const dropped = Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' })
const refused = (status: number, detail = 'x') => Object.assign(new Error(`status ${status}`), { response: { status, data: { detail } } })

test('which failures leave it unknown whether the step was saved', () => {
  expect(worthRechecking(dropped)).toBe(true)
  expect(worthRechecking(Object.assign(new Error('timeout'), { code: 'ECONNABORTED' }))).toBe(true)
  expect(worthRechecking(refused(404))).toBe(true)
  expect(worthRechecking(refused(500))).toBe(true)
  expect(worthRechecking(refused(422))).toBe(false)
  expect(worthRechecking(refused(403))).toBe(false)
  expect(worthRechecking(refused(503))).toBe(false)
})

test('what the shift must show for each step to have gone through', () => {
  expect(stepWentThrough('start', { status: 'active' })).toBe(true)
  expect(stepWentThrough('start', { status: 'scheduled' })).toBe(false)
  expect(stepWentThrough('end', { status: 'completed', auto_closed_at: null })).toBe(true)
  expect(stepWentThrough('end', { status: 'completed', auto_closed_at: '2026-10-26T10:00:00Z' })).toBe(false)
  expect(stepWentThrough('end', { status: 'active' })).toBe(false)
  expect(stepWentThrough('start', null)).toBe(false)
})

test('a check-in whose answer was lost, but which was saved, comes back as saved', async () => {
  mockPost.mockRejectedValueOnce(dropped)
  mockGet.mockResolvedValueOnce({ data: { id: 's1', status: 'active' } })
  await expect(sendStep('start', 's1', 'file:///selfie.jpg')).resolves.toEqual({ recovered: true })
  expect(mockGet).toHaveBeenCalledWith('/api/v1/shifts/s1')
})

test('a retry the server answers "already done" is reported as saved', async () => {
  mockPost.mockResolvedValueOnce({ data: { id: 's1', status: 'active', already_done: true } })
  await expect(sendStep('start', 's1', 'file:///selfie.jpg')).resolves.toEqual({ recovered: true })
  mockPost.mockResolvedValueOnce({ data: { id: 's1', status: 'active' } })
  await expect(sendStep('start', 's1', 'file:///selfie.jpg')).resolves.toEqual({ recovered: false })
  expect(mockGet).not.toHaveBeenCalled()
})

test('a check-in that was not saved still fails with its own error', async () => {
  mockPost.mockRejectedValueOnce(dropped)
  mockGet.mockResolvedValueOnce({ data: { id: 's1', status: 'scheduled' } })
  await expect(sendStep('start', 's1', 'file:///selfie.jpg')).rejects.toBe(dropped)

  mockPost.mockRejectedValueOnce(dropped)
  mockGet.mockRejectedValueOnce(dropped)
  await expect(sendStep('end', 's1', 'file:///selfie.jpg')).rejects.toBe(dropped)

  const liveness = refused(422, 'Liveness check failed')
  mockPost.mockRejectedValueOnce(liveness)
  await expect(sendStep('start', 's1', 'file:///selfie.jpg')).rejects.toBe(liveness)
  expect(mockGet).toHaveBeenCalledTimes(2)
})

test('the guard is told the step was saved and not to try again', async () => {
  expect(savedMessage('start').title).toBe("You're checked in")
  expect(savedMessage('end').body).toMatch(/check-out was saved.*no need to try again/)
  mockGet.mockResolvedValueOnce({ data: { id: 's2', status: 'completed' } })
  await expect(getShift('s2')).resolves.toMatchObject({ status: 'completed' })
})

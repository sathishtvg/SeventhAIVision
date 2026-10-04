/**
 * Drone event calls, on the wire.
 *
 * WHY THIS MATTERS
 *   These are the calls an officer makes standing in a car park with one bar of
 *   signal: acknowledge, escalate, send a guard. A wrong path is a button that
 *   does nothing; a wrong body is a 422 the officer cannot read. And the list
 *   endpoint is paginated — the same shape that once left the Alerts tab blank
 *   for every user (see rows() in client.ts).
 */
import {
  decideDroneEvent, dispatchDroneGuard, droneApiError, droneMediaHeaders, droneMediaUrl,
  findDroneEventForAlert, getDroneEventGuards, getDroneEvents, openDroneIncident,
} from './drones'
import { apiClient } from './client'

jest.mock('./client', () => {
  const actual = jest.requireActual('./client')
  return {
    ...actual,
    apiClient: {
      get: jest.fn(() => Promise.resolve({ data: { items: [] } })),
      post: jest.fn(() => Promise.resolve({ data: {} })),
      defaults: { baseURL: 'http://10.0.0.5:8000/', headers: { common: { Authorization: 'Bearer abc' } } },
    },
  }
})

const get = apiClient.get as jest.Mock
const post = apiClient.post as jest.Mock

beforeEach(() => {
  get.mockClear()
  post.mockClear()
})

// ── Reading ───────────────────────────────────────────────────────────────

test('asks for open events only when told to, and unwraps the paginated list', async () => {
  get.mockResolvedValueOnce({ data: { items: [{ id: 'e1' }], total: 1, has_more: false } })
  const events = await getDroneEvents({ openOnly: true })
  expect(get.mock.calls[0][0]).toBe('/api/v1/drone-events')
  expect(get.mock.calls[0][1].params).toMatchObject({ open_only: true })
  expect(events).toEqual([{ id: 'e1' }])
})

test('does not send open_only for the full list', async () => {
  await getDroneEvents()
  expect(get.mock.calls[0][1].params).not.toHaveProperty('open_only')
})

test('finds the event behind an alert by alert id', async () => {
  get.mockResolvedValueOnce({ data: { items: [{ id: 'e9' }] } })
  const event = await findDroneEventForAlert('a1')
  expect(get.mock.calls[0][1].params).toMatchObject({ alert_id: 'a1', limit: 1 })
  expect(event).toEqual({ id: 'e9' })
})

test('answers null when the alert was not a drone event', async () => {
  expect(await findDroneEventForAlert('a2')).toBeNull()
})

test('reads the guards for an event', async () => {
  get.mockResolvedValueOnce({ data: [{ user_id: 'g1' }] })
  expect(await getDroneEventGuards('e1')).toEqual([{ user_id: 'g1' }])
  expect(get.mock.calls[0][0]).toBe('/api/v1/drone-events/e1/guards')
})

// ── Deciding ──────────────────────────────────────────────────────────────

test('acknowledges with no note', async () => {
  await decideDroneEvent('e1', 'acknowledge')
  expect(post.mock.calls[0]).toEqual(['/api/v1/drone-events/e1/acknowledge', { note: null }])
})

test('escalates with the note, trimmed', async () => {
  await decideDroneEvent('e1', 'escalate', '  two people at the fuel store ')
  expect(post.mock.calls[0]).toEqual(['/api/v1/drone-events/e1/escalate', { note: 'two people at the fuel store' }])
})

test('sends a false positive as a reason, not a note', async () => {
  // The server's body for this one endpoint is {reason}, and it is required.
  await decideDroneEvent('e1', 'false-positive', 'A guard on his rounds')
  expect(post.mock.calls[0]).toEqual(['/api/v1/drone-events/e1/false-positive', { reason: 'A guard on his rounds' }])
})

test('refuses a false positive without a reason before calling the server', async () => {
  await expect(decideDroneEvent('e1', 'false-positive', '  ')).rejects.toThrow('Say why')
  expect(post).not.toHaveBeenCalled()
})

// ── Responding ────────────────────────────────────────────────────────────

test('opens the incident on the event', async () => {
  await openDroneIncident('e1')
  expect(post.mock.calls[0]).toEqual(['/api/v1/drone-events/e1/incident', { reason: null }])
})

test('dispatches the nearest free guard when none is named', async () => {
  // guard_user_id null is how the server is asked to choose; an empty string
  // would be a 422 on a UUID field.
  await dispatchDroneGuard('e1')
  expect(post.mock.calls[0]).toEqual(['/api/v1/drone-events/e1/dispatch', { guard_user_id: null, notes: null }])
})

test('dispatches the guard named, with notes', async () => {
  await dispatchDroneGuard('e1', 'g1', 'North gate, approach from the road')
  expect(post.mock.calls[0][1]).toEqual({ guard_user_id: 'g1', notes: 'North gate, approach from the road' })
})

// ── Media ─────────────────────────────────────────────────────────────────

test('builds media URLs against the server this phone is signed in to', () => {
  // Not a build-time constant: the server URL is set at sign-in.
  expect(droneMediaUrl('m1')).toBe('http://10.0.0.5:8000/api/v1/drone-media/m1/file')
})

test('passes the session token as a header, never in the URL', () => {
  expect(droneMediaHeaders()).toEqual({ Authorization: 'Bearer abc' })
  expect(droneMediaUrl('m1')).not.toContain('token')
})

// ── Errors ────────────────────────────────────────────────────────────────

test("shows the server's own reason", () => {
  expect(droneApiError({ response: { data: { detail: 'No guard on shift at this site is free.' } } }))
    .toBe('No guard on shift at this site is free.')
  expect(droneApiError({ response: { data: { detail: [{ msg: 'field required' }] } } })).toBe('field required')
  expect(droneApiError(new Error('Network Error'))).toBe('Network Error')
})

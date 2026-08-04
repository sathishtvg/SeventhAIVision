/**
 * Shift check-in/out request construction.
 *
 * WHY THIS MATTERS
 *   Three anti-fraud controls ride on these two calls: the selfie (proves a
 *   person, checked for liveness server-side), the GPS fix (geofence), and the
 *   `is_mock_location` flag (fake-GPS rejection, a hard 403 on the server).
 *
 *   The flag is the fragile one. It travels as a QUERY PARAM while the photo
 *   travels as the multipart BODY — an easy thing to break when someone
 *   refactors the body construction, and if it silently stopped being sent,
 *   FastAPI would default it to False and every spoofed check-in would sail
 *   through. The server re-checks it, but only if it arrives.
 *
 *   These tests assert the wire format, not the UI. The UI-level pre-check
 *   lives in the check-in screen; this is the layer that must not lose data.
 */
import { endShift, startShift } from './patrols'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: { post: jest.fn(() => Promise.resolve({ data: { ok: true } })) },
}))

const mockPost = apiClient.post as jest.Mock

beforeEach(() => mockPost.mockClear())

/** Pull the FormData parts back out — RN's FormData exposes _parts. */
function partsOf(form: FormData): Array<[string, unknown]> {
  return (form as unknown as { _parts: Array<[string, unknown]> })._parts ?? []
}

// ── startShift ────────────────────────────────────────────────────────────

test('posts to the shift start endpoint', async () => {
  await startShift('shift-1', 'file:///tmp/selfie.jpg', false, 1.35, 103.8)
  expect(mockPost).toHaveBeenCalledTimes(1)
  expect(mockPost.mock.calls[0][0]).toBe('/api/v1/shifts/shift-1/start')
})

test('sends the selfie as a multipart photo field', async () => {
  await startShift('shift-1', 'file:///tmp/selfie.jpg', false, 1.35, 103.8)
  const [, body, config] = mockPost.mock.calls[0]
  expect(config.headers['Content-Type']).toBe('multipart/form-data')
  const photo = partsOf(body).find(([k]) => k === 'photo')
  expect(photo).toBeDefined()
  expect(photo![1]).toMatchObject({ uri: 'file:///tmp/selfie.jpg', type: 'image/jpeg' })
})

test('carries coordinates as query params for the geofence check', async () => {
  await startShift('shift-1', 'file:///p.jpg', false, 1.3521, 103.8198)
  expect(mockPost.mock.calls[0][2].params).toMatchObject({
    latitude: 1.3521,
    longitude: 103.8198,
  })
})

test('is_mock_location=true reaches the server so it can reject the check-in', async () => {
  // The single most important assertion in this file. Losing this flag turns
  // a hard 403 into a silently accepted spoofed check-in.
  await startShift('shift-1', 'file:///p.jpg', true, 1.35, 103.8)
  expect(mockPost.mock.calls[0][2].params.is_mock_location).toBe(true)
})

test('is_mock_location=false is sent explicitly, not omitted', async () => {
  // Must be present-and-false rather than absent: an omitted param and a
  // false param are indistinguishable to the server, so an accidental
  // omission would look identical to a clean check-in.
  await startShift('shift-1', 'file:///p.jpg', false, 1.35, 103.8)
  const params = mockPost.mock.calls[0][2].params
  expect('is_mock_location' in params).toBe(true)
  expect(params.is_mock_location).toBe(false)
})

test('missing coordinates still check in — GPS failure must not block a guard', async () => {
  // Permission denied or GPS off is not the guard's fault; the server records
  // is_within_geofence as NULL rather than refusing the shift.
  await startShift('shift-1', 'file:///p.jpg', false, undefined, undefined)
  const params = mockPost.mock.calls[0][2].params
  expect(params.latitude).toBeUndefined()
  expect(params.longitude).toBeUndefined()
  expect(params.is_mock_location).toBe(false)
})

// ── endShift ──────────────────────────────────────────────────────────────

test('endShift posts to the end endpoint with the same envelope', async () => {
  await endShift('shift-9', 'file:///out.jpg', false, 1.30, 103.85)
  const [url, body, config] = mockPost.mock.calls[0]
  expect(url).toBe('/api/v1/shifts/shift-9/end')
  expect(config.headers['Content-Type']).toBe('multipart/form-data')
  expect(partsOf(body).some(([k]) => k === 'photo')).toBe(true)
  expect(config.params).toMatchObject({ latitude: 1.30, longitude: 103.85 })
})

test('endShift also propagates the mock-location flag', async () => {
  // Check-OUT is spoofable too: faking location at the end hides an early
  // departure just as effectively as faking it at the start hides a late start.
  await endShift('shift-9', 'file:///out.jpg', true, 1.30, 103.85)
  expect(mockPost.mock.calls[0][2].params.is_mock_location).toBe(true)
})

test('each call builds a fresh FormData — no cross-request leakage', async () => {
  await startShift('shift-1', 'file:///a.jpg', false, 1, 2)
  await endShift('shift-1', 'file:///b.jpg', false, 1, 2)
  const first = partsOf(mockPost.mock.calls[0][1])
  const second = partsOf(mockPost.mock.calls[1][1])
  expect(first).toHaveLength(1)
  expect(second).toHaveLength(1)
  expect((first[0][1] as { uri: string }).uri).toBe('file:///a.jpg')
  expect((second[0][1] as { uri: string }).uri).toBe('file:///b.jpg')
})

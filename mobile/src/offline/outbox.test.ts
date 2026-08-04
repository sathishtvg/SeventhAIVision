/**
 * Offline mutation outbox (Gap 88).
 *
 * WHY THIS FILE EXISTS
 *   A guard scans a checkpoint in a basement with no signal. That scan is the
 *   evidence they were physically there — it feeds patrol compliance and, via
 *   the violations engine, their disciplinary record. If the outbox loses it,
 *   a guard who did their job is recorded as having missed it. If the outbox
 *   jams, every later scan is lost too.
 *
 *   The replay rules encode which failures are safe to discard and which must
 *   be retried, and that distinction is the whole safety property. Getting
 *   "server rejected this" confused with "the tunnel has no bars" loses real
 *   work in one direction and blocks the queue forever in the other. These
 *   tests pin every branch.
 *
 * Sections:
 *   A — enqueue + persistence (4)
 *   B — replay: what gets sent, dropped, retained (7)
 *   C — network-vs-server error classification (5)
 *   D — concurrency + subscribers (3)
 */
import AsyncStorage from '@react-native-async-storage/async-storage'

import {
  enqueueRequest,
  flushOutbox,
  getOutboxCount,
  isNetworkError,
  subscribeOutbox,
} from './outbox'
import { apiClient } from '@/api/client'

jest.mock('@/api/client', () => ({
  apiClient: { post: jest.fn() },
}))

const mockPost = apiClient.post as jest.Mock

/** An axios-shaped error carrying an HTTP status (server answered). */
function httpError(status: number) {
  return { response: { status }, request: {}, message: `Request failed with status ${status}` }
}

/** An axios-shaped error with a request but no response (never reached server). */
function networkError() {
  return { request: {}, message: 'Network Error' }
}

beforeEach(async () => {
  await AsyncStorage.clear()
  mockPost.mockReset()
})

// ── A. Enqueue + persistence ──────────────────────────────────────────────

test('enqueued item is persisted and counted', async () => {
  await enqueueRequest('/api/v1/patrols/scan', { checkpoint_id: 'cp1' }, 'Checkpoint — Lobby')
  expect(await getOutboxCount()).toBe(1)
})

test('enqueue stamps createdAt so the server gets the real event time', async () => {
  const before = Date.now()
  const item = await enqueueRequest('/api/v1/patrols/scan', { checkpoint_id: 'cp1' }, 'Lobby')
  const stamped = new Date(item.createdAt).getTime()
  expect(stamped).toBeGreaterThanOrEqual(before - 1000)
  expect(stamped).toBeLessThanOrEqual(Date.now() + 1000)
})

test('items keep FIFO order', async () => {
  await enqueueRequest('/u/1', {}, 'first')
  await enqueueRequest('/u/2', {}, 'second')
  await enqueueRequest('/u/3', {}, 'third')
  mockPost.mockResolvedValue({ data: {} })
  await flushOutbox()
  expect(mockPost.mock.calls.map((c) => c[0])).toEqual(['/u/1', '/u/2', '/u/3'])
})

test('queue is capped so a long outage cannot exhaust device storage', async () => {
  for (let i = 0; i < 205; i += 1) {
    await enqueueRequest(`/u/${i}`, {}, `scan ${i}`)
  }
  // Cap is 200; the oldest are shed, not the newest — a guard's most recent
  // work is the most likely to still be actionable.
  expect(await getOutboxCount()).toBe(200)
  mockPost.mockResolvedValue({ data: {} })
  await flushOutbox()
  expect(mockPost.mock.calls[0][0]).toBe('/u/5')
})

// ── B. Replay semantics ───────────────────────────────────────────────────

test('successful replay removes the item', async () => {
  await enqueueRequest('/u/1', {}, 'scan')
  mockPost.mockResolvedValue({ data: {} })
  const r = await flushOutbox()
  expect(r).toMatchObject({ sent: 1, dropped: 0, remaining: 0 })
  expect(await getOutboxCount()).toBe(0)
})

test('a 4xx rejection is dropped permanently rather than jamming the queue', async () => {
  // e.g. the scan fell outside the server's 48h sync window. It will never
  // succeed, so retrying it forever would block every item behind it.
  await enqueueRequest('/u/1', {}, 'stale scan')
  await enqueueRequest('/u/2', {}, 'good scan')
  mockPost.mockRejectedValueOnce(httpError(422)).mockResolvedValueOnce({ data: {} })
  const r = await flushOutbox()
  expect(r).toMatchObject({ sent: 1, dropped: 1, remaining: 0 })
})

test('a network error retains the item and stops the flush', async () => {
  await enqueueRequest('/u/1', {}, 'scan 1')
  await enqueueRequest('/u/2', {}, 'scan 2')
  mockPost.mockRejectedValue(networkError())
  const r = await flushOutbox()
  expect(r).toMatchObject({ sent: 0, dropped: 0, remaining: 2 })
  // Stops after the first failure — no point hammering a dead connection,
  // and order must be preserved for the next attempt.
  expect(mockPost).toHaveBeenCalledTimes(1)
})

test.each([
  ['5xx server error', 500],
  ['401 token expired mid-outage', 401],
  ['408 request timeout', 408],
  ['429 rate limited', 429],
])('%s is retried, not discarded', async (_label, status) => {
  await enqueueRequest('/u/1', {}, 'scan')
  mockPost.mockRejectedValue(httpError(status))
  const r = await flushOutbox()
  expect(r).toMatchObject({ sent: 0, dropped: 0, remaining: 1 })
  expect(await getOutboxCount()).toBe(1)
})

test('a retained item survives to the next flush and then sends', async () => {
  await enqueueRequest('/u/1', {}, 'scan')
  mockPost.mockRejectedValueOnce(networkError())
  await flushOutbox()
  expect(await getOutboxCount()).toBe(1)

  mockPost.mockResolvedValueOnce({ data: {} })
  const r = await flushOutbox()
  expect(r.sent).toBe(1)
  expect(await getOutboxCount()).toBe(0)
})

test('flushing an empty queue is a no-op, not an error', async () => {
  const r = await flushOutbox()
  expect(r).toMatchObject({ sent: 0, dropped: 0, remaining: 0 })
  expect(mockPost).not.toHaveBeenCalled()
})

test('a permanent drop does not stop the items behind it', async () => {
  await enqueueRequest('/u/1', {}, 'bad')
  await enqueueRequest('/u/2', {}, 'bad too')
  await enqueueRequest('/u/3', {}, 'good')
  mockPost
    .mockRejectedValueOnce(httpError(400))
    .mockRejectedValueOnce(httpError(403))
    .mockResolvedValueOnce({ data: {} })
  const r = await flushOutbox()
  expect(r).toMatchObject({ sent: 1, dropped: 2, remaining: 0 })
})

// ── C. Error classification ───────────────────────────────────────────────

test('isNetworkError: request with no response is a network failure', () => {
  expect(isNetworkError(networkError())).toBe(true)
})

test('isNetworkError: a server response is NOT a network failure', () => {
  expect(isNetworkError(httpError(500))).toBe(false)
  expect(isNetworkError(httpError(422))).toBe(false)
})

test('isNetworkError: message-only "Network Error" still classifies', () => {
  expect(isNetworkError({ message: 'Network Error' })).toBe(true)
})

test('isNetworkError: unrelated thrown values are not network failures', () => {
  expect(isNetworkError(new Error('boom'))).toBe(false)
  expect(isNetworkError(null)).toBe(false)
  expect(isNetworkError(undefined)).toBe(false)
})

// ── D. Concurrency + subscribers ──────────────────────────────────────────

test('concurrent flushes do not double-send', async () => {
  // Two triggers can race: the connectivity listener firing while a manual
  // retry is already in flight. Sending a checkpoint scan twice would create
  // a duplicate patrol record.
  await enqueueRequest('/u/1', {}, 'scan')
  let resolvePost: (v: unknown) => void = () => {}
  mockPost.mockImplementation(() => new Promise((res) => { resolvePost = res }))

  const first = flushOutbox()
  const second = await flushOutbox()   // returns immediately, guard is set
  expect(second.sent).toBe(0)

  resolvePost({ data: {} })
  await first
  expect(mockPost).toHaveBeenCalledTimes(1)
})

test('subscribers are notified of the current count on subscribe', async () => {
  await enqueueRequest('/u/1', {}, 'scan')
  const seen: number[] = []
  const unsub = subscribeOutbox((n) => seen.push(n))
  await new Promise((r) => setTimeout(r, 0))
  expect(seen).toContain(1)
  unsub()
})

test('unsubscribed listeners stop receiving updates', async () => {
  const seen: number[] = []
  const unsub = subscribeOutbox((n) => seen.push(n))
  await new Promise((r) => setTimeout(r, 0))
  const countAfterSubscribe = seen.length
  unsub()
  await enqueueRequest('/u/1', {}, 'scan')
  expect(seen.length).toBe(countAfterSubscribe)
})

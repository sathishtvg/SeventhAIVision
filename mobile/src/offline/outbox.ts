/**
 * Offline mutation outbox (Gap 88).
 *
 * Guards scan checkpoints in signal-dead basements. Writes that fail with a
 * NETWORK error are queued here (AsyncStorage) carrying their original event
 * timestamp, and replayed FIFO when connectivity returns.
 *
 * Replay rules:
 *  - success            → remove from queue
 *  - 4xx (not 401/408/429) → drop permanently (server rejected it — e.g.
 *    outside the 48h sync window); keeping it would jam the queue forever
 *  - network error / 5xx / 401 / 408 / 429 → keep, stop this flush, retry later
 */
import AsyncStorage from '@react-native-async-storage/async-storage'
import { apiClient } from '@/api/client'

const STORAGE_KEY = 'seventh_ai_outbox_v1'
const MAX_ITEMS = 200

export interface OutboxItem {
  id: string
  url: string
  body: Record<string, unknown>
  label: string          // human description, e.g. "Checkpoint scan — Lobby"
  createdAt: string      // ISO — also the event time carried in the payload
  attempts: number
}

type Listener = (count: number) => void
const listeners = new Set<Listener>()
let flushing = false

async function readQueue(): Promise<OutboxItem[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as OutboxItem[]) : []
  } catch {
    return []
  }
}

async function writeQueue(items: OutboxItem[]): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(items))
  for (const cb of listeners) cb(items.length)
}

export function subscribeOutbox(cb: Listener): () => void {
  listeners.add(cb)
  void readQueue().then((q) => cb(q.length))
  return () => { listeners.delete(cb) }
}

export async function getOutboxCount(): Promise<number> {
  return (await readQueue()).length
}

/** True when an axios error means "no connectivity", not "server said no". */
export function isNetworkError(err: unknown): boolean {
  const e = err as { response?: unknown; request?: unknown; message?: string }
  return Boolean(e && !e.response && (e.request || /network/i.test(e.message ?? '')))
}

export async function enqueueRequest(
  url: string,
  body: Record<string, unknown>,
  label: string,
): Promise<OutboxItem> {
  const item: OutboxItem = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    url,
    body,
    label,
    createdAt: new Date().toISOString(),
    attempts: 0,
  }
  const queue = await readQueue()
  queue.push(item)
  await writeQueue(queue.slice(-MAX_ITEMS))
  return item
}

export interface FlushResult {
  sent: number
  dropped: number
  remaining: number
}

export async function flushOutbox(): Promise<FlushResult> {
  if (flushing) return { sent: 0, dropped: 0, remaining: await getOutboxCount() }
  flushing = true
  let sent = 0
  let dropped = 0
  try {
    let queue = await readQueue()
    while (queue.length > 0) {
      const item = queue[0]
      try {
        await apiClient.post(item.url, item.body)
        sent += 1
        queue = queue.slice(1)
        await writeQueue(queue)
      } catch (err) {
        const status = (err as { response?: { status?: number } }).response?.status
        const permanent = status !== undefined &&
          status >= 400 && status < 500 &&
          status !== 401 && status !== 408 && status !== 429
        if (permanent) {
          dropped += 1
          queue = queue.slice(1)
          await writeQueue(queue)
        } else {
          // Still offline (or transient server trouble) — retry next flush
          item.attempts += 1
          queue = [item, ...queue.slice(1)]
          await writeQueue(queue)
          break
        }
      }
    }
    return { sent, dropped, remaining: queue.length }
  } finally {
    flushing = false
  }
}

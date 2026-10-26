/**
 * The AI assistant (backend/app/routers/ai_assistant.py).
 *
 * The reply streams as server-sent events: `conversation` (its id), `status` (what it is doing), `delta` (words
 * as they are written), then `done` or `error`. Read with fetch rather than EventSource, which cannot send the
 * sign-in header.
 */
import { apiClient } from './client'
import { useAuthStore } from '@/store/auth'

export interface AssistantDone {
  conversation_id: string
  message_id: string
  text: string
  stop: 'end' | 'refusal' | 'rounds' | 'max_tokens'
  used: Array<{ tool: string; what: string }>
}

export type AssistantEvent =
  | { kind: 'conversation'; data: { conversation_id: string } }
  | { kind: 'status'; data: { text: string } }
  | { kind: 'delta'; data: { text: string } }
  | { kind: 'done'; data: AssistantDone }
  | { kind: 'error'; data: { message: string } }
  | { kind: 'confirm'; data: { action: AssistantAction } }

export interface ConversationSummary {
  id: string
  title: string
  channel: 'WEB' | 'PHONE'
  provider: 'LOCAL' | 'HOSTED'
  created_at: string
  updated_at: string
}

export interface ConversationMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  used: Array<{ tool: string; what: string }>
  stop: string | null
  created_at: string
}

export interface AssistantUsage {
  provider: 'LOCAL' | 'HOSTED'
  limit: number | null
  used: number
  left: number | null
  month: string
}

/** Split a stream of server-sent events into events, keeping a partial one for the next chunk. */
export function parseEvents(buffer: string): { events: AssistantEvent[]; rest: string } {
  const events: AssistantEvent[] = []
  const blocks = buffer.split('\n\n')
  const rest = blocks.pop() ?? ''
  for (const block of blocks) {
    const kind = /^event: (\w+)$/m.exec(block)?.[1]
    const data = /^data: (.*)$/m.exec(block)?.[1]
    if (!kind || data === undefined) continue
    try {
      events.push({ kind, data: JSON.parse(data) } as AssistantEvent)
    } catch {
      // A malformed event is skipped rather than ending the conversation.
    }
  }
  return { events, rest }
}

/** Send one message; `onEvent` is called for each event as it arrives. */
export async function streamChat(
  body: { message: string; conversation_id?: string | null },
  onEvent: (e: AssistantEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = useAuthStore.getState().accessToken
  const res = await fetch(`${apiClient.defaults.baseURL ?? ''}/api/v1/ai/assistant/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ message: body.message, ...(body.conversation_id ? { conversation_id: body.conversation_id } : {}) }),
    signal,
  })
  if (!res.ok || !res.body) {
    let message = res.status === 401 ? 'Your sign-in has expired. Reload the page and sign in again.' : `The assistant could not answer (${res.status}).`
    try {
      const detail = (await res.json())?.detail
      if (typeof detail === 'string') message = detail
    } catch { /* not JSON */ }
    onEvent({ kind: 'error', data: { message } })
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parsed = parseEvents(buffer)
    buffer = parsed.rest
    parsed.events.forEach(onEvent)
  }
}

export const getConversations = () =>
  apiClient.get<{ items: ConversationSummary[] }>('/api/v1/ai/assistant/conversations').then((r) => r.data.items)

export const getConversation = (id: string) =>
  apiClient.get<ConversationSummary & { messages: ConversationMessage[] }>(`/api/v1/ai/assistant/conversations/${id}`)
    .then((r) => r.data)

export const getAssistantUsage = () =>
  apiClient.get<AssistantUsage>('/api/v1/ai/assistant/usage').then((r) => r.data)

// ── Confirm cards (phase 7) ──

export interface AssistantAction {
  id: string
  conversation_id: string
  title: string
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string
  fields: Array<{ name: string; value: unknown }>
  status: 'PENDING' | 'CONFIRMED' | 'FAILED' | 'CANCELLED' | 'EXPIRED'
  result: { status: number; answer: unknown } | null
  created_at: string
  expires_at: string
  decided_at: string | null
  /** Only on the answer to a Confirm that made one: shown to the person this once, never stored. */
  temporary_password?: string
}

export const getActions = (conversationId: string) =>
  apiClient.get<{ items: AssistantAction[] }>('/api/v1/ai/assistant/actions', { params: { conversation_id: conversationId } })
    .then((r) => r.data.items)

export const confirmAction = (id: string) =>
  apiClient.post<AssistantAction>(`/api/v1/ai/assistant/actions/${id}/confirm`).then((r) => r.data)

export const cancelAction = (id: string) =>
  apiClient.post<AssistantAction>(`/api/v1/ai/assistant/actions/${id}/cancel`).then((r) => r.data)

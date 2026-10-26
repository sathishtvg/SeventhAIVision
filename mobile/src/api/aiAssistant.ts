/**
 * The AI assistant on the phone (backend/app/routers/ai_assistant.py; AI_MODULE.md).
 *
 * The reply comes back whole (`stream: false`): React Native's fetch cannot read a stream as it arrives, so the
 * phone shows "Thinking" until the answer is in. It reads the app with the person's own permissions, and a change
 * it prepares comes back as a Confirm card: nothing changes until the person presses Confirm.
 */
import { apiClient } from './client'

export interface AiFeatureStatus {
  key: string
  title: string
  /** Licensed, switched on by the admin, and the person's role among those chosen. */
  mine: boolean
}

export interface AiStatus {
  licensed: boolean
  reason: string | null
  features: AiFeatureStatus[]
}

export interface AssistantAction {
  id: string
  conversation_id: string
  title: string
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string
  fields: Array<{ name: string; value: unknown }>
  status: 'PENDING' | 'CONFIRMED' | 'FAILED' | 'CANCELLED' | 'EXPIRED'
  result: { status: number; answer: unknown } | null
  expires_at: string
  /** Only on the answer to a Confirm that made one: shown to the person this once, never stored. */
  temporary_password?: string
}

export interface PhoneReply {
  conversation_id: string
  message_id: string
  text: string
  stop: 'end' | 'refusal' | 'rounds' | 'max_tokens'
  /** What it did while answering, in words: "Reading shifts". */
  statuses: string[]
  /** Changes it prepared, each waiting for Confirm. */
  actions: AssistantAction[]
}

export interface AssistantUsage {
  limit: number | null
  used: number
  left: number | null
}

/** A reply can read several screens' worth of the app first; the app's usual 15 s would give up before it. */
export const ASSISTANT_TIMEOUT_MS = 120_000

export const getAiStatus = () => apiClient.get<AiStatus>('/api/v1/ai/status').then((r) => r.data)

export const askAssistant = (message: string, conversationId: string | null) =>
  apiClient.post<PhoneReply>('/api/v1/ai/assistant/chat', {
    message, channel: 'PHONE', stream: false, ...(conversationId ? { conversation_id: conversationId } : {}),
  }, { timeout: ASSISTANT_TIMEOUT_MS }).then((r) => r.data)

export const getAssistantUsage = () =>
  apiClient.get<AssistantUsage>('/api/v1/ai/assistant/usage').then((r) => r.data)

export const confirmAction = (id: string) =>
  apiClient.post<AssistantAction>(`/api/v1/ai/assistant/actions/${id}/confirm`, undefined, { timeout: ASSISTANT_TIMEOUT_MS })
    .then((r) => r.data)

export const cancelAction = (id: string) =>
  apiClient.post<AssistantAction>(`/api/v1/ai/assistant/actions/${id}/cancel`).then((r) => r.data)

/** Whether the assistant is this person's to use: the licence, the admin's switch and their role. */
export const assistantIsMine = (status: AiStatus | undefined) =>
  !!status?.features.some((f) => f.key === 'assistant' && f.mine)

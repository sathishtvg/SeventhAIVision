/**
 * What the AI is waiting on this person to decide (backend/app/routers/ai_waiting.py), for the corner card.
 *
 * Never refused: a person the AI module is not for gets `assistant: false` and empty lists. Deciding uses the
 * endpoints the chat (`confirmAction`, `cancelAction`) and AI Insights (`decideAiInsight`) already use.
 */
import { apiClient } from './client'
import type { AiInsight } from './ai'
import type { AssistantAction } from './aiAssistant'

export interface AiWaiting {
  /** The assistant is this person's to use. */
  assistant: boolean
  /** Their own Confirm cards left waiting in the chat, soonest to expire first. */
  actions: AssistantAction[]
  /** Open warnings they may decide, most important first: at most 20, `counts.warnings` has them all. */
  warnings: AiInsight[]
  counts: { actions: number; warnings: number }
}

export const getAiWaiting = () => apiClient.get<AiWaiting>('/api/v1/ai/waiting').then((r) => r.data)

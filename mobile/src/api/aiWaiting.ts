/**
 * What the AI is waiting on this person to decide (backend/app/routers/ai_waiting.py), for the corner card.
 *
 * Never refused: a person the AI module is not for gets `assistant: false` and empty lists. A Confirm card is decided
 * with the chat's own calls (`confirmAction`, `cancelAction` in ./aiAssistant); a warning with AI Insights' decide.
 */
import { apiClient } from './client'
import type { AssistantAction } from './aiAssistant'

export interface AiWarning {
  id: string
  feature_key: string
  feature_title: string
  title: string
  detail: string | null
  score: number
  /** The factors that raised it, in words, largest first. */
  reasons: Array<{ factor: string; words: string; push: number }>
  site_name: string | null
  person_name: string | null
  status: 'OPEN' | 'ACCEPTED' | 'DISMISSED' | 'EXPIRED'
}

export interface AiWaiting {
  /** The assistant is this person's to use: the AI button is shown. */
  assistant: boolean
  /** Their own Confirm cards left waiting in the chat, soonest to expire first. */
  actions: AssistantAction[]
  /** Open warnings they may decide, most important first: at most 20, `counts.warnings` has them all. */
  warnings: AiWarning[]
  counts: { actions: number; warnings: number }
}

export const getAiWaiting = () => apiClient.get<AiWaiting>('/api/v1/ai/waiting').then((r) => r.data)

export const decideWarning = (id: string, decision: 'ACCEPT' | 'DISMISS') =>
  apiClient.post<AiWarning>(`/api/v1/ai/insights/${id}/decide`, { decision }).then((r) => r.data)

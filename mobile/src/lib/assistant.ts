/**
 * The words of the assistant's screen (src/screens/AssistantScreen.tsx), kept apart so they can be tested
 * without drawing it.
 */
import type { AssistantAction, PhoneReply } from '@/api/aiAssistant'
import { apiErrorText } from './apiErrorText'

export interface ChatLine {
  key: string
  kind: 'user' | 'assistant' | 'status' | 'card'
  text: string
  action?: AssistantAction
}

/** A value on a Confirm card, as words: nothing for empty, the JSON of a list or an object. */
export function shownValue(v: unknown): string {
  if (v === null || v === undefined) return ''
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}

/** What became of a card, once decided. */
export function cardOutcome(a: AssistantAction): string | null {
  switch (a.status) {
    case 'PENDING': return null
    case 'CONFIRMED': return 'Done.'
    case 'CANCELLED': return 'Cancelled; nothing was changed.'
    case 'EXPIRED': return 'Expired; nothing was changed.'
    default: return `Not done: ${shownValue((a.result as { answer?: unknown } | null)?.answer)}`
  }
}

/** One reply as lines: what it did, the cards it prepared, then its words. */
export function replyLines(reply: PhoneReply, n: number): ChatLine[] {
  return [
    ...reply.statuses.map((text, i) => ({ key: `${n}-s${i}`, kind: 'status' as const, text })),
    ...reply.actions.map((action) => ({ key: `card-${action.id}`, kind: 'card' as const, text: '', action })),
    ...(reply.text ? [{ key: `${n}-a`, kind: 'assistant' as const, text: reply.text }] : []),
  ]
}

/** Why a message got no answer, in words. */
export function askErrorText(err: unknown): string {
  if ((err as { code?: string })?.code === 'ECONNABORTED') {
    return 'The assistant took too long to answer. Try again, or ask a shorter question.'
  }
  return apiErrorText(err)
}

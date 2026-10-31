/**
 * What the AI is waiting on this person to decide, for the decision card (DecisionCard.tsx) and the count on the AI
 * button (AssistantPanel.tsx). Design: docs/plans/2026-10-31-ai-corner-button-and-decision-popups-design.md.
 */
import { useCallback, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getAiWaiting } from '@/api/aiWaiting'
import type { AiWaiting } from '@/api/aiWaiting'
import type { AiInsight } from '@/api/ai'
import type { AssistantAction } from '@/api/aiAssistant'

export const WAITING_EVERY_MS = 60_000
export const LATER_MS = 4 * 60 * 60 * 1000
export const LATER_KEY = 'ai-decisions-later'

export type Decision = { kind: 'action'; action: AssistantAction } | { kind: 'warning'; warning: AiInsight }

export const decisionId = (d: Decision) => (d.kind === 'action' ? d.action.id : d.warning.id)

function readLater(): Record<string, number> {
  try {
    const v = JSON.parse(localStorage.getItem(LATER_KEY) ?? '{}')
    return v && typeof v === 'object' ? v : {}
  } catch {
    return {}
  }
}

function writeLater(v: Record<string, number>) {
  try { localStorage.setItem(LATER_KEY, JSON.stringify(v)) } catch { /* private window: Later lasts this page only */ }
}

/** What to ask, in order: Confirm cards first, then warnings; leaving out what was put off and has not come back. */
export function decisionsToShow(w: AiWaiting | null | undefined, later: Record<string, number>, now: number) {
  if (!w) return { list: [] as Decision[], beyond: 0 }
  const open = (id: string) => !((later[id] ?? 0) > now)
  const list: Decision[] = [
    ...w.actions.filter((a) => open(a.id) && Date.parse(a.expires_at) > now).map((action) => ({ kind: 'action' as const, action })),
    ...w.warnings.filter((x) => open(x.id)).map((warning) => ({ kind: 'warning' as const, warning })),
  ]
  // The server sends the first 20 warnings and counts the rest.
  return { list, beyond: Math.max(0, w.counts.warnings - w.warnings.length) }
}

/** The waiting list for this person, and what to do with it. Asked only when some AI feature is theirs. */
export function useDecisions(enabled: boolean) {
  const qc = useQueryClient()
  const waiting = useQuery({
    queryKey: ['ai-waiting'], enabled, retry: false,
    refetchInterval: WAITING_EVERY_MS, refetchOnWindowFocus: true, staleTime: 30_000,
    // A check that fails keeps the last answer and says nothing: it runs every minute on every page, and would
    // otherwise reopen the app's "could not be loaded" notice each minute for as long as the network is down.
    queryFn: async (): Promise<AiWaiting | null> => {
      try {
        return await getAiWaiting()
      } catch {
        return qc.getQueryData<AiWaiting | null>(['ai-waiting']) ?? null
      }
    },
  })
  const [later, setLater] = useState<Record<string, number>>(readLater)
  // "Now" is when the list arrived: it is asked again every minute, so a card put off comes back within a minute of
  // its time, and nothing is read from the clock while drawing.
  const { list, beyond } = useMemo(
    () => decisionsToShow(enabled ? waiting.data : undefined, later, waiting.dataUpdatedAt),
    [enabled, waiting.data, waiting.dataUpdatedAt, later])
  const putOff = useCallback((id: string) => {
    const now = Date.now()
    setLater((old) => {
      const kept = Object.fromEntries(Object.entries(old).filter(([, until]) => until > now))
      const next = { ...kept, [id]: now + LATER_MS }
      writeLater(next)
      return next
    })
  }, [])
  const refresh = useCallback(() => { void qc.invalidateQueries({ queryKey: ['ai-waiting'] }) }, [qc])
  return { current: list[0] ?? null, more: list.length - 1 + beyond, total: list.length + beyond, putOff, refresh }
}

export type Decisions = ReturnType<typeof useDecisions>

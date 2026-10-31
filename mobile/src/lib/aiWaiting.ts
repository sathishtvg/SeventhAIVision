/**
 * The rules of the phone's AI corner (components/AiCorner.tsx), apart from React so they can be tested on their own.
 *
 * Design: docs/plans/2026-10-31-ai-corner-button-and-decision-popups-design.md.
 */
import type { AssistantAction } from '@/api/aiAssistant'
import type { AiWaiting, AiWarning } from '@/api/aiWaiting'

/** How often the corner asks, while the app is open. It asks again whenever the app comes back to the front. */
export const WAITING_EVERY_MS = 60_000
/** Later puts one card off this long, on this phone. */
export const LATER_MS = 4 * 60 * 60 * 1000
export const LATER_KEY = 'ai_decisions_later'
/** How long the answer to a decision stays before the next card. */
export const NOTICE_MS = 4000

/**
 * Screens the corner stays off: a live picture, a scan, a drawing, or the chat itself. The man-down countdown is a
 * full-screen modal and covers everything anyway.
 */
export const HIDDEN_ON = new Set([
  'CameraLive', 'IncidentCameraLive', 'SituationCameraLive', 'LiveWall', 'PatrolScan', 'ZoneDraw',
  'CameraPrivacyZones', 'Assistant',
])

export type Decision = { kind: 'action'; action: AssistantAction } | { kind: 'warning'; warning: AiWarning }

export const decisionId = (d: Decision) => (d.kind === 'action' ? d.action.id : d.warning.id)

/** What to ask, in order: Confirm cards first (they expire), then warnings; leaving out what was put off. */
export function decisionsToShow(w: AiWaiting | null, later: Record<string, number>, now: number) {
  if (!w) return { list: [] as Decision[], beyond: 0 }
  const open = (id: string) => !((later[id] ?? 0) > now)
  const list: Decision[] = [
    ...w.actions.filter((a) => open(a.id) && Date.parse(a.expires_at) > now).map((action) => ({ kind: 'action' as const, action })),
    ...w.warnings.filter((x) => open(x.id)).map((warning) => ({ kind: 'warning' as const, warning })),
  ]
  // The server sends the first 20 warnings and counts the rest.
  return { list, beyond: Math.max(0, w.counts.warnings - w.warnings.length) }
}

/** Put one off, dropping the entries whose time has passed. */
export function putOff(later: Record<string, number>, id: string, now: number): Record<string, number> {
  const kept = Object.fromEntries(Object.entries(later).filter(([, until]) => until > now))
  return { ...kept, [id]: now + LATER_MS }
}

/** The lines under a warning's title: its reasons in words, or its detail. */
export function warningLines(w: AiWarning): string[] {
  if (w.reasons.length) return w.reasons.slice(0, 3).map((r) => r.words)
  return w.detail ? [w.detail] : []
}

/** The words of the AI Insights page: how sure a warning is, what became of it, and where its bar is. */
import type { AiInsight, AiLearning } from '@/api/ai'

/** "71% chance", "105% of the overtime limit", or "By your roster rules" for a fatigue warning. */
export function howSure(i: Pick<AiInsight, 'subject_type' | 'score'>): string {
  if (i.subject_type === 'guard_week') return 'By your roster rules'
  if (i.subject_type === 'guard_month') return `${Math.round(i.score * 100)}% of the overtime limit`
  return `${Math.round(i.score * 100)}% chance`
}

/** The bar, on the feature's own scale. */
export function barWords(l: Pick<AiLearning, 'key' | 'threshold'>): string {
  const pct = Math.round(l.threshold * 100)
  return l.key === 'overtime_fatigue' ? `Warns at ${pct}% of the monthly overtime limit` : `Warns at a ${pct}% chance or more`
}

export function statusWords(i: Pick<AiInsight, 'status' | 'outcome'>): string {
  const was = { OPEN: 'Open', ACCEPTED: 'Accepted', DISMISSED: 'Dismissed', EXPIRED: 'Expired' }[i.status]
  if (i.outcome === 'HAPPENED') return `${was} · it came true`
  if (i.outcome === 'DID_NOT_HAPPEN') return `${was} · it did not happen`
  return was
}

/** How well the model ranks the newest shifts it did not learn from, in words. */
export function rankWords(auc: number | null | undefined): string | null {
  if (auc == null) return null
  const how = auc >= 0.8 ? 'well' : auc >= 0.65 ? 'fairly well' : 'little better than guessing'
  return `On the newest shifts it had not seen, it ranked the late ones first ${how} (${auc.toFixed(2)}; 1 is perfect, 0.5 is guessing).`
}

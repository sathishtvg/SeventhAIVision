/** The words of the AI Insights page: how sure a warning is, what became of it, and where its bar is. */
import type { AiInsight, AiLearning } from '@/api/ai'

/** How sure a warning is, on its own scale. */
export function howSure(i: Pick<AiInsight, 'subject_type' | 'score'>): string {
  const pct = Math.round(i.score * 100)
  switch (i.subject_type) {
    case 'guard_week': return 'By your roster rules'
    case 'guard_month': return `${pct}% of the overtime limit`
    case 'alert': return `${pct}% chance it is false`
    case 'camera_month': return `${pct}% of its alerts were false`
    case 'site_month': return `${pct} points more problems than patrols`
    case 'guard_trend': return 'A clear drop'
    case 'incident': return `${pct}% of similar incidents agree`
    case 'payslip': return `${(i.score * 10).toFixed(1)} deviations from usual`
    default: return `${pct}% chance`
  }
}

/** The bar, on the feature's own scale. */
export function barWords(l: Pick<AiLearning, 'key' | 'threshold'>): string {
  const pct = Math.round(l.threshold * 100)
  if (l.key === 'overtime_fatigue') return `Warns at ${pct}% of the monthly overtime limit`
  if (l.key === 'patrol_timing') return `Warns at a gap of ${pct} points between problems and patrols`
  if (l.key === 'incident_helper') return `Speaks when ${pct}% of similar past incidents agree`
  if (l.key === 'payroll_check') return `Warns at ${(l.threshold * 10).toFixed(1)} deviations from a guard's usual pay`
  if (l.key === 'guard_trends') return `Warns at a drop of 15 points or more that is unlikely to be chance (test ${(l.threshold * 10).toFixed(2)})`
  return `Warns at a ${pct}% chance or more`
}

export function statusWords(i: Pick<AiInsight, 'status' | 'outcome'>): string {
  const was = { OPEN: 'Open', ACCEPTED: 'Accepted', DISMISSED: 'Dismissed', EXPIRED: 'Expired' }[i.status]
  if (i.outcome === 'HAPPENED') return `${was} · it came true`
  if (i.outcome === 'DID_NOT_HAPPEN') return `${was} · it did not happen`
  return was
}

/** How well the model ranks the newest history it did not learn from, in words. */
export function rankWords(auc: number | null | undefined): string | null {
  if (auc == null) return null
  const how = auc >= 0.8 ? 'well' : auc >= 0.65 ? 'fairly well' : 'little better than guessing'
  return `On the newest history it had not seen, it ranked what happened first ${how} (${auc.toFixed(2)}; 1 is perfect, 0.5 is guessing).`
}

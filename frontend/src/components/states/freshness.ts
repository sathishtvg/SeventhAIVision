/** "12 s ago", "3 min ago", "2 h ago". */
export function ageText(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s} s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86_400)} d ago`
}

/**
 * True while a reading taken at `at` is younger than `staleAfterMs`. For
 * gating anything drawn as live or moving: a marker pulses, a status says
 * "live", only for a reading this calls fresh. No time at all is not fresh.
 */
export function isFresh(at: string | number | Date | null | undefined, staleAfterMs = 30_000, now: number = Date.now()): boolean {
  if (at === null || at === undefined) return false
  const taken = new Date(at).getTime()
  return !Number.isNaN(taken) && now - taken <= staleAfterMs
}

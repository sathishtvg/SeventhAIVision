/**
 * How the phone words a camera's latest check and its recording gaps - the same words as the web's device health
 * page (frontend/src/components/assets/CameraChecks.tsx). Every figure is the server's.
 */
import type { Check, CheckedCamera, CheckedStream } from '@/api/cameraChecks'

const minutes = (seconds: number) => Math.max(1, Math.round(seconds / 60))

function ago(iso: string, now: Date): string {
  const s = (now.getTime() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  const m = minutes(s)
  return m < 60 ? `${m} min ago` : m < 48 * 60 ? `${Math.floor(m / 60)} h ago` : `${Math.floor(m / 1440)} days ago`
}

/** A length of time as it is read on a gap. */
export function length(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  if (s < 600) return s % 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s / 60} min`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m} min` : m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`
}

/** One check in a line: its figures, or why it could not run. */
export function checkText(c: Check, now: Date = new Date()): string {
  if (c.result !== 'OK') return `${c.result_text} · ${ago(c.checked_at, now)}`
  const parts: string[] = []
  if (c.received_rate !== null) {
    parts.push(c.stated_rate !== null ? `${c.received_rate.toFixed(1)} of ${Math.round(c.stated_rate)} fps`
      : `${c.received_rate.toFixed(1)} fps`)
  }
  if (c.longest_stall_ms !== null) parts.push(`stall ${(c.longest_stall_ms / 1000).toFixed(1)} s`)
  if (c.missed_pct !== null) parts.push(`missed ${c.missed_pct.toFixed(c.missed_pct % 1 ? 1 : 0)}%`)
  if (c.answer_ms !== null) parts.push(`answer ${c.answer_ms} ms`)
  if (c.first_picture_ms !== null) parts.push(`first picture ${(c.first_picture_ms / 1000).toFixed(1)} s`)
  parts.push(`checked ${ago(c.checked_at, now)}`)
  return parts.join(' · ')
}

/** A stream's line, and whether it says something is wrong (a limit broken by a check recent enough to go by). */
export function streamLine(s: CheckedStream, now: Date = new Date()): { text: string; wrong: boolean; old: boolean } {
  if (s.not_checked) return { text: s.not_checked, wrong: false, old: false }
  if (!s.check) return { text: 'Not checked yet', wrong: false, old: false }
  const old = s.check.stale
  return { text: checkText(s.check, now) + (old ? ' (old: not gone by)' : ''), wrong: !old && s.check.breaks.length > 0, old }
}

/** What broke, in the server's words, for a check recent enough to go by. */
export function brokeText(s: CheckedStream): string | null {
  if (!s.check || s.check.stale || !s.check.breaks.length) return null
  return `Over its limit: ${s.check.breaks.map((b) => b.text).join('; ')}.`
}

/** The camera's recording gaps over the limit in the last 24 hours, or null when there are none. */
export function gapText(c: Pick<CheckedCamera, 'gaps_24h'>): string | null {
  const g = c.gaps_24h
  if (!g.gaps) return null
  if (g.open) return 'Not recording now, for longer than its limit'
  return `${g.gaps} recording ${g.gaps === 1 ? 'gap' : 'gaps'} over its limit in 24 hours` +
    (g.longest_s ? `, the longest ${length(g.longest_s)}` : '')
}

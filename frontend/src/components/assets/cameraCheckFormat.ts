/**
 * How a camera's check and a recording gap are worded (Phase 6 of the known limits, 4 November 2026). The phone
 * words them the same way (mobile/src/lib/cameraCheckWords.ts). Every figure is the server's.
 */
import type { Check, LimitKey } from '@/api/cameraChecks'
import { span } from './assetFormat'

/** A length of time as it is read on a gap: seconds while it is short. */
export function length(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  if (s < 600) return s % 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s / 60} min`
  return span(s)
}

const ago = (iso: string, now: Date) => {
  const s = (now.getTime() - new Date(iso).getTime()) / 1000
  return s < 60 ? 'just now' : `${span(s)} ago`
}

export interface Part { key: LimitKey | 'when'; text: string }

/** The figures of one check, in the order they are read, each with the limit it is judged by. */
export function checkParts(c: Check, now: Date = new Date()): Part[] {
  if (c.result !== 'OK') return [{ key: 'when', text: `${c.result_text} · ${ago(c.checked_at, now)}` }]
  const parts: Part[] = []
  if (c.received_rate !== null) {
    parts.push({ key: 'least_rate_pct', text: c.stated_rate !== null
      ? `${c.received_rate.toFixed(1)} of ${Math.round(c.stated_rate)} fps` : `${c.received_rate.toFixed(1)} fps` })
  }
  if (c.longest_stall_ms !== null) parts.push({ key: 'most_stall_ms', text: `stall ${(c.longest_stall_ms / 1000).toFixed(1)} s` })
  if (c.missed_pct !== null) parts.push({ key: 'most_missed_pct', text: `missed ${c.missed_pct.toFixed(c.missed_pct % 1 ? 1 : 0)}%` })
  if (c.answer_ms !== null) parts.push({ key: 'most_answer_ms', text: `answer ${c.answer_ms} ms` })
  if (c.first_picture_ms !== null) parts.push({ key: 'most_first_picture_ms', text: `first picture ${(c.first_picture_ms / 1000).toFixed(1)} s` })
  parts.push({ key: 'when', text: `checked ${ago(c.checked_at, now)}` })
  return parts
}

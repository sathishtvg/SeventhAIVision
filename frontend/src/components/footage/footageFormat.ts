/**
 * Footage Review — the words its screens share.
 */
import { clock } from '@/api/footage'
import type { Finding, FootageVideo } from '@/api/footage'

/** How long a video is kept, as a person would say it. */
export function keptText(v: Pick<FootageVideo, 'held' | 'deleted_in_days'>): string {
  if (v.held) return 'Held'
  if (v.deleted_in_days === null) return '—'
  if (v.deleted_in_days <= 0) return 'Deleted today'
  return v.deleted_in_days === 1 ? 'Deleted tomorrow' : `Deleted in ${v.deleted_in_days} days`
}

/** The file's name without its extension: what the video is called unless the person says otherwise. */
export function titleOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return (dot > 0 ? name.slice(0, dot) : name).trim()
}

/** A datetime-local value, read on the person's own clock, as an instant; or null. */
export function instantOf(local: string): string | null {
  if (!local) return null
  const at = new Date(local)
  return Number.isNaN(at.getTime()) ? null : at.toISOString()
}

/** Where a second sits along the timeline, as a percentage of the video. */
export function along(second: number, duration: number): number {
  if (!duration || duration <= 0) return 0
  return Math.max(0, Math.min(100, (second / duration) * 100))
}

/** What an event is, as a person would say it: "People present 0:03 – 0:05, up to 2 at once". */
export function eventText(f: Finding): string {
  const span = f.start_second === f.end_second ? clock(f.start_second) : `${clock(f.start_second)} – ${clock(f.end_second)}`
  const most = f.most && f.most > 1 ? `, up to ${f.most} at once` : ''
  return `${f.kind_words} ${span}${most}`
}

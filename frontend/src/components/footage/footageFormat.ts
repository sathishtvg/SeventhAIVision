/**
 * Footage Review — the words its screens share.
 */
import type { FootageVideo } from '@/api/footage'

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

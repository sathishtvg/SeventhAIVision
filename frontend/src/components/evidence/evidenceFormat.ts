/**
 * Evidence packages — the words the evidence screens share.
 */
import type { CustodyStep, EvidenceKind, Step } from '@/api/evidencePackages'

export const KIND_LABEL: Record<EvidenceKind, string> = {
  SNAPSHOT: 'Frame', CLIP: 'Clip', RECORDING: 'Recording', DRONE_MEDIA: 'Drone media',
}

/** Each step of custody, as a person would say it. */
export const STEP_LABEL: Record<CustodyStep, string> = {
  CAPTURED: 'Captured', COLLECTED: 'Added to the package', REMOVED: 'Taken out of the package',
  VIEWED: 'Package opened', ACCESSED: 'Accessed', SEALED: 'Sealed', LOCKED: 'Placed under a hold',
  UNLOCKED: 'Hold lifted', EXPORTED: 'Exported', DOWNLOADED: 'Original downloaded', SHARED: 'Shared',
  RELEASED: 'Released',
}

/** Steps in which evidence left the platform, or stopped being protected. */
export const WEIGHTY: CustodyStep[] = ['EXPORTED', 'DOWNLOADED', 'SHARED', 'RELEASED', 'UNLOCKED']

const ROLE: Record<number, string> = {
  1: 'Super Admin', 2: 'Admin', 3: 'Supervisor', 4: 'Operator', 5: 'Guard', 6: 'Viewer', 7: 'Client', 8: 'Manager',
}
export const roleName = (role: number | null) => (role === null ? null : ROLE[role] ?? `Role ${role}`)

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' })
}

/** The first and last of a checksum: enough to compare by eye, short enough to read. */
export const short = (sha: string | null | undefined) => (sha ? `${sha.slice(0, 10)}…${sha.slice(-6)}` : 'none recorded')

export function size(bytes: number | null | undefined): string | null {
  if (bytes === null || bytes === undefined) return null
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/** How long a clip or a recording runs, in the words a person would use. */
export function length(seconds: number | null | undefined): string | null {
  if (!seconds) return null
  if (seconds < 120) return `${Math.round(seconds)} s`
  const minutes = Math.round(seconds / 60)
  return minutes < 120 ? `${minutes} min` : `${(minutes / 60).toFixed(1)} h`
}

/** What a step of custody says beyond who and when. */
export function said(step: Step): string | null {
  const d = step.detail as {
    recipient?: string; organisation?: string | null; how?: string; checksum_verified?: boolean | null
    included?: number; verified?: number; mismatched?: number; left_out?: number; items?: number
  }
  if (step.step === 'SHARED' || step.step === 'RELEASED') {
    return `To ${d.recipient}${d.organisation ? `, ${d.organisation}` : ''}`
  }
  if (step.step === 'EXPORTED') {
    const parts = [`${d.included ?? 0} file(s)`, `${d.verified ?? 0} matched their checksum`]
    if (d.mismatched) parts.push(`${d.mismatched} DID NOT MATCH`)
    if (d.left_out) parts.push(`${d.left_out} left out`)
    return parts.join(' · ')
  }
  if (step.step === 'ACCESSED') {
    const checked = d.checksum_verified === true ? ' · checksum matched'
      : d.checksum_verified === false ? ' · CHECKSUM DID NOT MATCH' : ''
    return `${d.how ?? 'Opened'}${checked}`
  }
  if (step.step === 'SEALED') return `${d.items ?? 0} item(s)`
  return null
}

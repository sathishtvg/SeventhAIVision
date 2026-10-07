/** The words the SOP library screens share. */
import type { DocumentState, Passage, Procedure, Version, VersionState } from '@/api/sop'

export const STATE_LABEL: Record<DocumentState, string> = {
  IN_FORCE: 'In force', NOT_YET_APPROVED: 'Not yet approved', EXPIRED: 'Run out', RETIRED: 'Retired',
}

export const STATE_COLOUR: Record<DocumentState, 'success' | 'default' | 'warning'> = {
  IN_FORCE: 'success', NOT_YET_APPROVED: 'default', EXPIRED: 'warning', RETIRED: 'default',
}

export const VERSION_LABEL: Record<VersionState, string> = {
  DRAFT: 'Draft', SUBMITTED: 'Awaiting approval', APPROVED: 'Approved', REJECTED: 'Rejected',
}

export const label = (key: string) => {
  const words = key.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** Where a procedure stands, in a line: which version is in force, and what is waiting. */
export function standing(p: Pick<Procedure, 'state' | 'in_force_version_no' | 'effective_until' | 'open_version'>): string {
  const parts: string[] = []
  if (p.state === 'IN_FORCE') {
    parts.push(`Version ${p.in_force_version_no} in force${p.effective_until ? ` until ${fmt(p.effective_until)}` : ''}`)
  } else if (p.state === 'EXPIRED') {
    parts.push(`Version ${p.in_force_version_no} ran out ${fmt(p.effective_until)}: nothing is in force`)
  } else if (p.state === 'RETIRED') {
    parts.push('Retired: not in force')
  } else {
    parts.push('No version has been approved')
  }
  if (p.open_version) {
    parts.push(`version ${p.open_version.version_no} ${p.open_version.state === 'DRAFT' ? 'being written' : 'awaiting approval'}`)
  }
  return parts.join(' · ')
}

/** Where a passage is from: the procedure, the version, who approved it. */
export function source(p: Pick<Passage, 'procedure' | 'version'>): string {
  const by = p.version.approved_by_name ? ` by ${p.version.approved_by_name}` : ''
  return `${p.procedure.code} ${p.procedure.title} · version ${p.version.version_no}, approved${by}, ${fmt(p.version.approved_at)}`
}

/** Which of the words asked a passage uses. */
export function matched(p: Pick<Passage, 'matched' | 'of' | 'matched_words'>): string {
  return `Uses ${p.matched} of the ${p.of} ${p.of === 1 ? 'word' : 'words'} looked for: ${p.matched_words.join(', ')}`
}

/** A version in a line: who drafted it, and what became of it. */
export function versionLine(v: Version): string {
  const drafted = `drafted by ${v.drafted_by_name ?? 'somebody no longer on the system'}, ${fmt(v.drafted_at)}`
  if (v.state === 'APPROVED') {
    const from = v.in_force ? 'in force' : v.effective_from && new Date(v.effective_from) > new Date()
      ? `comes into force ${fmt(v.effective_from)}` : 'replaced'
    return `${drafted} · approved by ${v.decided_by_name ?? 'somebody no longer on the system'}, ${fmt(v.decided_at)} · ${from}`
  }
  if (v.state === 'REJECTED') {
    return `${drafted} · rejected by ${v.decided_by_name ?? 'somebody no longer on the system'}: ${v.decision_note ?? ''}`
  }
  return v.state === 'SUBMITTED' ? `${drafted} · submitted ${fmt(v.submitted_at)}` : drafted
}

/** Kinds of incident typed with commas or spaces, as the server takes them. */
export function typedKinds(text: string): string[] {
  return [...new Set(text.split(/[\s,]+/).map((k) => k.trim().toLowerCase()).filter(Boolean))].sort()
}

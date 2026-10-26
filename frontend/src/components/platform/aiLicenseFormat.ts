/** What the AI licence form holds while it is being edited, and how it is read back. */
import type { AiLicense, AiLicenseBody, AiProvider } from '@/api/platformAiLicense'
import { asLimit, untilEndOf } from '@/components/platform/droneLicenseFormat'

export interface AiDraft {
  on: boolean
  until: string
  features: string[]
  provider: AiProvider
  limit: string
  notes: string
}

export function aiDraftOf(l: AiLicense): AiDraft {
  return {
    on: l.is_enabled, until: l.expires_at ? l.expires_at.slice(0, 10) : '', features: [...l.features].sort(),
    provider: l.assistant_provider, limit: l.monthly_reply_limit == null ? '' : String(l.monthly_reply_limit),
    notes: l.notes ?? '',
  }
}

/** Why the draft cannot be saved, in words, or null. The hosted model is paid per reply, so it needs a limit. */
export function aiDraftProblem(d: AiDraft): string | null {
  const limit = asLimit(d.limit)
  if (Number.isNaN(limit)) return 'The monthly limit must be a whole number, or empty.'
  if (d.provider === 'HOSTED' && limit === null) return 'The hosted model is paid per reply: set a monthly limit.'
  return null
}

export function aiBodyOf(d: AiDraft): AiLicenseBody {
  return {
    is_enabled: d.on, expires_at: untilEndOf(d.until), features: [...d.features].sort(),
    assistant_provider: d.provider, monthly_reply_limit: asLimit(d.limit), notes: d.notes.trim() || null,
  }
}

export function toggled(list: string[], key: string): string[] {
  return list.includes(key) ? list.filter((k) => k !== key) : [...list, key].sort()
}

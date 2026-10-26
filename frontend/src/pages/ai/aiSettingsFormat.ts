/** The words and the editing rules of the AI Settings page. */
import type { AiFeatureSetting, AiFeatureSettingBody } from '@/api/ai'

export const ROLE_NAMES: Record<number, string> = {
  2: 'Admin', 8: 'Manager', 3: 'Supervisor', 4: 'Operator', 5: 'Guard', 6: 'Viewer',
}

export function draftOfSetting(f: AiFeatureSetting): AiFeatureSettingBody {
  return {
    enabled: f.enabled, role_ids: [...f.role_ids].sort((a, b) => a - b),
    site_ids: f.site_ids === null ? null : [...f.site_ids].sort(),
    sensitivity: f.sensitivity, learning_enabled: f.learning_enabled,
  }
}

/** Why the feature cannot be switched on, as a short label, or null when it can. Off is always allowed. */
export function featureLock(f: Pick<AiFeatureSetting, 'licensed' | 'ready'>): string | null {
  if (!f.licensed) return 'Not in your licence'
  if (!f.ready) return 'Coming in a later release'
  return null
}

export function roleToggled(roles: number[], role: number): number[] {
  return roles.includes(role) ? roles.filter((r) => r !== role) : [...roles, role].sort((a, b) => a - b)
}

/**
 * Whether the signed-in person holds a permission, for showing a button only to people the server will let use it.
 *
 * 26 October 2026: an audit of every call the phone makes found buttons shown to people the server refuses
 * ("Failed to start patrol session" was the one the owner hit). Same rule as the menu (lib/access.ts canSee): when the
 * permissions have not loaded yet, the button shows and the server still decides.
 */
import { useCallback } from 'react'

import { canSee } from '@/lib/access'
import { useAuthStore } from '@/store/auth'

export function useCan(): (permission: string) => boolean {
  const permissions = useAuthStore((s) => s.permissions)
  const roleId = useAuthStore((s) => s.user?.roleId)
  return useCallback((permission: string) => canSee({ permission }, permissions, roleId), [permissions, roleId])
}

import { useEffect, useRef } from 'react'
import { AppState, type AppStateStatus } from 'react-native'
import { useBiometricStore } from '@/store/biometric'
import { useAuthStore } from '@/store/auth'

/**
 * When the app returns to the foreground while the user is authenticated
 * and biometric lock is enabled, re-prompt for biometric verification.
 * Any non-success (cancel or failure) signs the user out.
 */
export function useAppResumeBiometric() {
  const appStateRef = useRef<AppStateStatus>(AppState.currentState)
  const enabled = useBiometricStore((s) => s.enabled)
  const hardwareAvailable = useBiometricStore((s) => s.hardwareAvailable)
  const authenticate = useBiometricStore((s) => s.authenticate)
  const accessToken = useAuthStore((s) => s.accessToken)
  const logout = useAuthStore((s) => s.logout)

  useEffect(() => {
    const subscription = AppState.addEventListener('change', async (nextState) => {
      const comingToForeground =
        appStateRef.current.match(/inactive|background/) !== null && nextState === 'active'

      if (comingToForeground && accessToken && enabled && hardwareAvailable) {
        const ok = await authenticate('Verify your identity to continue')
        if (!ok) {
          await logout()
        }
      }
      appStateRef.current = nextState
    })
    return () => subscription.remove()
  }, [accessToken, enabled, hardwareAvailable, authenticate, logout])
}

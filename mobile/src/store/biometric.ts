import * as SecureStore from 'expo-secure-store'
import * as LocalAuthentication from 'expo-local-authentication'
import { create } from 'zustand'

const BIOMETRIC_PREF_KEY = 'seventh_ai_biometric_enabled'

interface BiometricState {
  initialized: boolean
  enabled: boolean
  hardwareAvailable: boolean
  biometricType: LocalAuthentication.AuthenticationType | null
  init: () => Promise<void>
  enable: () => Promise<void>
  disable: () => Promise<void>
  authenticate: (reason?: string) => Promise<boolean>
}

export const useBiometricStore = create<BiometricState>((set) => ({
  initialized: false,
  enabled: false,
  hardwareAvailable: false,
  biometricType: null,

  init: async () => {
    const [hasHardware, isEnrolled, types] = await Promise.all([
      LocalAuthentication.hasHardwareAsync(),
      LocalAuthentication.isEnrolledAsync(),
      LocalAuthentication.supportedAuthenticationTypesAsync(),
    ])
    const available = hasHardware && isEnrolled
    const stored = available ? await SecureStore.getItemAsync(BIOMETRIC_PREF_KEY) : null
    set({
      initialized: true,
      hardwareAvailable: available,
      enabled: available && stored === 'true',
      biometricType: types[0] ?? null,
    })
  },

  enable: async () => {
    await SecureStore.setItemAsync(BIOMETRIC_PREF_KEY, 'true')
    set({ enabled: true })
  },

  disable: async () => {
    await SecureStore.setItemAsync(BIOMETRIC_PREF_KEY, 'false')
    set({ enabled: false })
  },

  authenticate: async (reason = 'Verify your identity to access Seventh AI Vision') => {
    const result = await LocalAuthentication.authenticateAsync({
      promptMessage: reason,
      cancelLabel: 'Use Password',
      disableDeviceFallback: false,
    })
    return result.success
  },
}))

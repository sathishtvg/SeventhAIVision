/** Gap 83 — control-room alert sound logic (pure parts; Web Audio not exercised) */
import { describe, expect, it } from 'vitest'
import { shouldPlaySound, useAlertSoundStore } from './alertSound'

describe('shouldPlaySound', () => {
  it('plays for high and critical when enabled', () => {
    expect(shouldPlaySound('critical', true)).toBe(true)
    expect(shouldPlaySound('high', true)).toBe(true)
  })

  it('never plays for lower severities', () => {
    for (const sev of ['medium', 'low', 'info', '']) {
      expect(shouldPlaySound(sev, true)).toBe(false)
    }
  })

  it('never plays when muted, regardless of severity', () => {
    expect(shouldPlaySound('critical', false)).toBe(false)
    expect(shouldPlaySound('high', false)).toBe(false)
  })
})

describe('useAlertSoundStore', () => {
  it('defaults to enabled and toggles with localStorage persistence', () => {
    const initial = useAlertSoundStore.getState().soundEnabled
    useAlertSoundStore.getState().toggleSound()
    expect(useAlertSoundStore.getState().soundEnabled).toBe(!initial)
    expect(localStorage.getItem('seventh_ai_sound_enabled')).toBe(String(!initial))
    useAlertSoundStore.getState().toggleSound()
    expect(useAlertSoundStore.getState().soundEnabled).toBe(initial)
  })
})

/** Gap 83 — control-room alert sound logic (pure parts; Web Audio not exercised) */
import { describe, expect, it } from 'vitest'
import { announcementText, shouldPlaySound, useAlertSoundStore } from './alertSound'

describe('shouldPlaySound', () => {
  it('plays for the three actionable severities when enabled', () => {
    expect(shouldPlaySound('critical', true)).toBe(true)
    expect(shouldPlaySound('high', true)).toBe(true)
    // Medium became audible so an operator hears that something happened;
    // its tone is distinct from high/critical (see playAlertSound).
    expect(shouldPlaySound('medium', true)).toBe(true)
  })

  it('stays silent for informational severities', () => {
    for (const sev of ['low', 'info', '']) {
      expect(shouldPlaySound(sev, true)).toBe(false)
    }
  })

  it('never plays when muted, regardless of severity', () => {
    for (const sev of ['critical', 'high', 'medium']) {
      expect(shouldPlaySound(sev, false)).toBe(false)
    }
  })
})

describe('announcementText', () => {
  it('leads with severity, then module, then site', () => {
    expect(announcementText('critical', 'intrusion', 'Marina Bay Tower'))
      .toBe('critical alert. intrusion. at Marina Bay Tower.')
  })

  it('speaks module codes as words a person would say', () => {
    expect(announcementText('high', 'fire_smoke', 'HQ')).toBe('high alert. fire or smoke. at HQ.')
    expect(announcementText('high', 'lpr', 'HQ')).toBe('high alert. licence plate. at HQ.')
  })

  it('falls back readably when module or site is unknown', () => {
    expect(announcementText('high', null, 'HQ')).toBe('high alert. at HQ.')
    expect(announcementText('high', 'intrusion', null)).toBe('high alert. intrusion.')
    expect(announcementText('medium', null, null)).toBe('medium alert.')
    // An unmapped module still says something rather than reading an
    // underscore_separated identifier aloud.
    expect(announcementText('high', 'new_module', 'HQ')).toBe('high alert. new module. at HQ.')
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

/** Gap 93 — bundled i18n resources resolve for every supported locale. */
import { describe, expect, it } from 'vitest'
import i18n, { SUPPORTED_LOCALES } from '@/i18n'

describe('i18n', () => {
  it('defaults to English and translates nav keys', () => {
    expect(i18n.getFixedT('en')('nav.cameras')).toBe('Cameras')
    expect(i18n.getFixedT('en')('nav.audit_logs')).toBe('Audit Logs')
  })

  it('has real translations for every supported locale', () => {
    const expected: Record<string, string> = {
      en: 'Cameras', zh: '摄像头', ms: 'Kamera', ta: 'கேமராக்கள்',
    }
    for (const loc of SUPPORTED_LOCALES) {
      expect(i18n.getFixedT(loc)('nav.cameras')).toBe(expected[loc])
    }
  })

  it('falls back to the inline default for an unknown key', () => {
    expect(i18n.getFixedT('zh')('nav.does_not_exist', { defaultValue: 'Fallback' }))
      .toBe('Fallback')
  })
})

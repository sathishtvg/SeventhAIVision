import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './locales/en.json'
import zh from './locales/zh.json'
import ms from './locales/ms.json'
import ta from './locales/ta.json'

export const SUPPORTED_LOCALES = ['en', 'zh', 'ms', 'ta'] as const
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number]

export const LOCALE_LABELS: Record<SupportedLocale, string> = {
  en: 'English',
  zh: '中文',
  ms: 'Bahasa Melayu',
  ta: 'தமிழ்',
}

const savedLocale = localStorage.getItem('locale') ?? 'en'

// Resources are bundled (not fetched at runtime) so translations are available
// synchronously — works offline, inside the Electron shell, and in tests.
// Strings without a key fall back to English (fallbackLng) or their inline
// defaultValue.
i18n
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      zh: { translation: zh },
      ms: { translation: ms },
      ta: { translation: ta },
    },
    lng: savedLocale,
    fallbackLng: 'en',
    supportedLngs: SUPPORTED_LOCALES,
    interpolation: { escapeValue: false },
  })

export default i18n

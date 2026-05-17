import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import zh from './zh.json';
import en from './en.json';

export const LANGUAGE_STORAGE_KEY = 'aezab-lang';
const LEGACY_LANGUAGE_STORAGE_KEY = 'hlab-lang';

if (typeof window !== 'undefined') {
  const legacyLanguage = window.localStorage.getItem(LEGACY_LANGUAGE_STORAGE_KEY);
  if (legacyLanguage && !window.localStorage.getItem(LANGUAGE_STORAGE_KEY)) {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, legacyLanguage);
  }
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      zh: { translation: zh },
      en: { translation: en },
    },
    fallbackLng: 'zh',
    supportedLngs: ['zh', 'en'],
    detection: {
      // localStorage first (user manual override), then browser navigator
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
      caches: ['localStorage'],
    },
    interpolation: {
      escapeValue: false,
    },
  });

export default i18n;

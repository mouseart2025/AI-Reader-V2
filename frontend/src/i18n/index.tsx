import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"
import {
  getCurrentLocale,
  setCurrentLocale,
  subscribeLocale,
  SUPPORTED_LOCALES,
  translate,
  type Locale,
  type TranslationKey,
  type TranslationParams,
} from "./runtime"

export {
  getCurrentLocale,
  isLocale,
  setCurrentLocale,
  SUPPORTED_LOCALES,
  translate,
} from "./runtime"
export type { Locale, TranslationKey, TranslationParams } from "./runtime"

type I18nContextValue = {
  locale: Locale
  setLocale: (locale: Locale) => void
  supportedLocales: readonly Locale[]
  t: (key: TranslationKey, params?: TranslationParams) => string
}

const I18nContext = createContext<I18nContextValue | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => getCurrentLocale())

  useEffect(() => subscribeLocale(setLocaleState), [])

  const setLocale = useCallback((nextLocale: Locale) => {
    setCurrentLocale(nextLocale)
  }, [])

  const t = useCallback((key: TranslationKey, params?: TranslationParams) => {
    return translate(key, params)
  }, [locale])

  const value = useMemo<I18nContextValue>(() => ({
    locale,
    setLocale,
    supportedLocales: SUPPORTED_LOCALES,
    t,
  }), [locale, setLocale, t])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n() {
  const context = useContext(I18nContext)
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider")
  }
  return context
}

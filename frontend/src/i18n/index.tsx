import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react"
import { en } from "./locales/en"
import { vi } from "./locales/vi"
import { zhCN, type TranslationKey } from "./locales/zh-CN"

export const SUPPORTED_LOCALES = ["zh-CN", "en", "vi"] as const
export type Locale = (typeof SUPPORTED_LOCALES)[number]
export type { TranslationKey }

type TranslationParams = Record<string, number | string>

type I18nContextValue = {
  locale: Locale
  setLocale: (locale: Locale) => void
  supportedLocales: readonly Locale[]
  t: (key: TranslationKey, params?: TranslationParams) => string
}

const DEFAULT_LOCALE: Locale = "zh-CN"
const STORAGE_KEY = "ai-reader.locale"

const messages: Record<Locale, Record<TranslationKey, string>> = {
  "zh-CN": zhCN,
  en,
  vi,
}

const I18nContext = createContext<I18nContextValue | null>(null)

function isLocale(value: string | null): value is Locale {
  return SUPPORTED_LOCALES.includes(value as Locale)
}

function getStoredLocale(): Locale {
  if (typeof window === "undefined") return DEFAULT_LOCALE

  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return isLocale(stored) ? stored : DEFAULT_LOCALE
  } catch {
    return DEFAULT_LOCALE
  }
}

function formatMessage(template: string, params?: TranslationParams) {
  if (!params) return template
  return template.replace(/\{\{(\w+)\}\}/g, (_, key: string) => String(params[key] ?? ""))
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => getStoredLocale())

  const setLocale = useCallback((nextLocale: Locale) => {
    setLocaleState(nextLocale)
    try {
      window.localStorage.setItem(STORAGE_KEY, nextLocale)
    } catch {
      // Storage may be unavailable in restricted environments.
    }
  }, [])

  const t = useCallback((key: TranslationKey, params?: TranslationParams) => {
    const template = messages[locale][key] || messages[DEFAULT_LOCALE][key] || key
    return formatMessage(template, params)
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

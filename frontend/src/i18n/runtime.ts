import { en } from "./locales/en"
import { vi } from "./locales/vi"
import { zhCN, type TranslationKey } from "./locales/zh-CN"

export const SUPPORTED_LOCALES = ["zh-CN", "en", "vi"] as const
export type Locale = (typeof SUPPORTED_LOCALES)[number]
export type { TranslationKey }

export type TranslationParams = Record<string, number | string>

const DEFAULT_LOCALE: Locale = "zh-CN"
const STORAGE_KEY = "ai-reader.locale"

const messages: Record<Locale, Record<TranslationKey, string>> = {
  "zh-CN": zhCN,
  en,
  vi,
}

const listeners = new Set<(locale: Locale) => void>()
let currentLocale = getStoredLocale()

export function isLocale(value: string | null): value is Locale {
  return SUPPORTED_LOCALES.includes(value as Locale)
}

export function getCurrentLocale(): Locale {
  return currentLocale
}

export function setCurrentLocale(nextLocale: Locale) {
  currentLocale = nextLocale
  try {
    window.localStorage.setItem(STORAGE_KEY, nextLocale)
  } catch {
    // Storage may be unavailable in restricted environments.
  }
  listeners.forEach((listener) => listener(currentLocale))
}

export function subscribeLocale(listener: (locale: Locale) => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function translate(key: TranslationKey, params?: TranslationParams): string {
  const template = messages[currentLocale][key] || messages[DEFAULT_LOCALE][key] || key
  return formatMessage(template, params)
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

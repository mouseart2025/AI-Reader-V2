import { useCallback, useEffect, useState } from "react"
import { Clock, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useI18n, type TranslationKey } from "@/i18n"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const TEMPLATES = [
  { id: "blank_lines", labelKey: "shared.regexTemplate.blankLines", regex: "\\n\\n\\n+" },
  { id: "separator", labelKey: "shared.regexTemplate.separator", regex: "^[*=\\-]{3,}" },
  { id: "numbered", labelKey: "shared.regexTemplate.numbered", regex: "^\\d+[.、]" },
  { id: "jp_chapter", labelKey: "shared.regexTemplate.jpChapter", regex: "^第.{1,6}[帖贴]" },
  { id: "cn_numbered", labelKey: "shared.regexTemplate.cnNumbered", regex: "^[一二三四五六七八九十百]+、" },
  { id: "custom", labelKey: "shared.regexTemplate.custom", regex: "" },
] as const

const STORAGE_KEY = "ai-reader-custom-regex-history"
const MAX_RECENT = 5

interface RecentRegex {
  regex: string
  usedAt: string // ISO8601
}

function loadRecentRegexes(): RecentRegex[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (item: unknown): item is RecentRegex =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as RecentRegex).regex === "string" &&
        typeof (item as RecentRegex).usedAt === "string",
    )
  } catch {
    return []
  }
}

function saveRecentRegex(regex: string): void {
  try {
    const existing = loadRecentRegexes().filter((r) => r.regex !== regex)
    const updated: RecentRegex[] = [
      { regex, usedAt: new Date().toISOString() },
      ...existing,
    ].slice(0, MAX_RECENT)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updated))
  } catch {
    // localStorage not available — silently ignore
  }
}

function formatTimeAgo(
  iso: string,
  t: (key: TranslationKey, params?: Record<string, number | string>) => string,
): string {
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return t("date.justNow")
  if (minutes < 60) return t("date.minutesAgo", { count: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t("date.hoursAgo", { count: hours })
  const days = Math.floor(hours / 24)
  return t("date.daysAgo", { count: days })
}

interface Props {
  onApply: (regex: string) => void
  disabled?: boolean
}

export function RegexTemplateSelector({ onApply, disabled }: Props) {
  const { t } = useI18n()
  const [selected, setSelected] = useState<string>("")
  const [customRegex, setCustomRegex] = useState("")
  const [recentRegexes, setRecentRegexes] = useState<RecentRegex[]>([])

  useEffect(() => {
    setRecentRegexes(loadRecentRegexes())
  }, [])

  const handleApplyAndSave = useCallback(
    (regex: string) => {
      onApply(regex)
      saveRecentRegex(regex)
      setRecentRegexes(loadRecentRegexes())
    },
    [onApply],
  )

  const handleSelect = (templateId: string) => {
    setSelected(templateId)
    // Recent regex items have id starting with "recent:"
    if (templateId.startsWith("recent:")) {
      const regex = templateId.slice("recent:".length)
      setCustomRegex(regex)
      handleApplyAndSave(regex)
      return
    }
    // Auto-apply for non-custom templates
    if (templateId !== "custom") {
      const tpl = TEMPLATES.find((t) => t.id === templateId)
      if (tpl) onApply(tpl.regex)
    }
  }

  const handleApplyCustom = () => {
    const regex = customRegex.trim()
    if (regex) {
      handleApplyAndSave(regex)
    }
  }

  return (
    <div className="space-y-2">
      <Label className="text-xs">{t("shared.regexTemplate.label")}</Label>
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <Select
            value={selected}
            onValueChange={handleSelect}
            disabled={disabled}
          >
            <SelectTrigger className="h-8 text-sm">
              <SelectValue placeholder={t("shared.regexTemplate.placeholder")} />
            </SelectTrigger>
            <SelectContent>
              {recentRegexes.length > 0 && (
                <SelectGroup>
                  <SelectLabel className="flex items-center gap-1 text-xs">
                    <Clock className="h-3 w-3" />
                    {t("shared.regexTemplate.recent")}
                  </SelectLabel>
                  {recentRegexes.map((r) => (
                    <SelectItem
                      key={`recent:${r.regex}`}
                      value={`recent:${r.regex}`}
                    >
                      <span className="font-mono text-xs">
                        {r.regex.length > 30
                          ? r.regex.slice(0, 30) + "…"
                          : r.regex}
                      </span>
                      <span className="ml-2 text-muted-foreground">
                        {formatTimeAgo(r.usedAt, t)}
                      </span>
                    </SelectItem>
                  ))}
                </SelectGroup>
              )}
              <SelectGroup>
                {recentRegexes.length > 0 && (
                  <SelectLabel className="text-xs">{t("shared.regexTemplate.presets")}</SelectLabel>
                )}
                {TEMPLATES.map((template) => (
                  <SelectItem key={template.id} value={template.id}>
                    {t(template.labelKey as TranslationKey)}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>
        {selected === "custom" && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleApplyCustom}
            disabled={disabled || !customRegex.trim()}
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            {t("common.apply")}
          </Button>
        )}
      </div>
      {selected === "custom" && (
        <div className="space-y-1">
          <Input
            value={customRegex}
            onChange={(e) => setCustomRegex(e.target.value)}
            placeholder={t("shared.regexTemplate.examplePlaceholder")}
            className="h-8 font-mono text-sm"
            disabled={disabled}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                handleApplyCustom()
              }
            }}
          />
          <p className="text-xs text-muted-foreground">
            {t("shared.regexTemplate.multilineHint")}
          </p>
        </div>
      )}
    </div>
  )
}

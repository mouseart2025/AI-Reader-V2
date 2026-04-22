import { useCallback, useEffect, useMemo, useState } from "react"
import { useParams } from "react-router-dom"
import { apiFetch } from "@/api/client"
import { Button } from "@/components/ui/button"
import { useI18n, type TranslationKey } from "@/i18n"
import { cn } from "@/lib/utils"
import { trackEvent } from "@/lib/tracker"

interface ConflictItem {
  type: string
  severity: string
  description: string
  chapters: number[]
  entity: string
  details: Record<string, unknown>
}

interface ConflictsResponse {
  conflicts: ConflictItem[]
  total: number
  severity_counts: Record<string, number>
  type_counts: Record<string, number>
}

type SeverityLevel = "high" | "medium" | "info"

const SEVERITY_CONFIG: Record<SeverityLevel, { value: string; labelKey: TranslationKey; color: string; bg: string }> = {
  high: { value: "严重", labelKey: "conflicts.severity.high", color: "text-red-600", bg: "bg-red-50 dark:bg-red-950/30" },
  medium: { value: "一般", labelKey: "conflicts.severity.medium", color: "text-amber-600", bg: "bg-amber-50 dark:bg-amber-950/30" },
  info: { value: "提示", labelKey: "conflicts.severity.info", color: "text-blue-600", bg: "bg-blue-50 dark:bg-blue-950/30" },
}

const TYPE_CONFIG: Record<string, { labelKey: TranslationKey; icon: string }> = {
  ability: { labelKey: "conflicts.type.ability", icon: "⚡" },
  relation: { labelKey: "conflicts.type.relation", icon: "🔗" },
  location: { labelKey: "conflicts.type.location", icon: "📍" },
  death: { labelKey: "conflicts.type.death", icon: "💀" },
  direction: { labelKey: "conflicts.type.direction", icon: "🧭" },
  distance: { labelKey: "conflicts.type.distance", icon: "📏" },
}

type SeverityFilter = "all" | SeverityLevel

function severityConfigByValue(value: string) {
  return Object.values(SEVERITY_CONFIG).find((config) => config.value === value) ?? SEVERITY_CONFIG.info
}

export default function ConflictsPage() {
  const { t } = useI18n()
  const { novelId } = useParams<{ novelId: string }>()

  const [data, setData] = useState<ConflictsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>("all")
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)

  useEffect(() => {
    if (!novelId) return
    let cancelled = false
    setLoading(true)
    setError(null)
    trackEvent("view_conflicts")

    apiFetch<ConflictsResponse>(`/novels/${novelId}/conflicts`)
      .then((res) => {
        if (!cancelled) setData(res)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : t("conflicts.loadFailed"))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [novelId, t])

  const filteredConflicts = useMemo(() => {
    if (!data) return []
    if (severityFilter === "all") return data.conflicts
    return data.conflicts.filter((c) => c.severity === SEVERITY_CONFIG[severityFilter].value)
  }, [data, severityFilter])

  const toggleExpand = useCallback((idx: number) => {
    setExpandedIdx((prev) => (prev === idx ? null : idx))
  }, [])

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-muted-foreground">{t("conflicts.loading")}</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-red-500">{error}</p>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-auto">
      <div className="max-w-3xl mx-auto p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-medium">{t("conflicts.title")}</h1>
          <span className="text-xs text-muted-foreground">
            {t("conflicts.count", { count: data?.total ?? 0 })}
          </span>
        </div>

        {/* Summary badges */}
        {data && data.total > 0 && (
          <div className="flex items-center gap-3">
            {Object.entries(data.severity_counts)
              .filter(([, count]) => count > 0)
              .map(([sev, count]) => {
                const cfg = severityConfigByValue(sev)
                return cfg ? (
                  <span
                    key={sev}
                    className={cn("text-xs px-2 py-1 rounded", cfg.bg, cfg.color)}
                  >
                    {t(cfg.labelKey)}: {count}
                  </span>
                ) : null
              })}
          </div>
        )}

        {/* Severity filter */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">{t("conflicts.filter")}</span>
          {(["all", "high", "medium", "info"] as SeverityFilter[]).map((f) => (
            <Button
              key={f}
              variant={severityFilter === f ? "default" : "outline"}
              size="xs"
              onClick={() => setSeverityFilter(f)}
            >
              {f === "all" ? t("conflicts.filterAll") : t(SEVERITY_CONFIG[f].labelKey)}
            </Button>
          ))}
        </div>

        {/* Conflict list */}
        {filteredConflicts.length === 0 ? (
          <div className="text-center py-12">
            <p className="text-muted-foreground">
              {data?.total === 0 ? t("conflicts.empty") : t("conflicts.noFilteredResults")}
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {filteredConflicts.map((conflict, idx) => {
              const sevCfg = severityConfigByValue(conflict.severity)
              const typeCfg = TYPE_CONFIG[conflict.type]
              const isExpanded = expandedIdx === idx

              return (
                <div
                  key={idx}
                  className={cn(
                    "border rounded-lg transition-colors cursor-pointer",
                    isExpanded ? "bg-muted/30" : "hover:bg-muted/20",
                  )}
                  onClick={() => toggleExpand(idx)}
                >
                  <div className="flex items-start gap-3 p-3">
                    {/* Type icon */}
                    <span className="text-base flex-shrink-0 mt-0.5">
                      {typeCfg?.icon ?? "?"}
                    </span>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span
                          className={cn(
                            "text-[10px] px-1.5 py-0.5 rounded font-medium",
                            sevCfg.bg, sevCfg.color,
                          )}
                        >
                          {t(sevCfg.labelKey)}
                        </span>
                        <span className="text-[10px] text-muted-foreground px-1.5 py-0.5 rounded bg-muted">
                          {typeCfg ? t(typeCfg.labelKey) : conflict.type}
                        </span>
                        <span className="text-[10px] text-muted-foreground ml-auto flex-shrink-0">
                          {t("conflicts.chapterList", { chapters: conflict.chapters.join("/") })}
                        </span>
                      </div>
                      <p className="text-sm leading-relaxed">{conflict.description}</p>
                    </div>

                    {/* Expand indicator */}
                    <span className="text-muted-foreground text-xs mt-1 flex-shrink-0">
                      {isExpanded ? "▾" : "▸"}
                    </span>
                  </div>

                  {/* Expanded details */}
                  {isExpanded && Object.keys(conflict.details).length > 0 && (
                    <div className="px-3 pb-3 pt-0 ml-8">
                      <div className="text-xs text-muted-foreground space-y-1 border-t pt-2">
                        <p><span className="font-medium">{t("conflicts.involvedEntity")}</span> {conflict.entity}</p>
                        {Object.entries(conflict.details).map(([key, val]) => (
                          <p key={key}>
                            <span className="font-medium">{key}:</span>{" "}
                            {Array.isArray(val) ? val.join(" → ") : String(val)}
                          </p>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

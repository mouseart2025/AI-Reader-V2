import { useState } from "react"
import { ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"
import type { QualityMetrics } from "@/api/types"
import { useI18n, type TranslationKey } from "@/i18n"

const SPATIAL_TYPE_LABEL_KEYS: Record<string, TranslationKey> = {
  direction: "visualization.spatial.type.direction",
  distance: "visualization.spatial.type.distance",
  contains: "visualization.spatial.type.contains",
  adjacent: "visualization.spatial.type.adjacent",
  separated_by: "visualization.spatial.type.separatedBy",
  in_between: "visualization.spatial.type.inBetween",
  travel_path: "visualization.spatial.type.travelPath",
  cluster: "visualization.spatial.type.cluster",
  terrain: "visualization.spatial.type.terrain",
  relative_scale: "visualization.spatial.type.relativeScale",
  on_coast: "visualization.spatial.type.onCoast",
}

function rateColor(rate: number): string {
  if (rate >= 0.8) return "text-green-600 dark:text-green-400"
  if (rate >= 0.5) return "text-amber-600 dark:text-amber-400"
  return "text-red-600 dark:text-red-400"
}

interface Props {
  qualityMetrics: QualityMetrics | null | undefined
}

export function MapQualityPanel({ qualityMetrics }: Props) {
  const { t } = useI18n()
  const [expanded, setExpanded] = useState(false)

  if (!qualityMetrics || qualityMetrics.total_constraints === 0) {
    return (
      <div className="rounded-lg border bg-background/90 px-2.5 py-2 w-44">
        <span className="text-[11px] text-muted-foreground">{t("visualization.mapQuality.noConstraints")}</span>
      </div>
    )
  }

  const { satisfied_constraints, total_constraints, total_satisfaction, by_type } = qualityMetrics
  const pct = Math.round(total_satisfaction * 100)
  const entries = Object.entries(by_type).filter(([, v]) => v.total > 0)

  return (
    <div className="rounded-lg border bg-background/90 px-2.5 py-2 w-44">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-1 text-[11px]"
      >
        <span className="text-muted-foreground">
          <span className={rateColor(total_satisfaction)}>{satisfied_constraints}</span>
          /{total_constraints} {t("visualization.mapQuality.satisfied")}
          <span className={cn("ml-1", rateColor(total_satisfaction))}>({pct}%)</span>
        </span>
        <ChevronDown
          className={cn(
            "h-3 w-3 text-muted-foreground/60 transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>

      {expanded && (
        <div className="mt-1.5 space-y-0.5 border-t pt-1.5">
          {entries.map(([type, v]) => {
            const label = SPATIAL_TYPE_LABEL_KEYS[type] ? t(SPATIAL_TYPE_LABEL_KEYS[type]) : type
            const typePct = Math.round(v.satisfaction * 100)
            return (
              <div key={type} className="flex items-center justify-between text-[10px] text-muted-foreground">
                <span>{label}</span>
                <span className={rateColor(v.satisfaction)}>
                  {v.satisfied}/{v.total} ({typePct}%)
                </span>
              </div>
            )
          })}
          {qualityMetrics.quality_baseline && (
            <div className="border-t pt-1 mt-1">
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted-foreground">{t("visualization.mapQuality.vsLast")}</span>
                <span className={qualityMetrics.quality_baseline.satisfaction_delta >= 0
                  ? "text-green-600 dark:text-green-400"
                  : "text-red-600 dark:text-red-400"}>
                  {qualityMetrics.quality_baseline.satisfaction_delta >= 0 ? "+" : ""}
                  {Math.round(qualityMetrics.quality_baseline.satisfaction_delta * 100)}%
                </span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

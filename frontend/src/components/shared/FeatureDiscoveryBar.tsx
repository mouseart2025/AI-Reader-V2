import { Button } from "@/components/ui/button"
import { useI18n, type TranslationKey } from "@/i18n"
import { useTourStore } from "@/stores/tourStore"

const FEATURES = [
  { labelKey: "nav.relationGraph", emoji: "📊", path: "/graph" },
  { labelKey: "nav.map", emoji: "🗺️", path: "/map" },
  { labelKey: "nav.timeline", emoji: "📅", path: "/timeline" },
  { labelKey: "nav.encyclopedia", emoji: "📤", path: "/encyclopedia" },
] as const

interface FeatureDiscoveryBarProps {
  novelId: string
  onNavigate: (path: string) => void
}

export function FeatureDiscoveryBar({ novelId, onNavigate }: FeatureDiscoveryBarProps) {
  const { t } = useI18n()
  const { currentStep, dismissed } = useTourStore()
  const tourDone = currentStep === -1

  if (tourDone || dismissed) {
    // Tour completed — show completion message
    return (
      <div className="border-t bg-muted/50 px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-center justify-between">
          <span className="text-sm text-muted-foreground">
            {t("shared.featureDiscovery.completed")}
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="default"
              className="h-7 text-xs"
              onClick={() => onNavigate("/")}
            >
              {t("shared.featureDiscovery.uploadOwnNovel")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => onNavigate(`/graph/${novelId}`)}
            >
              {t("shared.featureDiscovery.continueExploring")}
            </Button>
          </div>
        </div>
      </div>
    )
  }

  // Default — show feature buttons
  return (
    <div className="border-t bg-muted/50 px-4 py-2">
      <div className="mx-auto flex max-w-3xl items-center justify-center gap-2">
        {FEATURES.map((f) => (
          <Button
            key={f.path}
            variant="ghost"
            size="sm"
            className="h-8 gap-1.5 text-xs"
            onClick={() => onNavigate(`${f.path}/${novelId}`)}
          >
            <span>{f.emoji}</span>
            {t(f.labelKey as TranslationKey)}
          </Button>
        ))}
      </div>
    </div>
  )
}

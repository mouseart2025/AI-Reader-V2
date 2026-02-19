import { Button } from "@/components/ui/button"
import { useTourStore } from "@/stores/tourStore"

const FEATURES = [
  { label: "关系图", emoji: "📊", path: "/graph" },
  { label: "世界地图", emoji: "🗺️", path: "/map" },
  { label: "时间线", emoji: "📅", path: "/timeline" },
  { label: "百科", emoji: "📤", path: "/encyclopedia" },
] as const

interface FeatureDiscoveryBarProps {
  novelId: string
  onNavigate: (path: string) => void
}

export function FeatureDiscoveryBar({ novelId, onNavigate }: FeatureDiscoveryBarProps) {
  const { currentStep, dismissed } = useTourStore()
  const tourDone = currentStep === -1

  if (tourDone || dismissed) {
    // Tour completed — show completion message
    return (
      <div className="border-t bg-muted/50 px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-center justify-between">
          <span className="text-sm text-muted-foreground">
            ✅ 体验完成！
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="default"
              className="h-7 text-xs"
              onClick={() => onNavigate("/")}
            >
              上传我自己的小说
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => onNavigate(`/graph/${novelId}`)}
            >
              继续探索
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
            {f.label}
          </Button>
        ))}
      </div>
    </div>
  )
}

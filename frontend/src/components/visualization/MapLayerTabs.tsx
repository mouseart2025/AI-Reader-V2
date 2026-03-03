import { cn } from "@/lib/utils"
import type { MapLayerInfo } from "@/api/types"

interface MapLayerTabsProps {
  layers: MapLayerInfo[]
  activeLayerId: string
  onLayerChange: (layerId: string) => void
}

export function MapLayerTabs({
  layers,
  activeLayerId,
  onLayerChange,
}: MapLayerTabsProps) {
  // Only show layers with >= 2 locations (non-merged)
  const mainLayers = layers.filter((l) => !l.merged)

  // Hide tabs if only one main layer
  if (mainLayers.length <= 1) return null

  return (
    <div className="flex flex-wrap gap-1 px-3 py-1.5 border-b bg-background/95 items-center">
      {mainLayers.map((layer) => {
        const isActive = layer.layer_id === activeLayerId
        const isUnlocked =
          layer.location_count > 0 || layer.layer_id === "overworld"

        return (
          <button
            key={layer.layer_id}
            className={cn(
              "px-3 py-1 rounded-md text-xs whitespace-nowrap transition-colors",
              isActive
                ? "bg-primary text-primary-foreground font-medium"
                : isUnlocked
                  ? "hover:bg-muted/50 text-foreground"
                  : "text-muted-foreground/40 cursor-not-allowed",
            )}
            onClick={() => isUnlocked && onLayerChange(layer.layer_id)}
            disabled={!isUnlocked}
          >
            {layer.name}
            {layer.location_count > 0 && (
              <span
                className={cn(
                  "ml-1 tabular-nums",
                  isActive
                    ? "text-primary-foreground/70"
                    : "text-muted-foreground",
                )}
              >
                {layer.location_count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

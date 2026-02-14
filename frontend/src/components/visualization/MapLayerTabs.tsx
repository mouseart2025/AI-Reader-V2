import { useState, useRef, useEffect } from "react"
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
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropdownOpen) return
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [dropdownOpen])

  const mainLayers = layers.filter((l) => !l.merged)
  const mergedLayers = layers.filter((l) => l.merged)

  // Hide tabs if only one main layer and no merged
  if (mainLayers.length <= 1 && mergedLayers.length === 0) return null

  return (
    <div className="flex gap-1 px-3 py-1.5 border-b bg-background/95 overflow-x-auto items-center">
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

      {mergedLayers.length > 0 && (
        <div className="relative" ref={dropdownRef}>
          <button
            className={cn(
              "px-3 py-1 rounded-md text-xs whitespace-nowrap transition-colors",
              mergedLayers.some((l) => l.layer_id === activeLayerId)
                ? "bg-primary text-primary-foreground font-medium"
                : "hover:bg-muted/50 text-muted-foreground",
            )}
            onClick={() => setDropdownOpen(!dropdownOpen)}
          >
            更多 ({mergedLayers.length})
          </button>
          {dropdownOpen && (
            <div className="absolute top-full left-0 mt-1 z-50 min-w-[140px] rounded-md border bg-popover p-1 shadow-md">
              {mergedLayers.map((layer) => {
                const isActive = layer.layer_id === activeLayerId
                return (
                  <button
                    key={layer.layer_id}
                    className={cn(
                      "w-full text-left px-2 py-1.5 rounded-sm text-xs transition-colors",
                      isActive
                        ? "bg-accent text-accent-foreground font-medium"
                        : "hover:bg-accent/50 text-foreground",
                    )}
                    onClick={() => {
                      onLayerChange(layer.layer_id)
                      setDropdownOpen(false)
                    }}
                  >
                    {layer.name}
                    {layer.location_count > 0 && (
                      <span className="ml-1 text-muted-foreground tabular-nums">
                        {layer.location_count}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

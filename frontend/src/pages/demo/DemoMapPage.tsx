/**
 * DemoMapPage — interactive map using NovelMap with static demo data.
 * Renders locations with layout coordinates, supports zoom/pan/click.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useDemoData } from "@/app/DemoContext"
import { NovelMap, type NovelMapHandle } from "@/components/visualization/NovelMap"
import { useEntityCardStore } from "@/stores/entityCardStore"
import type { MapData } from "@/api/types"

export default function DemoMapPage() {
  const { data } = useDemoData()
  const mapData = data.map as unknown as MapData

  const [minMentions, setMinMentions] = useState(1)
  const [debouncedMinMentions, setDebouncedMinMentions] = useState(1)
  const novelMapRef = useRef<NovelMapHandle>(null)

  useEffect(() => {
    const t = setTimeout(() => setDebouncedMinMentions(minMentions), 150)
    return () => clearTimeout(t)
  }, [minMentions])

  // Filter locations by mention count
  const { filteredLocations, filteredLayout } = useMemo(() => {
    if (!mapData?.locations) return { filteredLocations: [], filteredLayout: [] }
    const names = new Set(
      mapData.locations
        .filter((loc) => (loc.mention_count ?? 0) >= debouncedMinMentions)
        .map((loc) => loc.name),
    )
    return {
      filteredLocations: mapData.locations.filter((loc) => names.has(loc.name)),
      filteredLayout: (mapData.layout ?? []).filter((item) => names.has(item.name)),
    }
  }, [mapData, debouncedMinMentions])

  const visibleNames = useMemo(
    () => new Set(filteredLocations.map((l) => l.name)),
    [filteredLocations],
  )

  const maxMentionCount = useMemo(
    () => mapData?.max_mention_count ?? Math.max(...(mapData?.locations ?? []).map((l) => l.mention_count ?? 0), 1),
    [mapData],
  )

  const openCard = useEntityCardStore((s) => s.openCard)
  const handleLocationClick = useCallback((name: string) => {
    openCard(name, "location")
  }, [openCard])

  if (!mapData || !mapData.locations?.length) {
    return <div className="flex h-full items-center justify-center text-gray-400">暂无地图数据</div>
  }

  return (
    <div className="flex h-full flex-col">
      {/* Filter bar */}
      <div className="flex items-center gap-3 border-b bg-white/80 px-4 py-2">
        <span className="text-xs text-gray-500">
          {filteredLocations.length} / {mapData.locations.length} 地点
        </span>
        <label className="flex items-center gap-1 text-xs">
          <span className="text-gray-500">提及≥</span>
          <input
            type="range"
            min={1}
            max={Math.min(50, maxMentionCount)}
            value={minMentions}
            onChange={(e) => setMinMentions(Number(e.target.value))}
            className="w-24"
          />
          <span className="w-6 text-center font-mono">{minMentions}</span>
        </label>
        <span className="text-xs text-gray-400">
          模式: {mapData.layout_mode ?? "hierarchy"}
        </span>
      </div>

      {/* Map */}
      <div className="flex-1 overflow-hidden">
        <NovelMap
          ref={novelMapRef}
          locations={filteredLocations}
          layout={filteredLayout}
          allLocations={mapData.locations}
          allLayout={mapData.layout}
          layoutMode={mapData.layout_mode ?? "hierarchy"}
          terrainUrl={mapData.terrain_url ?? null}
          visibleLocationNames={visibleNames}
          revealedLocationNames={new Set(mapData.revealed_location_names ?? [])}
          regionBoundaries={mapData.region_boundaries}
          portals={mapData.portals}
          rivers={mapData.rivers}
          canvasSize={mapData.canvas_size}
          spatialScale={mapData.spatial_scale ?? undefined}
          locationConflicts={mapData.location_conflicts}
          onLocationClick={handleLocationClick}
        />
      </div>
    </div>
  )
}

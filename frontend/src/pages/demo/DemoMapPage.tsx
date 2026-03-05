/**
 * DemoMapPage — interactive map using NovelMap with static demo data.
 * Renders locations with layout coordinates, supports zoom/pan/click.
 * Matches production MapPage: applies suggested_min_mentions + tier collapse.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useDemoData } from "@/app/DemoContext"
import { NovelMap, type NovelMapHandle } from "@/components/visualization/NovelMap"
import { useEntityCardStore } from "@/stores/entityCardStore"
import type { MapData } from "@/api/types"

const COLLAPSED_TIERS = new Set(["site", "building"])

export default function DemoMapPage() {
  const { data } = useDemoData()
  const mapData = data.map as unknown as MapData

  // Apply backend-suggested mention filter on mount
  const suggested = mapData?.suggested_min_mentions ?? 1
  const [minMentions, setMinMentions] = useState(suggested)
  const [debouncedMinMentions, setDebouncedMinMentions] = useState(suggested)
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set())
  const novelMapRef = useRef<NovelMapHandle>(null)

  // Re-apply when mapData changes (e.g. novel switch)
  useEffect(() => {
    if (mapData?.suggested_min_mentions) {
      setMinMentions(mapData.suggested_min_mentions)
      setDebouncedMinMentions(mapData.suggested_min_mentions)
    }
  }, [mapData?.suggested_min_mentions])

  useEffect(() => {
    const t = setTimeout(() => setDebouncedMinMentions(minMentions), 150)
    return () => clearTimeout(t)
  }, [minMentions])

  const locations = mapData?.locations ?? []
  const layout = mapData?.layout ?? []

  // Two-stage filtering: mention count → tier collapse
  const { filteredLocations, collapsedChildCount } = useMemo(() => {
    if (!locations.length) return { filteredLocations: [], collapsedChildCount: new Map<string, number>() }

    // Step 1: mention count filter
    const afterMention = debouncedMinMentions <= 1
      ? locations
      : locations.filter((l) => l.mention_count >= debouncedMinMentions)

    // Step 2: tier collapse — hide site/building unless parent is expanded
    const result: typeof locations = []
    const childCount = new Map<string, number>()

    for (const loc of afterMention) {
      const tier = loc.tier ?? "city"
      if (COLLAPSED_TIERS.has(tier) && loc.parent && !expandedNodes.has(loc.parent)) {
        childCount.set(loc.parent, (childCount.get(loc.parent) ?? 0) + 1)
      } else {
        result.push(loc)
      }
    }
    return { filteredLocations: result, collapsedChildCount: childCount }
  }, [locations, debouncedMinMentions, expandedNodes])

  const filteredLayout = useMemo(() => {
    const nameSet = new Set(filteredLocations.map((l) => l.name))
    return layout.filter((item) => item.is_portal || nameSet.has(item.name))
  }, [layout, filteredLocations])

  const visibleNames = useMemo(
    () => new Set(filteredLocations.map((l) => l.name)),
    [filteredLocations],
  )

  const maxMentionCount = useMemo(
    () => mapData?.max_mention_count ?? Math.max(...locations.map((l) => l.mention_count ?? 0), 1),
    [mapData, locations],
  )

  const openCard = useEntityCardStore((s) => s.openCard)
  const handleLocationClick = useCallback((name: string) => {
    openCard(name, "location")
  }, [openCard])

  const handleToggleExpand = useCallback((name: string) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])

  const handleExpandAll = useCallback(() => {
    const parents = new Set<string>()
    for (const loc of locations) {
      const tier = loc.tier ?? "city"
      if (COLLAPSED_TIERS.has(tier) && loc.parent) {
        parents.add(loc.parent)
      }
    }
    setExpandedNodes(parents)
  }, [locations])

  const handleCollapseAll = useCallback(() => {
    setExpandedNodes(new Set())
  }, [])

  if (!mapData || !locations.length) {
    return <div className="flex h-full items-center justify-center bg-slate-950 text-slate-500">暂无地图数据</div>
  }

  return (
    <div className="flex h-full flex-col bg-slate-950">
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/80 px-4 py-2">
        <span className="text-xs text-slate-400">
          {filteredLocations.length} / {locations.length} 地点
        </span>
        <label className="flex items-center gap-1 text-xs">
          <span className="text-slate-400">提及≥</span>
          <input
            type="range"
            min={1}
            max={Math.min(50, maxMentionCount)}
            value={minMentions}
            onChange={(e) => setMinMentions(Number(e.target.value))}
            className="w-24 accent-blue-500"
          />
          <span className="w-6 text-center font-mono text-slate-300">{minMentions}</span>
        </label>

        {/* Expand / Collapse all (when there are collapsed children) */}
        {collapsedChildCount.size > 0 && (
          <button
            onClick={expandedNodes.size > 0 ? handleCollapseAll : handleExpandAll}
            className="rounded border border-slate-700 bg-slate-800/80 px-2 py-1 text-[11px] text-slate-400 hover:text-white transition-colors"
          >
            {expandedNodes.size > 0 ? "全部折叠" : `展开子地点`}
          </button>
        )}

        <span className="text-xs text-slate-500">
          模式: {mapData.layout_mode ?? "hierarchy"}
        </span>
      </div>

      {/* Map */}
      <div className="flex-1 overflow-hidden">
        <NovelMap
          ref={novelMapRef}
          locations={filteredLocations}
          layout={filteredLayout}
          allLocations={locations}
          allLayout={layout}
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
          collapsedChildCount={collapsedChildCount}
          onLocationClick={handleLocationClick}
          onToggleExpand={handleToggleExpand}
        />
      </div>
    </div>
  )
}

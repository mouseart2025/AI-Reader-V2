/**
 * DemoMapPage — interactive map mirroring the desktop MapPage feature surface.
 * Renders locations via NovelMap (fantasy/hierarchy) or GeoMap (geographic).
 * Includes layer tabs, mention filter, tier collapse, trajectory playback,
 * geography panel, conflict markers, legend, and PNG export — all using static
 * demo data. Backend-only features (smart rebuild, edit mode, world structure
 * editor, override saving) are omitted in demo mode.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useDemoData } from "@/app/DemoContext"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { useVisualizationFocusStore } from "@/stores/visualizationFocusStore"
import type { MapData, MapLayerInfo } from "@/api/types"
import { NovelMap, type NovelMapHandle } from "@/components/visualization/NovelMap"
import { GeoMap } from "@/components/visualization/GeoMap"
import { MapLayerTabs } from "@/components/visualization/MapLayerTabs"
import { GeographyPanel } from "@/components/visualization/GeographyPanel"
import { MapQualityPanel } from "@/components/visualization/MapQualityPanel"
import { Button } from "@/components/ui/button"
import { Download, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { annealLabels, type AnnealItem } from "@/lib/labelAnnealing"

const ICON_LEGEND: { icon: string; label: string }[] = [
  { icon: "capital", label: "都城" },
  { icon: "city", label: "城市" },
  { icon: "town", label: "城镇" },
  { icon: "village", label: "村庄" },
  { icon: "camp", label: "营地" },
  { icon: "mountain", label: "山脉" },
  { icon: "forest", label: "森林" },
  { icon: "water", label: "水域" },
  { icon: "desert", label: "沙漠" },
  { icon: "island", label: "岛屿" },
  { icon: "temple", label: "寺庙" },
  { icon: "palace", label: "宫殿" },
  { icon: "cave", label: "洞穴" },
  { icon: "tower", label: "塔楼" },
  { icon: "gate", label: "关隘" },
  { icon: "portal", label: "传送门" },
  { icon: "ruins", label: "废墟" },
  { icon: "sacred", label: "圣地" },
  { icon: "generic", label: "其他" },
]

const COLLAPSED_TIERS = new Set(["site", "building"])

export default function DemoMapPage() {
  const { data } = useDemoData()
  const mapData = data.map as unknown as MapData

  const openCard = useEntityCardStore((s) => s.openCard)
  const storeFocusLoc = useVisualizationFocusStore((s) => s.focusLocation)
  const storeFocusSource = useVisualizationFocusStore((s) => s.source)
  const setStoreFocusLoc = useVisualizationFocusStore((s) => s.setFocusLocation)

  // Layer state
  const [layers, setLayers] = useState<MapLayerInfo[]>([])
  const [activeLayerId, setActiveLayerId] = useState("overworld")

  // Filter / interaction state
  const [minMentions, setMinMentions] = useState(1)
  const [maxMentionCount, setMaxMentionCount] = useState(1)
  const [debouncedMinMentions, setDebouncedMinMentions] = useState(1)
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set())
  const [legendOpen, setLegendOpen] = useState(false)
  const [showConflicts, setShowConflicts] = useState(false)
  const [rightTab, setRightTab] = useState<"geography" | "trajectory">("geography")
  const [focusLocation, setFocusLocationLocal] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  // Trajectory playback
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [playIndex, setPlayIndex] = useState(0)
  const [playSpeed, setPlaySpeed] = useState(800)
  const playTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  // Export state
  const [exporting, setExporting] = useState(false)
  const [exportProgress, setExportProgress] = useState("")

  const mapHandle = useRef<NovelMapHandle>(null)

  // Apply backend-suggested mention filter on first load + on novel switch
  useEffect(() => {
    if (!mapData) return
    if (mapData.world_structure?.layers) setLayers(mapData.world_structure.layers)
    const layoutNames = new Set((mapData.layout ?? []).map((li) => li.name))
    const layerLocs = layoutNames.size > 0
      ? (mapData.locations ?? []).filter((l) => layoutNames.has(l.name))
      : (mapData.locations ?? [])
    const layerCount = layerLocs.length
    const suggested = mapData.suggested_min_mentions ?? (layerCount > 300 ? 3 : layerCount > 150 ? 2 : 1)
    const maxMC = Math.max(1, ...layerLocs.map((l) => l.mention_count ?? 0))
    setMinMentions(suggested)
    setDebouncedMinMentions(suggested)
    setMaxMentionCount(maxMC)
  }, [mapData])

  // Debounce the mention slider (150ms)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedMinMentions(minMentions), 150)
    return () => clearTimeout(t)
  }, [minMentions])

  // Apply timeline-driven focus (after a short delay so SVG init is done)
  useEffect(() => {
    if (storeFocusLoc && storeFocusSource === "timeline" && mapData) {
      const timer = setTimeout(() => setFocusLocationLocal(storeFocusLoc), 500)
      return () => clearTimeout(timer)
    }
  }, [storeFocusLoc, storeFocusSource, mapData])

  const setFocusLocation = useCallback((name: string | null) => {
    setFocusLocationLocal(name)
    setStoreFocusLoc(name, "map")
  }, [setStoreFocusLoc])

  const locations = mapData?.locations ?? []
  const trajectories = mapData?.trajectories ?? {}
  const layout = mapData?.layout ?? []
  const layoutMode = mapData?.layout_mode ?? "hierarchy"
  const terrainUrl = mapData?.terrain_url ?? null
  const regionBoundaries = mapData?.region_boundaries
  const portals = mapData?.portals

  const activeLayerType = useMemo(() => {
    const layer = layers.find((l) => l.layer_id === activeLayerId)
    return layer?.layer_type ?? "overworld"
  }, [layers, activeLayerId])

  // Scope locations to the layer that owns the current layout
  const layerLocationNames = useMemo(
    () => new Set(layout.map((l) => l.name)),
    [layout],
  )
  const layerLocations = useMemo(
    () => (layout.length > 0 ? locations.filter((l) => layerLocationNames.has(l.name)) : locations),
    [locations, layout, layerLocationNames],
  )

  // mention filter → tier collapse (matches MapPage logic, including "core landmark" exemption)
  const { filteredLocations, collapsedChildCount } = useMemo(() => {
    if (!locations.length) return { filteredLocations: [], collapsedChildCount: new Map<string, number>() }
    const afterMention = debouncedMinMentions <= 1
      ? layerLocations
      : layerLocations.filter((l) => l.mention_count >= debouncedMinMentions)

    const skipCollapse = afterMention.length <= 100
    const result: typeof locations = []
    const childCount = new Map<string, number>()

    for (const loc of afterMention) {
      const tier = loc.tier ?? "city"
      const isCoreLandmark = (loc.mention_count ?? 0) >= 8
      if (!skipCollapse && !isCoreLandmark && COLLAPSED_TIERS.has(tier) && loc.parent && !expandedNodes.has(loc.parent)) {
        childCount.set(loc.parent, (childCount.get(loc.parent) ?? 0) + 1)
      } else {
        result.push(loc)
      }
    }
    return { filteredLocations: result, collapsedChildCount: childCount }
  }, [locations, layerLocations, debouncedMinMentions, expandedNodes])

  const filteredLayout = useMemo(() => {
    const nameSet = new Set(filteredLocations.map((l) => l.name))
    return layout.filter((item) => item.is_portal || nameSet.has(item.name))
  }, [layout, filteredLocations])

  const visibleLocationNames = useMemo(
    () => new Set(filteredLocations.map((l) => l.name)),
    [filteredLocations],
  )
  const revealedLocationNames = useMemo(() => {
    const names = mapData?.revealed_location_names
    if (!names || names.length === 0) return undefined
    return new Set(names)
  }, [mapData?.revealed_location_names])

  const conflictCount = useMemo(() => {
    const cs = mapData?.location_conflicts
    if (!cs?.length) return 0
    return new Set(cs.map((c) => c.entity)).size
  }, [mapData?.location_conflicts])

  const usedIcons = useMemo(() => {
    const icons = new Set<string>()
    for (const loc of locations) icons.add(loc.icon ?? "generic")
    return icons
  }, [locations])

  // ── Trajectory state ───────────────────────────────────────
  const personList = useMemo(
    () => Object.keys(trajectories).sort((a, b) => (trajectories[b]?.length ?? 0) - (trajectories[a]?.length ?? 0)),
    [trajectories],
  )

  const selectedTrajectory = useMemo(
    () => (selectedPerson ? trajectories[selectedPerson] ?? [] : []),
    [selectedPerson, trajectories],
  )

  const visibleTrajectory = useMemo(() => {
    if (!playing && playIndex === 0) return selectedTrajectory
    return selectedTrajectory.slice(0, playIndex + 1)
  }, [selectedTrajectory, playing, playIndex])

  const currentLocation = useMemo(() => {
    if (visibleTrajectory.length === 0) return null
    return visibleTrajectory[visibleTrajectory.length - 1].location
  }, [visibleTrajectory])

  const stayDurations = useMemo(() => {
    const durations = new Map<string, number>()
    for (const traj of selectedTrajectory) {
      durations.set(traj.location, (durations.get(traj.location) ?? 0) + 1)
    }
    return durations
  }, [selectedTrajectory])

  const hasTrajectory = selectedTrajectory.length > 0

  const startPlay = useCallback(() => {
    if (selectedTrajectory.length === 0) return
    setPlayIndex(0)
    setPlaying(true)
  }, [selectedTrajectory])

  const stopPlay = useCallback(() => {
    setPlaying(false)
    if (playTimer.current) {
      clearInterval(playTimer.current)
      playTimer.current = null
    }
  }, [])

  useEffect(() => {
    if (!playing) return
    playTimer.current = setInterval(() => {
      setPlayIndex((prev) => {
        if (prev >= selectedTrajectory.length - 1) {
          setPlaying(false)
          return prev
        }
        return prev + 1
      })
    }, playSpeed)
    return () => {
      if (playTimer.current) clearInterval(playTimer.current)
    }
  }, [playing, selectedTrajectory.length, playSpeed])

  useEffect(() => {
    stopPlay()
    setPlayIndex(0)
  }, [selectedPerson, stopPlay])

  // ── Layer / portal handlers ────────────────────────────────
  const handleLayerChange = useCallback((layerId: string) => {
    setActiveLayerId(layerId)
    setSelectedPerson(null)
  }, [])

  const handlePortalClick = useCallback((targetLayerId: string) => {
    setActiveLayerId(targetLayerId)
    setSelectedPerson(null)
  }, [])

  // ── Tier expand handlers ───────────────────────────────────
  const handleToggleExpand = useCallback((parentName: string) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev)
      if (next.has(parentName)) next.delete(parentName)
      else next.add(parentName)
      return next
    })
  }, [])

  const handleExpandAll = useCallback(() => {
    const parents = new Set<string>()
    for (const loc of locations) {
      if (loc.parent) parents.add(loc.parent)
    }
    setExpandedNodes(parents)
  }, [locations])

  const handleCollapseAll = useCallback(() => {
    setExpandedNodes(new Set())
  }, [])

  // ── Click handlers ─────────────────────────────────────────
  const handleLocationClick = useCallback((name: string) => {
    openCard(name, "location")
  }, [openCard])

  // GeographyPanel click → navigate map to location (fly-to + highlight) without
  // opening entity card, matches MapPage behaviour.
  const handleGeoLocationClick = useCallback((name: string) => {
    setFocusLocation(focusLocation === name ? null : name)
  }, [focusLocation, setFocusLocation])

  // ── Export PNG ─────────────────────────────────────────────
  const handleExport = useCallback(async () => {
    const svgEl = mapHandle.current?.getSvgElement()
    if (!svgEl || exporting) return

    setExporting(true)
    setExportProgress("准备中...")

    try {
      const clone = svgEl.cloneNode(true) as SVGSVGElement
      const viewport = clone.querySelector("#viewport")
      if (viewport) viewport.setAttribute("transform", "")
      clone.querySelectorAll(".location-item").forEach((g) => {
        ;(g as SVGGElement).setAttribute("transform", "")
      })
      const tiers = ["continent", "kingdom", "region", "city", "site", "building"]
      for (const tier of tiers) {
        const group = clone.querySelector(`#locations-${tier}`) as SVGGElement | null
        if (group) {
          group.style.display = ""
          group.style.opacity = "1"
        }
      }
      clone.querySelectorAll(".loc-label").forEach((el) => { (el as SVGElement).style.display = "" })
      clone.querySelectorAll(".loc-hitarea").forEach((el) => el.remove())
      const conflictG = clone.querySelector("#conflict-markers")
      if (conflictG) conflictG.innerHTML = ""
      const focusG = clone.querySelector("#focus-overlay")
      if (focusG) focusG.innerHTML = ""
      const overviewG = clone.querySelector("#overview-dots")
      if (overviewG) overviewG.innerHTML = ""

      const items: AnnealItem[] = []
      clone.querySelectorAll(".location-item").forEach((g) => {
        const name = g.getAttribute("data-name") ?? ""
        const cx = parseFloat(g.getAttribute("data-x") ?? "0")
        const cy = parseFloat(g.getAttribute("data-y") ?? "0")
        if (!name || isNaN(cx) || isNaN(cy)) return
        const label = g.querySelector(".loc-label") as SVGTextElement | null
        if (!label) return
        const fontSize = parseFloat(label.getAttribute("font-size") ?? "12")
        const icon = g.querySelector("use")
        const iconSize = parseFloat(icon?.getAttribute("width") ?? "20")
        items.push({
          name, cx, cy, iconSize, fontSize,
          labelW: name.length * fontSize + 4,
          labelH: fontSize + 4,
        })
      })

      setExportProgress("正在优化标签布局...")
      const placements = await annealLabels(items, (pct) => {
        setExportProgress(`正在优化标签布局... ${Math.round(pct * 100)}%`)
      })

      clone.querySelectorAll(".location-item").forEach((g) => {
        const name = g.getAttribute("data-name") ?? ""
        const label = g.querySelector(".loc-label") as SVGTextElement | null
        if (!label || !name) return
        const cx = parseFloat(g.getAttribute("data-x") ?? "0")
        const cy = parseFloat(g.getAttribute("data-y") ?? "0")
        const placement = placements.get(name)
        if (placement) {
          label.setAttribute("x", String(cx + placement.offsetX))
          label.setAttribute("y", String(cy + placement.offsetY))
          label.setAttribute("text-anchor", placement.textAnchor)
          label.style.display = ""
        } else {
          label.style.display = "none"
        }
      })

      const bgRect = clone.querySelector("#bg")
      const cw = parseFloat(bgRect?.getAttribute("width") ?? "1600")
      const ch = parseFloat(bgRect?.getAttribute("height") ?? "900")
      const pad = 50
      clone.setAttribute("viewBox", `${-pad} ${-pad} ${cw + pad * 2} ${ch + pad * 2}`)
      const outW = (cw + pad * 2) * 3
      const outH = (ch + pad * 2) * 3
      clone.setAttribute("width", String(outW))
      clone.setAttribute("height", String(outH))
      clone.style.width = `${outW}px`
      clone.style.height = `${outH}px`

      const watermark = document.createElementNS("http://www.w3.org/2000/svg", "text")
      watermark.setAttribute("x", String(cw - 10))
      watermark.setAttribute("y", String(ch + pad - 10))
      watermark.setAttribute("text-anchor", "end")
      watermark.setAttribute("font-size", "10")
      watermark.setAttribute("fill", "#999")
      watermark.setAttribute("opacity", "0.5")
      watermark.textContent = "Generated by AI Reader V2"
      viewport?.appendChild(watermark)

      setExportProgress("正在生成图片...")
      const svgStr = new XMLSerializer().serializeToString(clone)
      const blob = new Blob([svgStr], { type: "image/svg+xml;charset=utf-8" })
      const url = URL.createObjectURL(blob)

      const img = new Image()
      img.onload = () => {
        const canvas = document.createElement("canvas")
        canvas.width = outW
        canvas.height = outH
        const ctx = canvas.getContext("2d")!
        ctx.drawImage(img, 0, 0)
        URL.revokeObjectURL(url)

        canvas.toBlob((pngBlob) => {
          if (!pngBlob) {
            setExporting(false)
            setExportProgress("")
            return
          }
          const pngUrl = URL.createObjectURL(pngBlob)
          const a = document.createElement("a")
          a.href = pngUrl
          a.download = `novel-map-${Date.now()}.png`
          document.body.appendChild(a)
          a.click()
          document.body.removeChild(a)
          URL.revokeObjectURL(pngUrl)

          setExporting(false)
          setExportProgress("")
          setToast("地图已导出")
          setTimeout(() => setToast(null), 3000)
        }, "image/png")
      }
      img.onerror = () => {
        URL.revokeObjectURL(url)
        setExporting(false)
        setExportProgress("")
        setToast("导出失败")
        setTimeout(() => setToast(null), 4000)
      }
      img.src = url
    } catch {
      setExporting(false)
      setExportProgress("")
      setToast("导出失败")
      setTimeout(() => setToast(null), 4000)
    }
  }, [exporting])

  if (!mapData || !locations.length) {
    return <div className="flex h-full items-center justify-center text-muted-foreground">暂无地图数据</div>
  }

  return (
    <div className="flex h-full flex-col">
      {/* Layer tabs */}
      <MapLayerTabs
        layers={layers}
        activeLayerId={activeLayerId}
        onLayerChange={handleLayerChange}
      />

      <div className="flex flex-1 min-h-0">
        {/* Main map area */}
        <div className="relative flex-1">
          {/* Hierarchy mode hint */}
          {layoutMode === "hierarchy" && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs text-amber-700 shadow">
              空间约束不足，使用层级布局
            </div>
          )}

          {/* Bottom-left control stack */}
          <div className="absolute bottom-3 left-3 z-10 flex flex-col gap-1.5">
            {layoutMode !== "geographic" && (
              <MapQualityPanel qualityMetrics={mapData.quality_metrics} />
            )}

            {maxMentionCount > 1 && (
              <div className="rounded-lg border bg-background/90 px-2.5 py-2 w-44">
                <div className="flex items-center justify-between mb-1">
                  <label className="text-muted-foreground text-[11px]">
                    最少提及: {minMentions}
                  </label>
                  <span className="text-[10px] text-muted-foreground">
                    {filteredLocations.length} / {layerLocations.length}
                  </span>
                </div>
                <input
                  type="range"
                  min={1}
                  max={Math.min(maxMentionCount, 30)}
                  value={minMentions}
                  onChange={(e) => setMinMentions(Number(e.target.value))}
                  className="w-full h-1 accent-primary"
                />
              </div>
            )}

            {collapsedChildCount.size > 0 && (
              <div className="flex gap-1">
                <button
                  onClick={expandedNodes.size > 0 ? handleCollapseAll : handleExpandAll}
                  className="rounded-lg border bg-background/90 px-2.5 py-1.5 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                >
                  {expandedNodes.size > 0 ? "全部折叠" : "全部展开"}
                </button>
              </div>
            )}

            {conflictCount > 0 && layoutMode !== "geographic" && (
              <button
                onClick={() => setShowConflicts((v) => !v)}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] transition-colors",
                  showConflicts
                    ? "border-red-300 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-400"
                    : "bg-background/90 text-muted-foreground hover:text-foreground",
                )}
              >
                <span className={cn("inline-block size-2 rounded-full", showConflicts ? "bg-red-500" : "bg-muted-foreground/40")} />
                冲突 {conflictCount}
              </button>
            )}

            {layoutMode !== "geographic" && (
              <button
                onClick={handleExport}
                disabled={exporting}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] transition-colors",
                  "bg-background/90 text-muted-foreground hover:text-foreground",
                  exporting && "opacity-60 cursor-not-allowed",
                )}
              >
                {exporting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                {exporting ? exportProgress || "导出中..." : "导出全图"}
              </button>
            )}

            {layoutMode !== "geographic" && (
              <div className="rounded-lg border bg-background/90 p-2">
                <button
                  onClick={() => setLegendOpen((v) => !v)}
                  className="text-muted-foreground flex items-center gap-1 text-[10px] hover:text-foreground"
                >
                  图例 {legendOpen ? "▾" : "▸"}
                </button>
                {legendOpen && (
                  <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5">
                    {ICON_LEGEND.filter((item) => usedIcons.has(item.icon)).map((item) => (
                      <div key={item.icon} className="flex items-center gap-1.5 text-xs">
                        <img
                          src={`${import.meta.env.BASE_URL ?? "/"}map-icons/${item.icon}.svg`}
                          alt={item.label}
                          className="size-3.5 opacity-60"
                          style={{ filter: "invert(0.4)" }}
                        />
                        {item.label}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Toast */}
          {toast && (
            <div className="absolute top-3 right-3 z-20 rounded-lg border bg-background px-3 py-2 text-xs shadow-lg">
              {toast}
            </div>
          )}

          {/* Trajectory current-location bar */}
          {hasTrajectory && currentLocation && (
            <div className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 rounded-full border bg-background/95 px-4 py-1.5 shadow-lg flex items-center gap-2">
              <span className="text-xs">
                {selectedPerson}: {currentLocation}
                {playing && visibleTrajectory.length > 0 && (
                  <span className="text-muted-foreground ml-1">
                    (Ch.{visibleTrajectory[visibleTrajectory.length - 1].chapter})
                  </span>
                )}
              </span>
            </div>
          )}

          {/* The map itself */}
          {layoutMode === "geographic" && mapData?.geo_coords && activeLayerId === "overworld" ? (
            <GeoMap
              locations={filteredLocations}
              geoCoords={mapData.geo_coords}
              trajectoryPoints={visibleTrajectory}
              currentLocation={currentLocation}
              focusLocation={focusLocation}
              editingLocation={null}
              onLocationClick={handleLocationClick}
            />
          ) : (
            <NovelMap
              ref={mapHandle}
              locations={filteredLocations}
              layout={filteredLayout}
              allLocations={locations}
              allLayout={layout}
              layoutMode={layoutMode}
              layerType={activeLayerType}
              terrainUrl={terrainUrl}
              rivers={mapData?.rivers}
              roads={mapData?.roads}
              landmasses={mapData?.landmasses}
              shelves={mapData?.shelves}
              visibleLocationNames={visibleLocationNames}
              revealedLocationNames={revealedLocationNames}
              regionBoundaries={regionBoundaries}
              portals={portals}
              trajectoryPoints={visibleTrajectory}
              allTrajectoryPoints={selectedTrajectory}
              currentLocation={currentLocation}
              stayDurations={stayDurations}
              playing={playing}
              playIndex={playIndex}
              canvasSize={mapData?.canvas_size}
              spatialScale={mapData?.spatial_scale ?? undefined}
              focusLocation={focusLocation}
              locationConflicts={showConflicts ? mapData?.location_conflicts : undefined}
              collapsedChildCount={collapsedChildCount}
              spaceTheme={mapData?.space_theme}
              onLocationClick={handleLocationClick}
              onPortalClick={handlePortalClick}
              onToggleExpand={handleToggleExpand}
            />
          )}
        </div>

        {/* Right panel */}
        <div className="w-80 flex-shrink-0 border-l flex flex-col">
          <div className="p-2 border-b">
            <div className="flex gap-1">
              <Button
                variant={rightTab === "geography" ? "default" : "outline"}
                size="xs"
                onClick={() => setRightTab("geography")}
              >
                地理上下文
              </Button>
              <Button
                variant={rightTab === "trajectory" ? "default" : "outline"}
                size="xs"
                onClick={() => setRightTab("trajectory")}
              >
                人物轨迹
              </Button>
            </div>
          </div>

          <div className="flex-1 overflow-auto">
            {rightTab === "geography" ? (
              <GeographyPanel
                context={mapData?.geography_context ?? []}
                onLocationClick={handleGeoLocationClick}
              />
            ) : (
              <div className="p-3">
                {personList.length === 0 && (
                  <p className="text-muted-foreground text-xs">暂无轨迹数据</p>
                )}

                <div className="space-y-1 mb-3 max-h-48 overflow-auto">
                  {personList.map((person) => (
                    <button
                      key={person}
                      className={cn(
                        "w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-muted/50 transition-colors",
                        selectedPerson === person && "bg-primary/10 text-primary font-medium",
                      )}
                      onClick={() => setSelectedPerson(selectedPerson === person ? null : person)}
                    >
                      <span>{person}</span>
                      <span className="text-muted-foreground ml-1">
                        ({trajectories[person]?.length ?? 0}站)
                      </span>
                    </button>
                  ))}
                </div>

                {selectedPerson && selectedTrajectory.length > 0 && (
                  <div className="border-t pt-3">
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="text-xs font-medium">
                        {selectedPerson} ({selectedTrajectory.length}站)
                      </h4>
                      <div className="flex gap-1 items-center">
                        {playing ? (
                          <Button variant="outline" size="xs" onClick={stopPlay}>停止</Button>
                        ) : (
                          <Button variant="outline" size="xs" onClick={startPlay}>播放</Button>
                        )}
                        <div className="flex border rounded-md overflow-hidden ml-1">
                          {([
                            { label: "×0.5", ms: 1200 },
                            { label: "×1", ms: 800 },
                            { label: "×2", ms: 400 },
                          ] as const).map(({ label, ms }) => (
                            <button
                              key={ms}
                              className={cn(
                                "px-1.5 py-0.5 text-[10px] transition-colors",
                                playSpeed === ms ? "bg-primary text-primary-foreground" : "hover:bg-muted",
                              )}
                              onClick={() => setPlaySpeed(ms)}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>

                    {(playing || playIndex > 0) && (
                      <div className="mb-2">
                        <input
                          type="range"
                          min={0}
                          max={selectedTrajectory.length - 1}
                          value={playIndex}
                          onChange={(e) => {
                            stopPlay()
                            setPlayIndex(Number(e.target.value))
                          }}
                          className="w-full h-1 accent-primary"
                        />
                        <div className="flex justify-between text-[10px] text-muted-foreground">
                          <span>Ch.{selectedTrajectory[0]?.chapter}</span>
                          <span>Ch.{selectedTrajectory[selectedTrajectory.length - 1]?.chapter}</span>
                        </div>
                      </div>
                    )}

                    <div className="space-y-0">
                      {selectedTrajectory.map((point, i) => {
                        const isVisible = i <= playIndex || (!playing && playIndex === 0)
                        const isCurrent = playing && i === playIndex
                        const stays = stayDurations.get(point.location) ?? 0
                        return (
                          <div
                            key={`${i}-${point.chapter}-${point.location}`}
                            className={cn("flex items-start gap-2 transition-opacity", !isVisible && "opacity-20")}
                          >
                            <div className="flex flex-col items-center">
                              <div
                                className={cn(
                                  "rounded-full flex-shrink-0 transition-all",
                                  isCurrent
                                    ? "size-3 bg-amber-500 ring-2 ring-amber-300"
                                    : stays >= 3
                                      ? "size-2.5 bg-primary"
                                      : "size-2 bg-primary",
                                  i === 0 && !isCurrent && "ring-2 ring-primary/30",
                                )}
                              />
                              {i < selectedTrajectory.length - 1 && (
                                <div className="w-px h-5 bg-border" />
                              )}
                            </div>
                            <div className="flex-1 -mt-0.5 pb-1">
                              <span
                                className={cn(
                                  "text-xs hover:underline cursor-pointer",
                                  isCurrent && "font-bold text-amber-600",
                                )}
                                onClick={() => {
                                  handleGeoLocationClick(point.location)
                                  openCard(point.location, "location")
                                }}
                              >
                                {point.location}
                              </span>
                              <span className="text-[10px] text-muted-foreground ml-1">
                                Ch.{point.chapter}
                              </span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

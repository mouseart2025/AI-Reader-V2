import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import { fetchMapData, saveLocationOverride, saveGeoLocationOverride, rebuildHierarchy, applyHierarchyChanges } from "@/api/client"
import type { MapData, MapLayerInfo, HierarchyRebuildResult } from "@/api/types"
import { useChapterRangeStore } from "@/stores/chapterRangeStore"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { VisualizationLayout } from "@/components/visualization/VisualizationLayout"
import { NovelMap, type NovelMapHandle } from "@/components/visualization/NovelMap"
import { GeoMap } from "@/components/visualization/GeoMap"
import { MapLayerTabs } from "@/components/visualization/MapLayerTabs"
import { GeographyPanel } from "@/components/visualization/GeographyPanel"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { WorldStructureEditor } from "@/components/visualization/WorldStructureEditor"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog"
import { Loader2, RefreshCw } from "lucide-react"
import { cn } from "@/lib/utils"
import { trackEvent } from "@/lib/tracker"

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

export default function MapPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const { chapterStart, chapterEnd, setAnalyzedRange } = useChapterRangeStore()
  const openEntityCard = useEntityCardStore((s) => s.openCard)

  const [mapData, setMapData] = useState<MapData | null>(null)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState<string | null>(null)

  // Layer state
  const [layers, setLayers] = useState<MapLayerInfo[]>([])
  const [activeLayerId, setActiveLayerId] = useState("overworld")

  // World structure editor
  const [editorOpen, setEditorOpen] = useState(false)
  const [reloadTrigger, setReloadTrigger] = useState(0)

  // Legend state
  const [legendOpen, setLegendOpen] = useState(false)

  // Right panel tab
  const [rightTab, setRightTab] = useState<"geography" | "trajectory">("geography")

  // Focus location (click-to-navigate: fly to + highlight)
  const [focusLocation, setFocusLocation] = useState<string | null>(null)

  // Editing location (drag-to-reposition on GeoMap)
  const [editingLocation, setEditingLocation] = useState<string | null>(null)

  // Rebuild hierarchy
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildProgress, setRebuildProgress] = useState("")
  const [rebuildResult, setRebuildResult] = useState<HierarchyRebuildResult | null>(null)
  const [selectedChanges, setSelectedChanges] = useState<Set<number>>(new Set())
  const [applying, setApplying] = useState(false)

  // Loading stage animation
  const [loadingStage, setLoadingStage] = useState("加载地图数据...")
  const loadingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const loadingStartRef = useRef<number>(0)

  // Trajectory state
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [playIndex, setPlayIndex] = useState(0)
  const playTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const mapHandle = useRef<NovelMapHandle>(null)

  // Active layer type for background color
  const activeLayerType = useMemo(() => {
    const layer = layers.find((l) => l.layer_id === activeLayerId)
    return layer?.layer_type ?? "overworld"
  }, [layers, activeLayerId])

  // Load data
  useEffect(() => {
    if (!novelId) return
    let cancelled = false
    setLoading(true)
    trackEvent("view_map")

    const layerParam =
      activeLayerId !== "overworld" ? activeLayerId : undefined
    fetchMapData(novelId, chapterStart, chapterEnd, layerParam)
      .then((data) => {
        if (cancelled) return
        if (data.analyzed_range && data.analyzed_range[0] > 0) {
          setAnalyzedRange(data.analyzed_range[0], data.analyzed_range[1])
        }
        if (data.world_structure?.layers) {
          setLayers(data.world_structure.layers)
        }
        setMapData(data)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [novelId, chapterStart, chapterEnd, activeLayerId, setAnalyzedRange, reloadTrigger])

  // Loading stage text animation (time-driven)
  useEffect(() => {
    if (loading) {
      loadingStartRef.current = Date.now()
      setLoadingStage("加载地图数据...")
      loadingTimerRef.current = setInterval(() => {
        const elapsed = Date.now() - loadingStartRef.current
        if (elapsed < 1500) setLoadingStage("加载地图数据...")
        else if (elapsed < 3000) setLoadingStage("聚合地点与轨迹...")
        else if (elapsed < 6000) setLoadingStage("计算地理坐标...")
        else if (elapsed < 10000) setLoadingStage("求解空间布局...")
        else setLoadingStage("优化布局中，请稍候...")
      }, 500)
    } else {
      if (loadingTimerRef.current) {
        clearInterval(loadingTimerRef.current)
        loadingTimerRef.current = null
      }
    }
    return () => {
      if (loadingTimerRef.current) {
        clearInterval(loadingTimerRef.current)
      }
    }
  }, [loading])

  const locations = mapData?.locations ?? []
  const trajectories = mapData?.trajectories ?? {}
  const layout = mapData?.layout ?? []
  const layoutMode = mapData?.layout_mode ?? "hierarchy"
  const terrainUrl = mapData?.terrain_url ?? null
  const regionBoundaries = mapData?.region_boundaries
  const portals = mapData?.portals

  // ── Visible locations (fog of war: active) ────────────────
  const visibleLocationNames = useMemo(() => {
    const set = new Set<string>()
    for (const loc of locations) {
      set.add(loc.name)
    }
    return set
  }, [locations])

  // ── Revealed locations (fog of war: previously seen) ──────
  const revealedLocationNames = useMemo(() => {
    const names = mapData?.revealed_location_names
    if (!names || names.length === 0) return undefined
    return new Set(names)
  }, [mapData?.revealed_location_names])

  // ── Icons used in current data (for legend) ──
  const usedIcons = useMemo(() => {
    const icons = new Set<string>()
    for (const loc of locations) {
      icons.add(loc.icon ?? "generic")
    }
    return icons
  }, [locations])

  // ── Person list sorted by trajectory length ──
  const personList = useMemo(
    () =>
      Object.keys(trajectories).sort(
        (a, b) => (trajectories[b]?.length ?? 0) - (trajectories[a]?.length ?? 0),
      ),
    [trajectories],
  )

  const selectedTrajectory = useMemo(
    () => (selectedPerson ? trajectories[selectedPerson] ?? [] : []),
    [selectedPerson, trajectories],
  )

  // Visible trajectory points (for animation)
  const visibleTrajectory = useMemo(() => {
    if (!playing && playIndex === 0) return selectedTrajectory
    return selectedTrajectory.slice(0, playIndex + 1)
  }, [selectedTrajectory, playing, playIndex])

  // Current location during playback
  const currentLocation = useMemo(() => {
    if (visibleTrajectory.length === 0) return null
    return visibleTrajectory[visibleTrajectory.length - 1].location
  }, [visibleTrajectory])

  // Stay durations
  const stayDurations = useMemo(() => {
    const durations = new Map<string, number>()
    for (const traj of selectedTrajectory) {
      durations.set(traj.location, (durations.get(traj.location) ?? 0) + 1)
    }
    return durations
  }, [selectedTrajectory])

  const hasTrajectory = selectedTrajectory.length > 0

  // ── Layer tab handler ──
  const handleLayerChange = useCallback(
    (layerId: string) => {
      setActiveLayerId(layerId)
      setSelectedPerson(null)
    },
    [],
  )

  // ── Portal click → switch layer tab ──
  const handlePortalClick = useCallback(
    (targetLayerId: string) => {
      setActiveLayerId(targetLayerId)
      setSelectedPerson(null)
    },
    [],
  )

  // ── Animation controls ──
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
    }, 800)
    return () => {
      if (playTimer.current) clearInterval(playTimer.current)
    }
  }, [playing, selectedTrajectory.length])

  useEffect(() => {
    stopPlay()
    setPlayIndex(0)
  }, [selectedPerson, stopPlay])

  // ── Handlers ──
  const handleLocationClick = useCallback(
    (name: string) => {
      openEntityCard(name, "location")
    },
    [openEntityCard],
  )

  // Navigate map to a location (fly to + highlight, no entity card)
  const handleGeoLocationClick = useCallback(
    (name: string) => {
      setFocusLocation((prev) => (prev === name ? null : name))
    },
    [],
  )

  // Enter edit mode for a location (crosshair + drag)
  const handleEditLocation = useCallback((name: string) => {
    setEditingLocation(name)
    setFocusLocation(null)
  }, [])

  // Handle drag end: save new lat/lng and exit edit mode
  const handleEditDragEnd = useCallback(
    (name: string, lat: number, lng: number) => {
      if (!novelId) return
      saveGeoLocationOverride(novelId, name, lat, lng).then(() => {
        setToast(`「${name}」位置已更新`)
        setTimeout(() => setToast(null), 3000)
        // Update local geo_coords immediately for visual feedback
        setMapData((prev) => {
          if (!prev?.geo_coords) return prev
          return {
            ...prev,
            geo_coords: { ...prev.geo_coords, [name]: { lat, lng } },
          }
        })
      })
      setEditingLocation(null)
    },
    [novelId],
  )

  const handleEditCancel = useCallback(() => {
    setEditingLocation(null)
  }, [])

  const handleDragEnd = useCallback(
    (name: string, x: number, y: number) => {
      if (!novelId) return
      saveLocationOverride(novelId, name, x, y).then(() => {
        setToast("位置已保存，下次刷新地图将以此为锚定")
        setTimeout(() => setToast(null), 3000)
      })
    },
    [novelId],
  )

  return (
    <VisualizationLayout>
      <div className="flex h-full flex-col">
        {/* Layer tabs */}
        <MapLayerTabs
          layers={layers}
          activeLayerId={activeLayerId}
          onLayerChange={handleLayerChange}
        />

        <div className="flex flex-1 min-h-0">
        {/* Main: D3+SVG map */}
        <div className="relative flex-1">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60">
              <div className="flex flex-col items-center gap-2">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
                <p className="text-muted-foreground text-sm">{loadingStage}</p>
              </div>
            </div>
          )}

          {!loading && locations.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center">
              <p className="text-muted-foreground">暂无地点数据</p>
            </div>
          )}

          {/* Hierarchy mode hint */}
          {!loading && layoutMode === "hierarchy" && locations.length > 0 && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs text-amber-700 shadow">
              空间约束不足，使用层级布局
            </div>
          )}

          {/* Legend (hide in geographic mode — icons are fantasy-specific) */}
          {layoutMode !== "geographic" && <div className="absolute bottom-10 left-3 z-10 rounded-lg border bg-background/90 p-2">
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
                      src={`/map-icons/${item.icon}.svg`}
                      alt={item.label}
                      className="size-3.5 opacity-60"
                      style={{ filter: "invert(0.4)" }}
                    />
                    {item.label}
                  </div>
                ))}
              </div>
            )}
          </div>}

          {/* Toast / Progress */}
          {(toast || (rebuilding && rebuildProgress)) && (
            <div className="absolute top-3 right-3 z-20 rounded-lg border bg-background px-3 py-2 text-xs shadow-lg">
              {rebuilding && rebuildProgress ? (
                <span className="text-blue-600 animate-pulse">{rebuildProgress}</span>
              ) : (
                toast
              )}
            </div>
          )}

          {/* Current trajectory info bar */}
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

          {!loading && locations.length > 0 && (
            layoutMode === "geographic" && mapData?.geo_coords && activeLayerId === "overworld" ? (
              <GeoMap
                locations={locations}
                geoCoords={mapData.geo_coords}
                trajectoryPoints={visibleTrajectory}
                currentLocation={currentLocation}
                focusLocation={focusLocation}
                editingLocation={editingLocation}
                onLocationClick={handleLocationClick}
                onEditLocation={handleEditLocation}
                onEditDragEnd={handleEditDragEnd}
                onEditCancel={handleEditCancel}
              />
            ) : (
              <NovelMap
                ref={mapHandle}
                locations={locations}
                layout={layout}
                layoutMode={layoutMode}
                layerType={activeLayerType}
                terrainUrl={terrainUrl}
                visibleLocationNames={visibleLocationNames}
                revealedLocationNames={revealedLocationNames}
                regionBoundaries={regionBoundaries}
                portals={portals}
                trajectoryPoints={visibleTrajectory}
                currentLocation={currentLocation}
                canvasSize={mapData?.canvas_size}
                spatialScale={mapData?.spatial_scale}
                focusLocation={focusLocation}
                locationConflicts={mapData?.location_conflicts}
                onLocationClick={handleLocationClick}
                onLocationDragEnd={handleDragEnd}
                onPortalClick={handlePortalClick}
              />
            )
          )}

        </div>

        {/* Right panel */}
        <div className="w-80 flex-shrink-0 border-l flex flex-col">
          {/* Header: tabs + action buttons */}
          <div className="p-2 border-b space-y-1.5">
            {/* Row 1: Tab buttons */}
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
            {/* Row 2: Action buttons */}
            <div className="flex gap-1">
              <Button
                variant="outline"
                size="xs"
                disabled={rebuilding}
                onClick={() => {
                  if (!novelId || rebuilding) return
                  setRebuilding(true)
                  setRebuildProgress("正在初始化...")
                  rebuildHierarchy(novelId, setRebuildProgress)
                    .then((res) => {
                      if (res.changes.length === 0) {
                        setToast("层级无变化")
                        setTimeout(() => setToast(null), 4000)
                      } else {
                        setRebuildResult(res)
                        setSelectedChanges(new Set(res.changes.map((c, i) => c.auto_select ? i : -1).filter(i => i >= 0)))
                      }
                    })
                    .catch(() => {
                      setToast("层级重建失败")
                      setTimeout(() => setToast(null), 4000)
                    })
                    .finally(() => {
                      setRebuilding(false)
                      setRebuildProgress("")
                    })
                }}
              >
                <RefreshCw className={cn("h-3 w-3 mr-1", rebuilding && "animate-spin")} />
                {rebuilding ? "重建中..." : "重建层级"}
              </Button>
              <Button
                variant="outline"
                size="xs"
                onClick={() => setEditorOpen(true)}
              >
                编辑世界
              </Button>
            </div>
          </div>

          {/* Content area */}
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

                {/* Person selector */}
                <div className="space-y-1 mb-3 max-h-48 overflow-auto">
                  {personList.map((person) => (
                    <button
                      key={person}
                      className={cn(
                        "w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-muted/50 transition-colors",
                        selectedPerson === person &&
                          "bg-primary/10 text-primary font-medium",
                      )}
                      onClick={() =>
                        setSelectedPerson(selectedPerson === person ? null : person)
                      }
                    >
                      <span>{person}</span>
                      <span className="text-muted-foreground ml-1">
                        ({trajectories[person]?.length ?? 0}站)
                      </span>
                    </button>
                  ))}
                </div>

                {/* Selected trajectory with playback */}
                {selectedPerson && selectedTrajectory.length > 0 && (
                  <div className="border-t pt-3">
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="text-xs font-medium">
                        {selectedPerson} ({selectedTrajectory.length}站)
                      </h4>
                      <div className="flex gap-1">
                        {playing ? (
                          <Button variant="outline" size="xs" onClick={stopPlay}>
                            停止
                          </Button>
                        ) : (
                          <Button variant="outline" size="xs" onClick={startPlay}>
                            播放
                          </Button>
                        )}
                      </div>
                    </div>

                    {/* Progress bar during playback */}
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
                          <span>
                            Ch.{selectedTrajectory[selectedTrajectory.length - 1]?.chapter}
                          </span>
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
                            className={cn(
                              "flex items-start gap-2 transition-opacity",
                              !isVisible && "opacity-20",
                            )}
                          >
                            {/* Timeline dot + line */}
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

                            {/* Content */}
                            <div className="flex-1 -mt-0.5 pb-1">
                              <span
                                className={cn(
                                  "text-xs hover:underline cursor-pointer",
                                  isCurrent && "font-bold text-amber-600",
                                )}
                                onClick={() => {
                                  handleGeoLocationClick(point.location)
                                  openEntityCard(point.location, "location")
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

        {novelId && <EntityCardDrawer novelId={novelId} />}
        {novelId && (
          <WorldStructureEditor
            novelId={novelId}
            open={editorOpen}
            onClose={() => setEditorOpen(false)}
            onStructureChanged={() => setReloadTrigger((n) => n + 1)}
          />
        )}

        {/* Hierarchy changes preview Dialog */}
        <Dialog open={rebuildResult !== null} onOpenChange={(open) => { if (!open) setRebuildResult(null) }}>
          <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
            <DialogHeader>
              <DialogTitle>层级变更预览</DialogTitle>
              {rebuildResult && (
                <div className="flex items-center gap-3 text-xs text-muted-foreground pt-1">
                  <span className="text-green-600">+{rebuildResult.summary.added} 新增</span>
                  <span className="text-yellow-600">~{rebuildResult.summary.changed} 变更</span>
                  <span className="text-red-600">-{rebuildResult.summary.removed} 移除</span>
                  <span className="mx-1">|</span>
                  <span>根节点 {rebuildResult.summary.old_root_count} → {rebuildResult.summary.new_root_count}</span>
                  {rebuildResult.summary.scene_analysis_used && <span className="px-1 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300">场景分析</span>}
                  {rebuildResult.summary.llm_review_used && <span className="px-1 py-0.5 rounded bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300">LLM审查</span>}
                </div>
              )}
            </DialogHeader>

            {rebuildResult && (
              <div className="flex-1 overflow-auto border rounded-md">
                <table className="w-full text-xs">
                  <thead className="bg-muted/50 sticky top-0">
                    <tr>
                      <th className="w-8 px-2 py-1.5 text-left">
                        <input
                          type="checkbox"
                          checked={selectedChanges.size === rebuildResult.changes.length}
                          ref={(el) => { if (el) el.indeterminate = selectedChanges.size > 0 && selectedChanges.size < rebuildResult.changes.length }}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelectedChanges(new Set(rebuildResult.changes.map((_, i) => i)))
                            } else {
                              setSelectedChanges(new Set())
                            }
                          }}
                        />
                      </th>
                      <th className="px-2 py-1.5 text-left">地点</th>
                      <th className="px-2 py-1.5 text-left">原父级</th>
                      <th className="w-6 px-1 py-1.5" />
                      <th className="px-2 py-1.5 text-left">新父级</th>
                      <th className="w-16 px-2 py-1.5 text-left">类型</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {rebuildResult.changes.map((change, idx) => (
                      <tr key={change.location} className={cn("hover:bg-muted/30", !change.auto_select && "opacity-60")}>
                        <td className="px-2 py-1.5">
                          <input
                            type="checkbox"
                            checked={selectedChanges.has(idx)}
                            onChange={(e) => {
                              setSelectedChanges((prev) => {
                                const next = new Set(prev)
                                if (e.target.checked) next.add(idx)
                                else next.delete(idx)
                                return next
                              })
                            }}
                          />
                        </td>
                        <td className="px-2 py-1.5 font-medium">
                          {change.location}
                          {change.reason && <span className="block text-[10px] text-muted-foreground font-normal">{change.reason}</span>}
                        </td>
                        <td className="px-2 py-1.5 text-muted-foreground">{change.old_parent ?? "—"}</td>
                        <td className="px-1 py-1.5 text-muted-foreground text-center">→</td>
                        <td className="px-2 py-1.5">{change.new_parent ?? "—"}</td>
                        <td className="px-2 py-1.5">
                          <span className={cn(
                            "px-1.5 py-0.5 rounded text-[10px]",
                            change.change_type === "added" && "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
                            change.change_type === "changed" && "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
                            change.change_type === "removed" && "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
                          )}>
                            {change.change_type === "added" ? "新增" : change.change_type === "changed" ? "变更" : "移除"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <DialogFooter className="flex-row items-center gap-2 sm:flex-row">
              <span className="text-xs text-muted-foreground flex-1">
                已选 {selectedChanges.size} / {rebuildResult?.changes.length ?? 0} 项
              </span>
              <Button variant="outline" size="sm" onClick={() => setRebuildResult(null)}>
                取消
              </Button>
              <Button
                size="sm"
                disabled={applying || selectedChanges.size === 0}
                onClick={() => {
                  if (!novelId || !rebuildResult || applying) return
                  setApplying(true)
                  const selected = rebuildResult.changes
                    .filter((_, i) => selectedChanges.has(i))
                    .map((c) => ({ location: c.location, new_parent: c.new_parent }))
                  applyHierarchyChanges(novelId, selected, rebuildResult.location_tiers)
                    .then((res) => {
                      setRebuildResult(null)
                      setToast(`层级已更新: ${res.root_count} 个根节点`)
                      setTimeout(() => setToast(null), 4000)
                      setReloadTrigger((n) => n + 1)
                    })
                    .catch(() => {
                      setToast("应用变更失败")
                      setTimeout(() => setToast(null), 4000)
                    })
                    .finally(() => setApplying(false))
                }}
              >
                {applying ? "应用中..." : `应用 ${selectedChanges.size} 项变更`}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        </div>
      </div>
    </VisualizationLayout>
  )
}

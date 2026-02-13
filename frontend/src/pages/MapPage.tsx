import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import { fetchMapData, saveLocationOverride } from "@/api/client"
import type { MapData, MapLayerInfo } from "@/api/types"
import { useChapterRangeStore } from "@/stores/chapterRangeStore"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { VisualizationLayout } from "@/components/visualization/VisualizationLayout"
import { NovelMap, type NovelMapHandle } from "@/components/visualization/NovelMap"
import { MapLayerTabs } from "@/components/visualization/MapLayerTabs"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { WorldStructureEditor } from "@/components/visualization/WorldStructureEditor"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const TYPE_LEGEND = [
  { label: "界/域/国", color: "#3b82f6" },
  { label: "城镇", color: "#10b981" },
  { label: "山林洞谷", color: "#84cc16" },
  { label: "宗门派", color: "#8b5cf6" },
  { label: "水域", color: "#06b6d4" },
  { label: "天界", color: "#f59e0b" },
  { label: "冥界", color: "#7c3aed" },
  { label: "其他", color: "#6b7280" },
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
        {/* Main: MapLibre map */}
        <div className="relative flex-1">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60">
              <p className="text-muted-foreground">加载地图数据...</p>
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

          {/* Legend */}
          <div className="absolute bottom-3 left-3 z-10 rounded-lg border bg-background/90 p-2">
            <p className="text-muted-foreground mb-1 text-[10px]">地点类型</p>
            {TYPE_LEGEND.map((item) => (
              <div key={item.label} className="flex items-center gap-1.5 text-xs">
                <span
                  className="inline-block size-2.5 rounded-full"
                  style={{ backgroundColor: item.color }}
                />
                {item.label}
              </div>
            ))}
          </div>

          {/* Toast */}
          {toast && (
            <div className="absolute top-3 right-3 z-20 rounded-lg border bg-background px-3 py-2 text-xs shadow-lg">
              {toast}
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
              onLocationClick={handleLocationClick}
              onLocationDragEnd={handleDragEnd}
              onPortalClick={handlePortalClick}
            />
          )}
        </div>

        {/* Right: Trajectory panel */}
        <div className="w-64 flex-shrink-0 overflow-auto border-l">
          <div className="p-3">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-medium">人物轨迹</h3>
              <Button
                variant="outline"
                size="xs"
                onClick={() => setEditorOpen(true)}
              >
                编辑世界
              </Button>
            </div>

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
                            onClick={() =>
                              openEntityCard(point.location, "location")
                            }
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
        </div>
      </div>
    </VisualizationLayout>
  )
}

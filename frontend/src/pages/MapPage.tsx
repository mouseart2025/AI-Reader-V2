import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d"
import { fetchMapData } from "@/api/client"
import { useChapterRangeStore } from "@/stores/chapterRangeStore"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { VisualizationLayout } from "@/components/visualization/VisualizationLayout"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface MapLocation {
  id: string
  name: string
  type: string
  parent: string | null
  level: number
  mention_count: number
}

interface TrajectoryPoint {
  location: string
  chapter: number
}

// Graph node/edge types for force-graph
interface LocNode {
  id: string
  name: string
  type: string
  level: number
  mention_count: number
  x?: number
  y?: number
}

interface LocEdge {
  source: string | LocNode
  target: string | LocNode
  type: "hierarchy" | "trajectory"
  order?: number // trajectory step order
}

// Color by location type
function locationColor(type: string): string {
  const t = type.toLowerCase()
  if (t.includes("国") || t.includes("域") || t.includes("界")) return "#3b82f6"
  if (t.includes("城") || t.includes("镇") || t.includes("都")) return "#10b981"
  if (t.includes("山") || t.includes("洞") || t.includes("谷") || t.includes("林")) return "#84cc16"
  if (t.includes("宗") || t.includes("派") || t.includes("门")) return "#8b5cf6"
  if (t.includes("海") || t.includes("河") || t.includes("湖")) return "#06b6d4"
  return "#6b7280"
}

const TYPE_LEGEND = [
  { label: "界/域/国", color: "#3b82f6" },
  { label: "城镇", color: "#10b981" },
  { label: "山林洞谷", color: "#84cc16" },
  { label: "宗门派", color: "#8b5cf6" },
  { label: "水域", color: "#06b6d4" },
  { label: "其他", color: "#6b7280" },
]

export default function MapPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const { chapterStart, chapterEnd, setAnalyzedRange } = useChapterRangeStore()
  const openEntityCard = useEntityCardStore((s) => s.openCard)

  const [locations, setLocations] = useState<MapLocation[]>([])
  const [trajectories, setTrajectories] = useState<Record<string, TrajectoryPoint[]>>({})
  const [loading, setLoading] = useState(true)
  const [hoverNode, setHoverNode] = useState<string | null>(null)

  // Trajectory state
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [playIndex, setPlayIndex] = useState(0)
  const playTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  // Graph sizing
  const graphRef = useRef<ForceGraphMethods<LocNode, LocEdge>>(undefined)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })

  // Resize observer
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const obs = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      setDimensions({ width, height })
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // Load data
  useEffect(() => {
    if (!novelId) return
    let cancelled = false
    setLoading(true)

    fetchMapData(novelId, chapterStart, chapterEnd)
      .then((data) => {
        if (cancelled) return
        const range = data.analyzed_range as number[]
        if (range && range[0] > 0) {
          setAnalyzedRange(range[0], range[1])
        }
        setLocations((data.locations as MapLocation[]) ?? [])
        setTrajectories((data.trajectories as Record<string, TrajectoryPoint[]>) ?? {})
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [novelId, chapterStart, chapterEnd, setAnalyzedRange])

  // ── Person list sorted by trajectory length ──
  const personList = useMemo(
    () => Object.keys(trajectories).sort((a, b) => (trajectories[b]?.length ?? 0) - (trajectories[a]?.length ?? 0)),
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

  // Set of highlighted location names
  const highlightedLocations = useMemo(
    () => new Set(visibleTrajectory.map((t) => t.location)),
    [visibleTrajectory],
  )

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

  // ── Build trajectory edges for the graph ──
  const trajectoryEdgeSet = useMemo(() => {
    const set = new Set<string>()
    for (let i = 0; i < visibleTrajectory.length - 1; i++) {
      const a = visibleTrajectory[i].location
      const b = visibleTrajectory[i + 1].location
      if (a !== b) {
        set.add(`${a}--${b}`)
        set.add(`${b}--${a}`)
      }
    }
    return set
  }, [visibleTrajectory])

  // ── Build graph data ──
  const graphData = useMemo(() => {
    const nodeMap = new Map<string, LocNode>()
    for (const loc of locations) {
      nodeMap.set(loc.name, {
        id: loc.name,
        name: loc.name,
        type: loc.type,
        level: loc.level,
        mention_count: loc.mention_count,
      })
    }

    // Hierarchy edges (parent → child)
    const edges: LocEdge[] = []
    const locById = new Map(locations.map((l) => [l.id, l]))
    for (const loc of locations) {
      if (loc.parent) {
        const parentLoc = locById.get(loc.parent)
        if (parentLoc && nodeMap.has(parentLoc.name) && nodeMap.has(loc.name)) {
          edges.push({
            source: parentLoc.name,
            target: loc.name,
            type: "hierarchy",
          })
        }
      }
    }

    // Trajectory edges
    if (visibleTrajectory.length > 1) {
      for (let i = 0; i < visibleTrajectory.length - 1; i++) {
        const a = visibleTrajectory[i].location
        const b = visibleTrajectory[i + 1].location
        if (a !== b && nodeMap.has(a) && nodeMap.has(b)) {
          // Avoid duplicate trajectory edges (force-graph handles multi-edges poorly)
          const key = `traj-${a}-${b}`
          if (!edges.some((e) => {
            const src = typeof e.source === "string" ? e.source : e.source.id
            const tgt = typeof e.target === "string" ? e.target : e.target.id
            return e.type === "trajectory" && ((src === a && tgt === b) || (src === b && tgt === a))
          })) {
            edges.push({ source: a, target: b, type: "trajectory", order: i })
          }
        }
      }
    }

    return { nodes: Array.from(nodeMap.values()), links: edges }
  }, [locations, visibleTrajectory])

  // ── Connected nodes on hover ──
  const connectedNodes = useMemo(() => {
    if (!hoverNode) return new Set<string>()
    const connected = new Set<string>([hoverNode])
    for (const e of graphData.links) {
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      if (src === hoverNode) connected.add(tgt)
      if (tgt === hoverNode) connected.add(src)
    }
    return connected
  }, [hoverNode, graphData.links])

  const hasTrajectory = highlightedLocations.size > 0

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

  const handleNodeClick = useCallback(
    (node: LocNode) => {
      openEntityCard(node.name, "location")
    },
    [openEntityCard],
  )

  return (
    <VisualizationLayout>
      <div className="flex h-full">
        {/* Main: Force-directed location graph */}
        <div className="relative flex-1" ref={containerRef}>
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60">
              <p className="text-muted-foreground">Loading map data...</p>
            </div>
          )}

          {!loading && locations.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center">
              <p className="text-muted-foreground">暂无地点数据</p>
            </div>
          )}

          {/* Legend */}
          <div className="absolute top-3 left-3 z-10 rounded-lg border bg-background/90 p-2">
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

          <ForceGraph2D
            ref={graphRef}
            graphData={graphData}
            width={dimensions.width}
            height={dimensions.height}
            nodeLabel={(node: LocNode) =>
              `${node.name} (${node.type} · ${node.mention_count}章)`
            }
            nodeVal={(node: LocNode) => Math.max(3, Math.sqrt(node.mention_count) * 2.5)}
            nodeCanvasObject={(node: LocNode, ctx, globalScale) => {
              const isHighlighted = highlightedLocations.has(node.id)
              const isCurrent = currentLocation === node.id
              const isHovered = hoverNode === node.id
              const isConnected = hoverNode != null && connectedNodes.has(node.id)
              const baseSize = Math.max(3, Math.sqrt(node.mention_count) * 2)
              const size = isCurrent ? baseSize * 1.5 : isHighlighted ? baseSize * 1.2 : baseSize

              // Determine color
              let color = locationColor(node.type)
              if (hasTrajectory && !isHighlighted) {
                color = "#d1d5db"
              }
              if (hoverNode && !isHovered && !isConnected && !isHighlighted) {
                color = "#d1d5db"
              }
              if (isCurrent) {
                color = "#f59e0b"
              }

              // Draw node
              ctx.beginPath()
              ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI)
              ctx.fillStyle = color
              ctx.fill()

              // Ring for current or highlighted trajectory nodes
              if (isCurrent) {
                ctx.beginPath()
                ctx.arc(node.x!, node.y!, size + 2.5, 0, 2 * Math.PI)
                ctx.strokeStyle = "#d97706"
                ctx.lineWidth = 2
                ctx.stroke()
              } else if (isHighlighted) {
                ctx.beginPath()
                ctx.arc(node.x!, node.y!, size + 1.5, 0, 2 * Math.PI)
                ctx.strokeStyle = "#fbbf24"
                ctx.lineWidth = 1
                ctx.stroke()
              }

              // Label — progressive visibility by zoom
              const alwaysShow = isHighlighted || isCurrent || isHovered || isConnected
              const chapterThreshold = Math.max(1, Math.round(15 / globalScale))
              const showLabel = alwaysShow || node.mention_count >= chapterThreshold

              if (showLabel) {
                const fontSize = 12 / globalScale
                ctx.font = isCurrent
                  ? `bold ${fontSize}px sans-serif`
                  : isHighlighted
                    ? `600 ${fontSize}px sans-serif`
                    : `${fontSize}px sans-serif`
                ctx.textAlign = "center"
                ctx.textBaseline = "top"
                ctx.fillStyle = isCurrent
                  ? "#92400e"
                  : hasTrajectory && !isHighlighted
                    ? "#9ca3af"
                    : hoverNode && !isHovered && !isConnected
                      ? "#9ca3af"
                      : "#374151"
                ctx.fillText(node.name, node.x!, node.y! + size + fontSize * 0.2)
              }
            }}
            linkCanvasObject={(edge: LocEdge, ctx, globalScale) => {
              const src = typeof edge.source === "object" ? edge.source : null
              const tgt = typeof edge.target === "object" ? edge.target : null
              if (!src || !tgt || src.x == null || tgt.x == null) return

              ctx.beginPath()
              ctx.moveTo(src.x!, src.y!)
              ctx.lineTo(tgt.x!, tgt.y!)

              if (edge.type === "trajectory") {
                ctx.strokeStyle = "#f59e0b"
                ctx.lineWidth = 2.5 / globalScale
                ctx.setLineDash([])
                ctx.stroke()

                // Draw arrow at midpoint
                const mx = (src.x! + tgt.x!) / 2
                const my = (src.y! + tgt.y!) / 2
                const angle = Math.atan2(tgt.y! - src.y!, tgt.x! - src.x!)
                const arrowLen = 6 / globalScale
                ctx.beginPath()
                ctx.moveTo(mx, my)
                ctx.lineTo(
                  mx - arrowLen * Math.cos(angle - Math.PI / 6),
                  my - arrowLen * Math.sin(angle - Math.PI / 6),
                )
                ctx.moveTo(mx, my)
                ctx.lineTo(
                  mx - arrowLen * Math.cos(angle + Math.PI / 6),
                  my - arrowLen * Math.sin(angle + Math.PI / 6),
                )
                ctx.strokeStyle = "#d97706"
                ctx.lineWidth = 1.5 / globalScale
                ctx.stroke()
              } else {
                // Hierarchy edge
                const srcId = src.id
                const tgtId = tgt.id
                const isOnTrajectory =
                  hasTrajectory &&
                  (trajectoryEdgeSet.has(`${srcId}--${tgtId}`) ||
                    trajectoryEdgeSet.has(`${tgtId}--${srcId}`))

                if (hasTrajectory && !isOnTrajectory) {
                  ctx.strokeStyle = "#e5e7eb"
                } else if (hoverNode) {
                  const connected = connectedNodes.has(srcId) && connectedNodes.has(tgtId)
                  ctx.strokeStyle = connected ? "#9ca3af" : "#e5e7eb"
                } else {
                  ctx.strokeStyle = "#c4c8d0"
                }
                ctx.lineWidth = 1 / globalScale
                ctx.setLineDash([4 / globalScale, 3 / globalScale])
                ctx.stroke()
                ctx.setLineDash([])
              }
            }}
            linkDirectionalParticles={0}
            onNodeClick={handleNodeClick}
            onNodeHover={(node: LocNode | null) => setHoverNode(node?.id ?? null)}
            cooldownTicks={150}
            d3AlphaDecay={0.015}
            d3VelocityDecay={0.25}
          />
        </div>

        {/* Right: Trajectory panel */}
        <div className="w-64 flex-shrink-0 overflow-auto border-l">
          <div className="p-3">
            <h3 className="text-sm font-medium mb-2">人物轨迹</h3>

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
                            onClick={() => openEntityCard(point.location, "location")}
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
      </div>
    </VisualizationLayout>
  )
}

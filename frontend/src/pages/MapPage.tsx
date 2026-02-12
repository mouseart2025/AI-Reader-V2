import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
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

interface TreeNode {
  location: MapLocation
  children: TreeNode[]
  expanded: boolean
}

function buildTree(locations: MapLocation[]): TreeNode[] {
  const byId = new Map<string, TreeNode>()
  const roots: TreeNode[] = []

  // Create all nodes
  for (const loc of locations) {
    byId.set(loc.id, { location: loc, children: [], expanded: loc.level <= 1 })
  }

  // Build parent-child hierarchy
  for (const loc of locations) {
    const node = byId.get(loc.id)!
    if (loc.parent) {
      const parentNode = byId.get(loc.parent)
      if (parentNode) {
        parentNode.children.push(node)
      } else {
        roots.push(node)
      }
    } else {
      roots.push(node)
    }
  }

  // Sort children by mention count desc
  function sortChildren(nodes: TreeNode[]) {
    nodes.sort((a, b) => b.location.mention_count - a.location.mention_count)
    for (const n of nodes) sortChildren(n.children)
  }
  sortChildren(roots)

  return roots
}

export default function MapPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const { chapterStart, chapterEnd, setAnalyzedRange } = useChapterRangeStore()
  const openEntityCard = useEntityCardStore((s) => s.openCard)

  const [locations, setLocations] = useState<MapLocation[]>([])
  const [trajectories, setTrajectories] = useState<Record<string, TrajectoryPoint[]>>({})
  const [loading, setLoading] = useState(true)

  // Tree state
  const [treeRoots, setTreeRoots] = useState<TreeNode[]>([])
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null)

  // Animation state
  const [playing, setPlaying] = useState(false)
  const [playIndex, setPlayIndex] = useState(0)
  const playTimer = useRef<ReturnType<typeof setInterval> | null>(null)

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
        const locs = (data.locations as MapLocation[]) ?? []
        setLocations(locs)
        setTrajectories((data.trajectories as Record<string, TrajectoryPoint[]>) ?? {})
        setTreeRoots(buildTree(locs))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [novelId, chapterStart, chapterEnd, setAnalyzedRange])

  const personList = useMemo(
    () => Object.keys(trajectories).sort((a, b) => (trajectories[b]?.length ?? 0) - (trajectories[a]?.length ?? 0)),
    [trajectories],
  )

  const selectedTrajectory = useMemo(
    () => (selectedPerson ? trajectories[selectedPerson] ?? [] : []),
    [selectedPerson, trajectories],
  )

  // Visible trajectory points (for animation: only show up to playIndex)
  const visibleTrajectory = useMemo(() => {
    if (!playing && playIndex === 0) return selectedTrajectory
    return selectedTrajectory.slice(0, playIndex + 1)
  }, [selectedTrajectory, playing, playIndex])

  // Set of highlighted location names (from visible trajectory)
  const highlightedLocations = useMemo(
    () => new Set(visibleTrajectory.map((t) => t.location)),
    [visibleTrajectory],
  )

  // Count consecutive chapters at each location for dot sizing
  const stayDurations = useMemo(() => {
    const durations = new Map<string, number>()
    for (const traj of selectedTrajectory) {
      durations.set(traj.location, (durations.get(traj.location) ?? 0) + 1)
    }
    return durations
  }, [selectedTrajectory])

  // Animation controls
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

  // Animation step
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

  // Reset animation when person changes
  useEffect(() => {
    stopPlay()
    setPlayIndex(0)
  }, [selectedPerson, stopPlay])

  // Toggle tree node expansion
  const toggleNode = useCallback((nodeId: string) => {
    setTreeRoots((prev) => {
      function toggle(nodes: TreeNode[]): TreeNode[] {
        return nodes.map((n) => {
          if (n.location.id === nodeId) {
            return { ...n, expanded: !n.expanded }
          }
          return { ...n, children: toggle(n.children) }
        })
      }
      return toggle(prev)
    })
  }, [])

  const handleLocationClick = useCallback(
    (name: string) => {
      openEntityCard(name, "location")
    },
    [openEntityCard],
  )

  // Render tree node
  function renderTreeNode(node: TreeNode, depth: number) {
    const loc = node.location
    const hasChildren = node.children.length > 0
    const color = locationColor(loc.type)
    const isHighlighted = highlightedLocations.has(loc.name)
    const stayCount = stayDurations.get(loc.name) ?? 0

    return (
      <div key={loc.id}>
        <div
          className={cn(
            "flex items-center gap-2 py-1.5 px-2 rounded-md hover:bg-muted/50 cursor-pointer transition-colors",
            isHighlighted && "bg-amber-50 dark:bg-amber-950/30",
          )}
          style={{ paddingLeft: `${depth * 20 + 8}px` }}
        >
          {/* Expand/collapse toggle */}
          {hasChildren ? (
            <button
              className="text-muted-foreground hover:text-foreground w-4 flex-shrink-0"
              onClick={() => toggleNode(loc.id)}
            >
              {node.expanded ? "▾" : "▸"}
            </button>
          ) : (
            <span className="w-4 flex-shrink-0" />
          )}

          {/* Color dot — larger if stayed for 3+ chapters */}
          <span
            className={cn(
              "inline-block rounded-full flex-shrink-0",
              isHighlighted && stayCount >= 3 ? "size-3.5" : "size-2.5",
            )}
            style={{ backgroundColor: color }}
          />

          {/* Name */}
          <span
            className={cn(
              "text-sm hover:underline flex-1 truncate",
              isHighlighted && "font-medium",
            )}
            onClick={() => handleLocationClick(loc.name)}
          >
            {loc.name}
          </span>

          {/* Type badge */}
          <span className="text-muted-foreground text-[10px] flex-shrink-0">
            {loc.type}
          </span>

          {/* Mention count */}
          <span className="text-muted-foreground text-[10px] w-8 text-right flex-shrink-0">
            {loc.mention_count}章
          </span>
        </div>

        {/* Children */}
        {hasChildren && node.expanded && (
          <div>
            {node.children.map((child) => renderTreeNode(child, depth + 1))}
          </div>
        )}
      </div>
    )
  }

  return (
    <VisualizationLayout activeTab="map">
      <div className="flex h-full">
        {/* Left: Location tree */}
        <div className="flex-1 overflow-auto border-r">
          {loading && (
            <div className="flex items-center justify-center h-full">
              <p className="text-muted-foreground">Loading map data...</p>
            </div>
          )}

          {!loading && locations.length === 0 && (
            <div className="flex items-center justify-center h-full">
              <p className="text-muted-foreground">暂无地点数据</p>
            </div>
          )}

          {!loading && locations.length > 0 && (
            <div className="p-3">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium">地点层级 ({locations.length})</h3>
                {/* Type legend */}
                <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                  {[
                    { label: "界/域", color: "#3b82f6" },
                    { label: "城镇", color: "#10b981" },
                    { label: "山林", color: "#84cc16" },
                    { label: "宗门", color: "#8b5cf6" },
                    { label: "水域", color: "#06b6d4" },
                  ].map((item) => (
                    <span key={item.label} className="flex items-center gap-1">
                      <span
                        className="inline-block size-2 rounded-full"
                        style={{ backgroundColor: item.color }}
                      />
                      {item.label}
                    </span>
                  ))}
                </div>
              </div>

              <div className="space-y-0.5">
                {treeRoots.map((root) => renderTreeNode(root, 0))}
              </div>
            </div>
          )}
        </div>

        {/* Right: Trajectory panel */}
        <div className="w-72 flex-shrink-0 overflow-auto">
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
                    {selectedPerson} 的轨迹 ({selectedTrajectory.length}站)
                  </h4>
                  <div className="flex gap-1">
                    {playing ? (
                      <Button variant="outline" size="xs" onClick={stopPlay}>
                        暂停
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
                      <span>Ch.{selectedTrajectory[playIndex]?.chapter}</span>
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
                        key={`${point.chapter}-${point.location}`}
                        className={cn(
                          "flex items-start gap-2 transition-opacity",
                          !isVisible && "opacity-20",
                        )}
                      >
                        {/* Timeline line */}
                        <div className="flex flex-col items-center">
                          <div className={cn(
                            "rounded-full bg-primary flex-shrink-0 transition-all",
                            isCurrent ? "size-3 ring-2 ring-primary/30" : stays >= 3 ? "size-2.5" : "size-2",
                            i === 0 && "ring-2 ring-primary/30",
                          )} />
                          {i < selectedTrajectory.length - 1 && (
                            <div className="w-px h-6 bg-border" />
                          )}
                        </div>

                        {/* Content */}
                        <div className="flex-1 -mt-0.5 pb-2">
                          <span
                            className={cn(
                              "text-xs hover:underline cursor-pointer",
                              isCurrent && "font-bold text-primary",
                            )}
                            onClick={() => handleLocationClick(point.location)}
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

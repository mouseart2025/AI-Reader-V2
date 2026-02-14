import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d"
import { fetchGraphData } from "@/api/client"
import { useChapterRangeStore } from "@/stores/chapterRangeStore"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { VisualizationLayout } from "@/components/visualization/VisualizationLayout"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

interface GraphNode {
  id: string
  name: string
  type: string
  chapter_count: number
  org: string
  aliases?: string[]
  x?: number
  y?: number
}

interface GraphEdge {
  source: string | GraphNode
  target: string | GraphNode
  relation_type: string
  weight: number
  chapters: number[]
}

// Color palette for organizations
const ORG_COLORS = [
  "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#ec4899", "#06b6d4", "#84cc16", "#f97316", "#6366f1",
]

// Relation type to color category
function edgeColor(type: string): string {
  const t = type
  // Intimate — check before family (夫/妻 was previously in family)
  if (t.includes("夫") || t.includes("妻") || t.includes("恋") || t.includes("情侣") || t.includes("情人") || t.includes("爱人") || t.includes("宠")) return "#ec4899"
  // Family/blood
  if (t.includes("亲") || t.includes("父") || t.includes("母") || t.includes("兄") || t.includes("姐") || t.includes("弟") || t.includes("妹") || t.includes("叔") || t.includes("侄")) return "#f59e0b"
  // Friendly/alliance
  if (t.includes("友") || t.includes("盟") || t.includes("同门") || t.includes("师")) return "#10b981"
  // Hostile
  if (t.includes("敌") || t.includes("仇") || t.includes("对手")) return "#ef4444"
  return "#6b7280"
}

/**
 * BFS to find shortest path between two nodes in the graph.
 * Returns array of node IDs forming the path, or empty array if no path.
 */
function bfsPath(nodeIds: Set<string>, edges: GraphEdge[], startId: string, endId: string): string[] {
  if (startId === endId) return [startId]
  if (!nodeIds.has(startId) || !nodeIds.has(endId)) return []

  // Build adjacency list
  const adj = new Map<string, string[]>()
  for (const id of nodeIds) adj.set(id, [])
  for (const e of edges) {
    const src = typeof e.source === "string" ? e.source : e.source.id
    const tgt = typeof e.target === "string" ? e.target : e.target.id
    adj.get(src)?.push(tgt)
    adj.get(tgt)?.push(src)
  }

  const visited = new Set<string>([startId])
  const parent = new Map<string, string>()
  const queue = [startId]

  while (queue.length > 0) {
    const current = queue.shift()!
    for (const neighbor of adj.get(current) ?? []) {
      if (visited.has(neighbor)) continue
      visited.add(neighbor)
      parent.set(neighbor, current)
      if (neighbor === endId) {
        // Reconstruct path
        const path: string[] = []
        let node: string | undefined = endId
        while (node !== undefined) {
          path.unshift(node)
          node = parent.get(node)
        }
        return path
      }
      queue.push(neighbor)
    }
  }
  return []
}

export default function GraphPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const { chapterStart, chapterEnd, setAnalyzedRange } = useChapterRangeStore()
  const openEntityCard = useEntityCardStore((s) => s.openCard)

  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [edges, setEdges] = useState<GraphEdge[]>([])
  const [loading, setLoading] = useState(true)
  const [hoverNode, setHoverNode] = useState<string | null>(null)

  // Filters
  const [minChapters, setMinChapters] = useState(1)
  const [showFilters, setShowFilters] = useState(false)

  // Path finding state
  const [pathStart, setPathStart] = useState<string | null>(null)
  const [pathNodes, setPathNodes] = useState<Set<string>>(new Set())
  const [pathEdges, setPathEdges] = useState<Set<string>>(new Set())
  const [pathSearchA, setPathSearchA] = useState("")
  const [pathSearchB, setPathSearchB] = useState("")
  const [showPathPanel, setShowPathPanel] = useState(false)
  const [pathInfo, setPathInfo] = useState<string[]>([]) // Node names along path

  const graphRef = useRef<ForceGraphMethods<GraphNode, GraphEdge>>(undefined)
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

    fetchGraphData(novelId, chapterStart, chapterEnd)
      .then((data) => {
        if (cancelled) return
        const range = data.analyzed_range as number[]
        if (range && range[0] > 0) {
          setAnalyzedRange(range[0], range[1])
        }
        setNodes((data.nodes as GraphNode[]) ?? [])
        setEdges((data.edges as GraphEdge[]) ?? [])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [novelId, chapterStart, chapterEnd, setAnalyzedRange])

  // Org -> color mapping
  const orgColorMap = useMemo(() => {
    const orgs = [...new Set(nodes.map((n) => n.org).filter(Boolean))]
    const map = new Map<string, string>()
    orgs.forEach((org, i) => map.set(org, ORG_COLORS[i % ORG_COLORS.length]))
    return map
  }, [nodes])

  // Filter nodes: chapter threshold + must have at least 1 edge (remove isolated dots)
  const { filteredNodes, filteredEdges, filteredNodeIds, degreeMap } = useMemo(() => {
    const chapterFiltered = nodes.filter((n) => n.chapter_count >= minChapters)
    const chapterIds = new Set(chapterFiltered.map((n) => n.id))
    const validEdges = edges.filter((e) => {
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      return chapterIds.has(src) && chapterIds.has(tgt)
    })

    // Compute degree for each node
    const deg = new Map<string, number>()
    for (const e of validEdges) {
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      deg.set(src, (deg.get(src) || 0) + 1)
      deg.set(tgt, (deg.get(tgt) || 0) + 1)
    }

    // Remove degree-0 nodes (they have no edges and just clutter the viewport)
    const connected = chapterFiltered.filter((n) => (deg.get(n.id) || 0) > 0)
    const connectedIds = new Set(connected.map((n) => n.id))

    return {
      filteredNodes: connected,
      filteredEdges: validEdges.filter((e) => {
        const src = typeof e.source === "string" ? e.source : e.source.id
        const tgt = typeof e.target === "string" ? e.target : e.target.id
        return connectedIds.has(src) && connectedIds.has(tgt)
      }),
      filteredNodeIds: connectedIds,
      degreeMap: deg,
    }
  }, [nodes, edges, minChapters])

  // Highlight connected nodes on hover
  const connectedNodes = useMemo(() => {
    if (!hoverNode) return new Set<string>()
    const connected = new Set<string>([hoverNode])
    for (const e of filteredEdges) {
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      if (src === hoverNode) connected.add(tgt)
      if (tgt === hoverNode) connected.add(src)
    }
    return connected
  }, [hoverNode, filteredEdges])

  // Build edge key helper
  const edgeKey = useCallback((e: GraphEdge) => {
    const src = typeof e.source === "string" ? e.source : e.source.id
    const tgt = typeof e.target === "string" ? e.target : e.target.id
    return `${src}--${tgt}`
  }, [])

  // Run path finding
  const findPath = useCallback((startId: string, endId: string) => {
    const path = bfsPath(filteredNodeIds, filteredEdges, startId, endId)
    if (path.length === 0) {
      setPathNodes(new Set())
      setPathEdges(new Set())
      setPathInfo([])
      return
    }

    const pNodes = new Set(path)
    const pEdges = new Set<string>()
    for (let i = 0; i < path.length - 1; i++) {
      pEdges.add(`${path[i]}--${path[i + 1]}`)
      pEdges.add(`${path[i + 1]}--${path[i]}`)
    }

    // Get names for display
    const nodeMap = new Map(filteredNodes.map((n) => [n.id, n.name]))
    const names = path.map((id) => nodeMap.get(id) ?? id)

    setPathNodes(pNodes)
    setPathEdges(pEdges)
    setPathInfo(names)
  }, [filteredNodeIds, filteredEdges, filteredNodes])

  // Handle search-based path finding
  const handleSearchPath = useCallback(() => {
    const a = pathSearchA.trim()
    const b = pathSearchB.trim()
    if (!a || !b) return

    const nodeA = filteredNodes.find((n) => n.name === a)
    const nodeB = filteredNodes.find((n) => n.name === b)
    if (!nodeA || !nodeB) return

    findPath(nodeA.id, nodeB.id)
  }, [pathSearchA, pathSearchB, filteredNodes, findPath])

  // Clear path
  const clearPath = useCallback(() => {
    setPathStart(null)
    setPathNodes(new Set())
    setPathEdges(new Set())
    setPathInfo([])
  }, [])

  const graphData = useMemo(
    () => ({ nodes: filteredNodes, links: filteredEdges }),
    [filteredNodes, filteredEdges],
  )

  // Customize D3 forces: degree-proportional charge + radial containment
  useEffect(() => {
    const fg = graphRef.current
    if (!fg || filteredNodes.length === 0) return

    // Charge: high-degree nodes repel strongly, low-degree nodes weakly
    const charge = fg.d3Force("charge")
    if (charge && typeof charge.strength === "function") {
      charge.strength((node: GraphNode) => {
        const deg = degreeMap.get(node.id) || 0
        return deg <= 1 ? -8 : -Math.min(30, 8 + deg * 3)
      })
    }

    // Add soft radial containment — pulls nodes beyond threshold back toward center
    const containRadius = Math.max(150, Math.sqrt(filteredNodes.length) * 30)
    const containStrength = 0.08
    interface SimNode { x?: number; y?: number; vx?: number; vy?: number }
    let simNodes: SimNode[] = []
    const containment = Object.assign(
      (alpha: number) => {
        for (const node of simNodes) {
          const x = node.x || 0
          const y = node.y || 0
          const dist = Math.sqrt(x * x + y * y)
          if (dist > containRadius) {
            const k = ((dist - containRadius) / dist) * containStrength * alpha
            node.vx = (node.vx || 0) - x * k
            node.vy = (node.vy || 0) - y * k
          }
        }
      },
      { initialize: (nodes: SimNode[]) => { simNodes = nodes } },
    )
    fg.d3Force("containment", containment as never)

    fg.d3ReheatSimulation()
  }, [graphData, degreeMap, filteredNodes.length])

  // Auto zoom-to-fit after layout stabilizes
  const handleEngineStop = useCallback(() => {
    graphRef.current?.zoomToFit(400, 60)
  }, [])

  const handleNodeClick = useCallback(
    (node: GraphNode, event: MouseEvent) => {
      // Shift+click for path finding
      if (event.shiftKey) {
        if (!pathStart) {
          setPathStart(node.id)
          setPathNodes(new Set([node.id]))
          setPathEdges(new Set())
          setPathInfo([node.name])
        } else {
          findPath(pathStart, node.id)
          setPathStart(null)
        }
        return
      }

      // Normal click: clear path and open entity card
      clearPath()
      openEntityCard(node.name, "person")
    },
    [openEntityCard, pathStart, findPath, clearPath],
  )

  // Is there an active path highlight?
  const hasPath = pathNodes.size > 1

  return (
    <VisualizationLayout>
      <div className="relative h-full" ref={containerRef}>
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60">
            <p className="text-muted-foreground">Loading graph...</p>
          </div>
        )}

        {/* Filter panel toggle */}
        <div className="absolute top-3 left-3 z-10 flex gap-1">
          <Button
            variant="outline"
            size="xs"
            onClick={() => { setShowFilters(!showFilters); setShowPathPanel(false) }}
          >
            筛选
          </Button>
          <Button
            variant={showPathPanel ? "default" : "outline"}
            size="xs"
            onClick={() => { setShowPathPanel(!showPathPanel); setShowFilters(false) }}
          >
            路径查找
          </Button>

          {showFilters && (
            <div className="absolute top-8 left-0 w-52 rounded-lg border bg-background p-3 shadow-lg">
              <div className="mb-2">
                <label className="text-muted-foreground text-xs">最少出场章节</label>
                <Input
                  type="number"
                  min={1}
                  value={minChapters}
                  onChange={(e) => setMinChapters(Math.max(1, Number(e.target.value)))}
                  className="mt-1 h-7 text-xs"
                />
              </div>
              <p className="text-muted-foreground text-[10px]">
                显示 {filteredNodes.length} / {nodes.length} 人物（隐藏无关系的孤立节点）,{" "}
                {filteredEdges.length} 条关系
              </p>
            </div>
          )}

          {showPathPanel && (
            <div className="absolute top-8 left-0 w-64 rounded-lg border bg-background p-3 shadow-lg space-y-2">
              <p className="text-xs text-muted-foreground">
                Shift+点击两个人物节点，或输入名字搜索
              </p>
              <Input
                placeholder="人物 A"
                value={pathSearchA}
                onChange={(e) => setPathSearchA(e.target.value)}
                className="h-7 text-xs"
              />
              <Input
                placeholder="人物 B"
                value={pathSearchB}
                onChange={(e) => setPathSearchB(e.target.value)}
                className="h-7 text-xs"
              />
              <div className="flex gap-1">
                <Button size="xs" onClick={handleSearchPath}>
                  查找
                </Button>
                <Button variant="ghost" size="xs" onClick={clearPath}>
                  清除
                </Button>
              </div>
              {pathInfo.length > 1 && (
                <div className="border-t pt-2">
                  <p className="text-[10px] text-muted-foreground mb-1">
                    最短路径 ({pathInfo.length - 1} 步)
                  </p>
                  <p className="text-xs">{pathInfo.join(" → ")}</p>
                </div>
              )}
              {pathInfo.length === 0 && pathStart && (
                <p className="text-[10px] text-amber-600">
                  已选择起点，请 Shift+点击终点
                </p>
              )}
            </div>
          )}
        </div>

        {/* Path indicator bar */}
        {(hasPath || pathStart) && (
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 rounded-full border bg-background/95 px-4 py-1.5 shadow-lg flex items-center gap-2">
            {hasPath ? (
              <>
                <span className="text-xs">{pathInfo.join(" → ")}</span>
                <Button variant="ghost" size="xs" onClick={clearPath}>
                  清除
                </Button>
              </>
            ) : (
              <span className="text-xs text-amber-600">
                Shift+点击第二个人物查找路径
              </span>
            )}
          </div>
        )}

        {/* Legend */}
        <div className="absolute top-3 right-3 z-10 flex flex-col gap-2">
          {/* Relationship line colors */}
          <div className="rounded-lg border bg-background/90 p-2">
            <p className="text-muted-foreground mb-1 text-[10px]">关系线</p>
            {[
              { label: "亲密", color: "#ec4899", desc: "夫妻/恋人/宠物" },
              { label: "亲属", color: "#f59e0b", desc: "父母/兄弟/叔侄" },
              { label: "友好", color: "#10b981", desc: "师徒/同门/盟友" },
              { label: "敌对", color: "#ef4444", desc: "仇人/对手" },
              { label: "其他", color: "#6b7280", desc: "一般关系" },
            ].map((item) => (
              <div key={item.label} className="flex items-center gap-1.5 text-xs">
                <span
                  className="inline-block h-0.5 w-4 rounded-full"
                  style={{ backgroundColor: item.color }}
                />
                <span>{item.label}</span>
                <span className="text-muted-foreground text-[10px]">{item.desc}</span>
              </div>
            ))}
          </div>

          {/* Organization node colors */}
          {orgColorMap.size > 0 && (
            <div className="rounded-lg border bg-background/90 p-2">
              <p className="text-muted-foreground mb-1 text-[10px]">组织</p>
              {Array.from(orgColorMap.entries()).map(([org, color]) => (
                <div key={org} className="flex items-center gap-1.5 text-xs">
                  <span
                    className="inline-block size-2.5 rounded-full"
                    style={{ backgroundColor: color }}
                  />
                  {org}
                </div>
              ))}
            </div>
          )}
        </div>

        <ForceGraph2D
          ref={graphRef}
          graphData={graphData}
          width={dimensions.width}
          height={dimensions.height}
          nodeLabel={(node: GraphNode) => {
            const aliasStr = node.aliases?.length ? ` (${node.aliases.join("/")})` : ""
            return `${node.name}${aliasStr} — ${node.chapter_count}章${node.org ? ` · ${node.org}` : ""}`
          }}
          nodeVal={(node: GraphNode) => Math.max(2, Math.sqrt(node.chapter_count) * 2)}
          nodeColor={(node: GraphNode) => {
            // Path highlight takes priority
            if (hasPath) {
              if (pathNodes.has(node.id)) return "#f59e0b"
              return "#e5e7eb"
            }
            if (pathStart === node.id) return "#f59e0b"
            if (hoverNode && !connectedNodes.has(node.id)) return "#d1d5db"
            return node.org ? (orgColorMap.get(node.org) ?? "#6b7280") : "#6b7280"
          }}
          nodeCanvasObject={(node: GraphNode, ctx, globalScale) => {
            const isOnPath = hasPath && pathNodes.has(node.id)
            const isPathStart = pathStart === node.id
            const isHovered = hoverNode === node.id
            const isConnected = hoverNode != null && connectedNodes.has(node.id)
            const size = isOnPath
              ? Math.max(5, Math.sqrt(node.chapter_count) * 2)
              : Math.max(3, Math.sqrt(node.chapter_count) * 1.5)

            const color = isOnPath || isPathStart
              ? "#f59e0b"
              : hasPath
                ? "#e5e7eb"
                : hoverNode && !connectedNodes.has(node.id)
                  ? "#d1d5db"
                  : node.org
                    ? (orgColorMap.get(node.org) ?? "#6b7280")
                    : "#6b7280"

            // Draw circle
            ctx.beginPath()
            ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI)
            ctx.fillStyle = color
            ctx.fill()

            // Path node ring
            if (isOnPath || isPathStart) {
              ctx.beginPath()
              ctx.arc(node.x!, node.y!, size + 2, 0, 2 * Math.PI)
              ctx.strokeStyle = "#d97706"
              ctx.lineWidth = 1.5
              ctx.stroke()
            }

            // Draw label — constant screen-pixel size, progressive visibility by zoom
            // Priority labels always shown; others shown progressively as user zooms in
            const alwaysShow = isOnPath || isPathStart || isHovered || isConnected
            // Threshold decreases as you zoom in: zoom 1x → need 20+ chapters,
            // zoom 2x → 10+, zoom 4x → 5+, zoom 8x → 2+
            const chapterThreshold = Math.max(2, Math.round(20 / globalScale))
            const showLabel = alwaysShow || node.chapter_count >= chapterThreshold

            if (showLabel) {
              // Constant 12px on screen regardless of zoom level
              const fontSize = 12 / globalScale
              ctx.font = isOnPath ? `bold ${fontSize}px sans-serif` : `${fontSize}px sans-serif`
              ctx.textAlign = "center"
              ctx.textBaseline = "top"
              ctx.fillStyle = isOnPath || isPathStart
                ? "#92400e"
                : hasPath
                  ? "#d1d5db"
                  : hoverNode && !connectedNodes.has(node.id)
                    ? "#9ca3af"
                    : "#374151"
              ctx.fillText(node.name, node.x!, node.y! + size + fontSize * 0.2)
            }
          }}
          linkColor={(edge: GraphEdge) => {
            const ek = edgeKey(edge)
            if (hasPath) {
              if (pathEdges.has(ek)) return "#f59e0b"
              return "#f3f4f6"
            }
            if (hoverNode) {
              const src = typeof edge.source === "string" ? edge.source : edge.source.id
              const tgt = typeof edge.target === "string" ? edge.target : edge.target.id
              if (!connectedNodes.has(src) || !connectedNodes.has(tgt)) return "#e5e7eb"
            }
            return edgeColor(edge.relation_type)
          }}
          linkWidth={(edge: GraphEdge) => {
            if (hasPath && pathEdges.has(edgeKey(edge))) return 3
            return Math.max(0.5, Math.min(edge.weight, 5))
          }}
          linkLabel={(edge: GraphEdge) => edge.relation_type}
          linkDirectionalParticles={0}
          onNodeClick={handleNodeClick}
          onNodeHover={(node: GraphNode | null) => setHoverNode(node?.id ?? null)}
          cooldownTicks={100}
          d3AlphaDecay={0.02}
          d3VelocityDecay={0.3}
          onEngineStop={handleEngineStop}
        />

        {novelId && <EntityCardDrawer novelId={novelId} />}
      </div>
    </VisualizationLayout>
  )
}
